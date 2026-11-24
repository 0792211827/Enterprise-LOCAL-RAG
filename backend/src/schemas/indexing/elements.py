"""Where a passage came from, and the parsed structure it is cut from.

A parser returns :class:`DocumentElement` objects (a slide, a run of rows, a section) each with a :class:`Locator`.
The chunker keeps the locator on every passage it makes, the index stores it, and a Citation shows
:func:`locator_label`. Passages indexed before locators existed have none and are shown as they always were.
"""

from typing import Any, List, Mapping, Optional, Tuple

from pydantic import BaseModel, Field


class Locator(BaseModel):
    """A position in the source Document. Which fields are set depends on the kind of source."""

    kind: str = "text"  # text | table | slide
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


def locator_label(locator: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The words a Citation shows for a locator (``slide 7``, ``sheet Codes, rows 12-30``, ``Leave, page 14``)."""
    if not locator:
        return None
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
