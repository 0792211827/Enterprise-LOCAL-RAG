"""Small synthetic files of every supported type, generated in memory. No real data, no network."""

import csv
import io
from typing import Iterable, Optional, Sequence, Tuple


def make_pptx(slides: Sequence[Tuple[str, str, Optional[str]]]) -> bytes:
    """A deck of ``(title, body, speaker notes)`` slides."""
    import pptx

    deck = pptx.Presentation()
    for title, body, notes in slides:
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()


def make_docx(blocks: Iterable[Tuple[str, str]]) -> bytes:
    """A document of ``("h1" | "h2" | "p", text)`` blocks."""
    import docx

    document = docx.Document()
    for kind, text in blocks:
        if kind == "p":
            document.add_paragraph(text)
        else:
            document.add_heading(text, int(kind[1]))
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def make_xlsx(sheets: dict) -> bytes:
    """A workbook of ``{sheet name: [row, ...]}``; the first row of each sheet is its header."""
    import openpyxl

    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(list(row))
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def make_csv(rows: Iterable[Sequence[str]]) -> bytes:
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue().encode()


def make_html(title: str, body: str) -> bytes:
    return f"<html><body><nav>site menu</nav><h1>{title}</h1><p>{body}</p></body></html>".encode()
