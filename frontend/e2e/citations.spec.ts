import { expect, test, type Page } from "@playwright/test";
import { SAMPLE_CITATION, stubApi, type ApiStub, type Citation } from "./support/api-stub";
import { captureAll, VIEWPORTS } from "./support/screenshots";

const SECOND: Citation = {
  index: 2,
  document_id: "doc-sick",
  title: "Sick leave policy",
  section_title: "Certificates",
  chunk_id: "chunk-2",
  passage: "A medical certificate is needed after three days of absence.",
  score: 0.7,
};

const OWN_FILE: Citation = {
  index: 3,
  document_id: null,
  attachment_id: "att-1",
  title: "my-contract.pdf",
  section_title: null,
  chunk_id: null,
  passage: "The notice period is two months.",
  score: 0.6,
};

const LEAVE_TEXT = `Introduction to leave.\n\n${SAMPLE_CITATION.passage}\n\nSee also the sick leave policy.`;

const ANSWER = "You get 25 days of leave [1], and a certificate is needed for sick days [2].";

async function askWithCitations(page: Page, citations: Citation[], setup?: (s: ApiStub) => void, shows = /You get 25 days of leave/) {
  const api = await stubApi(page, "end-user");
  api.respond("GET", "/api/v1/chat/documents/doc-leave", {
    id: "doc-leave",
    title: "Leave policy",
    source_uri: null,
    content_type: "application/pdf",
    text: LEAVE_TEXT,
  });
  api.respond("GET", "/api/v1/chat/documents/doc-sick", {
    id: "doc-sick",
    title: "Sick leave policy",
    source_uri: null,
    content_type: "application/pdf",
    text: `Sick leave basics.\n\n${SECOND.passage}`,
  });
  api.streamAnswer({ text: ANSWER, citations });
  setup?.(api);
  await page.goto("/chat");
  await page.getByRole("textbox", { name: "Your question" }).fill("What about leave?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(shows)).toBeVisible();
  return api;
}

const panel = (page: Page) => page.getByRole("complementary", { name: "Citation" });

test.describe("Citations on a wide screen", () => {
  test("lists the Citations under the answer with Document title and section", async ({ page }) => {
    await askWithCitations(page, [SAMPLE_CITATION, SECOND]);
    const list = page.getByRole("list", { name: "Citations" });
    await expect(list.getByRole("button", { name: /Leave policy/ })).toContainText("Annual leave");
    await expect(list.getByRole("button", { name: /Sick leave policy/ })).toContainText("Certificates");
  });

  test("a marker in the text and the list item open the same panel beside the answer", async ({ page }) => {
    await askWithCitations(page, [SAMPLE_CITATION, SECOND]);
    await page.getByRole("button", { name: "Open citation 1" }).click();
    await expect(panel(page)).toBeVisible();
    await expect(panel(page).getByRole("heading", { name: "Leave policy" })).toBeVisible();
    // The answer is still there beside it.
    await expect(page.getByText(/You get 25 days of leave/)).toBeVisible();
    await captureAll(page, "citation-panel");

    await panel(page).getByRole("button", { name: "Close" }).click();
    await expect(panel(page)).toHaveCount(0);
    await page.getByRole("list", { name: "Citations" }).getByRole("button", { name: /Leave policy/ }).click();
    await expect(panel(page).getByRole("heading", { name: "Leave policy" })).toBeVisible();
  });

  test("a Citation from a web page says where in it and links to the page", async ({ page }) => {
    const page_url = "https://docs.example.com/support";
    await askWithCitations(page, [{ ...SAMPLE_CITATION, locator_text: "image on slide 7", source_url: page_url }]);
    await expect(page.getByRole("list", { name: "Citations" }).getByRole("button", { name: /Leave policy/ })).toContainText("image on slide 7");

    await page.getByRole("button", { name: "Open citation 1" }).click();

    await expect(panel(page).getByRole("link", { name: "Open the page" })).toHaveAttribute("href", page_url);
    await expect(panel(page).getByRole("link", { name: "Open the page" })).toHaveAttribute("target", "_blank");
  });

  test("highlights the cited passage in its source", async ({ page }) => {
    await askWithCitations(page, [SAMPLE_CITATION, SECOND]);
    await page.getByRole("button", { name: "Open citation 1" }).click();
    await expect(panel(page).getByText("Introduction to leave.")).toBeVisible();
    await expect(panel(page).getByText("See also the sick leave policy.")).toBeVisible();
    await expect(panel(page).locator("mark")).toHaveText(SAMPLE_CITATION.passage);
  });

  test("labels a Citation of the End user's own file", async ({ page }) => {
    await askWithCitations(page, [SAMPLE_CITATION, OWN_FILE], (api) =>
      api.streamAnswer({ text: "Leave [1]; notice period [2].", citations: [SAMPLE_CITATION, { ...OWN_FILE, index: 2 }] }),
      /notice period/,
    );
    const list = page.getByRole("list", { name: "Citations" });
    await expect(list.getByRole("button", { name: /my-contract\.pdf/ })).toContainText("Your file");
    await list.getByRole("button", { name: /my-contract\.pdf/ }).click();
    await expect(panel(page)).toContainText("From your file");
    await expect(panel(page).locator("mark")).toHaveText(OWN_FILE.passage);
  });

  test("says access is limited when the Document cannot be opened", async ({ page }) => {
    await askWithCitations(page, [SAMPLE_CITATION, SECOND], (api) =>
      api.respond("GET", "/api/v1/chat/documents/doc-sick", { detail: "Document not found" }, 404),
    );
    await page.getByRole("button", { name: "Open citation 2" }).click();
    await expect(panel(page)).toContainText("Access to this document is limited");
    await expect(panel(page)).not.toContainText("Something went wrong");
    // The cited passage is still shown: it was part of the answer the End user may read.
    await expect(panel(page)).toContainText(SECOND.passage);
  });

  test("moves between Citations with the arrow keys and closes with Escape", async ({ page }) => {
    await askWithCitations(page, [SAMPLE_CITATION, SECOND]);
    const opener = page.getByRole("button", { name: "Open citation 1" });
    await opener.click();
    await expect(panel(page).getByRole("heading", { name: "Leave policy" })).toBeVisible();

    await page.keyboard.press("ArrowDown");
    await expect(panel(page).getByRole("heading", { name: "Sick leave policy" })).toBeVisible();
    await expect(panel(page).locator("mark")).toHaveText(SECOND.passage);
    await page.keyboard.press("ArrowUp");
    await expect(panel(page).getByRole("heading", { name: "Leave policy" })).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(panel(page)).toHaveCount(0);
    await expect(opener).toBeFocused();
  });
});

test.describe("Citations on a phone", () => {
  test("open as a bottom sheet that closes with Escape", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.phone);
    await askWithCitations(page, [SAMPLE_CITATION, SECOND]);
    await page.getByRole("button", { name: "Open citation 1" }).click();

    const sheet = page.getByRole("dialog", { name: "Citation" });
    await expect(sheet).toBeVisible();
    await expect(sheet.locator("mark")).toHaveText(SAMPLE_CITATION.passage);
    const box = await sheet.boundingBox();
    expect(box!.y + box!.height).toBeGreaterThan(VIEWPORTS.phone.height - 2); // anchored to the bottom edge
    await captureAll(page, "citation-sheet");
    await page.setViewportSize(VIEWPORTS.phone);

    await page.keyboard.press("ArrowDown");
    await expect(sheet.getByRole("heading", { name: "Sick leave policy" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
  });
});
