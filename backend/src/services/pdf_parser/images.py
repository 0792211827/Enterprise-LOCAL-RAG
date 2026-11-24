"""Finding the pictures inside a PDF, PPTX or DOCX that are worth describing, and where each one sits.

Docling is not used here: it does not hand back the picture bytes of Office files, and the layout models it would need
for a PDF are slow. python-pptx and python-docx walk the file's own structure, and pypdfium2 lists a PDF page's image
objects, so each picture comes with the exact slide, page (or heading) it belongs to and the text around it.

A picture is left out when it is an icon (both sides under ``min_px``) or a rule (a side under ``THIN_PX``), when the same picture sits in many places
(a logo or a background), or when the Document already has ``max_images`` to describe. Anything this module cannot read
is left out too: pictures never fail a Document.
"""

import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Hashable, Iterable, Iterator, List

from src.schemas.indexing.elements import ExtractedImage, ImageExtraction, Locator

logger = logging.getLogger(__name__)

MAX_SIDE_PX = 1600  # larger pictures are scaled down before they are sent to the vision model
CONTEXT_CHARS = 800
THIN_PX = 20  # a line or a rule, whatever its length


@dataclass(frozen=True)
class ImageRules:
    min_px: int = 100
    repeat_places: int = 3  # the same picture in this many places is decoration
    max_images: int = 200


@dataclass
class Candidate:
    digest: str
    width: int
    height: int
    locator: Locator
    context: str
    place: Hashable  # a page, a slide or a paragraph: how many places a picture is used decides whether it is a logo
    load: Callable[[], bytes]  # the picture as PNG; only called for the ones that will be described


def to_png(picture: Any) -> bytes:
    """A PIL image as PNG bytes, scaled down so that its longest side is at most MAX_SIDE_PX."""
    if max(picture.size) > MAX_SIDE_PX:
        picture = picture.copy()
        picture.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    if picture.mode not in ("RGB", "RGBA", "L"):
        picture = picture.convert("RGB")
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def _blob_loader(blob: bytes) -> Callable[[], bytes]:
    def load() -> bytes:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as picture:
            picture.load()
            return to_png(picture)

    return load


def _blob_size(blob: bytes) -> tuple:
    from PIL import Image

    with Image.open(io.BytesIO(blob)) as picture:
        return picture.size


def select_images(candidates: Iterable[Candidate], rules: ImageRules, load: bool) -> ImageExtraction:
    """Apply the rules to a Document's pictures, in document order. ``load=False`` counts them without reading their bytes."""
    found = list(candidates)
    result = ImageExtraction()
    places: dict = {}
    for candidate in found:
        places.setdefault(candidate.digest, set()).add(candidate.place)

    kept: List[Candidate] = []
    for candidate in found:
        if (candidate.width < rules.min_px and candidate.height < rules.min_px) or min(candidate.width, candidate.height) < THIN_PX:
            result.skipped_small += 1
        elif len(places[candidate.digest]) >= rules.repeat_places:
            result.skipped_repeated += 1
        else:
            kept.append(candidate)

    result.over_cap = max(len(kept) - rules.max_images, 0)
    for candidate in kept[: rules.max_images]:
        data = b""
        if load:
            try:
                data = candidate.load()
            except Exception as exc:  # noqa: BLE001 - a picture that cannot be read is simply not described
                logger.warning("Could not read an image (%s): %s", candidate.locator.as_index_field(), exc)
                result.unreadable += 1
                continue
        result.images.append(
            ExtractedImage(data=data, width=candidate.width, height=candidate.height, locator=candidate.locator, context=candidate.context)
        )
    return result


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text[:CONTEXT_CHARS]


# --- PPTX ------------------------------------------------------------------------------------------------------- #
def _shapes(shapes: Iterable[Any]) -> Iterator[Any]:
    for shape in shapes:
        yield shape
        children = getattr(shape, "shapes", None)  # a group
        if children is not None:
            yield from _shapes(children)


def _slide_text(slide: Any) -> str:
    parts = [shape.text_frame.text for shape in _shapes(slide.shapes) if getattr(shape, "has_text_frame", False) and shape.has_text_frame]
    return _clip(" ".join(parts))


def pptx_candidates(path: Path) -> Iterator[Candidate]:
    from pptx import Presentation

    for number, slide in enumerate(Presentation(str(path)).slides, 1):
        context = _slide_text(slide)
        for shape in _shapes(slide.shapes):
            try:
                image = shape.image  # only pictures (and picture placeholders) have one
                blob = image.blob
                width, height = image.size
            except Exception:  # noqa: BLE001 - not a picture, or one python-pptx cannot read
                continue
            yield Candidate(
                digest=hashlib.sha1(blob).hexdigest(),
                width=width,
                height=height,
                locator=Locator(kind="image", slide=number),
                context=context,
                place=number,
                load=_blob_loader(blob),
            )


# --- DOCX ------------------------------------------------------------------------------------------------------- #
def docx_candidates(path: Path) -> Iterator[Candidate]:
    import docx
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    stack: List[str] = []
    titled = False
    recent: List[str] = []
    for index, element in enumerate(document.element.body.iter(qn("w:p"))):
        paragraph = Paragraph(element, document)
        text = paragraph.text.strip()
        style = (paragraph.style.name if paragraph.style is not None else "") or ""
        if text and style.startswith(("Heading", "Title")):
            level = int(style.split()[-1]) if style.split()[-1].isdigit() else 1
            if style.startswith("Title"):
                stack, titled = [text], True
            else:
                stack = stack[: (1 if titled else 0) + level - 1] + [text]
        elif text:
            recent = (recent + [text])[-2:]
        for rid in element.xpath(".//a:blip/@r:embed"):
            try:
                blob = document.part.related_parts[rid].blob
                width, height = _blob_size(blob)
            except Exception:  # noqa: BLE001 - a vector picture (EMF, WMF) or a broken one: nothing for a vision model to read
                continue
            yield Candidate(
                digest=hashlib.sha1(blob).hexdigest(),
                width=width,
                height=height,
                locator=Locator(kind="image", heading_path=list(stack)),
                context=_clip(" ".join([*recent, text])),
                place=index,
                load=_blob_loader(blob),
            )


# --- PDF -------------------------------------------------------------------------------------------------------- #
def pdf_candidates(pdf: Any) -> Iterator[Candidate]:
    import pypdfium2.raw as pdfium_c

    for index in range(len(pdf)):
        number = index + 1
        page = pdf[index]
        context = ""
        for position, obj in enumerate(page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=3)):
            if not context:
                context = _clip(page.get_textpage().get_text_bounded())
            try:
                meta = obj.get_metadata()
                width, height = meta.width, meta.height
            except Exception:  # noqa: BLE001
                continue
            try:
                digest = hashlib.sha1(obj.get_data(decode_simple=False)).hexdigest()
            except Exception:  # noqa: BLE001 - without its bytes it cannot be recognised as a repeat
                digest = f"page{number}-object{position}"
            yield Candidate(
                digest=digest,
                width=width,
                height=height,
                locator=Locator(kind="image", page=number),
                context=context,
                place=number,
                load=lambda obj=obj: to_png(obj.get_bitmap().to_pil()),
            )


def extract_images(path: Path, rules: ImageRules, load: bool) -> ImageExtraction:
    """The pictures of a PDF, PPTX or DOCX worth describing; any other type has none.

    :raises Exception: the file could not be read for its pictures.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        try:
            return select_images(pdf_candidates(pdf), rules, load)
        finally:
            pdf.close()
    if suffix == ".pptx":
        return select_images(pptx_candidates(path), rules, load)
    if suffix == ".docx":
        return select_images(docx_candidates(path), rules, load)
    return ImageExtraction()
