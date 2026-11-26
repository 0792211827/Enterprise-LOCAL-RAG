import { expect, test, type Page } from "@playwright/test";
import { application, connector, job } from "./support/admin-fixtures";
import { stubApi, type ApiStub } from "./support/api-stub";
import { captureAll } from "./support/screenshots";

async function openDashboard(page: Page, setup?: (api: ApiStub) => void) {
  const api = await stubApi(page, "admin");
  api.respond("GET", "/api/v1/applications", [application()]);
  api.respond("GET", "/api/v1/connectors", [connector()]);
  api.respond("GET", "/api/v1/ingestion-jobs", [job()]);
  api.respond("GET", "/api/v1/chat-admin/ratings/summary", [{ application_id: "app-policies", application_name: "HR policies", up: 9, down: 1 }]);
  api.respond("GET", "/api/v1/system/health", {
    status: "healthy",
    components: [
      { name: "postgres", status: "healthy", latency_ms: 3, version: "16", detail: null },
      { name: "opensearch", status: "healthy", latency_ms: 12, version: "2.17", detail: null },
    ],
  });
  setup?.(api);
  await page.goto("/");
  return api;
}

const attention = (page: Page) => page.getByRole("list", { name: "Needs action" });

test("a healthy system says everything is OK and that nothing needs action", async ({ page }) => {
  await openDashboard(page);
  await expect(page.getByRole("status", { name: "Overall status" })).toContainText("Everything is OK");
  await expect(page.getByText("Nothing needs action.")).toBeVisible();
  await expect(attention(page)).toHaveCount(0);
  // Below the attention list: counts, services and recent ingestion.
  await expect(page.getByRole("region", { name: "At a glance" })).toContainText("Documents14");
  await expect(page.getByRole("region", { name: "Services" })).toContainText("opensearch");
  await expect(page.getByRole("table", { name: "Recent ingestion jobs" })).toContainText("Completed");
  await captureAll(page, "dashboard-healthy");
});

test("the overall status leads and the attention list comes before everything else", async ({ page }) => {
  await openDashboard(page);
  const headings = await page.getByRole("heading", { level: 2 }).allTextContents();
  expect(headings.slice(0, 2)).toEqual(["Needs action", "At a glance"]);
  const status = await page.getByRole("status", { name: "Overall status" }).boundingBox();
  const needs = await page.getByRole("heading", { name: "Needs action" }).boundingBox();
  expect(status!.y).toBeLessThan(needs!.y);
});

test("a failing Connector appears in the attention list and links to where it is fixed", async ({ page }) => {
  await openDashboard(page, (a) => {
    a.respond("GET", "/api/v1/connectors", [connector({ last_run_status: "error", last_run_summary: { error: "Could not reach https://minio.internal:9000" } }), connector({ id: "conn-ok", name: "healthy-one" })]);
    a.respond("GET", "/api/v1/knowledge-bases/:id", { id: "kb-hr", name: "HR handbook", document_count: 3, chunk_count: 120, created_at: null, updated_at: null });
    a.respond("GET", "/api/v1/knowledge-bases/:id/documents", []);
    a.respond("GET", "/api/v1/knowledge-bases/:id/document-restrictions", {});
  });
  await expect(page.getByRole("status", { name: "Overall status" })).toContainText("1 thing needs action");
  const item = attention(page).getByRole("link", { name: /Connector policies-share failed on its last run/ });
  await expect(item).toContainText("Could not reach https://minio.internal:9000");
  await expect(attention(page)).not.toContainText("healthy-one");
  await expect(page.getByText("Nothing needs action.")).toHaveCount(0);
  await captureAll(page, "dashboard-attention");
  await item.click();
  await expect(page).toHaveURL(/\/knowledge-bases\/kb-hr\?tab=connectors$/);
  await expect(page.getByRole("tab", { name: "Connectors" })).toHaveAttribute("aria-selected", "true");
});

test("telemetry that is exporting or off needs no action, and one that is failing is flagged", async ({ page }) => {
  const health = (telemetry: string) => ({
    status: "healthy",
    components: [
      { name: "postgres", status: "healthy", latency_ms: 3, version: "16", detail: null },
      { name: "Telemetry", status: telemetry, latency_ms: null, version: null, detail: telemetry === "failing" ? "The collector is unreachable" : null },
    ],
  });
  await openDashboard(page, (a) => a.respond("GET", "/api/v1/system/health", health("exporting")));
  await expect(page.getByText("Nothing needs action.")).toBeVisible();

  await openDashboard(page, (a) => a.respond("GET", "/api/v1/system/health", health("disabled")));
  await expect(page.getByText("Nothing needs action.")).toBeVisible();

  await openDashboard(page, (a) => a.respond("GET", "/api/v1/system/health", health("failing")));
  const item = attention(page).getByRole("link", { name: /Telemetry is failing/ });
  await expect(item).toContainText("The collector is unreachable");
  await expect(item).toHaveAttribute("href", "/system");
});

test("lists failed ingestion, Applications with no Knowledge Base, low-rated answers and unhealthy services, each linking onward", async ({ page }) => {
  await openDashboard(page, (a) => {
    a.respond("GET", "/api/v1/ingestion-jobs", [
      job({ id: "job-bad", document_id: "doc-a", status: "FAILED", error: "Docling could not parse the file", created_at: "2026-11-14T09:00:00Z" }),
    ]);
    a.respond("GET", "/api/v1/applications", [application({ knowledge_bases: [] })]);
    a.respond("GET", "/api/v1/chat-admin/ratings/summary", [
      { application_id: "app-policies", application_name: "HR policies", up: 1, down: 4 },
      { application_id: "app-finance", application_name: "Finance desk", up: 8, down: 1 },
    ]);
    a.respond("GET", "/api/v1/system/health", {
      status: "degraded",
      components: [
        { name: "postgres", status: "healthy", latency_ms: 3, version: "16", detail: null },
        { name: "opensearch", status: "unhealthy", latency_ms: null, version: null, detail: "Connection refused" },
      ],
    });
  });
  await expect(page.getByRole("status", { name: "Overall status" })).toContainText("4 things need action");
  const list = attention(page);
  const link = (name: RegExp) => list.getByRole("link", { name });
  await expect(link(/opensearch is unhealthy/)).toHaveAttribute("href", "/system");
  await expect(link(/opensearch is unhealthy/)).toContainText("Connection refused");
  await expect(link(/1 Document failed to ingest/)).toHaveAttribute("href", "/knowledge-bases/kb-hr");
  await expect(link(/1 Document failed to ingest/)).toContainText("Docling could not parse the file");
  await expect(link(/HR policies has no Knowledge Base/)).toHaveAttribute("href", "/applications/app-policies?tab=knowledge-bases");
  await expect(link(/HR policies has 4 low-rated answers/)).toHaveAttribute("href", "/feedback");
  // Finance desk is rated well, so it is not flagged.
  await expect(list).not.toContainText("Finance desk");
  // The most urgent come first.
  const titles = await list.getByRole("listitem").allTextContents();
  expect(titles[0]).toContain("opensearch");
});

test("a failure a Document has since recovered from is not flagged", async ({ page }) => {
  await openDashboard(page, (a) =>
    a.respond("GET", "/api/v1/ingestion-jobs", [
      job({ id: "job-old", status: "FAILED", error: "timeout", created_at: "2026-11-13T09:00:00Z" }),
      job({ id: "job-new", status: "COMPLETED", created_at: "2026-11-14T09:00:00Z" }),
    ]),
  );
  await expect(page.getByText("Nothing needs action.")).toBeVisible();
});

test("does not claim everything is OK when a check could not run", async ({ page }) => {
  await openDashboard(page, (a) => a.respond("GET", "/api/v1/connectors", { detail: "Connectors unavailable" }, 500));
  const status = page.getByRole("status", { name: "Overall status" });
  await expect(status).toContainText("Some checks could not run");
  await expect(status).toContainText("Could not check Connectors");
  await expect(page.getByText("Everything is OK")).toHaveCount(0);
  await expect(page.getByText("Nothing needs action.")).toHaveCount(0);
});

test("shows what is running recently, with status and age", async ({ page }) => {
  await openDashboard(page, (a) =>
    a.respond("GET", "/api/v1/ingestion-jobs", [
      job({ id: "aaaaaaaa-1", status: "PROCESSING", stage: "embed", created_at: new Date(Date.now() - 5 * 60_000).toISOString() }),
      job({ id: "bbbbbbbb-2", document_id: "doc-b", status: "COMPLETED", created_at: new Date(Date.now() - 3 * 3600_000).toISOString() }),
    ]),
  );
  const table = page.getByRole("table", { name: "Recent ingestion jobs" });
  const running = table.getByRole("row", { name: /aaaaaaaa/ });
  await expect(running).toContainText("Embed");
  await expect(running).toContainText("Processing");
  await expect(running).toContainText("5 minutes ago");
  await expect(table.getByRole("row", { name: /bbbbbbbb/ })).toContainText("3 hours ago");
});

test("shows a GPU when there is one", async ({ page }) => {
  await openDashboard(page, (a) =>
    a.respond("GET", "/api/v1/system/gpu", {
      available: true,
      cuda_version: "12.4",
      driver_version: "550",
      gpus: [{ index: 0, name: "NVIDIA L4", memory_total_mb: 24000, memory_used_mb: 9000, utilization_percent: 41, temperature_c: 58 }],
      message: null,
    }),
  );
  await expect(page.getByRole("region", { name: "GPU" })).toContainText("NVIDIA L4");
  await expect(page.getByRole("region", { name: "GPU" })).toContainText("9000 / 24000 MB");
});

test("asks for more than the API's first hundred so older problems are not missed", async ({ page }) => {
  const asked: string[] = [];
  await openDashboard(page, (a) => {
    a.respondWith("GET", "/api/v1/ingestion-jobs", (url) => {
      asked.push(`jobs${url.search}`);
      return [job()];
    });
    a.respondWith("GET", "/api/v1/applications", (url) => {
      asked.push(`applications${url.search}`);
      return [application()];
    });
  });
  await expect(page.getByText("Nothing needs action.")).toBeVisible();
  expect(asked).toEqual(expect.arrayContaining(["jobs?limit=1000", "applications?limit=1000"]));
});
