"""Where a passage came from, and the parsed structure it is cut from.

A parser returns :class:`DocumentElement` objects (a slide, a run of rows, a section) each with a :class:`Locator`.
The chunker keeps the locator on every passage it makes, the index stores it, and a Citation shows
:func:`locator_label`. Passages indexed before locators existed have none and are shown as they always were.
"""

from typing import Any, List, Mapping, Optional, Tuple

from pydantic import BaseModel, Field


class Locator(BaseModel):
    """A position in the source Document. Which fields are set depends on the kind of source."""

    kind: str = "text"  # text | table | slide | image
    page: Optional[int] = None
    slide: Optional[int] = None
    sheet: Optional[str] = None
    row_start: Optional[int] = None
    row_end: Optional[int] = None
    heading_path: List[str] = Field(default_factory=list)

    def as_index_field(self) -> dict:
        """The object stored on the chunk in the index and in the locator table."""
        return self.model_dump(exclude_none=True)


class DocumentElement(BaseModel):
    """One unit of a parsed Document: a slide, a section of running text, or a table with its rows."""

    text: str = ""
    locator: Locator = Field(default_factory=Locator)
    title: Optional[str] = None  # shown as the passage's section (a slide's title, a sheet's name); text sections use their heading path
    header: Optional[str] = None  # a table's header row, repeated on every passage cut from it
    rows: List[Tuple[int, str]] = Field(default_factory=list)  # a table's rows as (row number, text)


def _image_label(locator: Mapping[str, Any]) -> str:
    if locator.get("slide"):
        return f"image on slide {locator['slide']}"
    if locator.get("page"):
        return f"image on page {locator['page']}"
    heading = " > ".join(locator.get("heading_path") or [])
    return f"image in {heading}" if heading else "image"


def locator_label(locator: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The words a Citation shows for a locator (``slide 7``, ``sheet Codes, rows 12-30``, ``Leave, page 14``)."""
    if not locator:
        return None
    if locator.get("kind") == "image":
        return _image_label(locator)
    if locator.get("slide"):
        return f"slide {locator['slide']}"
    start, end = locator.get("row_start"), locator.get("row_end")
    if start is not None:
        rows = f"rows {start}-{end if end is not None else start}"
        return f"sheet {locator['sheet']}, {rows}" if locator.get("sheet") else rows
    parts = []
    heading = " > ".join(locator.get("heading_path") or [])
    if heading:
        parts.append(heading)
    if locator.get("page"):
        parts.append(f"page {locator['page']}")
    return ", ".join(parts) or None


class ExtractedImage(BaseModel):
    """An image found inside a Document, with where it sits and the text around it."""

    data: bytes = b""  # PNG; empty when the bytes were not loaded (nothing will describe it)
    width: int
    height: int
    locator: Locator  # kind "image", with the page or slide (or the heading path, for a Word file)
    context: str = ""  # the text on the same page or slide, to help the vision model


class ImageExtraction(BaseModel):
    """The images of a Document worth describing, and a count of those left out and why."""

    images: List[ExtractedImage] = Field(default_factory=list)  # in document order, at most the cap
    skipped_small: int = 0  # icons and rules
    skipped_repeated: int = 0  # the same picture on many pages: a logo or a background
    over_cap: int = 0  # worth describing, but past the per-Document cap
    unreadable: int = 0  # worth describing, but the picture could not be read

    @property
    def eligible(self) -> int:
        return len(self.images) + self.over_cap + self.unreadable
