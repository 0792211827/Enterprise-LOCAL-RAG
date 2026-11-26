"""Ingestion telemetry: each stage timed with its outcome, and the wait for a free slot, through the real pipeline.

Same setup as the background-ingestion tests (real pipeline and search client over an in-memory index, in-memory
SQLite) with in-memory telemetry attached. Offline.
"""

import asyncio

import pytest
from src.services.ingestion.runner import GATE, run_ingestion
from src.services.telemetry import make_in_memory_telemetry
from tests.unit.services.test_ingestion_runner import plant  # noqa: F401  (fixture)

STAGE = "rag.ingestion.stage.duration"
WAIT = "rag.ingestion.queue_wait"


@pytest.fixture
def telemetry(plant):
    telemetry = make_in_memory_telemetry()
    plant.service.telemetry = telemetry
    return telemetry


def stages(telemetry, kb_id):
    """``{(stage, outcome): count}`` over the Knowledge Base's series."""
    return {
        (labels["stage"], labels["outcome"]): count
        for labels, count in telemetry.points(STAGE)
        if labels["knowledge_base"] == str(kb_id)
    }


async def run(plant, *queued, concurrency=2):
    await asyncio.gather(*(run_ingestion(plant.database, plant.service, doc, job, plant.kb_id, concurrency) for doc, job in queued))


async def test_every_stage_of_a_document_is_timed_as_succeeded_under_its_knowledge_base(plant, telemetry):
    await run(plant, plant.queue("a good text about pelicans"))

    assert stages(telemetry, plant.kb_id) == {
        ("parse", "succeeded"): 1,
        ("chunk", "succeeded"): 1,
        ("embed", "succeeded"): 1,
        ("index", "succeeded"): 1,
    }


async def test_a_stage_that_raises_is_failed_and_the_stages_after_it_are_absent(plant, telemetry):
    await run(plant, plant.queue("BOOM text"))

    assert stages(telemetry, plant.kb_id) == {("parse", "succeeded"): 1, ("chunk", "succeeded"): 1, ("embed", "failed"): 1}


async def test_a_document_stopped_by_its_time_limit_is_a_failed_stage(plant, telemetry):
    await run(plant, plant.queue("SLOW pathological file"))

    assert stages(telemetry, plant.kb_id)[("embed", "failed")] == 1
    assert ("embed", "interrupted") not in stages(telemetry, plant.kb_id)


async def test_a_document_whose_ingestion_is_cancelled_is_an_interrupted_stage(plant, telemetry):
    task = asyncio.create_task(run_ingestion(plant.database, plant.service, *plant.queue("SLOW text"), plant.kb_id, 2))
    await asyncio.sleep(0.3)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert stages(telemetry, plant.kb_id) == {("parse", "succeeded"): 1, ("chunk", "succeeded"): 1, ("embed", "interrupted"): 1}


async def test_the_wait_for_a_free_slot_is_recorded_per_document(plant, telemetry):
    gate = GATE.slot(1)
    await gate.acquire()  # something else is being ingested
    task = asyncio.create_task(run_ingestion(plant.database, plant.service, *plant.queue("waited-for text"), plant.kb_id, 1))
    await asyncio.sleep(0.3)
    gate.release()
    await task

    assert telemetry.value(WAIT, knowledge_base=str(plant.kb_id)) == 1
    assert telemetry.total(WAIT, knowledge_base=str(plant.kb_id)) >= 0.3


async def test_no_label_or_attribute_names_a_file_or_holds_its_text(plant, telemetry):
    text = "confidential remuneration policy for Dana Whitfield"
    await run(plant, plant.queue(text), plant.queue("BOOM " + text))

    emitted = [str(value) for _, _, value in telemetry.labels_and_attributes()]
    assert emitted
    assert not [v for v in emitted if "remuneration" in v or "Whitfield" in v or "BOOM" in v or "confidential" in v]
    labels = {name for where, name, _ in telemetry.labels_and_attributes() if where.startswith("metric rag.ingestion")}
    assert labels <= {"knowledge_base", "stage", "outcome"}

