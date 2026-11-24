"""Turning what the readers return into :class:`DocumentElement` objects, each knowing where it came from.

Docling reads PDF, DOCX, PPTX and HTML. It does not report speaker notes or sheet names, and flattens a spreadsheet to
a table with no row numbers, so slide notes come from python-pptx and spreadsheets and CSV files are read directly:
those readers give the exact row numbers a Citation needs.
"""

import csv
import io
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from docling_core.types.doc import DocItemLabel, ListItem, SectionHeaderItem, TableItem, TextItem, TitleItem
from src.schemas.indexing.elements import DocumentElement, Locator
from src.services.ingestion.uploads import decode_text

SKIPPED_LABELS = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}
NOTES_PREFIX = "Speaker notes: "
EMPTY_ROW_LIMIT = 1_000_000  # a sheet declared this large is read no further


def element_text(element: DocumentElement) -> str:
    """An element as plain text, for the Document's stored text."""
    if element.rows:
        return "\n".join(filter(None, [element.header, *(text for _, text in element.rows)]))
    return element.text


def _page_of(item: Any) -> Optional[int]:
    prov = getattr(item, "prov", None)
    return prov[0].page_no if prov else None


def _paragraph(item: Any, doc: Any) -> Optional[str]:
    if isinstance(item, TableItem):
        return item.export_to_markdown(doc=doc).strip() or None
    if not isinstance(item, TextItem) or item.label in SKIPPED_LABELS:
        return None
    text = (item.text or "").strip()
    if not text:
        return None
    return f"- {text}" if isinstance(item, ListItem) else text


def slide_elements(doc: Any, notes: Dict[int, str]) -> List[DocumentElement]:
    """One element per slide: its title, its text and tables, and its speaker notes."""
    paragraphs: Dict[int, List[str]] = {}
    titles: Dict[int, str] = {}
    for item, _level in doc.iterate_items():
        slide = _page_of(item)
        if slide is None:
            continue
        paragraph = _paragraph(item, doc)
        if paragraph is None:
            continue
        paragraphs.setdefault(slide, []).append(paragraph)
        if isinstance(item, (TitleItem, SectionHeaderItem)):
            titles.setdefault(slide, paragraph)

    elements = []
    for slide in sorted(set(paragraphs) | set(notes)):
        parts = list(paragraphs.get(slide, []))
        if notes.get(slide):
            parts.append(NOTES_PREFIX + notes[slide])
        elements.append(
            DocumentElement(text="\n\n".join(parts), locator=Locator(kind="slide", slide=slide), title=titles.get(slide))
        )
    return [e for e in elements if e.text.strip()]


def section_elements(doc: Any, paged: bool) -> List[DocumentElement]:
    """Running text cut at each heading (and, when the format has pages, at each page), carrying its heading path."""
    elements: List[DocumentElement] = []
    stack: List[str] = []
    titled = False
    body: List[str] = []
    path: List[str] = []
    page: Optional[int] = None

    def flush() -> None:
        nonlocal body
        if body:
            elements.append(
                DocumentElement(
                    text="\n\n".join(body),
                    locator=Locator(kind="text", page=page, heading_path=list(path)),
                )
            )
        body = []

    for item, _level in doc.iterate_items():
        if isinstance(item, (TitleItem, SectionHeaderItem)) and (item.text or "").strip():
            flush()
            heading = item.text.strip()
            if isinstance(item, TitleItem):
                stack, titled = [heading], True
            else:
                depth = (1 if titled else 0) + max(getattr(item, "level", 1), 1) - 1
                stack = stack[:depth] + [heading]
            continue
        paragraph = _paragraph(item, doc)
        if paragraph is None:
            continue
        item_page = _page_of(item) if paged else None
        if body and item_page is not None and item_page != page:
            flush()
        if not body:
            path, page = list(stack), item_page
        body.append(paragraph)
    flush()
    return elements


def pptx_notes(path: Path) -> Dict[int, str]:
    """Speaker notes by slide number (from 1)."""
    from pptx import Presentation

    notes = {}
    for number, slide in enumerate(Presentation(str(path)).slides, 1):
        if slide.has_notes_slide:
            text = (slide.notes_slide.notes_text_frame.text or "").strip() if slide.notes_slide.notes_text_frame else ""
            if text:
                notes[number] = text
    return notes


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def _row_text(cells: Iterable[Any]) -> str:
    values = [_cell(c) for c in cells]
    while values and not values[-1]:
        values.pop()
    return " | ".join(values) if any(values) else ""


def table_element(rows: Iterable[tuple], sheet: Optional[str]) -> Optional[DocumentElement]:
    """A table from ``(row number, cells)`` pairs: the first non-empty row is the header, the rest are its rows."""
    header: Optional[str] = None
    body: List[tuple] = []
    for number, cells in rows:
        text = _row_text(cells)
        if not text:
            continue
        if header is None:
            header = text
        else:
            body.append((number, text))
    if not body:
        return None
    return DocumentElement(
        locator=Locator(kind="table", sheet=sheet, row_start=body[0][0], row_end=body[-1][0]),
        title=sheet,
        header=header,
        rows=body,
    )


def spreadsheet_elements(path: Path) -> List[DocumentElement]:
    """One table element per non-empty sheet; the rows keep their numbers in the workbook."""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        elements = []
        for sheet in book.worksheets:
            numbered = ((n, row) for n, row in enumerate(sheet.iter_rows(values_only=True, max_row=EMPTY_ROW_LIMIT), 1))
            element = table_element(numbered, sheet.title)
            if element is not None:
                elements.append(element)
        return elements
    finally:
        book.close()


def csv_elements(path: Path) -> List[DocumentElement]:
    text = decode_text(path.read_bytes()).lstrip("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    element = table_element(enumerate(csv.reader(io.StringIO(text), dialect), 1), None)
    return [element] if element is not None else []


NOISE_TAGS = ("script", "style", "noscript", "template", "iframe", "svg", "canvas", "form", "button", "select", "dialog")
PAGE_FURNITURE = ("nav", "aside", "header", "footer")


def main_text_html(raw: bytes) -> bytes:
    """A web page reduced to its main text: the ``main`` or ``article`` part if it has one, else the body, without
    navigation, headers, footers, side bars, scripts and styles. What is left is what Docling reads."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(decode_text(raw), "lxml")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "") or ""
    root = soup.find("main") or soup.find(attrs={"role": "main"})
    if root is None:
        articles = soup.find_all("article")
        root = max(articles, key=lambda a: len(a.get_text(strip=True)), default=None)
    scoped = root is not None
    root = root or soup.body or soup
    for tag in root.find_all(NOISE_TAGS):
        tag.decompose()
    for tag in root.find_all(attrs={"role": "navigation"}):
        tag.decompose()
    # Inside main content a header or footer belongs to the article (its title, its byline); on a bare page it is the site's.
    for tag in root.find_all(("nav", "aside") if scoped else PAGE_FURNITURE):
        tag.decompose()
    heading = f"<h1>{title}</h1>" if title and root.find(("h1", "h2")) is None else ""
    return f"<html><body>{heading}{root.decode_contents() if root.name == 'body' else root}</body></html>".encode()
