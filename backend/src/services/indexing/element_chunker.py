"""Cutting parsed elements into passages that remember where they came from.

* a slide is one passage where it fits, else it is split between paragraphs; every part says which slide it is;
* a table (sheet or CSV) is cut into runs of rows, each repeating the header row, with the rows it holds as its locator;
* running text follows its headings and pages: each section (or page of one) is a passage, a big one is split
  between paragraphs, and every passage opens with its heading path so it makes sense on its own;
* an Image description is a passage of its own that opens with where the image sits ("Image on slide 7").
"""

from typing import List, Optional

from src.schemas.indexing.elements import DocumentElement, Locator, locator_label
from src.schemas.indexing.models import ChunkMetadata, TextChunk


def _words(text: str) -> int:
    return len(text.split())


def _pack(blocks: List[str], limit: int) -> List[str]:
    """Group blocks (paragraphs) into texts of at most ``limit`` words; a block bigger than that is cut by words."""
    groups: List[str] = []
    current: List[str] = []
    size = 0
    for block in blocks:
        for piece in _cut(block, limit):
            words = _words(piece)
            if current and size + words > limit:
                groups.append("\n\n".join(current))
                current, size = [], 0
            current.append(piece)
            size += words
    if current:
        groups.append("\n\n".join(current))
    return groups


def _cut(block: str, limit: int) -> List[str]:
    words = block.split()
    if len(words) <= limit:
        return [block]
    return [" ".join(words[start : start + limit]) for start in range(0, len(words), limit)]


def _paragraphs(text: str) -> List[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


class _Builder:
    def __init__(self, document_id: str):
        self.document_id = document_id
        self.chunks: List[TextChunk] = []

    def add(self, text: str, locator: Locator, section_title: Optional[str]) -> None:
        self.chunks.append(
            TextChunk(
                text=text,
                metadata=ChunkMetadata(
                    chunk_index=len(self.chunks),
                    start_char=0,
                    end_char=len(text),
                    word_count=_words(text),
                    overlap_with_previous=0,
                    overlap_with_next=0,
                    section_title=section_title,
                ),
                document_id=self.document_id,
                locator=locator,
            )
        )


def _slide(builder: _Builder, element: DocumentElement, limit: int) -> None:
    parts = _pack(_paragraphs(element.text), limit)
    for number, part in enumerate(parts):
        # Later parts open with the slide's title, so each makes sense alone.
        text = part if number == 0 or not element.title else f"{element.title}\n\n{part}"
        builder.add(text, element.locator, element.title)


def _table(builder: _Builder, element: DocumentElement, limit: int) -> None:
    sheet_line = f"Sheet: {element.locator.sheet}\n" if element.locator.sheet else ""
    top = f"{sheet_line}{element.header}\n" if element.header else sheet_line
    budget = max(limit - _words(top), 1)
    batch: List[tuple] = []
    size = 0

    def emit() -> None:
        locator = element.locator.model_copy(update={"row_start": batch[0][0], "row_end": batch[-1][0]})
        builder.add(top + "\n".join(text for _, text in batch), locator, element.title)

    for row in element.rows:
        words = _words(row[1])
        if batch and size + words > budget:
            emit()
            batch, size = [], 0
        batch.append(row)
        size += words
    if batch:
        emit()


def _prefixed(element: DocumentElement, body: str) -> str:
    heading = " > ".join(element.locator.heading_path)
    return f"{heading}\n\n{body}" if heading else body


def _text(builder: _Builder, element: DocumentElement, limit: int) -> None:
    """One passage for a section (or a page of it); a long one is split between paragraphs, each part keeping its heading."""
    heading_words = _words(" > ".join(element.locator.heading_path))
    for part in _pack(_paragraphs(element.text), max(limit - heading_words, 1)):
        builder.add(_prefixed(element, part), element.locator, element.title)


def _image(builder: _Builder, element: DocumentElement, limit: int) -> None:
    where = locator_label(element.locator.as_index_field()) or "image"
    where = where[0].upper() + where[1:]
    for part in _pack(_paragraphs(element.text), max(limit - _words(where), 1)):
        builder.add(f"{where}:\n\n{part}", element.locator, element.title)


def chunk_elements(elements: List[DocumentElement], document_id: str, limit: int) -> List[TextChunk]:
    """Passages of at most about ``limit`` words (a single table row or an unbreakable block may exceed it)."""
    builder = _Builder(document_id)
    for element in elements:
        if element.locator.kind == "slide":
            _slide(builder, element, limit)
        elif element.locator.kind == "image":
            _image(builder, element, limit)
        elif element.locator.kind == "table":
            _table(builder, element, limit)
        elif element.text.strip():
            _text(builder, element, limit)
    return builder.chunks
