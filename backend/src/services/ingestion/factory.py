from typing import Any

from src.config import Settings
from src.services.telemetry import NOOP_TELEMETRY, Telemetry

from .service import IngestionService


def make_ingestion_service(
    opensearch: Any,
    embeddings: Any,
    pdf_parser: Any,
    settings: Settings,
    vlm_provider: Any = None,
    url_fetcher: Any = None,
    telemetry: Telemetry = NOOP_TELEMETRY,
) -> IngestionService:
    """The ingestion pipeline over the platform's collaborators; the API and the Connector job build it the same way."""
    from src.services.indexing.text_chunker import TextChunker

    chunker = TextChunker(
        chunk_size=settings.chunking.chunk_size,
        overlap_size=settings.chunking.overlap_size,
        min_chunk_size=settings.chunking.min_chunk_size,
    )
    return IngestionService(
        chunker=chunker,
        embeddings_provider=embeddings,
        opensearch_client=opensearch,
        pdf_parser=pdf_parser,
        timeout_seconds=settings.ingestion.document_timeout_seconds,
        vlm_provider=vlm_provider,
        url_fetcher=url_fetcher,
        settings=settings,
        telemetry=telemetry,
    )
