import { expect, test } from "@playwright/test";
import { stubApi } from "./support/api-stub";
import { captureAll } from "./support/screenshots";

// The specimen page is how the owner approves the look before any page is rebuilt, so it has to show
// everything and has to hold to the foundation's promises: nothing fetched from outside the
// platform, readable contrast, visible focus, and reduced motion respected.

test.beforeEach(async ({ page }) => {
  await stubApi(page, "admin");
});

test("shows the palette, type scale and components for both the User and Admin surfaces", async ({ page }) => {
  await page.goto("/design");
  await expect(page.getByRole("heading", { level: 1, name: "Design specimen" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Palette" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Type" })).toBeVisible();
  for (const surface of ["User surface", "Admin surface"]) {
    const region = page.getByRole("region", { name: surface });
    await expect(region).toBeVisible();
    for (const component of ["Buttons", "Status badges", "Metric card", "Tabs", "Table", "Chips", "Toggle", "Skeleton", "Callouts", "Code block", "Secret reveal", "Breadcrumb"]) {
      await expect(region.getByRole("heading", { name: component })).toBeVisible();
    }
  }
  await captureAll(page, "design-specimen");
});

test("fetches nothing from outside the platform", async ({ page, baseURL }) => {
  const outside: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (url.startsWith("data:") || url.startsWith("blob:")) return;
    if (!url.startsWith(baseURL!) && !url.startsWith("http://api.stub.test")) outside.push(url);
  });
  await page.goto("/design");
  await page.waitForLoadState("networkidle");
  const fonts = await page.evaluate(async () => {
    await document.fonts.ready;
    return [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family);
  });
  expect(outside).toEqual([]);
  // The serif, sans and mono faces are bundled and actually used by the page.
  expect(fonts.join(" ")).toMatch(/Newsreader/);
  expect(fonts.join(" ")).toMatch(/Instrument Sans/);
});

test("text meets WCAG AA contrast against its background in the light theme", async ({ page }) => {
  await page.goto("/design");
  const pairs = await page.locator("[data-contrast]").evaluateAll((els) =>
    els.map((el) => {
      const s = getComputedStyle(el);
      return { name: el.getAttribute("data-contrast")!, fg: s.color, bg: s.backgroundColor };
    }),
  );
  expect(pairs.length).toBeGreaterThan(8);
  const channels = (css: string) => (css.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);
  const luminance = ([r, g, b]: number[]) => {
    const lin = (c: number) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  };
  for (const { name, fg, bg } of pairs) {
    const [a, b] = [luminance(channels(fg)), luminance(channels(bg))].sort((x, y) => y - x);
    const ratio = (a + 0.05) / (b + 0.05);
    expect(ratio, `${name} (${fg} on ${bg})`).toBeGreaterThanOrEqual(4.5);
  }
});

test("shows a visible focus ring on keyboard focus", async ({ page }) => {
  await page.goto("/design");
  const button = page.getByRole("region", { name: "User surface" }).getByRole("button", { name: "Primary" });
  await button.focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  await expect(button).toBeFocused();
  const outline = await button.evaluate((el) => {
    const s = getComputedStyle(el);
    return { style: s.outlineStyle, width: parseFloat(s.outlineWidth), shadow: s.boxShadow };
  });
  expect(outline.style !== "none" && outline.width > 0).toBe(true);
});

test("stops motion when the person prefers reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/design");
  const skeleton = page.getByRole("region", { name: "Admin surface" }).locator("[data-skeleton]").first();
  const duration = await skeleton.evaluate((el) => parseFloat(getComputedStyle(el).animationDuration));
  expect(duration).toBeLessThan(0.01);
});

test.describe("overlays and controls", () => {
  test("a modal, drawer and sheet open and close with Escape", async ({ page }) => {
    await page.goto("/design");
    const region = page.getByRole("region", { name: "Admin surface" });
    for (const [open, title] of [
      ["Open modal", "Delete Application"],
      ["Open drawer", "Audit event"],
      ["Open sheet", "Citation"],
    ]) {
      await region.getByRole("button", { name: open }).click();
      const dialog = page.getByRole("dialog", { name: title });
      await expect(dialog).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
    }
  });

  test("a toggle changes state and a toast announces the result", async ({ page }) => {
    await page.goto("/design");
    const region = page.getByRole("region", { name: "User surface" });
    const toggle = region.getByRole("switch", { name: "Share this exchange with Admins" });
    await expect(toggle).not.toBeChecked();
    await toggle.click();
    await expect(toggle).toBeChecked();
    await region.getByRole("button", { name: "Show toast" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Changes saved" })).toBeVisible();
  });

  test("a secret is hidden until revealed", async ({ page }) => {
    await page.goto("/design");
    const region = page.getByRole("region", { name: "Admin surface" });
    await expect(region.getByText("sk-live-specimen-0000")).toHaveCount(0);
    await region.getByRole("button", { name: "Reveal" }).click();
    await expect(region.getByText("sk-live-specimen-0000")).toBeVisible();
  });
});
