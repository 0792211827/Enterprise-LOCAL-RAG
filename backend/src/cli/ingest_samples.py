"""Ingest the sample folder into a scratch Knowledge Base and report how each file went.

    python -m src.cli.ingest_samples --folder "../data/File Examples"
    python -m src.cli.ingest_samples --folder ... --urls         # also add the web links in Links.docx (hosts on the allowlist)
    python -m src.cli.ingest_samples --folder ... --ask          # then ask the sample questions and print the Citations
    python -m src.cli.ingest_samples --clean                     # remove the scratch Knowledge Base

or ``make ingest-samples`` against the running ``rag-api`` container. It is opt-in and never part of CI, which uses small
synthetic files. Running it again replaces files that changed, skips those that did not, and retries those that failed.

Each file prints its status, chunk count, warnings and error; a file that fails (such as a corrupt 71-byte PPTX) is reported
and never stops the run. Exit status:

    0  the run finished (failed files are findings, not errors: see the report)
    2  the run could not be made (no such folder, ingestion refused, scratch Knowledge Base missing for --ask)
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, List, Optional

from src.services.ingestion.lifecycle import IndexNotUpdated
from src.services.ingestion.samples import SCRATCH_NAME, FileResult, QuestionResult, SampleIngest

EXIT_OK, EXIT_ERROR = 0, 2
DEFAULT_QUESTIONS = "evals/sample-data-questions.yaml"


def build() -> tuple[SampleIngest, Any]:
    """The collaborators the API itself is built from, plus the database to close afterwards."""
    from src.config import get_settings
    from src.db.factory import make_database
    from src.services.embeddings.factory import make_embeddings_service
    from src.services.ingestion.factory import make_ingestion_service
    from src.services.ingestion.url_fetch import make_url_fetcher
    from src.services.opensearch.factory import make_opensearch_client
    from src.services.pdf_parser.factory import make_pdf_parser_service
    from src.services.providers.vlm.factory import make_vlm_provider

    settings = get_settings()
    database = make_database()
    opensearch = make_opensearch_client()
    embeddings = make_embeddings_service()
    fetcher = make_url_fetcher(settings)
    ingestion = make_ingestion_service(opensearch, embeddings, make_pdf_parser_service(), settings, make_vlm_provider(settings), fetcher)
    return SampleIngest(database.get_session, ingestion, opensearch, embeddings, settings, fetcher), database


def questions_from(path: Path) -> List[str]:
    """The questions of a Golden set file (the same format ``make eval`` reads)."""
    from src.services.eval import load_golden_set

    return [case.question for case in load_golden_set(path).cases]


def render_files(results: List[FileResult]) -> str:
    lines = [f"{'ACTION':<10} {'STATUS':<10} {'CHUNKS':>6}  FILE"]
    for r in results:
        lines.append(f"{r.action:<10} {r.status or '-':<10} {r.chunks:>6}  {r.name}")
        for warning in r.warnings:
            lines.append(f"{'':<29}WARNING: {warning}")
        if r.error:
            lines.append(f"{'':<29}{'ERROR' if r.action == 'failed' else 'NOTE'}: {r.error}")
    counts: dict = {}
    for r in results:
        counts[r.action] = counts.get(r.action, 0) + 1
    lines.append("")
    lines.append(", ".join(f"{count} {action}" for action, count in sorted(counts.items())) or "nothing to ingest")
    completed = sum(1 for r in results if r.status.startswith("COMPLETED"))
    lines.append(f"{sum(r.chunks for r in results)} chunks across {completed} completed Documents")
    return "\n".join(lines)


def render_questions(results: List[QuestionResult]) -> str:
    lines = []
    for result in results:
        lines.append(f"\nQ: {result.question}")
        for number, citation in enumerate(result.citations, 1):
            where = f" ({citation['where']})" if citation["where"] else ""
            link = f" <{citation['url']}>" if citation["url"] else ""
            lines.append(f"  [{number}] {citation['title']}{where}{link}")
            lines.append(f"      {citation['passage']}")
        if not result.citations:
            lines.append("  no Citations: nothing in the scratch Knowledge Base matched")
    return "\n".join(lines)


async def _run(sample: SampleIngest, args: argparse.Namespace) -> int:
    results = await sample.run(Path(args.folder), name=args.name, urls=args.urls)
    print(render_files(results))
    if args.ask:
        print(render_questions(await sample.ask(questions_from(Path(args.questions)), name=args.name, size=args.top)))
    return EXIT_OK


def _guard(sample: SampleIngest) -> Optional[str]:
    """Why ingestion must not run, as the API refuses it: an index whose vectors disagree with configuration."""
    from src.services.opensearch.dimension_guard import check_index_dimension

    return check_index_dimension(sample.opensearch, sample.settings.embedding.dimension)


def main(argv: Optional[list] = None, sample: Optional[SampleIngest] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", default="data/File Examples", help="The folder of sample files (default: data/File Examples)")
    parser.add_argument("--name", default=SCRATCH_NAME, help=f"The scratch Knowledge Base (default: '{SCRATCH_NAME}')")
    parser.add_argument(
        "--urls",
        action="store_true",
        help="Also add the web addresses in Links.docx as URL Documents (their hosts must be on INGESTION__URL_ALLOWED_HOSTS)",
    )
    parser.add_argument("--ask", action="store_true", help="After ingesting, ask the sample questions and print the Citations")
    parser.add_argument("--questions", default=DEFAULT_QUESTIONS, help="A Golden set file whose questions --ask uses")
    parser.add_argument("--top", type=int, default=3, help="Citations printed per question (default 3)")
    parser.add_argument("--clean", action="store_true", help="Remove the scratch Knowledge Base and everything in it, and do nothing else")
    args = parser.parse_args(argv)

    database = None
    if sample is None:
        sample, database = build()
    try:
        if args.clean:
            held = sample.clean(args.name)
            print(f"No scratch Knowledge Base '{args.name}' to remove." if held is None else f"Removed '{args.name}' and its {held} Documents.")
            return EXIT_OK
        refusal = _guard(sample)
        if refusal:
            print(refusal, file=sys.stderr)
            return EXIT_ERROR
        sample.opensearch.setup_indices()  # the same idempotent step the API runs at startup
        return asyncio.run(_run(sample, args))
    except (FileNotFoundError, LookupError, IndexNotUpdated) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR
    finally:
        if database is not None:
            database.teardown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
