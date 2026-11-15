import type { Page } from "@playwright/test";

export const VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  tablet: { width: 820, height: 1180 },
  phone: { width: 390, height: 844 },
} as const;

/**
 * Capture the page at desktop, tablet and phone widths into e2e/screenshots/<name>-<viewport>.png,
 * for reviewing a screen against the design intent. Leaves the page at the desktop size.
 */
export async function captureAll(page: Page, name: string) {
  for (const [label, size] of Object.entries(VIEWPORTS)) {
    await page.setViewportSize(size);
    await page.waitForTimeout(150);
    await page.screenshot({ path: `e2e/screenshots/${name}-${label}.png`, fullPage: true });
  }
  await page.setViewportSize(VIEWPORTS.desktop);
}
