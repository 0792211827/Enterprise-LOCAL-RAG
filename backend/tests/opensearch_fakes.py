"""An in-memory stand-in for the slice of an OpenSearch cluster the reindex command drives: indices, aliases, scroll,
mget and bulk. Offline; the real cluster is exercised by the integration suite."""

import fnmatch
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional


class FakeEmbeddings:
    model_name = "new-model"

    def __init__(self, dimension: int):
        self.dimension = dimension
        self.embedded: List[str] = []

    async def embed_passages(self, texts: List[str], batch_size: int = 100):
        self.embedded.extend(texts)
        return [[float(len(t))] * self.dimension for t in texts]

    async def embed_query(self, query: str):
        return [float(len(query))] * self.dimension


class FakeCluster:
    """Indices hold documents by ``_id``; an alias maps to the indices it points at."""

    def __init__(self):
        self.indices_data: Dict[str, Dict[str, Any]] = {}
        self.aliases: Dict[str, List[str]] = {}
        self.bulk_calls = 0
        self.crash_on_bulk: Optional[int] = None  # raise on this (1-based) bulk call, as if the process died
        self.reject_ids: set = set()  # bulk items for these ids come back as errors
        self.scrolls: Dict[str, List[Dict[str, Any]]] = {}
        self.indices = SimpleNamespace(
            exists=self._exists,
            exists_alias=lambda name: name in self.aliases,
            get_alias=self._get_alias,
            get=self._get,
            get_mapping=self._get_mapping,
            create=self._create,
            refresh=lambda index: None,
            update_aliases=self._update_aliases,
            delete=lambda index: self.indices_data.pop(index, None),
        )

    # -- test setup ------------------------------------------------------------------------------------------ #
    def add_index(self, name: str, dimension: int, engine: str, docs: Dict[str, Dict[str, Any]], alias: Optional[str] = None):
        self.indices_data[name] = {"dimension": dimension, "engine": engine, "docs": dict(docs)}
        if alias:
            self.aliases.setdefault(alias, []).append(name)

    def resolve(self, name: str) -> str:
        return self.aliases[name][0] if name in self.aliases else name

    def docs(self, name: str) -> Dict[str, Dict[str, Any]]:
        return self.indices_data[self.resolve(name)]["docs"]

    # -- the API surface ------------------------------------------------------------------------------------- #
    def _exists(self, index):
        return index in self.indices_data or index in self.aliases

    def _get_alias(self, name):
        return {index: {"aliases": {name: {}}} for index in self.aliases[name]}

    def _get(self, index):
        return {name: {} for name in self.indices_data if fnmatch.fnmatch(name, index)}

    def _get_mapping(self, index):
        data = self.indices_data[self.resolve(index)]
        return {
            self.resolve(index): {
                "mappings": {
                    "properties": {
                        "embedding": {"type": "knn_vector", "dimension": data["dimension"], "method": {"engine": data["engine"]}}
                    }
                }
            }
        }

    def _create(self, index, body):
        embedding = body["mappings"]["properties"]["embedding"]
        self.indices_data[index] = {
            "dimension": embedding["dimension"],
            "engine": embedding["method"]["engine"],
            "docs": {},
        }

    def _update_aliases(self, body):
        for action in body["actions"]:
            if "remove_index" in action:
                name = action["remove_index"]["index"]
                self.indices_data.pop(name)
            elif "remove" in action:
                spec = action["remove"]
                self.aliases[spec["alias"]].remove(spec["index"])
                if not self.aliases[spec["alias"]]:
                    del self.aliases[spec["alias"]]
            elif "add" in action:
                spec = action["add"]
                assert spec["alias"] not in self.indices_data, "an alias cannot share a name with an index"
                self.aliases.setdefault(spec["alias"], []).append(spec["index"])

    def count(self, index, body=None):
        return {"count": len(self.docs(index))}

    def search(self, index, body=None, scroll=None, size=10, **_):
        hits = [{"_id": _id, "_source": dict(src)} for _id, src in self.docs(index).items()]
        token = uuid.uuid4().hex
        self.scrolls[token] = hits[size:]
        return {"_scroll_id": token, "hits": {"hits": hits[:size]}}

    def scroll(self, scroll_id, scroll=None):
        remaining = self.scrolls[scroll_id]
        page, self.scrolls[scroll_id] = remaining[:100], remaining[100:]
        return {"_scroll_id": scroll_id, "hits": {"hits": page}}

    def clear_scroll(self, scroll_id=None, **_):
        self.scrolls.pop(scroll_id, None)

    def mget(self, index, body, **_):
        present = self.docs(index)
        return {"docs": [{"_id": _id, "found": _id in present} for _id in body["ids"]]}

    def bulk(self, body, **_):
        self.bulk_calls += 1
        if self.crash_on_bulk == self.bulk_calls:
            raise ConnectionError("the process was killed")
        items = []
        for action, source in zip(body[::2], body[1::2]):
            spec = action["index"]
            if spec["_id"] in self.reject_ids:
                items.append({"index": {"_id": spec["_id"], "status": 400, "error": {"type": "mapper_parsing_exception"}}})
                continue
            self.indices_data[self.resolve(spec["_index"])]["docs"][spec["_id"]] = source
            items.append({"index": {"_id": spec["_id"], "status": 201}})
        return {"errors": any("error" in item["index"] for item in items), "items": items}


def chunk(n: int, dimension: int, **extra) -> Dict[str, Any]:
    return {
        "chunk_text": f"passage number {n}",
        "knowledge_base_id": "kb-1",
        "document_id": f"doc-{n}",
        "embedding": [float(n)] * dimension,
        "embedding_model": "old-model",
        **extra,
    }


class FilteringIndex:
    """A chunk index that honours the *filters* of the queries it is sent -- bool/filter/must_not, term(s), exists
    and a kNN clause's own filter -- and ignores relevance (every document that passes is a hit, in insertion order).

    Handed to the real ``OpenSearchClient`` as its raw client, so a leak caused by a missing or wrong filter in the
    real query-building code shows up as a document in the results.
    """

    def __init__(self):
        from opensearchpy.serializer import JSONSerializer

        self.docs: Dict[str, Dict[str, Any]] = {}
        self.queries: List[Dict[str, Any]] = []
        self.fail_deletes = False
        self.transport = SimpleNamespace(serializer=JSONSerializer())
        self.indices = SimpleNamespace(get_mapping=self._no_mapping, exists=lambda index: True)
        self.cluster = SimpleNamespace(health=lambda: {"status": "green"})

    @staticmethod
    def _no_mapping(index):
        raise KeyError("no mapping in the fake: the engine is unknown, so the portable query form is used")

    # -- writing --------------------------------------------------------------------------------------------- #
    def bulk(self, body, *args, **kwargs):
        import json

        lines = [json.loads(line) for line in (body if isinstance(body, str) else "\n".join(body)).splitlines() if line.strip()]
        items = []
        for _action, source in zip(lines[::2], lines[1::2]):
            doc_id = uuid.uuid4().hex
            self.docs[doc_id] = source
            items.append({"index": {"_id": doc_id, "status": 201}})
        return {"errors": False, "items": items}

    def add(self, source: Dict[str, Any], doc_id: Optional[str] = None) -> str:
        doc_id = doc_id or uuid.uuid4().hex
        self.docs[doc_id] = source
        return doc_id

    # -- filtering ------------------------------------------------------------------------------------------- #
    def matches(self, source: Dict[str, Any], query: Optional[Dict[str, Any]]) -> bool:
        if not query:
            return True
        (kind, spec), = query.items()
        if kind == "bool":
            def listed(key):
                value = spec.get(key, [])
                return value if isinstance(value, list) else [value]

            should = listed("should")
            return (
                all(self.matches(source, q) for q in listed("must"))
                and all(self.matches(source, q) for q in listed("filter"))
                and not any(self.matches(source, q) for q in listed("must_not"))
                and (not should or not spec.get("minimum_should_match") or any(self.matches(source, q) for q in should))
            )
        if kind == "terms":
            (field, values), = spec.items()
            held = source.get(field)
            # A keyword field may hold several values; one match is enough, as in OpenSearch.
            return any(v in values for v in held) if isinstance(held, list) else held in values
        if kind == "term":
            (field, value), = spec.items()
            wanted = value["value"] if isinstance(value, dict) else value
            held = source.get(field)
            return wanted in held if isinstance(held, list) else held == wanted
        if kind == "exists":
            return source.get(spec["field"]) not in (None, [])
        if kind == "knn":
            (inner,) = spec.values()
            return self.matches(source, inner.get("filter"))
        if kind == "hybrid":
            return any(self.matches(source, q) for q in spec["queries"])
        return True  # match_all, multi_match, ...: relevance is not modelled

    def _hits(self, query):
        return [
            {"_id": doc_id, "_score": 1.0, "_source": {k: v for k, v in source.items() if k != "embedding"}}
            for doc_id, source in self.docs.items()
            if self.matches(source, query)
        ]

    def search(self, index=None, body=None, params=None, **_):
        self.queries.append(body)
        hits = self._hits(body.get("query"))
        return {"hits": {"total": {"value": len(hits)}, "hits": hits[: body.get("size", 10)]}}

    def update_by_query(self, index=None, body=None, refresh=None, **_):
        """Understands the one script the client sends: set ``restriction_principals`` to ``params.keys``, or remove it."""
        self.updates = getattr(self, "updates", []) + [body]
        script = body["script"]
        hits = self._hits(body["query"])
        for hit in hits:
            source = self.docs[hit["_id"]]
            if "remove" in script["source"]:
                source.pop("restriction_principals", None)
            else:
                source["restriction_principals"] = list(script["params"]["keys"])
        return {"updated": len(hits), "failures": []}

    def delete_by_query(self, index=None, body=None, refresh=None, **_):
        if self.fail_deletes:
            raise ConnectionError("opensearch is down")
        gone = [hit["_id"] for hit in self._hits(body["query"])]
        for doc_id in gone:
            del self.docs[doc_id]
        return {"deleted": len(gone)}
