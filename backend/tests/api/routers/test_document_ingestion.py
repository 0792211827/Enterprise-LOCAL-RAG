"""Uploading documents of every supported type through the HTTP API, over the real ingestion pipeline and the real search
client (an in-memory index that matches on words). What is asserted is what an admin sees: the Document's status and
error, and what a search then finds. Files are small synthetic ones; nothing touches the network. Fixtures: chat_support.
"""

import time
from types import SimpleNamespace

import pytest
from fastapi import Depends
from src.auth import Role, require_role
from src.config import AuthSettings, IngestionSettings, Settings
from src.dependencies import get_database, get_embeddings_service, get_opensearch_client, get_pdf_parser
from src.routers import documents, hybrid_search
from src.services.opensearch.client import KNN_ENGINE, OpenSearchClient
from src.services.pdf_parser.parser import PDFParserService
from tests.audit_support import opener
from tests.chat_support import caller, stack, world  # noqa: F401
from tests.document_files import make_csv, make_docx, make_html, make_pptx, make_xlsx
from tests.opensearch_fakes import FakeEmbeddings, FilteringIndex

ADMIN = caller("root", "staff", roles=(Role.ADMIN, Role.QUERIER))  # also holds the hr Grant, so it can search what it adds
PDF_MIME = "application/pdf"
OFFICE_MIME = "application/octet-stream"


@pytest.fixture
def library(stack, world, tmp_path):  # noqa: F811
    """The documents and search routers over a real pipeline: real parser, chunker, search client and a word-matching index."""
    index = FilteringIndex(lexical=True)
    client = OpenSearchClient.__new__(OpenSearchClient)
    client.client, client.index_name, client.settings = index, "chunks", None
    client._engine, client._engine_read_at = KNN_ENGINE, time.monotonic()
    client.health_check = lambda: True
    parser = PDFParserService(max_pages=20, max_file_size_mb=10)

    app = stack.app
    app.dependency_overrides[get_opensearch_client] = lambda: client
    app.dependency_overrides[get_embeddings_service] = lambda: FakeEmbeddings(dimension=2)
    app.dependency_overrides[get_pdf_parser] = lambda: parser
    app.dependency_overrides[get_database] = lambda: SimpleNamespace(get_session=opener(world.factory))
    admin = [Depends(require_role(Role.ADMIN))]
    app.include_router(documents.router, dependencies=admin)
    app.include_router(hybrid_search.router, prefix="/api/v1", dependencies=[Depends(require_role(Role.QUERIER))])
    stack.use_settings(
        Settings(auth=AuthSettings(mode="jwt"), ingestion=IngestionSettings(upload_dir=str(tmp_path / "uploads")))
    )
    stack.act_as(ADMIN)
    stack.parser_service = parser
    stack.search_index = index
    stack.kb = world.kbs["hr"]
    return stack


def upload(library, name, data, mime=OFFICE_MIME, **form):
    return library.http.post(
        f"/api/v1/knowledge-bases/{library.kb}/documents", files={"file": (name, data, mime)}, data=form
    )


def document(library, document_id):
    response = library.http.get(f"/api/v1/documents/{document_id}")
    assert response.status_code == 200, response.text
    return response.json()


def search(library, query):
    response = library.http.post("/api/v1/hybrid-search/", json={"query": query, "size": 20, "use_hybrid": False})
    assert response.status_code == 200, response.text
    return response.json()["hits"]


SAMPLES = {
    "deck.pptx": (make_pptx([("Onboarding", "Welcome to zebrafish onboarding", None)]), "zebrafish"),
    "policy.docx": (make_docx([("h1", "Leave"), ("p", "Employees receive twentyfive vacation days")]), "twentyfive"),
    "codes.xlsx": (make_xlsx({"Codes": [("Code", "Meaning"), ("W01", "moved")]}), "moved"),
    "codes.csv": (make_csv([("Code", "Meaning"), ("E02", "enrolled")]), "enrolled"),
    "page.html": (make_html("Intranet", "Badges are collected at reception"), "reception"),
}


@pytest.mark.parametrize("name", SAMPLES)
def test_each_supported_type_is_ingested_and_found_by_search(library, name):
    data, needle = SAMPLES[name]

    uploaded = upload(library, name, data)

    assert uploaded.status_code == 202, uploaded.text
    stored = document(library, uploaded.json()["id"])
    assert (stored["status"], stored["error"]) == ("COMPLETED", None)
    hits = search(library, needle)
    assert [hit["document_id"] for hit in hits] == [stored["id"]]
    assert needle in hits[0]["chunk_text"]


def test_a_text_file_is_still_ingested_as_before(library):
    uploaded = upload(library, "notes.txt", b"Remember the lighthouse keys", "text/plain")

    assert document(library, uploaded.json()["id"])["status"] == "COMPLETED"
    assert len(search(library, "lighthouse")) == 1


def test_a_file_is_judged_by_its_content_not_its_name(library):
    deck = make_pptx([("Safety", "Wear a hardhat on site", None)])

    uploaded = upload(library, "notes.txt", deck, "text/plain")  # a deck renamed to .txt, labelled text

    assert uploaded.status_code == 202, uploaded.text
    stored = document(library, uploaded.json()["id"])
    assert stored["status"] == "COMPLETED"
    assert stored["content_type"].endswith("presentationml.presentation")
    assert "hardhat" in search(library, "hardhat")[0]["chunk_text"]


def test_content_beats_a_binary_extension_and_text_is_not_mistaken_for_an_office_file(library):
    page = upload(library, "export.xlsx", make_html("Intranet", "Badges are collected at reception"), "text/html")
    stored = document(library, page.json()["id"])
    refused = upload(library, "notes.docx", b"# Just markdown, named like a Word file", OFFICE_MIME)

    assert stored["status"] == "COMPLETED" and stored["content_type"] == "text/html"
    assert refused.status_code == 415 and "DOCX" in refused.json()["detail"]


def test_legacy_8_bit_text_is_still_accepted_for_a_text_file(library):
    uploaded = upload(library, "old.txt", "caf\xe9 lighthouse".encode("latin-1"), "text/plain")

    assert document(library, uploaded.json()["id"])["status"] == "COMPLETED"


def test_an_unsupported_file_is_refused_with_the_supported_types_named(library):
    before = library.http.get(f"/api/v1/knowledge-bases/{library.kb}/documents").json()
    refused = upload(library, "photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")

    assert refused.status_code == 415
    for kind in ("PDF", "DOCX", "PPTX", "XLSX", "CSV", "HTML", "TXT", "MD"):
        assert kind in refused.json()["detail"]
    assert library.http.get(f"/api/v1/knowledge-bases/{library.kb}/documents").json() == before


def test_a_corrupt_or_empty_office_file_becomes_a_failed_document_with_a_reason(library):
    stub = upload(library, "stub.pptx", b"PK\x03\x04 broken")  # like the 71-byte sample stub
    empty = upload(library, "empty.docx", b"")
    healthy = upload(library, "ok.txt", b"Still ingested", "text/plain")

    assert [r.status_code for r in (stub, empty, healthy)] == [202, 202, 202]
    stub_doc, empty_doc = (document(library, r.json()["id"]) for r in (stub, empty))
    assert stub_doc["status"] == empty_doc["status"] == "FAILED"
    assert "PPTX" in stub_doc["error"] and "corrupt" in stub_doc["error"].lower()
    assert "empty" in empty_doc["error"].lower()
    assert document(library, healthy.json()["id"])["status"] == "COMPLETED"


def test_a_file_with_no_extractable_text_is_reported_as_no_text_found(library):
    uploaded = upload(library, "blank.xlsx", make_xlsx({"Sheet1": []}))

    stored = document(library, uploaded.json()["id"])
    assert stored["status"] == "FAILED"
    assert "no text found" in stored["error"].lower()


# --- where a passage came from ---------------------------------------------------------------------------------- #
def only_hit(library, query):
    [hit] = search(library, query)
    return hit


def test_a_slide_is_one_passage_with_its_speaker_notes_and_is_cited_by_number(library):
    deck = make_pptx(
        [
            ("Welcome", "Introduction slide about harbour tours", None),
            ("Safety", "Wear a lifejacket at all times", "Remind everyone that narwhals are protected"),
        ]
    )
    upload(library, "tours.pptx", deck)

    hit = only_hit(library, "narwhals")

    assert hit["locator_text"] == "slide 2"
    assert hit["locator"]["slide"] == 1 + 1
    assert "lifejacket" in hit["chunk_text"]  # the slide stays together with its notes
    assert only_hit(library, "harbour")["locator_text"] == "slide 1"


def test_a_long_slide_is_split_between_paragraphs_and_every_part_cites_the_slide(library):
    paragraphs = [f"Paragraph {n} " + " ".join(f"filler{n}x{i}" for i in range(250)) for n in range(1, 4)]
    deck = make_pptx([("Big slide", "\n".join(paragraphs), None)])
    upload(library, "big.pptx", deck)

    hits = search(library, "Paragraph")

    assert len(hits) >= 2
    assert {hit["locator_text"] for hit in hits} == {"slide 1"}
    assert all(len(hit["chunk_text"].split()) <= 700 for hit in hits)


def test_a_spreadsheet_passage_names_its_sheet_and_rows_and_repeats_the_header(library):
    rows = [("Code", "Meaning")] + [(f"W{n:03d}", " ".join(f"word{n}x{i}" for i in range(12))) for n in range(1, 121)]
    upload(library, "withdraw.xlsx", make_xlsx({"Notes": [("Only", "here"), ("a", "b")], "Withdraw Codes": rows}))

    hits = search(library, "W120 W001")
    by_code = {code: hit for code in ("W001", "W120") for hit in hits if code in hit["chunk_text"]}

    first, last = by_code["W001"], by_code["W120"]
    assert first["locator_text"].startswith("sheet Withdraw Codes, rows 2-")
    assert last["locator_text"].endswith("-121") and last["locator_text"].startswith("sheet Withdraw Codes, rows ")
    assert first["locator_text"] != last["locator_text"]  # a long table is split
    for hit in (first, last):
        assert "Code | Meaning" in hit["chunk_text"]  # a split table still makes sense on its own
        assert hit["locator"]["sheet"] == "Withdraw Codes"
    assert only_hit(library, "here")["locator_text"] == "sheet Notes, rows 2-2"


def test_a_csv_passage_names_its_rows(library):
    upload(library, "codes.csv", make_csv([("Code", "Meaning"), ("E02", "enrolled"), ("E03", "transferred")]))

    hit = only_hit(library, "enrolled")

    assert hit["locator_text"] == "rows 2-3"
    assert "Code | Meaning" in hit["chunk_text"] and "E03 | transferred" in hit["chunk_text"]


def test_a_word_file_passage_names_its_heading_path(library):
    blocks = [("h1", "Policy"), ("p", "General rules apply"), ("h2", "Leave"), ("p", "Employees receive twentyfive days"), ("h2", "Travel"), ("p", "Book economy flights")]
    upload(library, "policy.docx", make_docx(blocks))

    hit = only_hit(library, "twentyfive")

    assert "Policy > Leave" in hit["locator_text"]
    assert hit["locator"]["heading_path"] == ["Policy", "Leave"]


def test_a_pdf_passage_names_its_page(library, monkeypatch):
    from src.schemas.indexing.elements import DocumentElement, Locator
    from src.schemas.pdf_parser.models import ParserType, PdfContent

    async def parse_pdf(path):  # layout models are not run in tests: the parser hands back what Docling would
        elements = [
            DocumentElement(text="Cover page about gadgets", locator=Locator(kind="text", page=1)),
            DocumentElement(text="Warranty lasts for eighteen months", locator=Locator(kind="text", page=14, heading_path=["Warranty"])),
        ]
        return PdfContent(raw_text="\n".join(e.text for e in elements), elements=elements, parser_used=ParserType.DOCLING)

    monkeypatch.setattr(library.parser_service.docling_parser, "parse_pdf", parse_pdf)
    upload(library, "manual.pdf", b"%PDF-1.4 stub", PDF_MIME)

    hit = only_hit(library, "eighteen")

    assert hit["locator_text"] == "Warranty, page 14"
    assert only_hit(library, "gadgets")["locator_text"] == "page 1"


def test_a_plain_text_passage_has_no_locator(library):
    upload(library, "notes.txt", b"Remember the lighthouse keys", "text/plain")

    hit = only_hit(library, "lighthouse")

    assert hit.get("locator") is None and hit["locator_text"] is None


def test_a_document_ingested_before_locators_still_answers_and_gains_them_when_reprocessed(library):
    uploaded = upload(library, "tours.pptx", make_pptx([("Welcome", "Harbour tours start at nine", None)]))
    for chunk in library.search_index.docs.values():  # as the index held it before locators existed
        chunk.pop("locator", None)

    before = only_hit(library, "harbour")
    reprocessed = library.http.post(f"/api/v1/documents/{uploaded.json()['id']}/reprocess")
    after = only_hit(library, "harbour")

    assert before["locator_text"] is None and "Harbour tours" in before["chunk_text"]
    assert reprocessed.status_code == 202, reprocessed.text
    assert after["locator_text"] == "slide 1"


def test_locators_are_kept_in_their_own_table_and_go_with_the_document(library, world):
    from sqlalchemy import func, select
    from src.models.document import DocumentChunk, DocumentChunkLocator

    uploaded = upload(library, "tours.pptx", make_pptx([("Welcome", "Harbour tours", None), ("Safety", "Lifejackets", None)]))

    def stored():
        with world.factory() as session:
            return session.scalar(select(func.count()).select_from(DocumentChunkLocator)), session.scalar(
                select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == uploaded.json()["id"])
            )

    locators_before, chunks = stored()
    deleted = library.http.delete(f"/api/v1/documents/{uploaded.json()['id']}")

    assert (locators_before, chunks) == (2, 2)
    assert deleted.status_code == 204 and stored() == (0, 0)
