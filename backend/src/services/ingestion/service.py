"""Generic, source-agnostic document ingestion pipeline.

Pipeline stages: parse -> extract -> chunk -> embed -> index.

The service is provider- and infrastructure-agnostic: OpenSearch, the embedding
provider, the chunker and the PDF parser are all injected, so the pipeline can
be unit-tested with mocks and swapped without source changes. Chunking reuses
the existing section-aware :class:`TextChunker` so the original intelligent
chunking strategy is preserved for generic documents.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from src.models.document import Document, DocumentChunk, DocumentChunkLocator
from src.models.enums import IngestionStatus
from src.models.ingestion import IngestionJob
from src.schemas.indexing.elements import DocumentElement, Locator
from src.services.telemetry import NOOP_TELEMETRY, IngestionStages, Telemetry

from .images import Described, describe_images
from .lifecycle import remove_stored_file
from .url_documents import fetch_content, is_url_document, keep_content

logger = logging.getLogger(__name__)

OLD_CONTENT_NOT_REMOVED = (
    "The previous content could not be removed from the search index, so it may still be answered from. "
    "Reprocess this Document to clear it."
)
IMAGES_UNREADABLE = "The images in this Document could not be read, so none were described. Reprocess this Document to try again."
IMAGE_TIME_SHARE = 0.5
NO_STATS = {"chunks_created": 0, "chunks_indexed": 0, "embeddings_generated": 0}


class DocumentGone(Exception):
    """The Document was deleted while it was being ingested; whatever was indexed for it must not stay."""


class IngestionService:
    """Orchestrates parsing, chunking, embedding and indexing of a document."""

    def __init__(
        self,
        chunker: Any,
        embeddings_provider: Any,
        opensearch_client: Any,
        pdf_parser: Any = None,
        timeout_seconds: int = 0,
        vlm_provider: Any = None,
        url_fetcher: Any = None,
        settings: Any = None,
        telemetry: Telemetry = NOOP_TELEMETRY,
    ) -> None:
        self.chunker = chunker
        self.embeddings_provider = embeddings_provider
        self.opensearch_client = opensearch_client
        self.pdf_parser = pdf_parser
        self.timeout_seconds = timeout_seconds
        self.vlm_provider = vlm_provider
        self.url_fetcher = url_fetcher
        self.settings = settings
        self.telemetry = telemetry

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
        document.status = IngestionStatus.PROCESSING.value
        return await self._run(document, job, document_repo, ingestion_repo, knowledge_base, replacement=None)

    async def replace_document(
        self,
        document: Document,
        job: IngestionJob,
        document_repo: Any,
        ingestion_repo: Any,
        knowledge_base: Any,
    ) -> Dict[str, int]:
        """Ingest the Document's pending replacement as a new revision, and make it current only once it is ready.

        The old revision keeps answering throughout. A failure (including the time limit) removes what the attempt
        indexed and leaves the Document as it was, with the error recorded on it.
        """
        replacement = dict((document.extra_metadata or {}).get("replacement") or {})
        if not replacement:
            raise ValueError("The Document has no replacement waiting.")
        document.extra_metadata = {**(document.extra_metadata or {}), "replacement": {**replacement, "state": "processing", "error": None}}
        return await self._run(document, job, document_repo, ingestion_repo, knowledge_base, replacement=replacement)

    async def _run(
        self,
        document: Document,
        job: IngestionJob,
        document_repo: Any,
        ingestion_repo: Any,
        knowledge_base: Any,
        replacement: Optional[Dict[str, Any]],
    ) -> Dict[str, int]:
        job.status = IngestionStatus.PROCESSING.value
        job.started_at = datetime.now(timezone.utc)
        try:
            ingestion_repo.save(job)
            document_repo.save(document)
        except Exception:  # pragma: no cover - defensive, repo may be a mock
            pass

        stages = IngestionStages(self.telemetry, document.knowledge_base_id)

        def mark_stage(stage: str) -> None:
            """Record the current pipeline stage so pollers can observe progress (and start timing it)."""
            stages.begin(stage)
            job.stage = stage
            try:
                ingestion_repo.save(job)
            except Exception:  # pragma: no cover - defensive, repo may be a mock
                pass

        try:
            async with asyncio.timeout(self.timeout_seconds or None) as limit:
                result = await self._pipeline(document, job, document_repo, ingestion_repo, knowledge_base, replacement, mark_stage)
            stages.end("succeeded")
            return result
        except DocumentGone:
            stages.end("interrupted")
            logger.info("Document %s was deleted while it was being ingested; its chunks are removed", document.id)
            self._purge_quietly(document, None)
            return dict(NO_STATS)
        except Exception as exc:  # noqa: BLE001 - surface as job failure
            if isinstance(exc, TimeoutError) and limit.expired():
                reason = (
                    f"Ingestion took longer than the {self.timeout_seconds} second limit and was stopped "
                    "(INGESTION__DOCUMENT_TIMEOUT_SECONDS)."
                )
            else:
                reason = str(exc) or exc.__class__.__name__
            stages.end("failed")
            logger.error("Ingestion failed for document %s: %s", document.id, reason)
            self._fail(document, job, document_repo, ingestion_repo, replacement, reason)
            return dict(NO_STATS)
        except BaseException:  # a cancellation (shutdown): the stage did not get to finish
            stages.end("interrupted")
            raise

    async def _pipeline(
        self,
        document: Document,
        job: IngestionJob,
        document_repo: Any,
        ingestion_repo: Any,
        knowledge_base: Any,
        replacement: Optional[Dict[str, Any]],
        mark_stage: Any,
    ) -> Dict[str, int]:
        revision = replacement["revision"] if replacement else None

        if replacement is None and is_url_document(document) and not document.file_path and not document.raw_text:
            mark_stage("fetch")
            await self._fetch_page(document)

        # Stage 1: parse/extract text.
        mark_stage("parse")
        source = (
            document
            if replacement is None
            else SimpleNamespace(file_path=replacement.get("file_path"), raw_text=replacement.get("raw_text"), sections=None)
        )
        text, sections, elements = await self._extract(source)
        images = await self._describe_images(source)
        if images.elements:
            if not elements and text.strip():
                elements = [DocumentElement(text=text, locator=Locator(kind="text"))]
            elements = [*elements, *images.elements]
        if not (text or "").strip() and not images.elements:
            raise ValueError(
                "No text found in the document. A scan or a file made only of images cannot be searched."
                + (f" {images.warnings[0]}" if images.warnings else "")
            )

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

        # Stage 4: index into OpenSearch (reusing the hybrid index). A replacement is indexed hidden (staged) beside
        # the revision that is answering.
        mark_stage("index")
        restriction_keys = self._restriction_keys(document, document_repo)
        index_payload = self._build_index_payload(document, knowledge_base, chunks, embeddings, restriction_keys, revision)
        index_result = self.opensearch_client.bulk_index_chunks(index_payload)
        indexed = index_result.get("success", 0) if isinstance(index_result, dict) else 0

        # An admin may have changed the restriction while the chunks were being built: their update found no
        # chunks to rewrite yet, so the ones just indexed would otherwise keep the old restriction.
        current_keys = self._restriction_keys(document, document_repo)
        if current_keys != restriction_keys:
            self.opensearch_client.set_document_restrictions(str(document.id), current_keys)

        # A Document deleted meanwhile must not be left behind in the index (or resurrected in the database).
        if not document_repo.exists(document.id):
            raise DocumentGone()

        chunk_rows = self._build_chunk_rows(document, knowledge_base, chunks)
        if replacement is None:
            document_repo.add_chunks(chunk_rows)
            document.status = IngestionStatus.COMPLETED.value
        else:
            self._make_current(document, document_repo, chunk_rows, replacement)

        # Finalise document + job.
        document.chunk_count = len(chunks)
        document.processed_at = datetime.now(timezone.utc)
        document.error = None
        document.raw_text = text  # the Document's own text: a reprocess without the file must not take descriptions for it
        self._note_images(document, images)
        document_repo.save(document)

        job.status = IngestionStatus.COMPLETED.value
        job.finished_at = datetime.now(timezone.utc)
        job.stats = {
            "chunks_created": len(chunks),
            "chunks_indexed": indexed,
            "embeddings_generated": len(embeddings),
            "images_described": images.described,
            "images_undescribed": images.undescribed,
        }
        ingestion_repo.save(job)

        logger.info("Ingested document %s: %d chunks indexed", document.id, indexed)
        return job.stats

    def _make_current(self, document: Document, document_repo: Any, chunk_rows: List[DocumentChunk], replacement: Dict[str, Any]) -> None:
        """Flip the Document to its new revision: searches switch, the old revision's chunks and file are removed.

        The new chunks become visible, then one database commit swaps the chunk rows and the Document's file, and only
        then is everything else of the Document removed from the index. A failure before the commit leaves the old
        revision whole (the caller removes the new one); the old content is never deleted until the new is committed.
        """
        revision = replacement["revision"]
        self.opensearch_client.activate_revision(str(document.id), revision)

        old_file = document.file_path
        document.file_path = replacement.get("file_path")
        document.content_type = replacement.get("content_type") or document.content_type
        document.file_size_bytes = replacement.get("file_size_bytes")
        document.checksum = replacement.get("checksum")
        document.sections = None
        document.status = IngestionStatus.COMPLETED.value
        document.extra_metadata = {k: v for k, v in (document.extra_metadata or {}).items() if k not in ("replacement", "warnings")} | {
            "current_revision": revision
        }
        document_repo.replace_chunks(document.id, chunk_rows)
        try:
            self.opensearch_client.purge_superseded(str(document.id), revision)
        except Exception as exc:  # noqa: BLE001 - the new revision is committed and must stay; the admin is told
            logger.error("Could not remove the superseded chunks of document %s: %s", document.id, exc)
            document.extra_metadata = {
                **(document.extra_metadata or {}),
                "warnings": [OLD_CONTENT_NOT_REMOVED],
            }
            document_repo.save(document)
        if old_file and old_file != document.file_path:
            remove_stored_file(document_repo, old_file, document.id)

    def _fail(
        self,
        document: Document,
        job: IngestionJob,
        document_repo: Any,
        ingestion_repo: Any,
        replacement: Optional[Dict[str, Any]],
        reason: str,
    ) -> None:
        """Record a failure on the Document and job, and take what the attempt indexed back out of the index."""
        self._purge_quietly(document, replacement["revision"] if replacement else None)
        try:
            document_repo.rollback()
        except Exception:  # pragma: no cover - repo may be a mock
            pass
        if replacement is None:
            document.status = IngestionStatus.FAILED.value
        else:
            # The old revision stays current and answering; only the error is shown.
            document.extra_metadata = {
                **(document.extra_metadata or {}),
                "replacement": {**replacement, "state": "failed", "error": reason},
            }
        document.error = reason
        try:
            document_repo.save(document)
        except Exception:  # pragma: no cover
            pass
        job.status = IngestionStatus.FAILED.value
        job.error = reason
        job.finished_at = datetime.now(timezone.utc)
        try:
            ingestion_repo.save(job)
        except Exception:  # pragma: no cover
            pass

    def _purge_quietly(self, document: Document, revision: Optional[str]) -> None:
        """Remove what an unfinished attempt indexed: one revision's chunks, or (no revision) all of the Document's."""
        try:
            if revision:
                self.opensearch_client.purge_revision_chunks(str(document.id), revision)
            else:
                self.opensearch_client.purge_document_chunks(str(document.id))
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not remove the chunks of a failed attempt for document %s: %s", document.id, exc)

    # ------------------------------------------------------------------ #
    async def _fetch_page(self, document: Document) -> None:
        """A URL Document is fetched when it is first ingested (or retried after a failed fetch) and kept like an upload."""
        if self.url_fetcher is None or self.settings is None:
            raise ValueError("Fetching web pages is not available here.")
        keep_content(self.settings, document, await fetch_content(self.url_fetcher, document.source_url))

    async def _describe_images(self, document: Any) -> Described:
        """Describe the pictures of a stored PDF, PPTX or DOCX; none for any other source. Never raises."""
        from pathlib import Path

        path = Path(document.file_path) if document.file_path else None
        if path is None or not path.exists() or not self.pdf_parser:
            return Described()
        try:
            extraction = await self.pdf_parser.extract_images(path, load=self.vlm_provider is not None)
            # Half of the Document's time limit at most: descriptions that run out of time are dropped, not the Document.
            budget = self.timeout_seconds * IMAGE_TIME_SHARE if self.timeout_seconds else None
            return await describe_images(self.vlm_provider, extraction, budget)
        except Exception as exc:  # noqa: BLE001 - pictures never fail a Document
            logger.warning("Images of document %s were not described: %s", getattr(document, "id", path.name), exc)
            return Described(warnings=[IMAGES_UNREADABLE])

    @staticmethod
    def _note_images(document: Document, images: Described) -> None:
        """Show the admin which pictures went undescribed; a Document whose pictures all were has no such warning."""
        meta = {k: v for k, v in (document.extra_metadata or {}).items() if k != "image_warnings"}
        if images.warnings:
            meta["image_warnings"] = list(images.warnings)
        document.extra_metadata = meta

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
        revision: Optional[str] = None,
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
            if is_url_document(document):
                chunk_data["source_url"] = document.source_url
            if restriction_keys:
                chunk_data["restriction_principals"] = restriction_keys
            if revision:
                chunk_data["revision"] = revision
                chunk_data["staged"] = True
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
