"""The sample-data check against a temporary folder of small synthetic files (never the real sample set): what it
ingests, what it reports for a file that fails, what a second run does, the web links, the questions and the clean-up.

The real ingestion pipeline and OpenSearch client run over an in-memory index and a fake embedder; offline.
"""

from types import SimpleNamespace

import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from sqlalchemy import select
from src.cli import ingest_samples as cli
from src.config import IngestionSettings, Settings
from src.models import KnowledgeBase
from src.models.document import Document, DocumentChunk
from src.services.ingestion.factory import make_ingestion_service
from src.services.ingestion.samples import SCRATCH_NAME, SampleIngest, links_in
from src.services.ingestion.url_fetch import UrlFetcher
from src.services.opensearch.client import OpenSearchClient
from src.services.pdf_parser.parser import PDFParserService

from tests.audit_support import opener, sqlite_session_factory
from tests.document_files import make_docx, make_pptx, make_xlsx
from tests.opensearch_fakes import FakeEmbeddings, FilteringIndex
from tests.url_support import site  # noqa: F401

BROKEN_STUB = b"PK\x03\x04" + b"\x00" * 67  # 71 bytes, like the sample set's broken deck


@pytest.fixture
def lab(tmp_path):
    folder = tmp_path / "samples"
    folder.mkdir()
    settings = Settings(ingestion=IngestionSettings(upload_dir=str(tmp_path / "uploads")))
    factory = sqlite_session_factory()
    index = FilteringIndex(lexical=True)
    opensearch = OpenSearchClient.__new__(OpenSearchClient)
    opensearch.client, opensearch.index_name, opensearch.settings = index, "chunks", settings
    opensearch.setup_indices = lambda: {}
    embeddings = FakeEmbeddings(dimension=2)
    parser = PDFParserService(max_pages=20, max_file_size_mb=10)

    def build(fetcher=None):
        ingestion = make_ingestion_service(opensearch, embeddings, parser, settings, None, fetcher)
        return SampleIngest(opener(factory), ingestion, opensearch, embeddings, settings, fetcher)

    def put(name, data):
        (folder / name).write_bytes(data if isinstance(data, bytes) else data.encode())

    return SimpleNamespace(folder=folder, factory=factory, index=index, settings=settings, build=build, sample=build(), put=put, tmp=tmp_path)


def samples(lab):
    lab.put("deck.pptx", make_pptx([("Withdraw", "Use the student screen", "Remember the exit date")]))
    lab.put("policy.docx", make_docx([("h1", "Leave"), ("p", "Employees receive twentyfive days")]))
    lab.put("codes.xlsx", make_xlsx({"Withdraw Codes": [("Code", "Meaning"), ("W01", "moved away")]}))
    lab.put("notes.txt", "Remember the lighthouse keys")
    lab.put("broken.pptx", BROKEN_STUB)
    lab.put("photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)


def by_name(results):
    return {r.name: r for r in results}


def titles(lab):
    with lab.factory() as session:
        return {d.title: d for d in session.scalars(select(Document))}


def indexed(lab):
    return sorted(s["chunk_text"] for s in lab.index.docs.values())


# --- the run ---------------------------------------------------------------------------------------------------- #
async def test_every_supported_file_is_ingested_into_a_scratch_knowledge_base_and_reported(lab):
    samples(lab)

    results = by_name(await lab.sample.run(lab.folder))

    for name in ("deck.pptx", "policy.docx", "codes.xlsx", "notes.txt"):
        assert (results[name].action, results[name].status) == ("added", "COMPLETED"), name
        assert results[name].chunks >= 1 and results[name].error is None
    assert results["photo.png"].action == "skipped" and "not a supported file type" in results["photo.png"].error
    with lab.factory() as session:
        [kb] = session.scalars(select(KnowledgeBase)).all()
        assert kb.name == SCRATCH_NAME and kb.document_count == 5  # the broken deck is a (failed) Document too
    assert any("lighthouse" in text for text in indexed(lab))


async def test_the_broken_stub_is_a_failed_file_with_a_reason_and_does_not_stop_the_run(lab):
    samples(lab)

    results = await lab.sample.run(lab.folder)

    broken = by_name(results)["broken.pptx"]
    assert broken.action == "failed" and broken.status == "FAILED"
    assert "PPTX" in broken.error and "corrupt" in broken.error.lower()
    assert [r.action for r in results if r.name == "notes.txt"] == ["added"]  # files after it (in name order) were still ingested


async def test_a_stub_holding_only_an_error_message_is_reported_as_plain_text_not_a_deck(lab):
    lab.put("HEAT.pptx", "HEAT.pptx: File not found or not readable.")  # what the real 71-byte stub holds

    [result] = await lab.sample.run(lab.folder)

    assert result.action == "failed" and "holds plain text, not a PPTX" in result.error


async def test_a_second_run_skips_unchanged_files_replaces_changed_ones_and_retries_failed_ones(lab):
    samples(lab)
    await lab.sample.run(lab.folder)
    lab.put("codes.xlsx", make_xlsx({"Withdraw Codes": [("Code", "Meaning"), ("W01", "transferred out")]}))

    again = by_name(await lab.sample.run(lab.folder))

    assert [again[n].action for n in ("deck.pptx", "policy.docx", "notes.txt")] == ["unchanged"] * 3
    assert again["codes.xlsx"].action == "replaced" and again["codes.xlsx"].status == "COMPLETED"
    assert again["broken.pptx"].action == "failed"
    text = " ".join(indexed(lab))
    assert "transferred out" in text and "moved away" not in text  # the old version no longer answers
    assert len([d for d in titles(lab).values()]) == 5  # nothing was duplicated


async def test_a_replacement_that_fails_leaves_the_old_content_and_is_reported(lab):
    samples(lab)
    await lab.sample.run(lab.folder)
    lab.put("deck.pptx", BROKEN_STUB)

    again = by_name(await lab.sample.run(lab.folder))

    assert again["deck.pptx"].action == "failed" and again["deck.pptx"].status == "COMPLETED (old content kept)"
    assert any("student screen" in text for text in indexed(lab))


async def test_a_folder_that_does_not_exist_is_an_error(lab):
    with pytest.raises(FileNotFoundError):
        await lab.sample.run(lab.tmp / "nope")


# --- web links -------------------------------------------------------------------------------------------------- #
async def test_the_links_in_a_links_document_are_added_when_their_hosts_are_allowlisted_and_skipped_otherwise(lab, site):
    site.serve("/guide", b"<html><body><main><h1>Guide</h1><p>Rotate the keys quarterly.</p></main></body></html>")
    lab.put("Links.docx", make_docx([("p", f"Guide: {site.url('/guide')}."), ("p", "Other: https://elsewhere.example.org/page")]))
    sample = lab.build(UrlFetcher(["127.0.0.1"], 1024 * 1024))

    results = by_name(await sample.run(lab.folder, urls=True))
    again = by_name(await sample.run(lab.folder, urls=True))

    assert results[site.url("/guide")].action == "added" and results[site.url("/guide")].status == "COMPLETED"
    skipped = results["https://elsewhere.example.org/page"]
    assert skipped.action == "skipped" and "not on the allowlist" in skipped.error
    assert again[site.url("/guide")].action == "unchanged"
    assert any("Rotate the keys" in text for text in indexed(lab))


async def test_links_are_not_followed_without_the_option(lab, site):
    lab.put("Links.docx", make_docx([("p", f"Guide: {site.url('/guide')}")]))

    results = await lab.build(UrlFetcher(["127.0.0.1"], 1024 * 1024)).run(lab.folder)

    assert [r.name for r in results] == ["Links.docx"] and site.requests == []


def test_links_are_read_from_a_word_files_text(tmp_path):
    path = tmp_path / "Links.docx"
    path.write_bytes(make_docx([("p", "See https://a.example.com/x, and https://b.example.com/y."), ("p", "Again https://a.example.com/x")]))

    assert links_in(path) == ["https://a.example.com/x", "https://b.example.com/y"]


# --- questions and clean-up ------------------------------------------------------------------------------------- #
async def test_questions_print_the_citations_with_their_locators(lab):
    samples(lab)
    await lab.sample.run(lab.folder)

    [answer] = await lab.sample.ask(["student screen"])

    [citation] = [c for c in answer.citations if c["title"] == "deck.pptx"]  # the fake index does not rank
    assert citation["where"] == "slide 1" and "student screen" in citation["passage"]


async def test_asking_before_the_scratch_knowledge_base_exists_says_so(lab):
    with pytest.raises(LookupError, match="Run the check first"):
        await lab.sample.ask(["anything"])


async def test_clean_removes_the_scratch_knowledge_base_its_documents_chunks_and_files(lab):
    samples(lab)
    await lab.sample.run(lab.folder)

    held = lab.sample.clean()

    assert held == 5 and lab.sample.clean() is None  # nothing left to remove the second time
    assert lab.index.docs == {}
    with lab.factory() as session:
        assert session.scalars(select(KnowledgeBase)).all() == [] and session.scalars(select(Document)).all() == []
        assert session.scalars(select(DocumentChunk)).all() == []
    assert not [p for p in (lab.tmp / "uploads").rglob("*") if p.is_file()]


# --- the command ------------------------------------------------------------------------------------------------ #
def test_the_command_prints_a_per_file_report_and_succeeds_even_though_a_file_failed(lab, capsys):
    samples(lab)

    code = cli.main(["--folder", str(lab.folder)], sample=lab.sample)

    out = capsys.readouterr().out
    assert code == 0
    assert "broken.pptx" in out and "ERROR:" in out and "corrupt" in out.lower()
    assert "notes.txt" in out and "added" in out and "chunks across" in out


def test_the_command_asks_the_questions_of_a_golden_set_file_and_cleans_up(lab, capsys, tmp_path):
    samples(lab)
    questions = tmp_path / "q.yaml"
    questions.write_text("version: 1\nname: q\ncases:\n  - question: student screen\n")

    assert cli.main(["--folder", str(lab.folder), "--ask", "--questions", str(questions), "--top", "10"], sample=lab.sample) == 0
    asked = capsys.readouterr().out
    assert "Q: student screen" in asked and "deck.pptx (slide 1)" in asked

    assert cli.main(["--clean"], sample=lab.sample) == 0
    assert "Removed" in capsys.readouterr().out
    assert cli.main(["--clean"], sample=lab.sample) == 0
    assert "No scratch Knowledge Base" in capsys.readouterr().out


def test_the_command_refuses_a_missing_folder_with_exit_status_2(lab, capsys):
    assert cli.main(["--folder", str(lab.tmp / "missing")], sample=lab.sample) == 2
    assert "not a folder" in capsys.readouterr().err
