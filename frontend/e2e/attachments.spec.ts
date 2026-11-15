import { expect, test, type Page } from "@playwright/test";
import { stubApi, type ApiStub } from "./support/api-stub";
import { captureAll, VIEWPORTS } from "./support/screenshots";

const NOW = new Date().toISOString();
const attachment = (over: Record<string, unknown> = {}) => ({
  id: "att-1",
  filename: "handbook.pdf",
  kind: "pdf",
  size_bytes: 2048 * 10,
  token_estimate: 500,
  searched: false,
  attached_to: null,
  created_at: NOW,
  ...over,
});

const uploadedName = (postData: string | null) => /filename="([^"]+)"/.exec(postData ?? "")?.[1] ?? "unknown";

async function openChat(page: Page, setup?: (s: ApiStub) => void) {
  const api = await stubApi(page, "end-user");
  api.respond("GET", "/api/v1/chat/config", { images_enabled: true, image_max_mb: 5, attachments_per_message: 3 });
  setup?.(api);
  await page.goto("/chat");
  return api;
}

const problems = (page: Page) => page.getByRole("alert", { name: "Attachment problems" });
const attachButton = (page: Page) => page.getByRole("button", { name: "Attach a file" });
const file = (name: string, body = "hello") => ({ name, mimeType: "application/octet-stream", buffer: Buffer.from(body) });
const uploads = (api: ApiStub) => api.calls.filter((c) => c.startsWith("POST") && c.endsWith("/attachments"));

test("attaches a file by button and shows a chip with name, type and size", async ({ page }) => {
  const api = await openChat(page);
  api.intercept("POST", "/api/v1/chat/conversations/:id/attachments", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "access-control-allow-origin": "*" },
      body: JSON.stringify(attachment({ filename: uploadedName(route.request().postData()) })),
    }),
  );
  await expect(page.getByText(/private to this conversation/i)).toBeVisible();

  const chooser = page.waitForEvent("filechooser");
  await attachButton(page).click();
  await (await chooser).setFiles(file("handbook.pdf"));

  const chip = page.getByRole("listitem").filter({ hasText: "handbook.pdf" });
  await expect(chip).toBeVisible();
  await expect(chip).toContainText("PDF");
  await expect(chip).toContainText("20 KB");
  await expect(chip).not.toContainText("Searched");
  await captureAll(page, "attachments-chip");
});

test("removes an attachment before sending", async ({ page }) => {
  const api = await openChat(page);
  api.respond("POST", "/api/v1/chat/conversations/:id/attachments", attachment());
  api.respond("DELETE", "/api/v1/chat/conversations/:id/attachments/att-1", undefined, 204);
  await page.locator('input[type="file"]').setInputFiles(file("handbook.pdf"));
  await expect(page.getByText("handbook.pdf")).toBeVisible();

  await page.getByRole("button", { name: "Remove handbook.pdf" }).click();
  await expect(page.getByText("handbook.pdf")).toHaveCount(0);
  expect(api.calls.some((c) => c.startsWith("DELETE") && c.endsWith("/attachments/att-1"))).toBe(true);
});

test("attaches by dragging a file onto the composer", async ({ page }) => {
  const api = await openChat(page);
  api.respondWith("POST", "/api/v1/chat/conversations/:id/attachments", () => attachment({ filename: "notes.txt", kind: "txt" }));
  const composer = page.getByRole("form", { name: "Ask a question" });
  const data = await page.evaluateHandle(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["hello"], "notes.txt", { type: "text/plain" }));
    return transfer;
  });
  await composer.dispatchEvent("dragenter", { dataTransfer: data });
  await expect(page.getByText("Drop to attach")).toBeVisible();
  await composer.dispatchEvent("drop", { dataTransfer: data });
  await expect(page.getByRole("listitem").filter({ hasText: "notes.txt" })).toBeVisible();
  await expect(page.getByText("Drop to attach")).toHaveCount(0);
});

test("flags a file too long to send whole as searched, with an explanation", async ({ page }) => {
  const api = await openChat(page);
  api.respond("POST", "/api/v1/chat/conversations/:id/attachments", attachment({ filename: "annual-report.pdf", searched: true }));
  await page.locator('input[type="file"]').setInputFiles(file("annual-report.pdf"));

  const chip = page.getByRole("listitem").filter({ hasText: "annual-report.pdf" });
  await expect(chip).toContainText("Searched");
  await expect(page.getByText(/too long to send in full/)).toBeVisible();
  await expect(page.getByText(/searched for the passages that answer each question/)).toBeVisible();
});

test("gives clear errors for an unsupported type and a file that is too large", async ({ page }) => {
  const api = await openChat(page);
  api.intercept("POST", "/api/v1/chat/conversations/:id/attachments", (route) =>
    route.fulfill({
      status: 413,
      contentType: "application/json",
      headers: { "access-control-allow-origin": "*" },
      body: JSON.stringify({ detail: "The file is larger than 20 MB (CHAT__ATTACHMENT_MAX_MB)." }),
    }),
  );
  const input = page.locator('input[type="file"]');

  // Refused in the browser, before anything is uploaded.
  await input.setInputFiles(file("installer.exe"));
  await expect(problems(page)).toContainText("installer.exe");
  await expect(problems(page)).toContainText("isn't a supported type");
  await expect(problems(page)).toContainText("PDF");
  expect(uploads(api)).toHaveLength(0);

  // Refused by the server: its reason is shown without setting names.
  await input.setInputFiles(file("huge.pdf"));
  await expect(problems(page)).toContainText("huge.pdf");
  await expect(problems(page)).toContainText("larger than 20 MB");
  await expect(problems(page)).not.toContainText("CHAT__");
  await expect(page.getByRole("list", { name: "Attachments" })).toHaveCount(0);
});

test("refuses an image that is over the image limit before uploading it", async ({ page }) => {
  const api = await openChat(page);
  await page.locator('input[type="file"]').setInputFiles({
    name: "scan.png",
    mimeType: "image/png",
    buffer: Buffer.alloc(6 * 1024 * 1024),
  });
  await expect(problems(page)).toContainText("scan.png");
  await expect(problems(page)).toContainText("5 MB");
  expect(uploads(api)).toHaveLength(0);
});

test("only offers images when the operator has switched them on", async ({ page }) => {
  const api = await openChat(page, (s) => s.respond("GET", "/api/v1/chat/config", { images_enabled: false, image_max_mb: 5, attachments_per_message: 3 }));
  await page.locator('input[type="file"]').setInputFiles(file("scan.png"));
  await expect(problems(page)).toContainText("scan.png");
  expect(uploads(api)).toHaveLength(0);
});

test("keeps showing the attachments of a sent message in the conversation", async ({ page }) => {
  const api = await openChat(page);
  api.respond("POST", "/api/v1/chat/conversations/:id/attachments", attachment({ filename: "handbook.pdf" }));
  await page.locator('input[type="file"]').setInputFiles(file("handbook.pdf"));
  await expect(page.getByText("handbook.pdf")).toBeVisible();

  await page.getByRole("textbox", { name: "Your question" }).fill("What does the handbook say?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/You get 25 days of annual leave/)).toBeVisible();

  // The chip now belongs to the sent message, once, and the composer is clear.
  await expect(page.getByText("handbook.pdf")).toHaveCount(1);
  await expect(page.getByText("What does the handbook say?")).toBeVisible();
});

test("works at phone width", async ({ page }) => {
  await page.setViewportSize(VIEWPORTS.phone);
  const api = await openChat(page);
  api.respond("POST", "/api/v1/chat/conversations/:id/attachments", attachment({ filename: "a-rather-long-file-name-for-a-phone-screen.pdf", searched: true }));
  await page.locator('input[type="file"]').setInputFiles(file("a-rather-long-file-name-for-a-phone-screen.pdf"));
  await expect(page.getByText(/too long to send in full/)).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await captureAll(page, "attachments-phone");
});
