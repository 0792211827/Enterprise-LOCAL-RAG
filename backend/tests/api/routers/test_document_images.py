"""Images in uploaded Documents, through the HTTP API: the vision model describes them, the descriptions are found by
search and cited as images, and a Document is never failed by its pictures. Fixtures come from the document ingestion
tests; the vision model is a fake."""

from src.dependencies import get_pdf_parser, get_vlm_provider
from src.services.pdf_parser.images import ImageRules
from src.services.pdf_parser.parser import PDFParserService
from tests.api.routers.test_document_ingestion import (  # noqa: F401
    OFFICE_MIME,
    PDF_MIME,
    document,
    library,
    search,
    upload,
)
from tests.chat_support import stack, world  # noqa: F401
from tests.document_files import make_docx, make_pdf_with_image, make_png, make_pptx
from tests.unit.services.test_image_extraction import FakeVision

RED, GREEN, BLUE = make_png((200, 30, 30)), make_png((30, 200, 30)), make_png((30, 30, 200))
ICON = make_png((1, 2, 3), (32, 32))


def vision(library, *answers):
    fake = FakeVision(*answers)
    library.app.dependency_overrides[get_vlm_provider] = lambda: fake
    return fake


def capped(library, max_images):
    parser = PDFParserService(max_pages=20, max_file_size_mb=10, image_rules=ImageRules(max_images=max_images))
    library.app.dependency_overrides[get_pdf_parser] = lambda: parser


DECK = make_pptx(
    [
        ("Welcome", "Introduction to the portal", None),
        ("Withdraw a student", "Use the student screen", None, [RED]),
    ]
)


def test_a_description_is_found_by_search_and_cited_as_an_image_on_its_slide(library):
    vision(library, "Screenshot of the Withdraw Student button and the exit date field")

    uploaded = upload(library, "training.pptx", DECK)

    stored = document(library, uploaded.json()["id"])
    assert (stored["status"], stored["error"], stored["warnings"]) == ("COMPLETED", None, [])
    [hit] = search(library, "exit")
    assert hit["locator_text"] == "image on slide 2"
    assert hit["locator"]["kind"] == "image"
    assert hit["chunk_text"].startswith("Image on slide 2:") and "Withdraw Student button" in hit["chunk_text"]
    assert search(library, "portal")[0]["locator_text"] == "slide 1"  # the slide's own text is still its own passage


def test_a_word_file_picture_is_cited_by_its_heading_and_a_pdf_one_by_its_page(library, monkeypatch):
    from src.schemas.pdf_parser.models import ParserType, PdfContent

    async def parse_pdf(path):  # no text of its own: a scan
        return PdfContent(raw_text="", elements=[], parser_used=ParserType.DOCLING)

    monkeypatch.setattr(library.parser_service.docling_parser, "parse_pdf", parse_pdf)
    vision(library, "A leave request form with a manager approval box", "A scanned invoice showing a total of forty euros")

    word = upload(library, "policy.docx", make_docx([("h1", "Policy"), ("h2", "Leave"), ("img", RED)]))
    scan = upload(library, "scan.pdf", make_pdf_with_image(GREEN), PDF_MIME)

    assert document(library, word.json()["id"])["status"] == "COMPLETED"
    assert document(library, scan.json()["id"])["status"] == "COMPLETED"  # only a picture, and now described
    assert search(library, "approval")[0]["locator_text"] == "image in Policy > Leave"
    assert search(library, "invoice")[0]["locator_text"] == "image on page 1"


def test_with_the_vision_model_off_the_document_succeeds_with_text_only_and_a_warning(library):
    uploaded = upload(library, "training.pptx", make_pptx([("Withdraw a student", "Use the student screen", None, [RED, GREEN])]))

    stored = document(library, uploaded.json()["id"])

    assert (stored["status"], stored["error"]) == ("COMPLETED", None)
    [warning] = stored["warnings"]
    assert warning.startswith("2 images were not described") and "VLM__ENABLED" in warning
    assert [hit["locator_text"] for hit in search(library, "student")] == ["slide 1"]


def test_a_file_of_only_pictures_fails_with_no_text_found_and_says_why_when_the_model_is_off(library, monkeypatch):
    from src.schemas.pdf_parser.models import ParserType, PdfContent

    async def parse_pdf(path):
        return PdfContent(raw_text="", elements=[], parser_used=ParserType.DOCLING)

    monkeypatch.setattr(library.parser_service.docling_parser, "parse_pdf", parse_pdf)

    stored = document(library, upload(library, "scan.pdf", make_pdf_with_image(RED), PDF_MIME).json()["id"])

    assert stored["status"] == "FAILED"
    assert "no text found" in stored["error"].lower() and "VLM__ENABLED" in stored["error"]


def test_reprocessing_after_the_vision_model_is_on_adds_the_missing_descriptions(library):
    uploaded = upload(library, "training.pptx", DECK)
    before = document(library, uploaded.json()["id"])
    found_before = search(library, "button")
    vision(library, "Screenshot of the Withdraw Student button")

    reprocessed = library.http.post(f"/api/v1/documents/{uploaded.json()['id']}/reprocess")

    after = document(library, uploaded.json()["id"])
    assert before["warnings"] and found_before == []
    assert reprocessed.status_code == 202
    assert (after["status"], after["warnings"]) == ("COMPLETED", [])
    assert [hit["locator_text"] for hit in search(library, "button")] == ["image on slide 2"]
    assert len(search(library, "Withdraw")) == 2  # the slide and its picture, each once: nothing doubled


def test_a_picture_the_model_fails_on_is_a_warning_not_a_failure(library):
    vision(library, "A blue chart of enrolment by month", RuntimeError("model busy"), "A red warning banner")
    deck = make_pptx([("Charts", "Monthly figures", None, [BLUE, GREEN, RED])])

    stored = document(library, upload(library, "charts.pptx", deck).json()["id"])

    assert (stored["status"], stored["error"]) == ("COMPLETED", None)
    assert stored["warnings"] == ["1 image was not described (the vision model failed or returned nothing). Reprocess this Document to try again."]
    assert len(search(library, "enrolment")) == 1 and len(search(library, "banner")) == 1


def test_icons_repeated_logos_and_pictures_past_the_cap_are_not_described(library):
    logo = make_png((9, 9, 9), (300, 100))
    fake = vision(library, "Picture one", "Picture two")
    capped(library, 1)
    deck = make_pptx(
        [
            ("One", "a", None, [logo, ICON, RED]),
            ("Two", "b", None, [logo, GREEN]),
            ("Three", "c", None, [logo]),
        ]
    )

    stored = document(library, upload(library, "deck.pptx", deck).json()["id"])

    assert stored["status"] == "COMPLETED"
    assert len(fake.prompts) == 1  # not the icon, not the logo on three slides, and only one of the two real pictures
    [warning] = stored["warnings"]
    assert warning.startswith("1 image was left out") and "INGESTION__MAX_IMAGES_PER_DOCUMENT" in warning


def test_a_replacement_brings_its_own_image_warnings_and_the_descriptions_of_the_old_content_go(library):
    vision(library, "An old screenshot of the legacy portal")
    first = upload(library, "training.pptx", DECK)
    doc_id = first.json()["id"]
    assert search(library, "legacy") != []
    library.app.dependency_overrides[get_vlm_provider] = lambda: None

    replaced = library.http.post(
        f"/api/v1/documents/{doc_id}/replace",
        files={"file": ("training-v2.pptx", make_pptx([("New", "Fresh content", None, [GREEN])]), OFFICE_MIME)},
    )

    assert replaced.status_code == 202, replaced.text
    stored = document(library, doc_id)
    assert stored["status"] == "COMPLETED" and stored["warnings"][0].startswith("1 image was not described")
    assert search(library, "legacy") == []
    assert [hit["locator_text"] for hit in search(library, "Fresh")] == ["slide 1"]
