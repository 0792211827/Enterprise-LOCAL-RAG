import { expect, test, type Page } from "@playwright/test";
import { connector, document as doc, job, kb } from "./support/admin-fixtures";
import { stubApi, type ApiStub } from "./support/api-stub";
import { captureAll } from "./support/screenshots";

const NO_CONTENT = { status: 204, headers: { "access-control-allow-origin": "*" } };

async function openConnectors(page: Page, connectors: unknown[], setup?: (api: ApiStub) => void) {
  const api = await stubApi(page, "admin");
  api.respond("GET", "/api/v1/knowledge-bases/:id", kb());
  api.respond("GET", "/api/v1/knowledge-bases/:id/documents", [doc()]);
  api.respond("GET", "/api/v1/knowledge-bases/:id/document-restrictions", {});
  api.respond("GET", "/api/v1/ingestion-jobs", [job()]);
  api.respond("GET", "/api/v1/connectors", connectors);
  setup?.(api);
  await page.goto("/knowledge-bases/kb-hr?tab=connectors");
  return api;
}

test("lists each Connector's source, schedule, last run and health", async ({ page }) => {
  await openConnectors(page, [
    connector(),
    connector({
      id: "conn-s3",
      name: "bucket-docs",
      source_type: "s3",
      source: { endpoint_url: "https://minio.internal:9000", bucket: "docs", prefix: "hr/", access_key_ref: "env:MINIO_ACCESS_KEY", secret_key_ref: "env:MINIO_SECRET_KEY" },
      enabled: false,
      last_run_at: null,
      last_run_status: null,
      files_synced: 0,
    }),
    connector({ id: "conn-other", name: "elsewhere", knowledge_base_id: "kb-fin" }),
  ]);
  const table = page.getByRole("table", { name: "Connectors" });
  const folder = table.getByRole("row", { name: /policies-share/ });
  await expect(folder).toContainText("/mnt/sources/hr");
  await expect(folder).toContainText("Runs with the Connector job");
  await expect(folder).toContainText("12 synced");
  await expect(folder).toContainText("Healthy");
  const bucket = table.getByRole("row", { name: /bucket-docs/ });
  await expect(bucket).toContainText("s3://docs/hr");
  await expect(bucket).toContainText("https://minio.internal:9000");
  await expect(bucket).toContainText("Paused");
  await expect(bucket).toContainText("Not run yet");
  await expect(table).not.toContainText("elsewhere");
  await captureAll(page, "connectors-list");
});

test("calls out a failed Connector in the tab", async ({ page }) => {
  await openConnectors(page, [
    connector({ last_run_status: "error", files_synced: 4, last_run_summary: { error: "Could not reach https://minio.internal:9000" } }),
    connector({ id: "conn-ok", name: "healthy-one" }),
  ]);
  const callout = page.getByText("policies-share failed on its last run");
  await expect(callout).toBeVisible();
  await expect(page.getByText("Could not reach https://minio.internal:9000").first()).toBeVisible();
  const row = page.getByRole("table", { name: "Connectors" }).getByRole("row", { name: /policies-share/ });
  await expect(row).toContainText("Failed");
  await expect(page.getByRole("table", { name: "Connectors" }).getByRole("row", { name: /healthy-one/ })).toContainText("Healthy");
  await captureAll(page, "connectors-failed");
});

test("creates a mounted-path Connector in this Knowledge Base", async ({ page }) => {
  let posted: Record<string, unknown> = {};
  await openConnectors(page, [], (a) =>
    a.respondWith("POST", "/api/v1/connectors", (_url, req) => {
      posted = req.body as Record<string, unknown>;
      return connector({ name: "hr-share" });
    }, 201),
  );
  await expect(page.getByText("No Connectors yet")).toBeVisible();
  await page.getByRole("button", { name: "New Connector" }).first().click();
  const dialog = page.getByRole("dialog", { name: "New Connector" });
  const create = dialog.getByRole("button", { name: "Create Connector" });
  await expect(create).toBeDisabled();
  await dialog.getByLabel("Name").fill("has spaces");
  await expect(dialog).toContainText("with no spaces");
  await dialog.getByLabel("Name").fill("hr-share");
  await dialog.getByLabel("Folder path").fill("/mnt/sources/hr");
  await create.click();
  await expect.poll(() => posted).toMatchObject({ name: "hr-share", knowledge_base_id: "kb-hr", source_type: "path", path: "/mnt/sources/hr", enabled: true });
  expect(posted).not.toHaveProperty("bucket");
});

test("creates an S3-compatible Connector that takes environment-variable references only", async ({ page }) => {
  let posted: Record<string, unknown> = {};
  await openConnectors(page, [], (a) =>
    a.respondWith("POST", "/api/v1/connectors", (_url, req) => {
      posted = req.body as Record<string, unknown>;
      return connector({ name: "bucket-docs", source_type: "s3" });
    }, 201),
  );
  await page.getByRole("button", { name: "New Connector" }).first().click();
  const dialog = page.getByRole("dialog", { name: "New Connector" });
  await dialog.getByLabel("Name").fill("bucket-docs");
  await dialog.getByRole("radio", { name: /S3-compatible bucket/ }).check();
  await expect(dialog).toContainText("Credentials are references, not secrets");
  await dialog.getByLabel("Endpoint URL").fill("https://minio.internal:9000");
  await dialog.getByLabel("Bucket", { exact: true }).fill("docs");
  await dialog.getByLabel("Prefix (optional)").fill("hr/");

  const create = dialog.getByRole("button", { name: "Create Connector" });
  await dialog.getByLabel("Access key reference").fill("AKIAREALLOOKINGKEY");
  await dialog.getByLabel("Secret key reference").fill("env:MINIO_SECRET_KEY");
  await expect(dialog).toContainText("This is not an environment-variable reference");
  await expect(create).toBeDisabled();
  await captureAll(page, "connectors-new-s3");

  await dialog.getByLabel("Access key reference").fill("env:MINIO_ACCESS_KEY");
  await expect(create).toBeEnabled();
  await create.click();
  await expect.poll(() => posted).toMatchObject({
    name: "bucket-docs",
    source_type: "s3",
    endpoint_url: "https://minio.internal:9000",
    bucket: "docs",
    prefix: "hr/",
    access_key_ref: "env:MINIO_ACCESS_KEY",
    secret_key_ref: "env:MINIO_SECRET_KEY",
  });
  expect(posted).not.toHaveProperty("path");
});

test("shows the API's refusal in the form", async ({ page }) => {
  await openConnectors(page, [], (a) => a.respond("POST", "/api/v1/connectors", { detail: "path is outside the allowed mount root" }, 422));
  await page.getByRole("button", { name: "New Connector" }).first().click();
  const dialog = page.getByRole("dialog", { name: "New Connector" });
  await dialog.getByLabel("Name").fill("hr-share");
  await dialog.getByLabel("Folder path").fill("/etc");
  await dialog.getByRole("button", { name: "Create Connector" }).click();
  await expect(dialog.getByRole("alert")).toContainText("outside the allowed mount root");
});

test("edits a Connector's source and pauses it", async ({ page }) => {
  let patched: Record<string, unknown> = {};
  await openConnectors(page, [connector()], (a) =>
    a.respondWith("PATCH", "/api/v1/connectors/:id", (_url, req) => {
      patched = req.body as Record<string, unknown>;
      return connector({ enabled: false });
    }),
  );
  await page.getByRole("button", { name: "Edit policies-share" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit policies-share" });
  await expect(dialog.getByLabel("Folder path")).toHaveValue("/mnt/sources/hr");
  await dialog.getByLabel("Folder path").fill("/mnt/sources/hr-2026");
  await dialog.getByRole("switch", { name: "Enabled" }).click();
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect.poll(() => patched).toEqual({ enabled: false, path: "/mnt/sources/hr-2026" });
});

test("warns that Documents may be removed before deleting a Connector", async ({ page }) => {
  let deleted = "";
  await openConnectors(page, [connector()], (a) =>
    a.intercept("DELETE", "/api/v1/connectors/:id", (route, url) => {
      deleted = url.pathname;
      return route.fulfill(NO_CONTENT);
    }),
  );
  await page.getByRole("button", { name: "Delete policies-share" }).click();
  const dialog = page.getByRole("dialog", { name: "Delete policies-share?" });
  await expect(dialog).toContainText("Documents it added may be removed");
  await dialog.getByRole("button", { name: "Delete Connector" }).click();
  await expect.poll(() => deleted).toBe("/api/v1/connectors/conn-1");
});

test("says what failed when Connectors cannot be loaded", async ({ page }) => {
  await openConnectors(page, [], (a) => a.respond("GET", "/api/v1/connectors", { detail: "Connectors unavailable" }, 500));
  await expect(page.getByRole("alert").filter({ hasText: "Connectors unavailable" })).toBeVisible();
});
