import { expect, test, type Page } from "@playwright/test";
import { application, kb, provider, retrievalProfile, security } from "./support/admin-fixtures";
import { stubApi, type CallerKind } from "./support/api-stub";

// The whole-app pass: what must hold on every page, whichever ticket built it.

const ADMIN_PAGES = ["/", "/applications", "/applications/new", "/knowledge-bases", "/documents", "/retrieval", "/providers", "/system", "/feedback"];
const PHONE = { width: 390, height: 844 };
const TABLET = { width: 820, height: 1180 };

// Pictographs, arrows and dingbats used as icons. Typographic punctuation (— · … “ ”) is fine.
const ICON_GLYPHS = /[\u2190-\u21ff\u2300-\u23ff\u2460-\u27bf\u2b00-\u2bff\u25a0-\u25ff\u{1F000}-\u{1FFFF}\u00d7]/u;

async function open(page: Page, caller: CallerKind, path: string) {
  const api = await stubApi(page, caller);
  api.respond("GET", "/api/v1/knowledge-bases", [kb()]);
  api.respond("GET", "/api/v1/applications", [application()]);
  api.respond("GET", "/api/v1/providers", [provider()]);
  api.respond("GET", "/api/v1/retrieval-configurations", [retrievalProfile()]);
  api.respond("GET", "/api/v1/system/security", security());
  api.respond("GET", "/api/v1/documents", []);
  api.respond("GET", "/api/v1/chat-admin/conversation-counts", { conversations: 0, messages: 0, by_application: [] });
  api.respond("GET", "/api/v1/chat-admin/ratings", []);
  api.respond("GET", "/api/v1/chat-admin/shared-exchanges", []);
  await page.goto(path);
  await page.waitForLoadState("networkidle");
}

const overflows = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

test.describe("Admin pages", () => {
  for (const path of ADMIN_PAGES) {
    test(`${path} has landmarks, one heading, no icon glyphs, and fits a tablet`, async ({ page }) => {
      await page.setViewportSize(TABLET);
      await open(page, "admin", path);
      await expect(page.getByRole("main")).toHaveCount(1);
      await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      expect(await overflows(page)).toBe(false);
      const text = await page.locator("body").innerText();
      expect(text.match(ICON_GLYPHS)).toBeNull();
    });
  }
});

test("the Audit Log has the same landmarks and fits a tablet", async ({ page }) => {
  await page.setViewportSize(TABLET);
  await open(page, "auditor", "/audit");
  await expect(page.getByRole("main")).toHaveCount(1);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Audit Log");
  expect(await overflows(page)).toBe(false);
});

test("chat fits a phone and has landmarks", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await open(page, "end-user", "/chat");
  await expect(page.getByRole("main")).toHaveCount(1);
  await expect(page.getByRole("banner")).toBeVisible();
  expect(await overflows(page)).toBe(false);
  expect((await page.locator("body").innerText()).match(ICON_GLYPHS)).toBeNull();
});

test("the first Tab stop skips to the page content", async ({ page }) => {
  await open(page, "admin", "/system");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
});

test("animation stops when the person asks for reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await open(page, "admin", "/system");
  const duration = await page.evaluate(() => {
    const probe = document.createElement("div");
    probe.className = "animate-shimmer";
    document.body.appendChild(probe);
    return parseFloat(getComputedStyle(probe).animationDuration);
  });
  expect(duration).toBeLessThan(0.001);
});

test("every control that can be focused shows where focus is", async ({ page }) => {
  await open(page, "admin", "/applications");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  const outline = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement;
    const style = getComputedStyle(el);
    return { width: parseFloat(style.outlineWidth), style: style.outlineStyle };
  });
  expect(outline.style).not.toBe("none");
  expect(outline.width).toBeGreaterThanOrEqual(2);
});
