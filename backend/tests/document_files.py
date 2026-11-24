"""Small synthetic files of every supported type, generated in memory. No real data, no network."""

import csv
import io
from typing import Any, Iterable, Sequence, Tuple


def make_png(color: Tuple[int, int, int] = (200, 30, 30), size: Tuple[int, int] = (240, 160)) -> bytes:
    """A solid picture: two with different colours are different pictures."""
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def make_pdf_with_image(png: bytes) -> bytes:
    """A one-page PDF that holds just this picture."""
    from PIL import Image

    out = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").save(out, "PDF")
    return out.getvalue()


def make_pptx(slides: Sequence[tuple]) -> bytes:
    """A deck of ``(title, body, speaker notes)`` slides; a fourth item is the pictures (PNG bytes) on the slide."""
    import pptx
    from pptx.util import Inches

    deck = pptx.Presentation()
    for title, body, notes, *rest in slides:
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
        for number, picture in enumerate(rest[0] if rest else []):
            slide.shapes.add_picture(io.BytesIO(picture), Inches(0.5 + number), Inches(5), width=Inches(1))
    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()


def make_docx(blocks: Iterable[Tuple[str, Any]]) -> bytes:
    """A document of ``("h1" | "h2" | "p", text)`` blocks; ``("img", png bytes)`` is a picture."""
    import docx
    from docx.shared import Inches

    document = docx.Document()
    for kind, text in blocks:
        if kind == "img":
            document.add_picture(io.BytesIO(text), width=Inches(2))
        elif kind == "p":
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
