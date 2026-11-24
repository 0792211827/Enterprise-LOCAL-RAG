"""Background ingestion: Documents run a couple at a time, each on its own, against a time limit, and are picked up again
after a restart. Real pipeline, real search client over an in-memory index, in-memory SQLite. Offline.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from sqlalchemy import select
from src.config import IngestionSettings, Settings
from src.models import KnowledgeBase
from src.models.document import Document
from src.models.enums import IngestionStatus
from src.models.ingestion import IngestionJob
from src.services.ingestion.factory import make_ingestion_service
from src.services.ingestion.runner import resume_interrupted, run_ingestion
from src.services.opensearch.client import KNN_ENGINE, OpenSearchClient
from src.services.pdf_parser.parser import PDFParserService
from tests.audit_support import opener, sqlite_session_factory
from tests.opensearch_fakes import FakeEmbeddings, FilteringIndex


class SlowEmbeddings(FakeEmbeddings):
    """Embeds instantly, unless the text says it is slow."""

    def __init__(self):
        super().__init__(dimension=2)

    async def embed_passages(self, texts, batch_size=100):
        if any("SLOW" in text for text in texts):
            await asyncio.sleep(30)
        if any("BOOM" in text for text in texts):
            raise RuntimeError("the embedding endpoint rejected this text")
        return await super().embed_passages(texts, batch_size)


@pytest.fixture
def plant(tmp_path):
    settings = Settings(ingestion=IngestionSettings(upload_dir=str(tmp_path / "uploads"), document_timeout_seconds=1))
    factory = sqlite_session_factory()
    with factory() as session:
        kb = KnowledgeBase(name="hr", slug="hr", index_name="hr-idx")
        session.add(kb)
        session.commit()
        kb_id = kb.id

    index = FilteringIndex(lexical=True)
    opensearch = OpenSearchClient.__new__(OpenSearchClient)
    opensearch.client, opensearch.index_name, opensearch.settings = index, "chunks", settings
    opensearch._engine, opensearch._engine_read_at = KNN_ENGINE, time.monotonic()
    service = make_ingestion_service(opensearch, SlowEmbeddings(), PDFParserService(max_pages=20, max_file_size_mb=10), settings)
    database = SimpleNamespace(get_session=opener(factory))

    def queue(text, status=IngestionStatus.QUEUED.value, source_uri=None):
        """A Document as an upload leaves it: stored and queued, with a job."""
        with factory() as session:
            document = Document(knowledge_base_id=kb_id, title=text[:20], source_uri=source_uri, content_type="text/plain", raw_text=text, status=status)
            session.add(document)
            session.commit()
            job = IngestionJob(document_id=document.id, knowledge_base_id=kb_id, status=status)
            session.add(job)
            session.commit()
            return document.id, job.id

    def stored(document_id):
        with factory() as session:
            document = session.get(Document, document_id)
            return document.status, document.error

    return SimpleNamespace(
        factory=factory, index=index, service=service, database=database, kb_id=kb_id, queue=queue, stored=stored, settings=settings
    )


def hits(plant, word):
    return [c["document_id"] for c in plant.index.docs.values() if word in c["chunk_text"] and not c.get("staged")]


async def run(plant, *queued, concurrency=2):
    await asyncio.gather(*(run_ingestion(plant.database, plant.service, doc, job, plant.kb_id, concurrency) for doc, job in queued))


# --- isolation, time limit and concurrency ------------------------------------------------------------------------ #
async def test_a_failing_document_does_not_fail_or_block_the_others(plant):
    first, bad, last = plant.queue("first good text about pelicans"), plant.queue("BOOM text"), plant.queue("last good text about narwhals")

    await run(plant, first, bad, last)

    assert plant.stored(first[0]) == ("COMPLETED", None) and plant.stored(last[0]) == ("COMPLETED", None)
    status, error = plant.stored(bad[0])
    assert status == "FAILED" and "rejected this text" in error
    assert hits(plant, "pelicans") == [str(first[0])] and hits(plant, "narwhals") == [str(last[0])]


async def test_a_document_past_its_time_limit_becomes_a_failed_document_with_the_reason_and_leaves_nothing_indexed(plant):
    slow, fine = plant.queue("SLOW pathological file"), plant.queue("an ordinary file about pelicans")

    started = time.monotonic()
    await run(plant, slow, fine)

    assert time.monotonic() - started < 10
    status, error = plant.stored(slow[0])
    assert status == "FAILED" and "1 second limit" in error and "INGESTION__DOCUMENT_TIMEOUT_SECONDS" in error
    assert plant.stored(fine[0]) == ("COMPLETED", None) and hits(plant, "pelicans") == [str(fine[0])]
    assert hits(plant, "pathological") == []


async def test_only_the_configured_number_of_documents_are_ingested_at_once(plant):
    running, peak = 0, 0

    class CountingService:
        async def ingest_document(self, document, job, document_repo, ingestion_repo, knowledge_base):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            running -= 1

    plant.service = CountingService()
    queued = [plant.queue(f"document number {n}") for n in range(6)]

    await run(plant, *queued, concurrency=2)

    assert peak == 2


async def test_an_unexpected_error_in_one_run_never_reaches_the_others(plant):
    calls = []

    class FlakyService:
        async def ingest_document(self, document, job, document_repo, ingestion_repo, knowledge_base):
            calls.append(document.title)
            if "second" in document.title:
                raise RuntimeError("the database went away")

    plant.service = FlakyService()
    queued = [plant.queue("first one"), plant.queue("second one"), plant.queue("third one")]

    await run(plant, *queued)

    assert sorted(calls) == ["first one", "second one", "third one"]


# --- restart recovery --------------------------------------------------------------------------------------------- #
async def test_documents_left_mid_processing_by_a_restart_are_queued_again_without_being_answered_from_twice(plant):
    queued_doc = plant.queue("queued text about pelicans")
    stuck_doc = plant.queue("stuck text about narwhals", status=IngestionStatus.PROCESSING.value)
    finished = plant.queue("finished text about otters", status=IngestionStatus.COMPLETED.value)
    connector_owned = plant.queue("connector text about seals", status=IngestionStatus.PROCESSING.value, source_uri="connector:hr/seals.txt")
    # What the interrupted attempt had already put in the index.
    plant.index.add({"document_id": str(stuck_doc[0]), "knowledge_base_id": str(plant.kb_id), "chunk_text": "half done narwhals"})

    resumed = await resume_interrupted(plant.database, plant.service)

    assert resumed == 2
    assert plant.stored(queued_doc[0]) == ("COMPLETED", None) and plant.stored(stuck_doc[0]) == ("COMPLETED", None)
    assert plant.stored(finished[0])[0] == "COMPLETED" and hits(plant, "otters") == []  # untouched: nothing was in flight
    assert plant.stored(connector_owned[0])[0] == "PROCESSING"  # the Connector run that owns it retries it
    assert hits(plant, "narwhals") == [str(stuck_doc[0])]  # once, and not the half-done copy
    assert [c for c in plant.index.docs.values() if "half done" in c["chunk_text"]] == []
    with plant.factory() as session:
        jobs = list(session.scalars(select(IngestionJob).where(IngestionJob.document_id == stuck_doc[0])))
    assert sorted(j.status for j in jobs) == ["COMPLETED", "FAILED"]


async def test_a_replacement_left_unfinished_by_a_restart_is_resumed_while_the_old_content_keeps_answering(plant):
    from src.repositories import DocumentRepository
    from src.services.ingestion.lifecycle import stage_replacement
    from src.services.ingestion.types import sniff_document_type

    original = plant.queue("the original text about pelicans")
    await run(plant, original)
    with plant.factory() as session:
        document = session.get(Document, original[0])
        raw = b"the replacement text about albatrosses"
        replacement = stage_replacement(plant.settings, DocumentRepository(session), document, "v2.txt", raw, sniff_document_type("v2.txt", raw))
        document.extra_metadata = {**document.extra_metadata, "replacement": {**replacement, "state": "processing"}}
        session.commit()
    plant.index.add(
        {"document_id": str(original[0]), "knowledge_base_id": str(plant.kb_id), "chunk_text": "half built albatrosses", "revision": replacement["revision"], "staged": True}
    )

    assert hits(plant, "pelicans") == [str(original[0])]
    resumed = await resume_interrupted(plant.database, plant.service)

    assert resumed == 1
    assert hits(plant, "albatrosses") == [str(original[0])] and hits(plant, "pelicans") == []
    assert [c["chunk_text"] for c in plant.index.docs.values() if c["document_id"] == str(original[0])] == [
        "the replacement text about albatrosses"
    ]


# --- a Document deleted, or an index failing, while it is being ingested ------------------------------------------ #
async def test_a_document_deleted_while_it_is_being_ingested_leaves_nothing_in_the_index(plant):
    doomed = plant.queue("DELETEME text about pelicans")

    class DeletingEmbeddings(SlowEmbeddings):
        async def embed_passages(self, texts, batch_size=100):
            with plant.factory() as session:  # an admin deletes the Document while it is being embedded
                session.delete(session.get(Document, doomed[0]))
                session.commit()
            return await super().embed_passages(texts, batch_size)

    plant.service.embeddings_provider = DeletingEmbeddings()

    await run(plant, doomed)

    assert plant.index.docs == {}


async def test_a_replacement_whose_old_chunks_cannot_be_removed_still_takes_over_and_says_so(plant):
    from src.repositories import DocumentRepository
    from src.services.ingestion.lifecycle import stage_replacement
    from src.services.ingestion.types import sniff_document_type

    original = plant.queue("the original text about pelicans")
    await run(plant, original)
    with plant.factory() as session:
        raw = b"the replacement text about albatrosses"
        stage_replacement(plant.settings, DocumentRepository(session), session.get(Document, original[0]), "v2.txt", raw, sniff_document_type("v2.txt", raw))
        job = IngestionJob(document_id=original[0], knowledge_base_id=plant.kb_id, status=IngestionStatus.QUEUED.value)
        session.add(job)
        session.commit()
        job_id = job.id

    plant.index.fail_deletes = True  # the index takes writes but cannot delete
    await run_ingestion(plant.database, plant.service, original[0], job_id, plant.kb_id, replacement=True)

    with plant.factory() as session:
        stored = session.get(Document, original[0])
        assert stored.status == "COMPLETED" and stored.replacement is None
        assert "could not be removed" in stored.warnings[0]
    assert str(original[0]) in hits(plant, "albatrosses")
