import { expect, test } from "@playwright/test";
import { stubApi } from "./support/api-stub";

test.describe("each role lands on its own surface", () => {
  test("an End user lands on chat", async ({ page }) => {
    const api = await stubApi(page, "end-user");
    await page.goto("/");
    await expect(page).toHaveURL(/\/chat$/);
    await expect(page.getByRole("textbox", { name: "Your question" })).toBeVisible();
    expect(api.unhandled).toEqual([]);
  });

  test("an Admin lands on the Dashboard", async ({ page }) => {
    const api = await stubApi(page, "admin");
    await page.goto("/");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    expect(api.unhandled).toEqual([]);
  });

  test("an auditor lands on the Audit Log", async ({ page }) => {
    const api = await stubApi(page, "auditor");
    await page.goto("/");
    await expect(page).toHaveURL(/\/audit$/);
    await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
    expect(api.unhandled).toEqual([]);
  });

  test("a caller with no role is told they have no access", async ({ page }) => {
    await stubApi(page, "no-role");
    await page.goto("/");
    await expect(page.getByText(/no access/i).first()).toBeVisible();
  });
});

test("an End user who opens an admin address is sent back to chat", async ({ page }) => {
  await stubApi(page, "end-user");
  await page.goto("/knowledge-bases");
  await expect(page).toHaveURL(/\/chat$/);
});
