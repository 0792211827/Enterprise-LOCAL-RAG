"""Turning a Document's pictures into passages: the optional vision model describes each one, and the description is
searched and cited like the Document's own text ("image on slide 7").

Pictures never fail a Document. With the vision model off, or when it fails, the Document is ingested without those
descriptions and says so: the admin sees how many pictures were not described and why, and reprocessing the Document
once the model is available adds them.
"""

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from src.schemas.indexing.elements import DocumentElement, ExtractedImage, ImageExtraction, Locator

logger = logging.getLogger(__name__)

CONSECUTIVE_FAILURE_LIMIT = 3  # a model that keeps failing is not called for the rest of the Document
MAX_DESCRIPTION_CHARS = 4000

PROMPT = (
    "This image is {where} of a document.{context} "
    "Transcribe any text visible in the image exactly as written, then say what the image shows: screens, forms, buttons, "
    "menus, tables, charts or diagrams, and what they mean. Be factual and specific. Do not guess beyond what is visible."
)


@dataclass
class Described:
    elements: List[DocumentElement] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    described: int = 0
    undescribed: int = 0


def _count(count: int) -> str:
    """"1 image was", "3 images were"."""
    return f"{count} image was" if count == 1 else f"{count} images were"


def _prompt(image: ExtractedImage) -> str:
    where = "on a slide" if image.locator.slide else "on a page" if image.locator.page else "in the text"
    context = f" The text around it reads: {image.context}" if image.context else ""
    return PROMPT.format(where=where, context=context)


def _element(image: ExtractedImage, description: str) -> DocumentElement:
    locator = Locator(kind="image", page=image.locator.page, slide=image.locator.slide, heading_path=list(image.locator.heading_path))
    return DocumentElement(text=description, locator=locator)


async def describe_images(vlm: Optional[Any], extraction: ImageExtraction, budget_seconds: Optional[float] = None) -> Described:
    """Describe the pictures of ``extraction`` with ``vlm`` (``None`` = the vision model is off).

    ``budget_seconds`` bounds the time spent here: what is not described when it runs out is counted as undescribed, so
    slow pictures cost the Document its descriptions, never the Document its place in the index.
    """
    result = Described()
    if extraction.eligible == 0:
        return result

    failed = extraction.unreadable
    failures_in_a_row = 0
    deadline = time.monotonic() + budget_seconds if budget_seconds else None
    if vlm is not None:
        for index, image in enumerate(extraction.images):
            remaining = None if deadline is None else deadline - time.monotonic()
            if failures_in_a_row >= CONSECUTIVE_FAILURE_LIMIT or (remaining is not None and remaining <= 0):
                failed += len(extraction.images) - index
                break
            try:
                async with asyncio.timeout(remaining):
                    description = (await vlm.describe_image(base64.b64encode(image.data).decode("ascii"), _prompt(image)) or "").strip()
            except Exception as exc:  # noqa: BLE001 - one picture never fails the Document (a timeout here is this budget, not the Document's)
                logger.warning("The vision model could not describe an image (%s): %s", image.locator.as_index_field(), exc)
                description = ""
            if not description:
                failed += 1
                failures_in_a_row += 1
                continue
            failures_in_a_row = 0
            result.elements.append(_element(image, description[:MAX_DESCRIPTION_CHARS]))

    result.described = len(result.elements)
    result.undescribed = extraction.eligible - result.described
    within_cap = extraction.eligible - extraction.over_cap
    if vlm is None and within_cap:
        result.warnings.append(
            f"{_count(within_cap)} not described because the vision model is off (VLM__ENABLED), so text that exists only "
            "inside pictures cannot be found. Turn it on and reprocess this Document."
        )
    elif failed:
        result.warnings.append(
            f"{_count(failed)} not described (the vision model failed, ran out of time or returned nothing). Reprocess this Document to try again."
        )
    if extraction.over_cap:
        result.warnings.append(
            f"{_count(extraction.over_cap)} left out: the Document has more images than the per-Document limit "
            "(INGESTION__MAX_IMAGES_PER_DOCUMENT)."
        )
    return result
