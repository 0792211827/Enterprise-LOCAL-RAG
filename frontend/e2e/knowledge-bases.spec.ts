import { expect, test, type Page } from "@playwright/test";
import { document as doc, job, kb, security } from "./support/admin-fixtures";
import { stubApi, type ApiStub } from "./support/api-stub";
import { captureAll, VIEWPORTS } from "./support/screenshots";

const NO_CONTENT = { status: 204, headers: { "access-control-allow-origin": "*" } };

const KBS = [kb(), kb({ id: "kb-fin", name: "Finance rules", slug: "finance-rules", document_count: 5, chunk_count: 210 })];

async function openAdmin(page: Page, path: string, setup?: (api: ApiStub) => void) {
  const api = await stubApi(page, "admin");
  api.respond("GET", "/api/v1/knowledge-bases", KBS);
  api.respond("GET", "/api/v1/knowledge-bases/:id", kb());
  api.respond("GET", "/api/v1/knowledge-bases/:id/documents", [doc()]);
  api.respond("GET", "/api/v1/knowledge-bases/:id/document-restrictions", {});
  api.respond("GET", "/api/v1/ingestion-jobs", [job()]);
  api.respond("GET", "/api/v1/connectors", []);
  setup?.(api);
  await page.goto(path);
  return api;
}

test.describe("the Knowledge Bases list", () => {
  test("shows Document count, size and last update", async ({ page }) => {
    await openAdmin(page, "/knowledge-bases");
    const table = page.getByRole("table", { name: "Knowledge Bases" });
    const row = table.getByRole("row", { name: /Finance rules/ });
    await expect(row).toContainText("5");
    await expect(row).toContainText("210 chunks");
    await expect(row).toContainText(/ago|just now|\d{4}/);
    await captureAll(page, "knowledge-bases-list");
  });

  test("creates a Knowledge Base and opens it", async ({ page }) => {
    let posted: unknown;
    await openAdmin(page, "/knowledge-bases", (a) => {
      a.respondWith("POST", "/api/v1/knowledge-bases", (_url, req) => {
        posted = req.body;
        return kb({ id: "kb-new", name: "Legal", slug: "legal", document_count: 0, chunk_count: 0 });
      }, 201);
      a.respond("GET", "/api/v1/knowledge-bases/kb-new", kb({ id: "kb-new", name: "Legal", slug: "legal", document_count: 0, chunk_count: 0 }));
      a.respond("GET", "/api/v1/knowledge-bases/kb-new/documents", []);
      a.respond("GET", "/api/v1/knowledge-bases/kb-new/document-restrictions", {});
    });
    await page.getByRole("button", { name: "New Knowledge Base" }).click();
    await page.getByLabel("Name", { exact: true }).fill("Legal");
    await page.getByRole("button", { name: "Create Knowledge Base" }).click();
    await expect(page.getByRole("heading", { name: "Legal" })).toBeVisible();
    expect(posted).toMatchObject({ name: "Legal", retrieval_mode: "hybrid" });
    await expect(page.getByText("No Documents yet")).toBeVisible();
  });

  test("names the Knowledge Base when deleting it", async ({ page }) => {
    let deleted = "";
    await openAdmin(page, "/knowledge-bases", (a) =>
      a.intercept("DELETE", "/api/v1/knowledge-bases/:id", (route, url) => {
        deleted = url.pathname;
        return route.fulfill(NO_CONTENT);
      }),
    );
    await page.getByRole("button", { name: "Delete Finance rules" }).click();
    const dialog = page.getByRole("dialog", { name: "Delete Finance rules?" });
    await expect(dialog).toContainText("5 Documents");
    await dialog.getByRole("button", { name: "Delete Knowledge Base" }).click();
    await expect.poll(() => deleted).toBe("/api/v1/knowledge-bases/kb-fin");
  });

  test("says so when there are none, and when loading fails", async ({ page }) => {
    await openAdmin(page, "/knowledge-bases", (a) => a.respond("GET", "/api/v1/knowledge-bases", []));
    await expect(page.getByText("No Knowledge Bases yet")).toBeVisible();

    const failing = await page.context().newPage();
    const api = await stubApi(failing, "admin");
    api.respond("GET", "/api/v1/knowledge-bases", { detail: "Index unavailable" }, 500);
    await failing.goto("/knowledge-bases");
    await expect(failing.getByRole("alert").filter({ hasText: "Index unavailable" })).toBeVisible();
  });
});

test.describe("a Knowledge Base's detail page", () => {
  test("has Documents, Connectors, Access and Settings tabs, with Access in place", async ({ page }) => {
    await openAdmin(page, "/knowledge-bases/kb-hr", (a) => {
      a.respond("GET", "/api/v1/knowledge-bases/:id/grants", []);
      a.respond("GET", "/api/v1/system/security", security());
    });
    await expect(page.getByRole("heading", { name: "HR handbook" })).toBeVisible();
    for (const name of ["Documents", "Connectors", "Access", "Settings"]) await expect(page.getByRole("tab", { name })).toBeVisible();
    await expect(page.getByRole("table", { name: "Documents" })).toContainText("Leave policy.pdf");
    await captureAll(page, "knowledge-base-documents");

    await page.getByRole("tab", { name: "Connectors" }).click();
    await expect(page.getByText("No Connectors yet")).toBeVisible();
    await page.getByRole("tab", { name: "Access" }).click();
    await expect(page.getByRole("heading", { name: "Check what a Principal can read" })).toBeVisible();
  });

  test("edits settings and saves them", async ({ page }) => {
    let patched: unknown;
    await openAdmin(page, "/knowledge-bases/kb-hr?tab=settings", (a) =>
      a.respondWith("PATCH", "/api/v1/knowledge-bases/:id", (_url, req) => {
        patched = req.body;
        return kb();
      }),
    );
    await page.getByLabel("Name", { exact: true }).fill("People handbook");
    await page.getByLabel("Default passages (top K)").fill("12");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Knowledge Base saved")).toBeVisible();
    expect(patched).toMatchObject({ name: "People handbook", default_top_k: 12 });
    await captureAll(page, "knowledge-base-settings");
  });

  test("uploads a Document inside the Knowledge Base and shows its ingestion stage, then its status", async ({ page }) => {
    let phase: "none" | "embedding" | "done" = "none";
    let uploaded = 0;
    await openAdmin(page, "/knowledge-bases/kb-hr", (a) => {
      a.respondWith("GET", "/api/v1/knowledge-bases/:id/documents", () =>
        phase === "none" ? [] : [doc({ id: "doc-new", title: "Expenses.pdf", status: phase === "done" ? "COMPLETED" : "PROCESSING", chunk_count: phase === "done" ? 18 : 0 })],
      );
      a.respondWith("GET", "/api/v1/ingestion-jobs", () =>
        phase === "none" ? [] : [job({ id: "job-new", document_id: "doc-new", status: phase === "done" ? "COMPLETED" : "PROCESSING", stage: phase === "done" ? "index" : "embed" })],
      );
      a.intercept("POST", "/api/v1/knowledge-bases/:id/documents", (route) => {
        uploaded += 1;
        phase = "embedding";
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          headers: { "access-control-allow-origin": "*" },
          body: JSON.stringify(doc({ id: "doc-new", title: "Expenses.pdf", status: "QUEUED" })),
        });
      });
    });
    await expect(page.getByText("No Documents yet")).toBeVisible();

    await page.getByLabel("Upload Documents").setInputFiles({ name: "Expenses.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 test") });
    const row = page.getByRole("row", { name: /Expenses\.pdf/ });
    await expect(row).toContainText("Embedding");
    await expect(row).toContainText("Processing");
    expect(uploaded).toBe(1);
    await captureAll(page, "knowledge-base-ingesting");

    phase = "done";
    await expect(row).toContainText("Completed", { timeout: 15_000 });
    await expect(row).toContainText("Done");
    await expect(row).toContainText("18");
  });

  test("shows a failed Document's error, and refusals from the upload", async ({ page }) => {
    await openAdmin(page, "/knowledge-bases/kb-hr", (a) => {
      a.respond("GET", "/api/v1/knowledge-bases/:id/documents", [doc({ id: "doc-bad", title: "Scan.pdf", status: "FAILED", error: "No text could be extracted from this file.", chunk_count: 0 })]);
      a.respond("GET", "/api/v1/ingestion-jobs", [job({ id: "job-bad", document_id: "doc-bad", status: "FAILED", stage: "parse", error: "No text could be extracted from this file." })]);
      a.respond("POST", "/api/v1/knowledge-bases/:id/documents", { detail: "An identical document already exists in this knowledge base" }, 409);
    });
    const row = page.getByRole("row", { name: /Scan\.pdf/ });
    await expect(row).toContainText("Failed");
    await expect(row).toContainText("Stopped while parsing");
    await expect(row).toContainText("No text could be extracted from this file.");

    await page.getByLabel("Upload Documents").setInputFiles({ name: "Scan.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF") });
    await expect(page.getByText("Already in this Knowledge Base, so it was skipped.")).toBeVisible();
  });

  test("deletes a Document after naming it", async ({ page }) => {
    let deleted = "";
    await openAdmin(page, "/knowledge-bases/kb-hr", (a) =>
      a.intercept("DELETE", "/api/v1/documents/:id", (route, url) => {
        deleted = url.pathname;
        return route.fulfill(NO_CONTENT);
      }),
    );
    await page.getByRole("button", { name: "Delete Leave policy.pdf" }).click();
    const dialog = page.getByRole("dialog", { name: "Delete Leave policy.pdf?" });
    await dialog.getByRole("button", { name: "Delete Document" }).click();
    await expect.poll(() => deleted).toBe("/api/v1/documents/doc-leave");
  });

  test("says what failed when the Documents cannot be loaded", async ({ page }) => {
    await openAdmin(page, "/knowledge-bases/kb-hr", (a) => a.respond("GET", "/api/v1/knowledge-bases/:id/documents", { detail: "Documents unavailable" }, 500));
    await expect(page.getByRole("alert").filter({ hasText: "Documents unavailable" })).toBeVisible();
  });

  test("keeps the page in its width at tablet size, scrolling the table instead", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.tablet);
    await openAdmin(page, "/knowledge-bases/kb-hr", (a) =>
      a.respond("GET", "/api/v1/knowledge-bases/:id/documents", [doc({ title: "An extraordinarily long Document title that keeps going well past what a tablet column can hold.pdf", status: "FAILED", error: "A long explanation of what went wrong while parsing this particular file." })]),
    );
    await expect(page.getByRole("table", { name: "Documents" })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
});

test.describe("the Documents view across Knowledge Bases", () => {
  test("lists Documents from every Knowledge Base and searches them", async ({ page }) => {
    await openAdmin(page, "/documents", (a) => {
      a.respondWith("GET", "/api/v1/knowledge-bases/:id/documents", (url) =>
        url.pathname.includes("kb-fin")
          ? [doc({ id: "doc-budget", knowledge_base_id: "kb-fin", title: "Budget 2027.pdf" })]
          : [doc()],
      );
    });
    const table = page.getByRole("table", { name: "Documents across all Knowledge Bases" });
    await expect(table).toContainText("Leave policy.pdf");
    await expect(table).toContainText("Budget 2027.pdf");
    await expect(table.getByRole("row", { name: /Budget 2027/ }).getByRole("link", { name: "Finance rules" })).toBeVisible();
    await captureAll(page, "documents-global");

    await page.getByLabel("Search Documents").fill("budget");
    await expect(table).toContainText("Budget 2027.pdf");
    await expect(table).not.toContainText("Leave policy.pdf");

    await page.getByLabel("Search Documents").fill("nothing like this");
    await expect(page.getByText("No Documents match")).toBeVisible();
  });

  test("says so when no Knowledge Base has Documents", async ({ page }) => {
    await openAdmin(page, "/documents", (a) => a.respond("GET", "/api/v1/knowledge-bases/:id/documents", []));
    await expect(page.getByText("No Documents yet")).toBeVisible();
  });
});
