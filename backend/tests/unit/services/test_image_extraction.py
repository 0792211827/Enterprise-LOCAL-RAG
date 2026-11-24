"""Finding the pictures in a Document and describing them: which are worth it, where each sits, what happens when the
vision model is off or fails. Files are small synthetic ones; the vision model is a fake."""

import pytest
from src.schemas.indexing.elements import ImageExtraction, Locator, locator_label
from src.services.indexing.element_chunker import chunk_elements
from src.services.ingestion.images import CONSECUTIVE_FAILURE_LIMIT, describe_images
from src.services.pdf_parser.images import ImageRules, extract_images
from tests.document_files import make_docx, make_pdf_with_image, make_png, make_pptx

RED, GREEN, BLUE = make_png((200, 30, 30)), make_png((30, 200, 30)), make_png((30, 30, 200))
ICON = make_png((1, 2, 3), (32, 32))
RULES = ImageRules(min_px=100, repeat_places=3, max_images=200)


class FakeVision:
    """Answers each picture in turn from ``answers`` (an exception in the list is raised)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    async def describe_image(self, image_base64, prompt, model=""):
        self.prompts.append(prompt)
        answer = self.answers[len(self.prompts) - 1] if len(self.prompts) <= len(self.answers) else ""
        if isinstance(answer, Exception):
            raise answer
        return answer


def pictures(tmp_path, name, data, rules=RULES, load=True):
    path = tmp_path / name
    path.write_bytes(data)
    return extract_images(path, rules, load)


# --- which pictures are worth describing ------------------------------------------------------------------------ #
def test_a_deck_yields_its_pictures_with_their_slide_and_the_text_around_them(tmp_path):
    deck = make_pptx([("Intro", "Welcome", None), ("Withdraw a student", "Click Withdraw on the student screen", None, [RED, GREEN])])

    found = pictures(tmp_path, "deck.pptx", deck)

    assert [image.locator.as_index_field() for image in found.images] == [{"kind": "image", "slide": 2, "heading_path": []}] * 2
    assert all(image.data.startswith(b"\x89PNG") for image in found.images)
    assert "Click Withdraw on the student screen" in found.images[0].context


def test_icons_and_pictures_repeated_across_slides_are_left_out(tmp_path):
    logo = make_png((9, 9, 9), (300, 100))
    deck = make_pptx(
        [
            ("One", "a", None, [logo, ICON, RED]),
            ("Two", "b", None, [logo]),
            ("Three", "c", None, [logo]),
        ]
    )

    found = pictures(tmp_path, "deck.pptx", deck)

    assert len(found.images) == 1 and found.images[0].locator.slide == 1
    assert (found.skipped_small, found.skipped_repeated, found.over_cap) == (1, 3, 0)


def test_at_most_the_cap_is_described_and_the_rest_counted(tmp_path):
    deck = make_pptx([("One", "a", None, [RED, GREEN, BLUE])])

    found = pictures(tmp_path, "deck.pptx", deck, ImageRules(max_images=2))

    assert len(found.images) == 2 and found.over_cap == 1 and found.eligible == 3


def test_counting_without_loading_reads_no_picture_bytes(tmp_path):
    found = pictures(tmp_path, "deck.pptx", make_pptx([("One", "a", None, [RED])]), load=False)

    assert found.eligible == 1 and found.images[0].data == b""


def test_a_word_file_picture_is_placed_by_its_heading_path(tmp_path):
    document = make_docx([("h1", "Policy"), ("h2", "Leave"), ("p", "See the form below"), ("img", RED)])

    [image] = pictures(tmp_path, "policy.docx", document).images

    assert image.locator.heading_path == ["Policy", "Leave"]
    assert "See the form below" in image.context
    assert locator_label(image.locator.as_index_field()) == "image in Policy > Leave"


def test_a_pdf_picture_is_placed_by_its_page(tmp_path):
    [image] = pictures(tmp_path, "scan.pdf", make_pdf_with_image(RED)).images

    assert image.locator.page == 1 and image.data.startswith(b"\x89PNG")
    assert locator_label(image.locator.as_index_field()) == "image on page 1"


@pytest.mark.parametrize("name", ["sheet.xlsx", "t.txt", "page.html"])
def test_a_type_that_holds_no_pictures_has_none(tmp_path, name):
    assert pictures(tmp_path, name, b"whatever").eligible == 0


def test_a_file_whose_pictures_cannot_be_read_raises_so_the_caller_can_warn(tmp_path):
    with pytest.raises(Exception):
        pictures(tmp_path, "notes.pptx", b"PK\x03\x04 broken")


def test_a_wide_screenshot_is_described_but_a_thin_rule_is_not(tmp_path):
    banner, rule = make_png((5, 5, 5), (900, 60)), make_png((6, 6, 6), (600, 8))

    found = pictures(tmp_path, "deck.pptx", make_pptx([("One", "a", None, [banner, rule])]))

    assert [(i.width, i.height) for i in found.images] == [(900, 60)] and found.skipped_small == 1


# --- describing them -------------------------------------------------------------------------------------------- #
def extraction(count):
    deck = make_pptx([(f"Slide {n}", "text", None, [make_png((n * 20, 50, 50))]) for n in range(1, count + 1)])
    return deck


async def described(tmp_path, vision, count=3, rules=RULES):
    return await describe_images(vision, pictures(tmp_path, "deck.pptx", extraction(count), rules))


async def test_each_description_becomes_an_image_passage_on_its_slide(tmp_path):
    result = await described(tmp_path, FakeVision("A login form", "A grid of codes"), count=2)

    assert [(e.locator.kind, e.locator.slide, e.text) for e in result.elements] == [
        ("image", 1, "A login form"),
        ("image", 2, "A grid of codes"),
    ]
    assert (result.described, result.undescribed, result.warnings) == (2, 0, [])
    [chunk, _] = chunk_elements(result.elements, "doc", 100)
    assert chunk.text == "Image on slide 1:\n\nA login form" and chunk.locator.kind == "image"


async def test_the_prompt_carries_the_text_around_the_picture(tmp_path):
    vision = FakeVision("x")

    await described(tmp_path, vision, count=1)

    assert "Slide 1" in vision.prompts[0] and "text visible" in vision.prompts[0]


async def test_with_the_vision_model_off_nothing_is_described_and_the_count_is_a_warning(tmp_path):
    result = await described(tmp_path, None, count=3)

    assert result.elements == [] and result.undescribed == 3
    [warning] = result.warnings
    assert warning.startswith("3 images were not described") and "VLM__ENABLED" in warning


async def test_pictures_that_run_out_of_the_time_budget_are_left_undescribed_not_fatal(tmp_path):
    import asyncio

    class Slow(FakeVision):
        async def describe_image(self, image_base64, prompt, model=""):
            await asyncio.sleep(5)

    result = await describe_images(Slow(), pictures(tmp_path, "deck.pptx", extraction(2)), budget_seconds=0.2)

    assert result.described == 0 and result.undescribed == 2
    assert "ran out of time" in result.warnings[0]


async def test_a_picture_that_fails_does_not_stop_the_others(tmp_path):
    result = await described(tmp_path, FakeVision("first", RuntimeError("model busy"), "third"))

    assert [e.text for e in result.elements] == ["first", "third"]
    assert result.undescribed == 1 and result.warnings == ["1 image was not described (the vision model failed, ran out of time or returned nothing). Reprocess this Document to try again."]


async def test_a_model_that_keeps_failing_is_given_up_on(tmp_path):
    vision = FakeVision(*[RuntimeError("down")] * 10)

    result = await described(tmp_path, vision, count=6)

    assert len(vision.prompts) == CONSECUTIVE_FAILURE_LIMIT
    assert result.described == 0 and result.undescribed == 6


async def test_an_empty_answer_counts_as_not_described(tmp_path):
    result = await described(tmp_path, FakeVision("  "), count=1)

    assert result.elements == [] and result.undescribed == 1


async def test_pictures_past_the_cap_are_reported_separately(tmp_path):
    result = await described(tmp_path, FakeVision("one", "two"), count=3, rules=ImageRules(max_images=2))

    assert result.described == 2 and result.undescribed == 1
    [warning] = result.warnings
    assert warning.startswith("1 image was left out") and "INGESTION__MAX_IMAGES_PER_DOCUMENT" in warning


async def test_a_document_without_pictures_needs_nothing():
    result = await describe_images(None, ImageExtraction())

    assert result.warnings == [] and result.elements == []
