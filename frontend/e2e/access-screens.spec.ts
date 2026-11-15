import { expect, test } from "@playwright/test";
import { stubApi } from "./support/api-stub";
import { captureAll } from "./support/screenshots";

test("a caller with no role gets a full page that names the roles that would give access", async ({ page }) => {
  await stubApi(page, "no-role");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Your account has no access" })).toBeVisible();
  await expect(page.getByText("pat.guest@example.test")).toBeVisible();
  for (const role of ["querier", "admin", "auditor"]) await expect(page.getByText(role).first()).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
  await captureAll(page, "screen-no-access");
});

test("a caller whose session is missing is asked to sign in again", async ({ page }) => {
  await stubApi(page, "signed-out");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in to continue" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reload" })).toBeVisible();
  await captureAll(page, "screen-signed-out");
});

test("an unreachable API is a full page with a way to try again", async ({ page }) => {
  const api = await stubApi(page, "admin");
  api.abort("GET", "/api/v1/auth/me");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Can’t reach the platform" })).toBeVisible();

  const restored = await stubApi(page, "admin");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  expect(restored.unhandled).toEqual([]);
});
