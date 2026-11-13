"""The Connector job against a temporary directory and against an S3-compatible bucket: add, change, delete and failure.
Every behaviour test runs once per source, which is the point: a source only lists and reads, so they must not differ.

The real ingestion pipeline and OpenSearch client run over an in-memory index and a fake embedder, so what is asserted is
what ends up in the Knowledge Base (Documents, indexed chunks) and in the audit log -- not how the sync is written. Offline.
"""

import hashlib
import os
import shutil
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from sqlalchemy import select
from src.cli import connector as connector_cli
from src.config import ConnectorSettings, IngestionSettings, Settings
from src.models import AuditEvent, Connector, ConnectorFileState, KnowledgeBase
from src.models.document import Document
from src.repositories import RestrictionRepository
from src.services.connectors import ConnectorError, ConnectorSync, S3Source
from src.services.ingestion.factory import make_ingestion_service
from src.services.opensearch.client import OpenSearchClient

from tests.audit_support import opener, sqlite_session_factory
from tests.opensearch_fakes import FakeEmbeddings, FilteringIndex


class Embeddings(FakeEmbeddings):
    def __init__(self):
        super().__init__(dimension=2)

    async def embed_passages(self, texts, batch_size=100):
        if any("BOOM" in text for text in texts):
            raise RuntimeError("the embedding endpoint rejected this text")
        return await super().embed_passages(texts, batch_size)


class Parser:
    async def parse_pdf(self, path):
        return SimpleNamespace(text=f"parsed pdf {path.read_bytes().decode()}", sections=None)


class FakeS3:
    """The slice of the S3 API the source uses, over a dict; it serves only its one bucket and records every call."""

    BUCKET = "docs"

    def __init__(self):
        self.objects = {}  # key -> (bytes, advertised size)
        self.calls = []
        self.broken = False

    def put(self, key, data, size=None):
        self.objects[key] = (data, len(data) if size is None else size)

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        self.calls.append(("list", Bucket, Prefix))
        if self.broken or Bucket != self.BUCKET:
            raise ConnectionError("NoSuchBucket: the bucket does not exist")
        items = [
            {"Key": key, "Size": size, "ETag": f'"{hashlib.md5(data).hexdigest()}"'}
            for key, (data, size) in sorted(self.objects.items())
            if key.startswith(Prefix)
        ]
        for start in range(0, len(items), 2):  # small pages, so pagination is exercised
            yield {"Contents": items[start : start + 2]}
        if not items:
            yield {}

    def get_object(self, Bucket, Key):
        self.calls.append(("get", Bucket, Key))
        assert Bucket == self.BUCKET
        return {"Body": SimpleNamespace(read=lambda: self.objects[Key][0])}


S3_CONFIG = {
    "endpoint_url": "http://minio.internal:9000",
    "bucket": "docs",
    "prefix": "hr/",
    "access_key_ref": "env:MINIO_ACCESS_KEY",
    "secret_key_ref": "env:MINIO_SECRET_KEY",
}


@pytest.fixture(params=["path", "s3"])
def world(request, tmp_path):
    kind = request.param
    share = tmp_path / "mnt" / "hr-share"
    share.mkdir(parents=True)
    settings = Settings(
        connector=ConnectorSettings(mount_root=str(tmp_path / "mnt")),
        ingestion=IngestionSettings(upload_dir=str(tmp_path / "uploads")),
    )
    factory = sqlite_session_factory()
    with factory() as session:
        kb = KnowledgeBase(name="hr", slug="hr", index_name="hr-idx")
        session.add(kb)
        session.commit()
        config = {"path": str(share)} if kind == "path" else S3_CONFIG
        session.add(Connector(name="hr-share", knowledge_base_id=kb.id, source_type=kind, source_config=config))
        session.commit()
        kb_id = str(kb.id)

    index = FilteringIndex()
    opensearch = OpenSearchClient.__new__(OpenSearchClient)
    opensearch.client, opensearch.index_name, opensearch.settings = index, "chunks", settings
    embeddings = Embeddings()
    ingestion = make_ingestion_service(opensearch, embeddings, Parser(), settings)
    fake_s3 = FakeS3()
    if kind == "s3":
        from src.services.connectors import build_source

        def factory_for(connector, settings):
            return S3Source(connector.source_config, settings, client=fake_s3) if kind == "s3" else build_source(connector, settings)
    else:
        factory_for = None
    sync = ConnectorSync(opener(factory), ingestion, opensearch, settings, **({"source_factory": factory_for} if factory_for else {}))
    return SimpleNamespace(
        kind=kind, share=share, s3=fake_s3, settings=settings, factory=factory, index=index, opensearch=opensearch,
        embeddings=embeddings, sync=sync, kb_id=kb_id,
    )


def write(world, name, text="some policy text for the handbook", mtime=None):
    """Put a file in the source (the modified time only matters to a folder; an object changes with its content)."""
    if world.kind == "s3":
        world.s3.put("hr/" + name, text.encode())
        return None
    path = world.share / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def remove(world, name):
    if world.kind == "s3":
        del world.s3.objects["hr/" + name]
    else:
        (world.share / name).unlink()


def break_source(world):
    """Make the source unreachable: an unmounted folder, a bucket that is gone."""
    if world.kind == "s3":
        world.s3.broken = True
    else:
        shutil.rmtree(world.share)


UNREACHABLE = {"path": "not a directory", "s3": "Could not list the source"}


async def sync(world, **kwargs):
    return await world.sync.run("hr-share", **kwargs)


def documents(world):
    with world.factory() as session:
        return {d.title: d for d in session.scalars(select(Document))}


def indexed_text(world):
    return sorted(s["chunk_text"] for s in world.index.docs.values())


def states(world):
    with world.factory() as session:
        return {s.source_key: s for s in session.scalars(select(ConnectorFileState))}


def audit(world):
    with world.factory() as session:
        return list(session.scalars(select(AuditEvent).where(AuditEvent.action == "connector.sync")))


# --- add ----------------------------------------------------------------------------------------------------------- #
async def test_new_files_are_added_as_documents_in_the_knowledge_base(world):
    write(world, "leave.txt", "Employees get 25 days of annual leave.")
    write(world, "policies/remote.md", "# Remote work\n\nRemote work is allowed two days a week.")
    write(world, "policies/handbook.pdf", "handbook body")

    summary = await sync(world)

    assert (summary.added, summary.updated, summary.removed, summary.failed) == (3, 0, 0, 0)
    docs = documents(world)
    assert set(docs) == {"leave.txt", "policies/remote.md", "policies/handbook.pdf"}
    assert all(str(d.knowledge_base_id) == world.kb_id and d.status == "COMPLETED" for d in docs.values())
    assert any("25 days of annual leave" in t for t in indexed_text(world))
    assert any("parsed pdf handbook body" in t for t in indexed_text(world))  # a binary type goes through the parser
    assert {s["knowledge_base_id"] for s in world.index.docs.values()} == {world.kb_id}
    assert set(states(world)) == {"leave.txt", "policies/remote.md", "policies/handbook.pdf"}
    with world.factory() as session:
        kb = session.scalar(select(KnowledgeBase))
        assert kb.document_count == 3 and kb.chunk_count >= 3


async def test_ingested_documents_are_unrestricted_until_an_admin_tags_them(world):
    write(world, "leave.txt")

    await sync(world)

    assert all("restriction_principals" not in s for s in world.index.docs.values())
    with world.factory() as session:
        doc = session.scalar(select(Document))
        assert RestrictionRepository(session).list_for_document(doc.id) == []


async def test_a_run_prints_added_updated_removed_and_failed_counts(world):
    write(world, "a.txt")
    write(world, "b.txt", "BOOM")

    summary = await sync(world)

    text = summary.render()
    assert "added 1, updated 0, removed 0, failed 1" in text and "FAILED b.txt" in text


async def test_unsupported_types_and_hidden_files_are_skipped_not_failed(world):
    write(world, "notes.txt")
    write(world, "slides.pptx", "binary")
    write(world, ".hidden.txt")
    write(world, ".git/config.txt")

    summary = await sync(world)

    assert (summary.added, summary.failed, summary.skipped) == (1, 0, 1)
    assert set(documents(world)) == {"notes.txt"}


async def test_links_in_a_folder_are_never_followed(world, tmp_path):
    if world.kind != "path":
        pytest.skip("a folder concept")
    write(world, "notes.txt")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret outside the share")
    (world.share / "link.txt").symlink_to(outside)

    summary = await sync(world)

    assert (summary.added, summary.failed) == (1, 0) and set(documents(world)) == {"notes.txt"}
    assert not any("secret outside" in t for t in indexed_text(world))


# --- unchanged ----------------------------------------------------------------------------------------------------- #
async def test_unchanged_files_are_skipped_and_not_even_read(world, monkeypatch):
    write(world, "leave.txt")
    write(world, "remote.txt", "other text")
    await sync(world)
    embedded = len(world.embeddings.embedded)
    from src.services.connectors.sources import PathSource

    reads = []
    original = PathSource.read
    monkeypatch.setattr(PathSource, "read", lambda self, key: reads.append(key) or original(self, key))
    world.s3.calls.clear()

    summary = await sync(world)

    assert (summary.added, summary.updated, summary.removed, summary.failed, summary.unchanged) == (0, 0, 0, 0, 2)
    assert reads == [] and not [c for c in world.s3.calls if c[0] == "get"]  # listed, never downloaded
    assert len(world.embeddings.embedded) == embedded


async def test_a_file_touched_without_changing_is_not_re_ingested(world):
    if world.kind != "path":
        pytest.skip("an object's ETag does not change with its modified time")
    path = write(world, "leave.txt", mtime=1_000_000_000)
    await sync(world)
    embedded = len(world.embeddings.embedded)
    os.utime(path, (1_100_000_000, 1_100_000_000))

    summary = await sync(world)

    assert (summary.updated, summary.unchanged) == (0, 1) and len(world.embeddings.embedded) == embedded
    assert states(world)["leave.txt"].fingerprint.endswith(str(1_100_000_000 * 10**9))  # remembered, so not read again


# --- change -------------------------------------------------------------------------------------------------------- #
async def test_a_changed_file_updates_its_document_in_place_and_replaces_its_chunks(world):
    write(world, "leave.txt", "Employees get 25 days of annual leave.", mtime=1_000_000_000)
    await sync(world)
    original = documents(world)["leave.txt"]

    write(world, "leave.txt", "Employees now get 30 days of annual leave.", mtime=1_200_000_000)
    summary = await sync(world)

    assert (summary.added, summary.updated, summary.unchanged) == (0, 1, 0)
    docs = documents(world)
    assert len(docs) == 1 and docs["leave.txt"].id == original.id  # the same Document
    texts = indexed_text(world)
    assert any("30 days" in t for t in texts) and not any("25 days" in t for t in texts)
    with world.factory() as session:
        assert session.scalar(select(Document).where(Document.id == original.id)).chunk_count == len(texts)


async def test_a_restriction_an_admin_set_survives_an_update(world):
    write(world, "board.txt", "Board minutes, version one.", mtime=1_000_000_000)
    await sync(world)
    doc = documents(world)["board.txt"]
    with world.factory() as session:
        RestrictionRepository(session).replace_for_document(doc.id, [("group", "board")])
    world.opensearch.set_document_restrictions(str(doc.id), ["group:board"])

    write(world, "board.txt", "Board minutes, version two.", mtime=1_200_000_000)
    await sync(world)

    chunks = list(world.index.docs.values())
    assert chunks and all(c["restriction_principals"] == ["group:board"] for c in chunks)
    assert any("version two" in c["chunk_text"] for c in chunks)


# --- delete -------------------------------------------------------------------------------------------------------- #
async def test_a_file_removed_from_the_source_removes_its_document_and_indexed_chunks(world):
    write(world, "leave.txt", "Employees get 25 days of annual leave.")
    write(world, "remote.txt", "Remote work is allowed.")
    await sync(world)

    remove(world, "leave.txt")
    summary = await sync(world)

    assert (summary.added, summary.updated, summary.removed, summary.failed) == (0, 0, 1, 0)
    assert set(documents(world)) == {"remote.txt"} and set(states(world)) == {"remote.txt"}
    assert not any("25 days" in t for t in indexed_text(world)) and any("Remote work" in t for t in indexed_text(world))
    with world.factory() as session:
        assert session.scalar(select(KnowledgeBase)).document_count == 1


async def test_a_removal_that_cannot_reach_the_index_keeps_the_document_and_is_retried(world):
    write(world, "leave.txt", "Employees get 25 days of annual leave.")
    write(world, "remote.txt", "Remote work is allowed.")
    await sync(world)
    remove(world, "leave.txt")

    world.index.fail_deletes = True
    first = await sync(world)
    still_there = set(documents(world))
    world.index.fail_deletes = False
    second = await sync(world)

    assert (first.removed, first.failed) == (0, 1) and "could not remove" in first.files["failed"][0]["error"]
    assert still_there == {"leave.txt", "remote.txt"}  # kept, so the next run knows to retry
    assert (second.removed, second.failed) == (1, 0) and set(documents(world)) == {"remote.txt"}
    assert not any("25 days" in t for t in indexed_text(world))


async def test_a_document_deleted_by_hand_is_brought_back_on_the_next_run(world):
    write(world, "leave.txt")
    await sync(world)
    with world.factory() as session:
        session.delete(session.scalar(select(Document)))
        session.commit()

    summary = await sync(world)

    assert summary.added == 1 and set(documents(world)) == {"leave.txt"}


async def test_an_empty_or_unmounted_source_never_wipes_the_knowledge_base(world):
    write(world, "leave.txt")
    await sync(world)
    remove(world, "leave.txt")

    with pytest.raises(ConnectorError, match="--allow-empty"):
        await sync(world)
    assert set(documents(world)) == {"leave.txt"} and indexed_text(world) != []

    summary = await sync(world, allow_empty=True)
    assert summary.removed == 1 and documents(world) == {}


async def test_a_source_that_is_not_there_fails_the_run_and_removes_nothing(world):
    write(world, "leave.txt")
    await sync(world)
    break_source(world)

    with pytest.raises(ConnectorError, match=UNREACHABLE[world.kind]):
        await sync(world)

    assert set(documents(world)) == {"leave.txt"}
    assert audit(world)[-1].outcome == "error" and UNREACHABLE[world.kind] in audit(world)[-1].details["error"]


# --- failure ------------------------------------------------------------------------------------------------------- #
async def test_a_failing_file_does_not_stop_the_run_and_is_retried_next_time(world):
    write(world, "a.txt", "first good file")
    write(world, "b.txt", "BOOM")
    write(world, "c.txt", "last good file")

    first = await sync(world)

    assert (first.added, first.failed) == (2, 1)
    assert "rejected this text" in first.files["failed"][0]["error"]
    assert states(world)["b.txt"].status == "failed" and "rejected" in states(world)["b.txt"].error
    assert documents(world)["b.txt"].status == "FAILED"
    assert any("first good file" in t for t in indexed_text(world)) and any("last good file" in t for t in indexed_text(world))

    write(world, "b.txt", "fixed text", mtime=1_300_000_000)
    second = await sync(world)

    assert (second.added, second.updated, second.failed, second.unchanged) == (1, 0, 0, 2)  # the retry counts as its first add
    assert states(world)["b.txt"].status == "synced" and documents(world)["b.txt"].status == "COMPLETED"
    assert len(documents(world)) == 3


async def test_a_file_over_the_size_limit_fails_without_being_read(world):
    world.sync.settings = world.settings.model_copy(update={"ingestion": IngestionSettings(max_upload_mb=1, upload_dir="x")})
    if world.kind == "s3":
        world.s3.put("hr/huge.txt", b"x" * 10, size=2 * 1024 * 1024)
    else:
        with (world.share / "huge.txt").open("wb") as handle:
            handle.truncate(2 * 1024 * 1024)

    summary = await sync(world)

    assert summary.failed == 1 and "larger than the 1 MB limit" in summary.files["failed"][0]["error"]
    assert documents(world) == {} and not [c for c in world.s3.calls if c[0] == "get"]


# --- audit, dry run, lock ------------------------------------------------------------------------------------------ #
async def test_each_run_is_written_to_the_audit_log_with_counts_and_file_names(world):
    write(world, "leave.txt")
    write(world, "bad.txt", "BOOM")
    await sync(world)
    remove(world, "leave.txt")
    write(world, "bad.txt", "BOOM again", mtime=1_400_000_000)
    await sync(world)

    first, second = audit(world)

    assert first.outcome == "ok" and first.resource_type == "connector" and first.requested_kb_ids == [world.kb_id]
    assert first.details["connector"] == "hr-share" and (first.details["added"], first.details["failed"]) == (1, 1)
    assert first.details["files"]["added"] == ["leave.txt"] and first.details["files"]["failed"][0]["key"] == "bad.txt"
    assert (second.details["removed"], second.details["failed"]) == (1, 1)
    assert "text" not in str(first.details).replace("rejected this text", "")  # file names and counts, never content


async def test_a_dry_run_reports_the_changes_and_makes_none(world):
    write(world, "keep.txt", "keep this", mtime=1_000_000_000)
    write(world, "gone.txt")
    await sync(world)
    write(world, "keep.txt", "keep this, changed", mtime=1_500_000_000)
    write(world, "new.txt")
    remove(world, "gone.txt")
    runs_before, texts_before = len(audit(world)), indexed_text(world)

    summary = await sync(world, dry_run=True)

    assert (summary.added, summary.updated, summary.removed) == (1, 1, 1) and summary.render().startswith("Would have:")
    assert indexed_text(world) == texts_before and len(audit(world)) == runs_before
    assert set(documents(world)) == {"keep.txt", "gone.txt"}


async def test_a_run_is_refused_while_another_is_in_progress_and_a_stale_lock_expires(world):
    write(world, "leave.txt")
    with world.factory() as session:
        connector = session.scalar(select(Connector))
        connector.run_started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.commit()

    with pytest.raises(ConnectorError, match="already running"):
        await sync(world)
    assert documents(world) == {}

    with world.factory() as session:
        connector = session.scalar(select(Connector))
        connector.run_started_at = datetime.now(timezone.utc) - timedelta(hours=7)
        session.commit()
    assert (await sync(world)).added == 1
    with world.factory() as session:
        assert session.scalar(select(Connector)).run_started_at is None  # released when the run ended


async def test_a_disabled_or_unknown_connector_is_refused(world):
    with world.factory() as session:
        session.scalar(select(Connector)).enabled = False
        session.commit()

    with pytest.raises(ConnectorError, match="disabled"):
        await sync(world)
    with pytest.raises(LookupError):
        await world.sync.run("nope")


# --- the command --------------------------------------------------------------------------------------------------- #
def run_cli(world, *argv):
    world.opensearch.setup_indices = lambda: None
    world.opensearch.get_embedding_dimension = lambda: None
    return connector_cli.main(list(argv), sync=world.sync)


def test_the_command_prints_the_summary_and_exits_zero(world, capsys):
    write(world, "leave.txt")

    code = run_cli(world, "sync", "hr-share")

    assert code == 0 and "hr-share: added 1, updated 0, removed 0, failed 0" in capsys.readouterr().out


def test_the_command_exits_one_when_a_file_failed_but_still_ran_the_rest(world, capsys):
    write(world, "ok.txt")
    write(world, "bad.txt", "BOOM")

    code = run_cli(world, "sync", "--all")

    out = capsys.readouterr().out
    assert code == 1 and "added 1" in out and "FAILED bad.txt" in out and set(documents(world)) == {"ok.txt", "bad.txt"}


def test_the_command_exits_two_when_a_run_cannot_be_made(world, capsys):
    assert run_cli(world, "sync", "nope") == 2
    assert "No Connector named 'nope'" in capsys.readouterr().err
    assert run_cli(world, "sync") == 2  # no names and no --all
    break_source(world)
    assert run_cli(world, "sync", "hr-share") == 2


def test_the_command_refuses_to_ingest_into_an_index_that_needs_a_reindex(world, capsys):
    write(world, "leave.txt")
    world.opensearch.setup_indices = lambda: None
    world.opensearch.index_name = "chunks"
    world.opensearch.get_embedding_dimension = lambda: 4096  # the index holds other-sized vectors

    code = connector_cli.main(["sync", "hr-share"], sync=world.sync)

    assert code == 2 and "reindex" in capsys.readouterr().err and documents(world) == {}


def test_a_dry_run_command_needs_no_index_and_changes_nothing(world, capsys):
    write(world, "leave.txt")

    assert run_cli(world, "sync", "hr-share", "--dry-run") == 0

    assert "Would have: added 1" in capsys.readouterr().out and documents(world) == {}


def test_list_shows_connectors_and_their_last_run(world, capsys):
    write(world, "leave.txt")
    run_cli(world, "sync", "hr-share")
    capsys.readouterr()

    assert run_cli(world, "list") == 0

    line = capsys.readouterr().out.strip()
    assert line.startswith(f"hr-share\t{world.kind}\tenabled\t1 synced, 0 failed\tok at ")


# --- the mount root keeps the job inside the mounted shares -------------------------------------------------------- #
class TestMountRoot:
    def settings(self, root):
        return Settings(connector=ConnectorSettings(mount_root=root))

    def test_mounted_paths_are_refused_until_a_mount_root_is_set(self):
        from src.services.connectors import check_mount_path

        with pytest.raises(ConnectorError, match="CONNECTOR__MOUNT_ROOT"):
            check_mount_path("/mnt/sources/hr", self.settings(""))

    @pytest.mark.parametrize(
        "path", ["relative/dir", "/etc", "/mnt/sources/../etc", "/mnt/sources-evil/hr", "/mnt", "/mnt/sources/../../etc/passwd"]
    )
    def test_paths_outside_the_root_are_refused(self, path):
        from src.services.connectors import check_mount_path

        with pytest.raises(ConnectorError):
            check_mount_path(path, self.settings("/mnt/sources"))

    def test_paths_at_or_below_the_root_are_normalised(self):
        from src.services.connectors import check_mount_path

        settings = self.settings("/mnt/sources")
        assert check_mount_path("/mnt/sources", settings) == "/mnt/sources"
        assert check_mount_path("/mnt/sources/hr//share/./", settings) == "/mnt/sources/hr/share"

    def test_a_share_that_is_a_link_leading_outside_the_root_is_refused_by_the_job(self, tmp_path):
        from src.services.connectors import PathSource

        root = tmp_path / "mnt"
        root.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (root / "trap").symlink_to(elsewhere)

        with pytest.raises(ConnectorError, match="outside CONNECTOR__MOUNT_ROOT"):
            PathSource(str(root / "trap"), Settings(connector=ConnectorSettings(mount_root=str(root))))

    def test_a_link_inside_the_share_cannot_be_read(self, tmp_path):
        from src.services.connectors import PathSource

        root = tmp_path / "mnt"
        (root / "share").mkdir(parents=True)
        (tmp_path / "secret.txt").write_text("outside")
        (root / "share" / "link.txt").symlink_to(tmp_path / "secret.txt")
        source = PathSource(str(root / "share"), Settings(connector=ConnectorSettings(mount_root=str(root))))

        assert source.list_files() == []
        with pytest.raises(ConnectorError):
            source.read("link.txt")


# --- the S3 source ------------------------------------------------------------------------------------------------- #
class TestS3Source:
    @pytest.fixture(autouse=True)
    def credentials(self, monkeypatch):
        monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access")
        monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret")

    def test_only_the_prefix_is_mirrored_and_keys_are_relative_to_it(self):
        fake = FakeS3()
        for key in ("hr/leave.txt", "hr/policies/remote.md", "hr/.hidden.txt", "hr/folder/", "finance/salaries.txt", "other.txt"):
            fake.put(key, b"x")

        files = S3Source(S3_CONFIG, Settings(), client=fake).list_files()

        assert [f.key for f in files] == ["leave.txt", "policies/remote.md"]
        assert fake.calls == [("list", "docs", "hr/")]

    def test_a_prefix_without_a_trailing_slash_does_not_match_a_sibling_folder(self):
        fake = FakeS3()
        fake.put("hr/leave.txt", b"x")
        fake.put("hr-secret/board.txt", b"y")

        files = S3Source({**S3_CONFIG, "prefix": "hr"}, Settings(), client=fake).list_files()

        assert [f.key for f in files] == ["leave.txt"]

    def test_the_fingerprint_changes_with_the_content(self):
        fake = FakeS3()
        source = S3Source(S3_CONFIG, Settings(), client=fake)
        fake.put("hr/a.txt", b"one")
        before = source.list_files()[0].fingerprint
        fake.put("hr/a.txt", b"two")

        assert source.list_files()[0].fingerprint != before

    @pytest.mark.parametrize("reference", ["AKIAIOSFODNN7EXAMPLE", "", "env:", "plain-secret"])
    def test_a_literal_credential_is_refused_and_never_echoed(self, reference):
        with pytest.raises(ConnectorError) as raised:
            S3Source({**S3_CONFIG, "secret_key_ref": reference}, Settings())

        assert "environment reference" in str(raised.value) and (reference or "x") not in str(raised.value).replace("env:", "")

    def test_a_missing_variable_is_named_in_the_error(self, monkeypatch):
        monkeypatch.delenv("MINIO_SECRET_KEY")

        with pytest.raises(ConnectorError, match="MINIO_SECRET_KEY"):
            S3Source(S3_CONFIG, Settings())

    @pytest.mark.parametrize("endpoint", ["", "minio.internal:9000", "ftp://minio.internal", "file:///etc/passwd"])
    def test_the_endpoint_must_be_an_http_address(self, endpoint):
        with pytest.raises(ConnectorError, match="endpoint"):
            S3Source({**S3_CONFIG, "endpoint_url": endpoint}, Settings(), client=FakeS3())

    def test_the_operators_host_allowlist_applies(self):
        settings = Settings(connector=ConnectorSettings(allowed_s3_hosts="minio.internal, other.internal:9443"))

        S3Source(S3_CONFIG, settings, client=FakeS3())
        S3Source({**S3_CONFIG, "endpoint_url": "https://other.internal:9443"}, settings, client=FakeS3())
        for endpoint in ("http://evil.example:9000", "https://other.internal:1", "http://minio.internal.evil.example"):
            with pytest.raises(ConnectorError, match="CONNECTOR__ALLOWED_S3_HOSTS"):
                S3Source({**S3_CONFIG, "endpoint_url": endpoint}, settings, client=FakeS3())

    def test_the_real_client_is_configured_for_the_one_endpoint_with_explicit_credentials(self):
        source = S3Source({**S3_CONFIG, "region": "eu-west-2"}, Settings())

        client = source.client
        assert client.meta.endpoint_url == "http://minio.internal:9000" and client.meta.region_name == "eu-west-2"
        assert client.meta.config.s3["addressing_style"] == "path"  # never <bucket>.<host>
        credentials = client._request_signer._credentials
        assert (credentials.access_key, credentials.secret_key) == ("test-access", "test-secret")

    def test_no_host_other_than_the_endpoint_is_contacted(self, monkeypatch):
        import socket

        contacted = []
        real = socket.getaddrinfo

        def spy(host, *args, **kwargs):
            contacted.append(host)
            return real(host, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", spy)
        monkeypatch.delenv("AWS_EC2_METADATA_DISABLED", raising=False)
        source = S3Source({**S3_CONFIG, "endpoint_url": "http://127.0.0.1:9"}, Settings())  # nothing listens there

        with pytest.raises(Exception):  # noqa: B017 - the connection is refused; what matters is who was dialled
            source.list_files()

        assert set(contacted) == {"127.0.0.1"}  # no metadata service, no AWS endpoint, no bucket subdomain


# --- the schedule -------------------------------------------------------------------------------------------------- #
def test_the_schedule_runs_rounds_waits_between_them_and_survives_a_failed_one(world, capsys):
    write(world, "leave.txt")
    world.opensearch.setup_indices = lambda: None
    world.opensearch.get_embedding_dimension = lambda: None
    waits = []

    connector_cli._schedule(world.sync, 15, allow_empty=False, sleep=waits.append, rounds=1)
    assert "added 1" in capsys.readouterr().out and waits == []

    write(world, "remote.txt", "second file")
    world.opensearch.setup_indices = lambda: (_ for _ in ()).throw(ConnectionError("opensearch is down"))
    connector_cli._schedule(world.sync, 15, allow_empty=False, sleep=waits.append, rounds=2)

    assert waits == [900] and capsys.readouterr().err.count("round failed: opensearch is down") == 2
    assert set(documents(world)) == {"leave.txt"}  # nothing half-done; the next healthy round picks the file up
    world.opensearch.setup_indices = lambda: None
    connector_cli._schedule(world.sync, 15, allow_empty=False, sleep=waits.append, rounds=1)
    assert set(documents(world)) == {"leave.txt", "remote.txt"}


async def test_an_unreadable_folder_fails_the_run_instead_of_looking_like_deleted_files(world):
    if world.kind != "path":
        pytest.skip("a folder concept")
    write(world, "open/leave.txt")
    write(world, "locked/board.txt", "board minutes")
    await sync(world)
    locked = world.share / "locked"
    locked.chmod(0)
    try:
        with pytest.raises(ConnectorError, match="Could not read"):
            await sync(world)
    finally:
        locked.chmod(0o755)

    assert set(documents(world)) == {"open/leave.txt", "locked/board.txt"}
