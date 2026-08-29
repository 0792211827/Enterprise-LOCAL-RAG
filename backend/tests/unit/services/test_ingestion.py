"""Unit tests for the generic document ingestion pipeline."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.models.document import Document
from src.models.enums import IngestionStatus
from src.models.ingestion import IngestionJob
from src.schemas.indexing.models import ChunkMetadata, TextChunk
from src.services.ingestion.service import IngestionService


def _make_chunk(index: int, text: str) -> TextChunk:
    return TextChunk(
        text=text,
        metadata=ChunkMetadata(
            chunk_index=index,
            start_char=0,
            end_char=len(text),
            word_count=len(text.split()),
            overlap_with_previous=0,
            overlap_with_next=0,
            section_title="Intro",
        ),
        arxiv_id="x",
        paper_id="x",
    )


@pytest.fixture
def collaborators():
    chunker = MagicMock()
    chunker.chunk_paper = MagicMock(return_value=[_make_chunk(0, "hello world"), _make_chunk(1, "second chunk")])

    embeddings = AsyncMock()
    embeddings.embed_passages = AsyncMock(return_value=[[0.1] * 4, [0.2] * 4])

    opensearch = MagicMock()
    opensearch.bulk_index_chunks = MagicMock(return_value={"success": 2, "failed": 0})
    return chunker, embeddings, opensearch


@pytest.mark.anyio
async def test_ingest_document_success(collaborators):
    chunker, embeddings, opensearch = collaborators
    service = IngestionService(chunker, embeddings, opensearch)

    kb_id = uuid.uuid4()
    document = Document(
        id=uuid.uuid4(),
        knowledge_base_id=kb_id,
        title="Doc",
        raw_text="hello world second chunk",
        status=IngestionStatus.QUEUED.value,
    )
    job = IngestionJob(id=uuid.uuid4(), document_id=document.id, knowledge_base_id=kb_id)
    kb = SimpleNamespace(embedding_model="BAAI/bge-m3")

    doc_repo = MagicMock()
    ingestion_repo = MagicMock()

    stats = await service.ingest_document(document, job, doc_repo, ingestion_repo, kb)

    assert stats["chunks_created"] == 2
    assert stats["chunks_indexed"] == 2
    assert document.status == IngestionStatus.COMPLETED.value
    assert document.chunk_count == 2
    assert job.status == IngestionStatus.COMPLETED.value
    # Two chunk rows persisted, index called once.
    doc_repo.add_chunks.assert_called_once()
    opensearch.bulk_index_chunks.assert_called_once()


@pytest.mark.anyio
async def test_ingest_document_marks_failure_on_empty_text(collaborators):
    chunker, embeddings, opensearch = collaborators
    service = IngestionService(chunker, embeddings, opensearch)

    kb_id = uuid.uuid4()
    document = Document(id=uuid.uuid4(), knowledge_base_id=kb_id, title="Empty", raw_text="")
    job = IngestionJob(id=uuid.uuid4(), document_id=document.id, knowledge_base_id=kb_id)

    stats = await service.ingest_document(
        document, job, MagicMock(), MagicMock(), SimpleNamespace(embedding_model="m")
    )

    assert stats["chunks_indexed"] == 0
    assert document.status == IngestionStatus.FAILED.value
    assert job.status == IngestionStatus.FAILED.value
    assert job.error is not None


@pytest.mark.anyio
async def test_ingest_document_embedding_mismatch_fails(collaborators):
    chunker, embeddings, opensearch = collaborators
    embeddings.embed_passages = AsyncMock(return_value=[[0.1] * 4])  # only one vector for two chunks
    service = IngestionService(chunker, embeddings, opensearch)

    kb_id = uuid.uuid4()
    document = Document(id=uuid.uuid4(), knowledge_base_id=kb_id, title="Doc", raw_text="hello world second")
    job = IngestionJob(id=uuid.uuid4(), document_id=document.id, knowledge_base_id=kb_id)

    await service.ingest_document(document, job, MagicMock(), MagicMock(), SimpleNamespace(embedding_model="m"))
    assert document.status == IngestionStatus.FAILED.value
    opensearch.bulk_index_chunks.assert_not_called()
