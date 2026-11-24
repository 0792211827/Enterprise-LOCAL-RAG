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

test.describe("adding many Documents, and replacing one", () => {
  const upload = (name: string) => ({ name, mimeType: "application/octet-stream", buffer: Buffer.from(`content of ${name}`) });
  const json = (route: import("@playwright/test").Route, body: unknown, status = 202) =>
    route.fulfill({ status, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify(body) });

  test("a mixed batch: each file succeeds or fails on its own, two at a time, and failed ones offer Retry and Delete", async ({ page }) => {
    const added: string[] = [];
    let inFlight = 0;
    let peak = 0;
    let retried = "";
    await openAdmin(page, "/knowledge-bases/kb-hr", (a) => {
      a.respond("GET", "/api/v1/knowledge-bases/:id/documents", [
        doc({ id: "doc-a", title: "Handbook.docx", status: "COMPLETED" }),
        doc({ id: "doc-b", title: "Deck.pptx", status: "FAILED", error: "The file could not be read as a PPTX: it may be corrupt.", chunk_count: 0, content_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation" }),
        doc({ id: "doc-c", title: "Rates.xlsx", status: "QUEUED", chunk_count: 0 }),
      ]);
      a.respond("GET", "/api/v1/ingestion-jobs", [job({ document_id: "doc-b", status: "FAILED", stage: "parse" })]);
      a.intercept("POST", "/api/v1/knowledge-bases/:id/documents", async (route) => {
        const name = /filename="([^"]+)"/.exec(route.request().postData() ?? "")?.[1] ?? "";
        added.push(name);
        inFlight += 1;
        peak = Math.max(peak, inFlight);
        await new Promise((resolve) => setTimeout(resolve, 150));
        inFlight -= 1;
        if (name === "same-name.docx") return json(route, { detail: "A document named 'same-name.docx' already exists in this knowledge base. Use Replace on it to update its content." }, 409);
        return json(route, doc({ id: `doc-${name}`, title: name, status: "QUEUED" }));
      });
      a.intercept("POST", "/api/v1/documents/:id/reprocess", (route, url) => {
        retried = url.pathname;
        return json(route, job({ document_id: "doc-b", status: "QUEUED" }));
      });
    });

    await page.getByLabel("Upload Documents").setInputFiles(["a.pdf", "b.docx", "same-name.docx", "d.csv", "e.txt"].map(upload));

    await expect(page.getByText("A file was not added")).toBeVisible();
    await expect(page.getByText("Use Replace on it to update its content.")).toBeVisible();
    expect(added.sort()).toEqual(["a.pdf", "b.docx", "d.csv", "e.txt", "same-name.docx"]);
    expect(peak).toBeLessThanOrEqual(2);
    const failed = page.getByRole("row", { name: /Deck\.pptx/ });
    await expect(failed).toContainText("Failed");
    await expect(failed).toContainText("PPTX");
    await expect(failed).toContainText("it may be corrupt");
    await expect(page.getByRole("row", { name: /Handbook\.docx/ })).toContainText("Completed");
    await expect(page.getByRole("row", { name: /Rates\.xlsx/ })).toContainText("Queued");
    await expect(page.getByRole("button", { name: "Delete Deck.pptx" })).toBeVisible();

    await page.getByRole("button", { name: "Retry Deck.pptx" }).click();
    await expect.poll(() => retried).toBe("/api/v1/documents/doc-b/reprocess");
    await captureAll(page, "knowledge-base-bulk-upload");
  });

  test("replaces a Document's content with a new file, and shows a failed replacement without losing the old content", async ({ page }) => {
    let replaced = "";
    let state: "ready" | "failed" = "ready";
    await openAdmin(page, "/knowledge-bases/kb-hr", (a) => {
      a.respondWith("GET", "/api/v1/knowledge-bases/:id/documents", () => [
        doc({ id: "doc-leave", replacement: state === "failed" ? { state: "failed", filename: "Leave v2.pdf", error: "No text found in the document." } : null }),
      ]);
      a.intercept("POST", "/api/v1/documents/:id/replace", (route, url) => {
        replaced = `${url.pathname} ${/filename="([^"]+)"/.exec(route.request().postData() ?? "")?.[1]}`;
        state = "failed";
        return json(route, doc({ id: "doc-leave", replacement: { state: "queued", filename: "Leave v2.pdf", error: null } }));
      });
    });

    const chooser = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "Replace Leave policy.pdf" }).click();
    await (await chooser).setFiles(upload("Leave v2.pdf"));

    await expect.poll(() => replaced).toBe("/api/v1/documents/doc-leave/replace Leave v2.pdf");
    const row = page.getByRole("row", { name: /Leave policy\.pdf/ });
    await expect(row).toContainText("Replacement failed, the previous content still answers: No text found in the document.", { timeout: 15_000 });
    await expect(row.getByRole("button", { name: "Retry Leave policy.pdf" })).toBeVisible();
  });

  test("adds a web page by address, shows it as a link, and refreshes it instead of replacing it", async ({ page }) => {
    const pageUrl = "https://docs.example.com/guide";
    let added: unknown;
    let refreshed = "";
    let listed = [doc({ id: "doc-leave" })];
    await openAdmin(page, "/knowledge-bases/kb-hr", (a) => {
      a.respondWith("GET", "/api/v1/knowledge-bases/:id/documents", () => listed);
      a.intercept("POST", "/api/v1/knowledge-bases/:id/documents/url", async (route) => {
        added = route.request().postDataJSON();
        const body = route.request().postDataJSON() as { url: string };
        if (body.url.includes("blocked")) return json(route, { detail: "blocked.example.com is not on the allowlist of hosts a URL may be fetched from (INGESTION__URL_ALLOWED_HOSTS)." }, 403);
        const web = doc({ id: "doc-web", title: pageUrl, content_type: "text/html", source_kind: "url", source_url: pageUrl, status: "COMPLETED", chunk_count: 3, warnings: ["2 images were not described because the vision model is off (VLM__ENABLED)."] });
        listed = [...listed, web];
        return json(route, doc({ id: "doc-web", title: pageUrl, status: "QUEUED", source_kind: "url", source_url: pageUrl }));
      });
      a.intercept("POST", "/api/v1/documents/:id/refresh", (route, url) => {
        refreshed = url.pathname;
        return json(route, { changed: false, document: listed[1] });
      });
    });

    await page.getByLabel("Or add a web page by address").fill("https://blocked.example.com/x");
    await page.getByRole("button", { name: "Add URL" }).click();
    await expect(page.getByText("is not on the allowlist of hosts a URL may be fetched from")).toBeVisible();

    await page.getByLabel("Or add a web page by address").fill(pageUrl);
    await page.getByRole("button", { name: "Add URL" }).click();
    await expect.poll(() => added).toEqual({ url: pageUrl });

    const row = page.getByRole("row", { name: /docs\.example\.com\/guide/ });
    await expect(row).toContainText("Web page");
    await expect(row).toContainText("2 images were not described");
    await expect(row.getByRole("link", { name: pageUrl })).toHaveAttribute("href", pageUrl);
    await expect(row.getByRole("button", { name: `Replace ${pageUrl}` })).toHaveCount(0);

    await row.getByRole("button", { name: `Refresh ${pageUrl}` }).click();
    await expect.poll(() => refreshed).toBe("/api/v1/documents/doc-web/refresh");
    await expect(page.getByText("is already up to date: the page has not changed.")).toBeVisible();
    await captureAll(page, "knowledge-base-url-document");
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
