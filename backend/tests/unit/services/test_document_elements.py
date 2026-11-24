"""What the readers' output becomes: elements with the page, slide, sheet and rows a Citation will name.

A PDF is not parsed here (that needs Docling's layout models); a Docling document with pages is built by hand instead, so the
mapping from its items to pages and headings is still exercised. Spreadsheets are real, generated in memory.
"""

from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size
from src.schemas.indexing.elements import locator_label
from src.services.pdf_parser.elements import section_elements, spreadsheet_elements
from tests.document_files import make_xlsx


def pdf_like_document() -> DoclingDocument:
    doc = DoclingDocument(name="manual")

    def at(page, text):
        if page not in doc.pages:
            doc.add_page(page_no=page, size=Size(width=100, height=100))
        return ProvenanceItem(page_no=page, bbox=BoundingBox(l=0, t=0, r=1, b=1), charspan=(0, len(text)))

    doc.add_heading("Warranty", level=1, prov=at(3, "Warranty"))
    doc.add_text(label=DocItemLabel.PARAGRAPH, text="Covers parts for a year.", prov=at(3, "Covers parts for a year."))
    doc.add_text(label=DocItemLabel.PARAGRAPH, text="Labour is excluded.", prov=at(4, "Labour is excluded."))
    doc.add_heading("Claims", level=2, prov=at(4, "Claims"))
    doc.add_text(label=DocItemLabel.PARAGRAPH, text="Call the helpline.", prov=at(4, "Call the helpline."))
    return doc


def test_a_paged_document_is_cut_at_headings_and_pages_and_names_both():
    elements = section_elements(pdf_like_document(), paged=True)

    assert [(e.locator.page, e.locator.heading_path, e.text) for e in elements] == [
        (3, ["Warranty"], "Covers parts for a year."),
        (4, ["Warranty"], "Labour is excluded."),
        (4, ["Warranty", "Claims"], "Call the helpline."),
    ]
    assert [locator_label(e.locator.model_dump()) for e in elements] == [
        "Warranty, page 3",
        "Warranty, page 4",
        "Warranty > Claims, page 4",
    ]


def test_a_document_without_pages_names_only_its_headings():
    elements = section_elements(pdf_like_document(), paged=False)

    assert [(e.locator.page, e.locator.heading_path) for e in elements] == [(None, ["Warranty"]), (None, ["Warranty", "Claims"])]


def test_spreadsheet_rows_keep_their_numbers_in_the_workbook(tmp_path):
    path = tmp_path / "gaps.xlsx"
    path.write_bytes(make_xlsx({"S": [(), (), ("Code", "Days"), ("A", 25.0), (), (), ("B", "x")], "Empty": [()]}))

    [table] = spreadsheet_elements(path)

    assert table.header == "Code | Days"
    assert table.rows == [(4, "A | 25"), (7, "B | x")]
    assert (table.locator.sheet, table.locator.row_start, table.locator.row_end) == ("S", 4, 7)


def test_locators_read_as_words():
    assert locator_label({"slide": 7}) == "slide 7"
    assert locator_label({"sheet": "Withdraw Codes", "row_start": 12, "row_end": 30}) == "sheet Withdraw Codes, rows 12-30"
    assert locator_label({"row_start": 2, "row_end": 2}) == "rows 2-2"
    assert locator_label({"page": 14}) == "page 14"
    assert locator_label({"kind": "text", "heading_path": []}) is None
    assert locator_label(None) is None


def test_the_prompt_labels_an_excerpt_with_its_position_so_the_answer_can_say_where_to_look():
    from src.services.ollama.prompts import RAGPromptBuilder

    prompt = RAGPromptBuilder._build_user_prompt(
        "How much leave?",
        [
            {"chunk_text": "25 days", "title": "Handbook", "locator": {"kind": "slide", "slide": 7}},
            {"chunk_text": "plain", "title": "Notes"},
        ],
    )

    assert "[1. Handbook (slide 7)]" in prompt and "[2. Notes]" in prompt
