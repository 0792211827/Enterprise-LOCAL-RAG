import { test } from "@playwright/test";
import { type CallerKind, stubApi } from "./support/api-stub";
import { captureAll } from "./support/screenshots";

// Not a check: run `SCREENSHOTS=1 npm run test:e2e -- screenshots` to write desktop, tablet and
// phone images of each landing surface to e2e/screenshots/ for design review.
test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to capture");

const LANDINGS: [CallerKind, string][] = [
  ["end-user", "chat"],
  ["admin", "dashboard"],
  ["auditor", "audit"],
  ["no-role", "no-access"],
];

for (const [caller, name] of LANDINGS) {
  test(`capture ${name}`, async ({ page }) => {
    await stubApi(page, caller);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await captureAll(page, name);
  });
}
