"""Rebuilding the chunk index: a new index on the current mapping, filled from the live one, then an alias swap.

The platform reads and writes the chunk index by one name. After the swap that name is an alias, so search,
ingestion and deletion keep working unchanged while the physical index behind it changes (ADR-0001). The same
procedure serves the move to the lucene engine and any change of embedding model or dimension.

- Explicit: nothing in the API process calls this; an operator runs ``python -m src.cli.reindex``.
- Resumable: the new index is named ``<alias>-v<N>`` and chunks keep their ids, so a run that finds an unfinished
  ``-v<N>`` newer than the live index carries on into it and copies only what is missing.
- Safe: the alias moves only after every chunk is in the new index and the counts agree. Until then search reads
  the old index, untouched.
"""

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .index_config_hybrid import KNN_ENGINE, build_chunks_mapping

SCROLL = "5m"


class ReindexError(Exception):
    """The reindex cannot proceed or cannot safely finish; the message tells the operator what to do."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class ReindexResult:
    source: str
    target: str
    copied: int = 0
    already_there: int = 0
    re_embedded: bool = False
    resumed: bool = False
    swapped: bool = False
    removed_old: bool = False
    dry_run: bool = False


@dataclass
class _Live:
    index: str  # the concrete index holding the data
    is_alias: bool  # False: a concrete index still carries the name the alias is about to take


def _live_index(raw: Any, alias: str) -> _Live:
    if raw.indices.exists_alias(name=alias):
        targets = sorted(raw.indices.get_alias(name=alias))
        if len(targets) != 1:
            raise ReindexError(f"The alias '{alias}' points at {len(targets)} indices ({', '.join(targets)}); expected one.")
        return _Live(targets[0], True)
    if raw.indices.exists(index=alias):
        return _Live(alias, False)
    raise ReindexError(
        f"There is no chunk index '{alias}' to reindex. Start the API once so it creates the index, or ingest a document.",
        exit_code=2,
    )


def _versions(raw: Any, alias: str) -> Dict[str, int]:
    pattern = re.compile(rf"^{re.escape(alias)}-v(\d+)$")
    found = raw.indices.get(index=f"{alias}-v*") or {}
    return {name: int(m.group(1)) for name in found if (m := pattern.match(name))}


def _embedding_properties(raw: Any, index: str) -> Dict[str, Any]:
    mapping = raw.indices.get_mapping(index=index)
    return next(iter(mapping.values()))["mappings"]["properties"].get("embedding", {})


def _source_dimension(raw: Any, index: str) -> Optional[int]:
    dimension = _embedding_properties(raw, index).get("dimension")
    return int(dimension) if dimension is not None else None


def _scroll(raw: Any, index: str, batch_size: int):
    """Every document of ``index`` in pages, as ``(total_hint, page)``; the scroll is cleared when the caller is done."""
    response = raw.search(index=index, body={"query": {"match_all": {}}}, scroll=SCROLL, size=batch_size)
    scroll_id = response.get("_scroll_id")
    try:
        while True:
            hits = response["hits"]["hits"]
            if not hits:
                return
            yield hits
            response = raw.scroll(scroll_id=scroll_id, scroll=SCROLL)
            scroll_id = response.get("_scroll_id", scroll_id)
    finally:
        if scroll_id:
            try:
                raw.clear_scroll(scroll_id=scroll_id)
            except Exception:  # noqa: BLE001 - an expired scroll is not worth failing a finished copy for
                pass


def _missing(raw: Any, target: str, ids: List[str]) -> set:
    found = raw.mget(index=target, body={"ids": ids}, _source=False)
    present = {doc["_id"] for doc in found["docs"] if doc.get("found")}
    return set(ids) - present


async def run_reindex(
    opensearch: Any,
    dimension: int,
    embeddings_factory: Optional[Callable[[], Any]] = None,
    re_embed: bool = False,
    batch_size: int = 200,
    delete_old: bool = False,
    dry_run: bool = False,
    say: Callable[[str], None] = print,
) -> ReindexResult:
    """Reindex ``opensearch``'s chunk index onto the current mapping at ``dimension``.

    Chunks are re-embedded when the dimension changed or ``re_embed`` is set (a new embedding model of the same
    dimension); otherwise their vectors are copied as they are.

    :raises ReindexError: when it cannot start, or cannot finish safely (nothing is swapped in that case).
    """
    raw, alias = opensearch.client, opensearch.index_name
    live = _live_index(raw, alias)
    versions = _versions(raw, alias)
    unfinished = [name for name, _ in sorted(versions.items(), key=lambda item: item[1], reverse=True)[:1] if name != live.index]
    resumed = bool(unfinished)
    target = unfinished[0] if resumed else f"{alias}-v{max(versions.values(), default=0) + 1}"

    source_dimension = _source_dimension(raw, live.index)
    embed = re_embed or source_dimension != dimension
    total = raw.count(index=live.index)["count"]
    result = ReindexResult(source=live.index, target=target, re_embedded=embed, resumed=resumed, dry_run=dry_run)

    if resumed:
        existing = _source_dimension(raw, target)
        if existing != dimension:
            raise ReindexError(
                f"The unfinished index '{target}' holds {existing}-dimension vectors but EMBEDDING__DIMENSION is "
                f"{dimension}. Delete it (DELETE {target}) and run the command again."
            )
        say(f"Resuming into the unfinished index '{target}'.")

    say(
        f"Reindexing {total} chunk(s) from '{live.index}' into '{target}' ({KNN_ENGINE} engine, {dimension} dimensions"
        f"{', re-embedding every chunk' if embed else ', copying vectors as they are'}), then swapping the alias '{alias}'."
    )
    if dry_run:
        say("Dry run: nothing was changed.")
        return result

    embeddings = None
    if embed:
        if embeddings_factory is None:
            raise ReindexError("Re-embedding needs an embedding provider, and none is configured.")
        embeddings = embeddings_factory()

    if not raw.indices.exists(index=target):
        raw.indices.create(index=target, body=build_chunks_mapping(dimension))

    seen = failed = 0
    for page in _scroll(raw, live.index, batch_size):
        seen += len(page)
        wanted = _missing(raw, target, [hit["_id"] for hit in page])
        todo = [hit for hit in page if hit["_id"] in wanted]
        result.already_there += len(page) - len(todo)
        if not todo:
            continue
        sources = [dict(hit["_source"]) for hit in todo]
        if embeddings is not None:
            vectors = await embeddings.embed_passages([s.get("chunk_text") or "" for s in sources])
            for source, vector in zip(sources, vectors):
                source["embedding"] = vector
                source["embedding_model"] = getattr(embeddings, "model_name", source.get("embedding_model"))
        body: List[Dict[str, Any]] = []
        for hit, source in zip(todo, sources):
            body += [{"index": {"_index": target, "_id": hit["_id"]}}, source]
        response = raw.bulk(body=body)
        rejected = sum(1 for item in response.get("items", []) if "error" in item.get("index", {}))
        failed += rejected
        result.copied += len(todo) - rejected
        say(f"  copied {result.copied + result.already_there} of {total}")

    if failed:
        raise ReindexError(
            f"{failed} chunk(s) were rejected by the new index, so the alias was not swapped and search still reads "
            f"'{live.index}'. Fix the cause and run the command again: it resumes into '{target}'."
        )

    raw.indices.refresh(index=target)
    in_target, in_source = raw.count(index=target)["count"], raw.count(index=live.index)["count"]
    if not (in_target == in_source == seen):
        raise ReindexError(
            f"The counts do not agree (read {seen}, source now {in_source}, new index {in_target}), so the alias was "
            "not swapped. Chunks were probably ingested or deleted during the run: stop ingestion and run the command "
            f"again. If '{target}' holds chunks that no longer exist in the source, delete it first (DELETE {target})."
        )

    if live.is_alias:
        actions = [{"remove": {"index": live.index, "alias": alias}}, {"add": {"index": target, "alias": alias}}]
    else:
        # The old index carries the alias's name, so it must go in the same atomic step.
        actions = [{"remove_index": {"index": live.index}}, {"add": {"index": target, "alias": alias}}]
    raw.indices.update_aliases(body={"actions": actions})
    result.swapped = True
    say(f"Swapped: '{alias}' now points at '{target}'.")
    if not live.is_alias:
        say(f"The old index '{live.index}' had to be removed to free its name; it is not kept for rollback.")
    elif delete_old:
        raw.indices.delete(index=live.index)
        result.removed_old = True
        say(f"Deleted the previous index '{live.index}'.")
    else:
        say(f"The previous index '{live.index}' is kept for rollback; remove it with --delete-old or DELETE {live.index}.")
    return result
