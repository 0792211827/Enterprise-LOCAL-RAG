"""Generic, source-agnostic document ingestion pipeline.

Pipeline stages: parse -> extract -> chunk -> embed -> index.

The service is provider- and infrastructure-agnostic: OpenSearch, the embedding
provider, the chunker and the PDF parser are all injected, so the pipeline can
be unit-tested with mocks and swapped without source changes. Chunking reuses
the existing section-aware :class:`TextChunker` so the original intelligent
chunking strategy is preserved for generic documents.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.models.document import Document, DocumentChunk, DocumentChunkLocator
from src.models.enums import IngestionStatus
from src.models.ingestion import IngestionJob

logger = logging.getLogger(__name__)


class IngestionService:
    """Orchestrates parsing, chunking, embedding and indexing of a document."""

    def __init__(
        self,
        chunker: Any,
        embeddings_provider: Any,
        opensearch_client: Any,
        pdf_parser: Any = None,
    ) -> None:
        self.chunker = chunker
        self.embeddings_provider = embeddings_provider
        self.opensearch_client = opensearch_client
        self.pdf_parser = pdf_parser

    async def ingest_document(
        self,
        document: Document,
        job: IngestionJob,
        document_repo: Any,
        ingestion_repo: Any,
        knowledge_base: Any,
    ) -> Dict[str, int]:
        """Run the full ingestion pipeline for a single document.

        Updates the document + ingestion job state as it progresses and returns
        pipeline statistics. Any failure is captured on the job/document rather
        than raised, so the caller (background task) never crashes the worker.
        """
        job.status = IngestionStatus.PROCESSING.value
        job.started_at = datetime.now(timezone.utc)
        document.status = IngestionStatus.PROCESSING.value
        try:
            ingestion_repo.save(job)
        except Exception:  # pragma: no cover - defensive, repo may be a mock
            pass

        def mark_stage(stage: str) -> None:
            """Record the current pipeline stage so pollers can observe progress."""
            job.stage = stage
            try:
                ingestion_repo.save(job)
            except Exception:  # pragma: no cover - defensive, repo may be a mock
                pass

        try:
            # Stage 1: parse/extract text.
            mark_stage("parse")
            text, sections, elements = await self._extract(document)
            if not text or not text.strip():
                raise ValueError("No text found in the document. A scan or a file made only of images cannot be searched.")

            # Stage 2: chunk (section-aware where sections are available).
            mark_stage("chunk")
            if elements:
                chunks = self.chunker.chunk_elements(elements, str(document.id))
            else:
                chunks = self.chunker.chunk_document(
                    title=document.title or "",
                    abstract="",
                    full_text=text,
                    document_id=str(document.id),
                    sections=sections,
                )
            if not chunks:
                raise ValueError("Chunking produced no chunks")

            # Stage 3: embed.
            mark_stage("embed")
            texts = [c.text for c in chunks]
            embeddings = await self.embeddings_provider.embed_passages(texts=texts, batch_size=50)
            if len(embeddings) != len(chunks):
                raise ValueError(f"Embedding count mismatch: {len(embeddings)} != {len(chunks)}")

            # Stage 4: index into OpenSearch (reusing the hybrid index).
            mark_stage("index")
            restriction_keys = self._restriction_keys(document, document_repo)
            index_payload = self._build_index_payload(document, knowledge_base, chunks, embeddings, restriction_keys)
            index_result = self.opensearch_client.bulk_index_chunks(index_payload)
            indexed = index_result.get("success", 0) if isinstance(index_result, dict) else 0

            # An admin may have changed the restriction while the chunks were being built: their update found no
            # chunks to rewrite yet, so the ones just indexed would otherwise keep the old restriction.
            current_keys = self._restriction_keys(document, document_repo)
            if current_keys != restriction_keys:
                self.opensearch_client.set_document_restrictions(str(document.id), current_keys)

            # Persist chunk metadata rows.
            chunk_rows = self._build_chunk_rows(document, knowledge_base, chunks)
            document_repo.add_chunks(chunk_rows)

            # Finalise document + job.
            document.status = IngestionStatus.COMPLETED.value
            document.chunk_count = len(chunks)
            document.processed_at = datetime.now(timezone.utc)
            document.error = None
            document.raw_text = text
            document_repo.save(document)

            job.status = IngestionStatus.COMPLETED.value
            job.finished_at = datetime.now(timezone.utc)
            job.stats = {
                "chunks_created": len(chunks),
                "chunks_indexed": indexed,
                "embeddings_generated": len(embeddings),
            }
            ingestion_repo.save(job)

            logger.info("Ingested document %s: %d chunks indexed", document.id, indexed)
            return job.stats

        except Exception as exc:  # noqa: BLE001 - surface as job failure
            logger.error("Ingestion failed for document %s: %s", document.id, exc)
            document.status = IngestionStatus.FAILED.value
            document.error = str(exc)
            try:
                document_repo.save(document)
            except Exception:  # pragma: no cover
                pass
            job.status = IngestionStatus.FAILED.value
            job.error = str(exc)
            job.finished_at = datetime.now(timezone.utc)
            try:
                ingestion_repo.save(job)
            except Exception:  # pragma: no cover
                pass
            return {"chunks_created": 0, "chunks_indexed": 0, "embeddings_generated": 0}

    # ------------------------------------------------------------------ #
    async def _extract(self, document: Document):
        """Return ``(text, sections, elements)`` for a document; ``elements`` carry locators and are empty for plain text.

        A stored file (PDF, Office, CSV, HTML) is read by the injected parser; plain text kept on the row (TXT, MD)
        is used as it is. If the stored file has since been removed, the text kept from an earlier run stands in.
        """
        from pathlib import Path

        path = Path(document.file_path) if document.file_path else None
        if path is not None and path.exists() and self.pdf_parser:
            parsed = await self.pdf_parser.parse_document(path)
            if parsed is None:
                raise ValueError("The file is over the parser's size or page limit (PDF_PARSER__MAX_FILE_SIZE_MB, PDF_PARSER__MAX_PAGES).")
            text = getattr(parsed, "text", None) or getattr(parsed, "raw_text", None) or ""
            return text, getattr(parsed, "sections", None), getattr(parsed, "elements", None) or []
        return document.raw_text or "", document.sections, []

    @staticmethod
    def _restriction_keys(document: Document, document_repo: Any) -> List[str]:
        """The Principals the Document is restricted to, as stored on its chunks (empty: unrestricted)."""
        return sorted(document_repo.restriction_keys(document.id))

    def _build_index_payload(
        self,
        document: Document,
        knowledge_base: Any,
        chunks: List[Any],
        embeddings: List[List[float]],
        restriction_keys: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        payload = []
        for chunk, embedding in zip(chunks, embeddings):
            chunk_data = {
                "document_id": str(document.id),
                "knowledge_base_id": str(document.knowledge_base_id),
                "chunk_index": chunk.metadata.chunk_index,
                "chunk_text": chunk.text,
                "chunk_word_count": chunk.metadata.word_count,
                "start_char": chunk.metadata.start_char,
                "end_char": chunk.metadata.end_char,
                "section_title": chunk.metadata.section_title,
                "embedding_model": getattr(knowledge_base, "embedding_model", None),
                "title": document.title or "",
            }
            if chunk.locator is not None:
                chunk_data["locator"] = chunk.locator.as_index_field()
            if restriction_keys:
                chunk_data["restriction_principals"] = restriction_keys
            payload.append({"chunk_data": chunk_data, "embedding": embedding})
        return payload

    def _build_chunk_rows(self, document: Document, knowledge_base: Any, chunks: List[Any]) -> List[DocumentChunk]:
        rows = []
        for chunk in chunks:
            rows.append(
                DocumentChunk(
                    id=uuid.uuid4(),
                    locator=None
                    if chunk.locator is None
                    else DocumentChunkLocator(document_id=document.id, locator=chunk.locator.as_index_field()),
                    document_id=document.id,
                    knowledge_base_id=document.knowledge_base_id,
                    chunk_index=chunk.metadata.chunk_index,
                    text=chunk.text,
                    word_count=chunk.metadata.word_count,
                    section_title=chunk.metadata.section_title,
                    start_char=chunk.metadata.start_char,
                    end_char=chunk.metadata.end_char,
                    embedding_model=getattr(knowledge_base, "embedding_model", None),
                    indexed=True,
                )
            )
        return rows
