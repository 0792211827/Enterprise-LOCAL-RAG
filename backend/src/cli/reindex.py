"""Rebuild the chunk index on the current mapping (lucene engine, EMBEDDING__DIMENSION) and swap the alias.

Run it when moving an existing deployment to the lucene engine, or after changing the embedding model or dimension.
It is never run implicitly, and a run that stops part-way is resumed by running the same command again.

    python -m src.cli.reindex --dry-run         # show the plan, change nothing
    python -m src.cli.reindex                   # build, copy, swap
    python -m src.cli.reindex --re-embed        # also recompute every vector (a new model of the same dimension)
    python -m src.cli.reindex --delete-old      # drop the previous index once the swap succeeded

or ``make reindex`` against the running ``rag-api`` container. Pause ingestion while it runs: chunks written to
the old index during the copy make the counts disagree, and the swap is then refused (run it again). Search keeps
reading the old index until the swap. Exit status: 0 done, 1 failed or refused (re-run to resume), 2 nothing to do.
"""

import argparse
import asyncio
import logging
import sys
from typing import Any, Optional

from src.services.opensearch.reindex import ReindexError, run_reindex


def main(argv: Optional[list] = None, opensearch: Any = None, embeddings: Any = None, settings: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen and change nothing")
    parser.add_argument("--re-embed", action="store_true", help="Recompute every vector with the configured embedding model")
    parser.add_argument("--batch-size", type=int, default=200, help="Chunks copied per request (default 200)")
    parser.add_argument("--delete-old", action="store_true", help="Delete the previous index after the alias is swapped")
    args = parser.parse_args(argv)

    if settings is None:
        from src.config import get_settings

        settings = get_settings()
    if opensearch is None:
        from src.services.opensearch.factory import make_opensearch_client

        opensearch = make_opensearch_client(settings)

    def embeddings_factory():
        if embeddings is not None:
            return embeddings
        from src.services.providers.embeddings.factory import make_embedding_provider

        return make_embedding_provider(settings)

    try:
        asyncio.run(
            run_reindex(
                opensearch,
                dimension=settings.embedding.dimension,
                embeddings_factory=embeddings_factory,
                re_embed=args.re_embed,
                batch_size=args.batch_size,
                delete_old=args.delete_old,
                dry_run=args.dry_run,
            )
        )
    except ReindexError as error:
        print(f"Reindex stopped: {error}", file=sys.stderr)
        return error.exit_code
    except Exception as error:  # noqa: BLE001 - whatever broke, the operator gets one instruction: run it again
        print(
            f"Reindex failed: {type(error).__name__}: {error}. Nothing was swapped; run the command again to resume.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
