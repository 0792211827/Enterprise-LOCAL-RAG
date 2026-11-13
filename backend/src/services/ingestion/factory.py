from typing import Any

from src.config import Settings

from .service import IngestionService


def make_ingestion_service(opensearch: Any, embeddings: Any, pdf_parser: Any, settings: Settings) -> IngestionService:
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
    )
