"""Connector telemetry: a sync reports its duration and what it did to the Knowledge Base, through the real sync.

Same setup as the Connector sync tests (a temporary folder and a fake S3 bucket, the real ingestion pipeline over an
in-memory index) with in-memory telemetry attached. Offline.
"""

from pathlib import Path

import pytest
import yaml
from src.cli import connector as connector_cli
from src.services.connectors import ConnectorError
from src.services.telemetry import make_in_memory_telemetry

from tests.unit.test_connector_sync import break_source, remove, sync, world, write  # noqa: F401  (fixture and helpers)

DURATION = "rag.connector.sync.duration"
DOCUMENTS = "rag.connector.documents"
STAGE = "rag.ingestion.stage.duration"
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def telemetry(world):
    telemetry = make_in_memory_telemetry()
    world.sync.telemetry = telemetry
    world.sync.ingestion.telemetry = telemetry
    return telemetry


def counts(telemetry, kb_id):
    return {labels["outcome"]: value for labels, value in telemetry.points(DOCUMENTS) if labels["knowledge_base"] == kb_id}


async def test_a_sync_reports_its_duration_and_the_documents_it_added_updated_removed_and_failed(world, telemetry):
    write(world, "keep.txt", "text that stays")
    write(world, "change.txt", "text that changes")
    write(world, "withdraw.txt", "text that is withdrawn")
    await sync(world)
    assert counts(telemetry, world.kb_id) == {"added": 3}

    write(world, "change.txt", "the changed text")
    write(world, "BOOM.txt", "BOOM")
    remove(world, "withdraw.txt")
    await sync(world)

    assert counts(telemetry, world.kb_id) == {"added": 3, "updated": 1, "removed": 1, "failed": 1}
    assert telemetry.value(DURATION, knowledge_base=world.kb_id, outcome="ok") == 1
    assert telemetry.value(DURATION, knowledge_base=world.kb_id, outcome="partial") == 1


async def test_the_sync_duration_is_a_real_time(world, telemetry):
    write(world, "a.txt")

    await sync(world)

    assert telemetry.value(DURATION) == 1
    assert 0 < telemetry.total(DURATION) < 60


async def test_a_run_that_could_not_be_made_reports_an_error_outcome_and_no_documents(world, telemetry):
    write(world, "a.txt")
    await sync(world)
    break_source(world)

    with pytest.raises(ConnectorError):
        await sync(world)

    assert telemetry.value(DURATION, outcome="error") == 1
    assert counts(telemetry, world.kb_id) == {"added": 1}


async def test_a_dry_run_changes_nothing_and_emits_nothing(world, telemetry):
    write(world, "a.txt")

    summary = await sync(world, dry_run=True)

    assert summary.added == 1
    assert telemetry.points(DURATION) == [] and telemetry.points(DOCUMENTS) == [] and telemetry.points(STAGE) == []


async def test_the_documents_it_ingests_are_timed_stage_by_stage_under_the_knowledge_base(world, telemetry):
    write(world, "a.txt")

    await sync(world)

    stages = {labels["stage"] for labels, _ in telemetry.points(STAGE) if labels["knowledge_base"] == world.kb_id}
    assert stages == {"parse", "chunk", "embed", "index"}


async def test_no_label_names_a_file_or_the_connector(world, telemetry):
    write(world, "salaries-2025.txt", "confidential salary bands")

    await sync(world)

    emitted = [str(value) for _, _, value in telemetry.labels_and_attributes()]
    assert emitted
    assert not [v for v in emitted if "salaries" in v or "confidential" in v or "hr-share" in v]


async def test_a_sync_with_telemetry_off_is_unchanged(world):
    write(world, "a.txt")

    summary = await sync(world)

    assert world.sync.telemetry.enabled is False
    assert (summary.added, summary.failed) == (1, 0)


def test_the_job_sends_what_it_has_before_it_exits(world, telemetry, monkeypatch):
    sent = []
    monkeypatch.setattr(telemetry, "shutdown", lambda: sent.append("shutdown"))

    connector_cli.main(["list"], sync=world.sync)

    assert sent == ["shutdown"]


@pytest.fixture
def job_collaborators(monkeypatch):
    """The job's other collaborators, faked: what is under test is the telemetry the job builds from its settings."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from src.config import OtelSettings, Settings

    monkeypatch.setattr("src.config.get_settings", lambda: Settings(otel=OtelSettings(enabled=True)))
    monkeypatch.setattr("src.db.factory.make_database", lambda: SimpleNamespace(get_session=MagicMock()))
    monkeypatch.setattr("src.services.opensearch.factory.make_opensearch_client", lambda: MagicMock())
    monkeypatch.setattr("src.services.embeddings.factory.make_embeddings_service", lambda: MagicMock())
    monkeypatch.setattr("src.services.pdf_parser.factory.make_pdf_parser_service", lambda: MagicMock())
    monkeypatch.setattr("src.services.providers.vlm.factory.make_vlm_provider", lambda settings: None)


def test_the_job_builds_its_telemetry_from_the_same_settings_as_the_api(job_collaborators, monkeypatch):
    built = make_in_memory_telemetry()
    seen = []
    monkeypatch.setattr("src.services.telemetry.make_telemetry", lambda settings: seen.append(settings.otel.enabled) or built)

    sync, _ = connector_cli.build_sync()

    assert seen == [True]
    assert sync.telemetry is built and sync.ingestion.telemetry is built


def test_a_job_whose_telemetry_cannot_start_still_syncs(job_collaborators, monkeypatch):
    def broken(settings):
        raise RuntimeError("bad endpoint")

    monkeypatch.setattr("src.services.telemetry.make_telemetry", broken)

    sync, _ = connector_cli.build_sync()

    assert sync.telemetry.enabled is False


def test_the_connector_service_in_compose_receives_the_otel_settings():
    environment = yaml.safe_load((REPO / "compose.yml").read_text())["services"]["connector"]["environment"]
    names = {entry.split("=", 1)[0] for entry in environment}

    assert {"OTEL__ENABLED", "OTEL__ENDPOINT", "OTEL__TRACE_SAMPLE_RATIO", "OTEL__EXPORT_TIMEOUT", "OTEL__QUEUE_SIZE"} <= names
    assert "OTEL__SERVICE_NAME=rag-connector" in environment
