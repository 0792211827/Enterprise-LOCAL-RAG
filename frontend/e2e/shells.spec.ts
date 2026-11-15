import { expect, test } from "@playwright/test";
import { stubApi } from "./support/api-stub";
import { captureAll } from "./support/screenshots";

const adminNav = (page: import("@playwright/test").Page) => page.getByRole("navigation", { name: "Main" });

test.describe("the User shell", () => {
  test("an End user sees the chat app and nothing of the control plane", async ({ page }) => {
    await stubApi(page, "end-user");
    await page.goto("/");
    await expect(page).toHaveURL(/\/chat$/);
    await expect(page.getByText("Enterprise Local RAG").first()).toBeVisible();
    await expect(page.getByText("maria.santos@example.test")).toBeVisible();
    await expect(adminNav(page)).toHaveCount(0);
    for (const name of ["Dashboard", "Knowledge Bases", "Applications", "Audit Log"]) {
      await expect(page.getByRole("link", { name })).toHaveCount(0);
    }
    await captureAll(page, "shell-user");
  });

  test("an End user is sent back to chat from admin and audit addresses", async ({ page }) => {
    await stubApi(page, "end-user");
    for (const address of ["/knowledge-bases", "/applications/abc", "/audit", "/system"]) {
      await page.goto(address);
      await expect(page).toHaveURL(/\/chat$/);
    }
  });
});

test.describe("the Admin shell", () => {
  test("an Admin lands on the Dashboard with a sidebar grouped Build, Operate and Review", async ({ page }) => {
    await stubApi(page, "admin");
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    const nav = adminNav(page);
    await expect(nav).toBeVisible();
    for (const group of ["Build", "Operate", "Review"]) await expect(nav.getByText(group, { exact: true })).toBeVisible();
    for (const link of ["Applications", "Knowledge Bases", "Retrieval", "Dashboard", "Models & Providers", "System", "Feedback"]) {
      await expect(nav.getByRole("link", { name: link })).toBeVisible();
    }
    await expect(page.getByText("Assistants")).toHaveCount(0);
    // Audit Log belongs to the auditor role; an Admin without it never sees it.
    await expect(nav.getByRole("link", { name: "Audit Log" })).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
    await captureAll(page, "shell-admin");
  });

  test("shows who is signed in and where the page sits", async ({ page }) => {
    await stubApi(page, "admin");
    await page.goto("/");
    await expect(page.getByText("sam.okafor@example.test")).toBeVisible();
    const crumbs = page.getByRole("navigation", { name: "Breadcrumb" });
    await expect(crumbs).toContainText("Operate");
    await expect(crumbs).toContainText("Dashboard");
  });

  test("an auditor lands on the Audit Log and sees only that", async ({ page }) => {
    await stubApi(page, "auditor");
    await page.goto("/");
    await expect(page).toHaveURL(/\/audit$/);
    const nav = adminNav(page);
    await expect(nav.getByRole("link", { name: "Audit Log" })).toBeVisible();
    await expect(nav.getByRole("link")).toHaveCount(1);
    await expect(nav.getByText("Review", { exact: true })).toBeVisible();
    await expect(nav.getByText("Build", { exact: true })).toHaveCount(0);
  });

  test("an Admin or auditor who opens the wrong address is sent to their own page", async ({ page }) => {
    await stubApi(page, "admin");
    await page.goto("/chat");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();

    await stubApi(page, "auditor");
    await page.goto("/knowledge-bases");
    await expect(page).toHaveURL(/\/audit$/);
  });

  test("existing pages render inside the shell", async ({ page }) => {
    const api = await stubApi(page, "admin");
    api.respond("GET", "/api/v1/knowledge-bases", []);
    await page.goto("/");
    await adminNav(page).getByRole("link", { name: "Knowledge Bases" }).click();
    await expect(page).toHaveURL(/\/knowledge-bases$/);
    await expect(adminNav(page)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Knowledge Bases" }).first()).toBeVisible();
  });

  test("collapses the sidebar to icons at tablet width and keeps every link reachable", async ({ page }) => {
    await stubApi(page, "admin");
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const nav = adminNav(page);
    const wide = (await nav.boundingBox())!.width;
    expect(wide).toBeGreaterThan(180);

    await page.setViewportSize({ width: 820, height: 1100 });
    await expect.poll(async () => (await nav.boundingBox())!.width).toBeLessThan(100);
    await expect(nav.getByRole("link", { name: "Knowledge Bases" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  });
});

test.describe("with authentication off", () => {
  test("the anonymous caller gets the Admin shell and the Dashboard", async ({ page }) => {
    await stubApi(page, "anonymous");
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect(page.getByText("Authentication off")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toHaveCount(0);
  });
});
