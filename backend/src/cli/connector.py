"""Run Connectors: mirror a source folder into its Knowledge Base.

    python -m src.cli.connector list
    python -m src.cli.connector sync hr-share
    python -m src.cli.connector sync --all
    python -m src.cli.connector sync hr-share --dry-run
    python -m src.cli.connector schedule --interval-minutes 60     # run every enabled Connector, forever

or ``make connector-sync NAME=hr-share`` against the running ``rag-api`` container. Connectors are defined by an admin
(``/api/v1/connectors``); this job is the only thing that runs them, so the API process stays stateless.

Each run prints how many files were added, updated, removed and failed, and leaves a ``connector.sync`` audit event. A
file that fails is reported and retried next run; it never stops the others. Exit status:

    0  the run finished and every file was handled
    1  the run finished but some files failed
    2  a run could not be made (unknown or disabled Connector, source unreachable, already running, ingestion refused)
"""

import argparse
import asyncio
import logging
import sys
import time
from typing import Any, Optional

from src.services.connectors import ConnectorError, ConnectorSync

EXIT_OK, EXIT_PARTIAL, EXIT_ERROR = 0, 1, 2


def build_sync() -> tuple[ConnectorSync, Any]:
    """The collaborators the API itself is built from, plus the database to close afterwards."""
    from src.config import get_settings
    from src.db.factory import make_database
    from src.services.embeddings.factory import make_embeddings_service
    from src.services.ingestion.factory import make_ingestion_service
    from src.services.opensearch.factory import make_opensearch_client
    from src.services.pdf_parser.factory import make_pdf_parser_service
    from src.services.providers.vlm.factory import make_vlm_provider
    from src.services.telemetry import NOOP_TELEMETRY, make_telemetry

    settings = get_settings()
    try:
        telemetry = make_telemetry(settings)
    except Exception:  # noqa: BLE001 - telemetry that cannot start must not stop a sync
        logging.getLogger(__name__).exception("Telemetry could not be set up; running without it")
        telemetry = NOOP_TELEMETRY
    database = make_database()
    opensearch = make_opensearch_client()
    ingestion = make_ingestion_service(
        opensearch,
        make_embeddings_service(),
        make_pdf_parser_service(),
        settings,
        make_vlm_provider(settings),
        telemetry=telemetry,
    )
    return ConnectorSync(database.get_session, ingestion, opensearch, settings, telemetry=telemetry), database


def _guard(sync: ConnectorSync) -> Optional[str]:
    """Why ingestion must not run, as the API refuses it: an index whose vectors disagree with configuration."""
    from src.services.opensearch.dimension_guard import check_index_dimension

    return check_index_dimension(sync.opensearch, sync.settings.embedding.dimension)


def _names(sync: ConnectorSync) -> list[str]:
    from src.repositories.connector import ConnectorRepository

    with sync.open_session() as session:
        return [c.name for c in ConnectorRepository(session).list_all() if c.enabled]


def _list(sync: ConnectorSync) -> int:
    from src.repositories.connector import ConnectorRepository

    with sync.open_session() as session:
        repo = ConnectorRepository(session)
        connectors = repo.list_all()
        for connector in connectors:
            counts = repo.file_counts(connector.id)
            last = f"{connector.last_run_status} at {connector.last_run_at:%Y-%m-%d %H:%M}" if connector.last_run_at else "never run"
            print(
                f"{connector.name}\t{connector.source_type}\t{'enabled' if connector.enabled else 'disabled'}\t"
                f"{counts['synced']} synced, {counts['failed']} failed\t{last}"
            )
        if not connectors:
            print("No Connectors defined. An admin creates them with POST /api/v1/connectors.")
    return EXIT_OK


async def _sync_all(sync: ConnectorSync, names: list[str], dry_run: bool, allow_empty: bool) -> int:
    """Run the Connectors one after another in one event loop (the embedding client lives in it)."""
    status = EXIT_OK
    for name in names:
        try:
            summary = await sync.run(name, dry_run=dry_run, allow_empty=allow_empty)
        except (LookupError, ConnectorError) as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            status = EXIT_ERROR
            continue
        print(f"{name}: {summary.render()}")
        if summary.failed and status == EXIT_OK:
            status = EXIT_PARTIAL
    return status


def _schedule(sync: ConnectorSync, interval_minutes: int, allow_empty: bool, sleep=time.sleep, rounds: Optional[int] = None) -> int:
    """Run every enabled Connector, then wait, again and again. A failed round never ends the loop: the next one retries.

    One process for the whole schedule, so the embedding model is loaded once rather than every round.
    """
    done = 0
    while rounds is None or done < rounds:
        refusal = _guard(sync)
        if refusal:
            print(refusal, file=sys.stderr, flush=True)
        else:
            try:
                sync.opensearch.setup_indices()
                asyncio.run(_sync_all(sync, _names(sync), False, allow_empty))
            except Exception as exc:  # noqa: BLE001 - the schedule outlives any one round
                print(f"round failed: {exc}", file=sys.stderr, flush=True)
        done += 1
        if rounds is None or done < rounds:
            sleep(interval_minutes * 60)
    return EXIT_OK


def main(argv: Optional[list] = None, sync: Optional[ConnectorSync] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Show the defined Connectors and how their last run went")
    run = commands.add_parser("sync", help="Mirror one or more Connectors' sources into their Knowledge Bases")
    run.add_argument("names", nargs="*", help="Connector names")
    run.add_argument("--all", action="store_true", help="Run every enabled Connector")
    run.add_argument("--dry-run", action="store_true", help="Report what would change, and change and record nothing")
    run.add_argument(
        "--allow-empty",
        action="store_true",
        help="Accept a source that lists no files and remove everything synced from it (otherwise refused, as an "
        "unmounted share looks the same)",
    )
    loop = commands.add_parser("schedule", help="Run every enabled Connector on an interval, until stopped")
    loop.add_argument("--interval-minutes", type=int, default=60, help="Minutes between rounds (default 60)")
    args = parser.parse_args(argv)

    database = None
    _real = sync is None  # an injected sync (a test) runs one round instead of forever
    if sync is None:
        sync, database = build_sync()
    try:
        if args.command == "list":
            return _list(sync)
        if args.command == "schedule":
            if args.interval_minutes < 1:
                print("--interval-minutes must be at least 1", file=sys.stderr)
                return EXIT_ERROR
            return _schedule(sync, args.interval_minutes, allow_empty=False, rounds=None if _real else 1)
        if bool(args.names) == bool(args.all):
            print("Name the Connectors to run, or pass --all.", file=sys.stderr)
            return EXIT_ERROR
        if not args.dry_run:
            refusal = _guard(sync)
            if refusal:
                print(refusal, file=sys.stderr)
                return EXIT_ERROR
            sync.opensearch.setup_indices()  # the same idempotent step the API runs at startup
        return asyncio.run(_sync_all(sync, _names(sync) if args.all else args.names, args.dry_run, args.allow_empty))
    finally:
        sync.telemetry.shutdown()  # sends the run's measurements: this process is about to exit
        if database is not None:
            database.teardown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
