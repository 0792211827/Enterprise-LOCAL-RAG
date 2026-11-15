import { expect, test } from "@playwright/test";
import { stubApi } from "./support/api-stub";
import { stubIdp } from "./support/idp-stub";
import { captureAll } from "./support/screenshots";

// The OIDC build: the app signs people in against the company identity provider.

test("a signed-out visitor sees one button to sign in with the company account", async ({ page }) => {
  await stubApi(page, "end-user");
  await stubIdp(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in to continue" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in with your company account" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
  await captureAll(page, "screen-oidc-signed-out");
});

test("signing in lands each role on its own surface, and signing out returns to the sign-in page", async ({ page }) => {
  await stubApi(page, "end-user");
  const idp = await stubIdp(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in with your company account" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  expect(idp.authorizeRequests).toHaveLength(1);
  await expect(page.getByText("maria.santos@example.test")).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to continue" })).toBeVisible();
});

test("an Admin signing in lands on the Dashboard", async ({ page }) => {
  await stubApi(page, "admin");
  await stubIdp(page, "sam.okafor@example.test");
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in with your company account" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
});

test("a signed-in person with no role sees the no-access page and can still sign out", async ({ page }) => {
  await stubApi(page, "no-role");
  await stubIdp(page, "pat.guest@example.test");
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in with your company account" }).click();
  await expect(page.getByRole("heading", { name: "Your account has no access" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
});

test("a failed sign-in is a clear page with a way to try again", async ({ page }) => {
  await stubApi(page, "end-user");
  const idp = await stubIdp(page);
  idp.failNextSignIn("access_denied");
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in with your company account" }).click();
  await expect(page.getByRole("heading", { name: "We couldn’t sign you in" })).toBeVisible();
  await expect(page.getByText(/access_denied/)).toBeVisible();
  await captureAll(page, "screen-sign-in-failed");

  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page).toHaveURL(/\/chat$/);
});
