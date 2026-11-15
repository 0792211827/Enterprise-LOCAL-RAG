import { expect, test, type Page } from "@playwright/test";
import { apiKey, application, kb, security } from "./support/admin-fixtures";
import { stubApi, type ApiStub } from "./support/api-stub";
import { captureAll } from "./support/screenshots";

const NO_CONTENT = { status: 204, headers: { "access-control-allow-origin": "*" } };

async function openAdmin(page: Page, path: string, setup?: (api: ApiStub) => void) {
  const api = await stubApi(page, "admin");
  api.respond("GET", "/api/v1/applications", [application(), application({ id: "app-empty", name: "Scratchpad", slug: "scratchpad", knowledge_bases: [] })]);
  api.respond("GET", "/api/v1/applications/:id", application());
  api.respond("GET", "/api/v1/applications/:id/api-keys", [apiKey()]);
  api.respond("GET", "/api/v1/knowledge-bases", [kb(), kb({ id: "kb-fin", name: "Finance rules", slug: "finance-rules", document_count: 5, chunk_count: 210 })]);
  api.respond("GET", "/api/v1/providers", []);
  api.respond("GET", "/api/v1/system/security", security());
  setup?.(api);
  await page.goto(path);
  return api;
}

test.describe("the Applications list", () => {
  test("shows each Application's Knowledge Bases, model and status", async ({ page }) => {
    await openAdmin(page, "/applications");
    const table = page.getByRole("table", { name: "Applications" });
    const row = table.getByRole("row", { name: /HR policies/ });
    await expect(row).toContainText("HR handbook");
    await expect(row).toContainText("llama3.2:1b");
    await expect(row).toContainText("Active");
    await expect(table.getByRole("row", { name: /Scratchpad/ })).toContainText("No Knowledge Base");
    await captureAll(page, "applications-list");
  });

  test("names the Application in the delete confirmation and deletes it", async ({ page }) => {
    let deleted = "";
    await openAdmin(page, "/applications", (a) =>
      a.intercept("DELETE", "/api/v1/applications/:id", (route, url) => {
        deleted = url.pathname;
        return route.fulfill(NO_CONTENT);
      }),
    );
    await page.getByRole("button", { name: "Delete HR policies" }).click();
    const dialog = page.getByRole("dialog", { name: "Delete HR policies?" });
    await expect(dialog).toContainText("HR policies");
    await dialog.getByRole("button", { name: "Delete Application" }).click();
    await expect.poll(() => deleted).toBe("/api/v1/applications/app-policies");
  });

  test("says so when there are none", async ({ page }) => {
    await openAdmin(page, "/applications", (a) => a.respond("GET", "/api/v1/applications", []));
    await expect(page.getByText("No Applications yet")).toBeVisible();
    await expect(page.getByRole("link", { name: "New Application" }).first()).toBeVisible();
  });

  test("says what failed and offers a retry", async ({ page }) => {
    await openAdmin(page, "/applications", (a) => a.respond("GET", "/api/v1/applications", { detail: "Database is down" }, 500));
    await expect(page.getByRole("alert").filter({ hasText: "Database is down" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  });
});

test.describe("creating an Application", () => {
  test("on one page, with a live summary, and lands on its API key", async ({ page }) => {
    let posted: Record<string, unknown> = {};
    const api = await openAdmin(page, "/applications/new");
    api.respondWith(
      "POST",
      "/api/v1/applications",
      (_url, req) => {
        posted = req.body as Record<string, unknown>;
        return { ...application({ id: "app-new", name: "Finance desk", slug: "finance-desk" }), api_key: "rag_live_TESTKEY_once_only" };
      },
      201,
    );
    api.respond("GET", "/api/v1/applications/app-new", application({ id: "app-new", name: "Finance desk", slug: "finance-desk" }));
    api.respond("GET", "/api/v1/applications/app-new/api-keys", [apiKey({ application_id: "app-new" })]);

    const summary = page.getByRole("complementary", { name: "Summary" });
    await expect(summary.getByRole("button", { name: "Create Application" })).toBeDisabled();
    await expect(summary).toContainText("has no Knowledge Base yet");

    await page.getByLabel("Name", { exact: true }).fill("Finance desk");
    await expect(summary).toContainText("finance-desk");
    await page.getByRole("checkbox", { name: /Finance rules/ }).check();
    await expect(summary).toContainText("5 Documents in 1 Knowledge Base");
    await page.getByLabel("Passages used (top K)").fill("12");
    await expect(summary).toContainText("top 12");
    await page.getByLabel("System prompt (optional)").fill("Answer briefly.");
    await expect(summary).toContainText("Custom");
    await captureAll(page, "applications-new");

    await summary.getByRole("button", { name: "Create Application" }).click();
    await expect(page.getByTestId("new-api-key")).toHaveText("rag_live_TESTKEY_once_only");
    await expect(page.getByText("It is shown only once")).toBeVisible();
    expect(posted).toMatchObject({ name: "Finance desk", knowledge_base_ids: ["kb-fin"], top_k: 12, system_prompt: "Answer briefly.", retrieval_mode: "hybrid" });
  });

  test("refuses a name that is already taken", async ({ page }) => {
    await openAdmin(page, "/applications/new");
    await page.getByLabel("Name", { exact: true }).fill("HR policies");
    await expect(page.getByRole("alert").filter({ hasText: "already exists" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Create Application" })).toBeDisabled();
  });
});

test.describe("an Application's detail page", () => {
  test("has Overview, Knowledge Bases, Retrieval, Test and API access tabs", async ({ page }) => {
    await openAdmin(page, "/applications/app-policies");
    await expect(page.getByRole("heading", { name: "HR policies" })).toBeVisible();
    for (const name of ["Overview", "Knowledge Bases", "Retrieval", "Test", "API access"]) {
      await expect(page.getByRole("tab", { name })).toBeVisible();
    }
    await expect(page.getByRole("heading", { name: "Readiness" })).toBeVisible();
    await captureAll(page, "application-overview");

    await page.getByRole("tab", { name: "Retrieval" }).click();
    await expect(page.getByText("Hybrid (keyword and meaning)")).toBeVisible();
    await captureAll(page, "application-retrieval");
  });

  test("saves a changed name and attached Knowledge Bases", async ({ page }) => {
    const patches: Record<string, unknown>[] = [];
    await openAdmin(page, "/applications/app-policies", (a) =>
      a.respondWith("PATCH", "/api/v1/applications/:id", (_url, req) => {
        patches.push(req.body as Record<string, unknown>);
        return application();
      }),
    );
    await page.getByLabel("Name", { exact: true }).fill("People policies");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Application saved")).toBeVisible();
    expect(patches[0]).toMatchObject({ name: "People policies" });

    await page.getByRole("tab", { name: "Knowledge Bases" }).click();
    await page.getByRole("checkbox", { name: /Finance rules/ }).check();
    await page.getByRole("button", { name: "Save Knowledge Bases" }).click();
    await expect.poll(() => patches.length).toBe(2);
    expect(patches[1]).toEqual({ knowledge_base_ids: ["kb-hr", "kb-fin"] });
  });

  test("the Test tab answers a question and shows Citations and retrieval detail", async ({ page }) => {
    await openAdmin(page, "/applications/app-policies?tab=test", (a) =>
      a.respond("POST", "/api/v1/applications/:id/ask", {
        query: "How much leave?",
        answer: "You get **25 days** of leave.",
        sources: [{ document_id: "doc-leave", document_title: "Leave policy", chunk_text: "Full-time employees receive 25 days.", score: 0.8123, section_title: "Annual leave", retrieval_method: "hybrid" }],
        search_mode: "hybrid",
        chunks_used: 1,
      }),
    );
    await page.getByLabel("Test question").fill("How much leave?");
    await page.getByRole("button", { name: "Ask" }).click();
    await expect(page.getByText("25 days", { exact: true })).toBeVisible();
    const detail = page.getByRole("list", { name: "Citations" });
    await expect(detail).toContainText("Leave policy");
    await expect(detail).toContainText("Annual leave");
    await expect(detail).toContainText("score 0.812");
    await expect(page.getByText(/Retrieval detail: 1 source, hybrid search/)).toBeVisible();
    await captureAll(page, "application-test");
  });

  test("the Test tab shows a plain error when the Application cannot answer", async ({ page }) => {
    await openAdmin(page, "/applications/app-policies?tab=test", (a) => a.respond("POST", "/api/v1/applications/:id/ask", { detail: "The model is not answering." }, 503));
    await page.getByLabel("Test question").fill("Anything");
    await page.getByRole("button", { name: "Ask" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "The model is not answering." })).toBeVisible();
  });

  test("says so when the Application cannot be loaded", async ({ page }) => {
    await openAdmin(page, "/applications/missing", (a) => a.respond("GET", "/api/v1/applications/:id", { detail: "Application not found" }, 404));
    await expect(page.getByRole("alert").filter({ hasText: "Application not found" })).toBeVisible();
  });
});

test.describe("API access", () => {
  test("creates a key, shows it once with a warning and a copy control, then never again", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    let named: unknown;
    await openAdmin(page, "/applications/app-policies?tab=api", (a) =>
      a.respondWith(
        "POST",
        "/api/v1/applications/:id/api-keys",
        (_url, req) => {
          named = req.body;
          return { ...apiKey({ id: "key-2", name: "Wiki bot", key_last4: "77aa" }), key: "rag_live_wikibot_SECRET" };
        },
        201,
      ),
    );
    await expect(page.getByRole("table", { name: "Active API keys" })).toContainText("rag_live_ab…9f2c");

    await page.getByLabel("Key name (optional)").fill("Wiki bot");
    await page.getByRole("button", { name: "Create key" }).click();
    await expect(page.getByTestId("new-api-key")).toHaveText("rag_live_wikibot_SECRET");
    await expect(page.getByText("Copy your new API key now. It is shown only once.")).toBeVisible();
    expect(named).toEqual({ name: "Wiki bot" });

    await page.getByRole("button", { name: "Copy key" }).click();
    await expect(page.getByText("Copied").first()).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("rag_live_wikibot_SECRET");
    await captureAll(page, "application-api-access");

    await page.getByRole("button", { name: "I have saved it" }).click();
    await expect(page.getByText("rag_live_wikibot_SECRET")).toHaveCount(0);
    await page.reload();
    await expect(page.getByText("rag_live_wikibot_SECRET")).toHaveCount(0);
  });

  test("offers code samples for the OpenAI-compatible endpoint", async ({ page }) => {
    await openAdmin(page, "/applications/app-policies?tab=api");
    await expect(page.getByText("/v1/chat/completions").first()).toBeVisible();
    await expect(page.locator("pre").first()).toContainText('"model": "hr-policies"');
    await page.getByRole("tab", { name: "Python" }).click();
    await expect(page.locator("pre").first()).toContainText("from openai import OpenAI");
    await page.getByRole("tab", { name: "TypeScript" }).click();
    await expect(page.locator("pre").first()).toContainText('import OpenAI from "openai"');
  });

  test("rotates a key after confirming, and revokes one", async ({ page }) => {
    let revoked = "";
    await openAdmin(page, "/applications/app-policies?tab=api", (a) => {
      a.respond("POST", "/api/v1/applications/:id/api-keys/:id/rotate", { ...apiKey({ key_last4: "0001" }), key: "rag_live_ROTATED" });
      a.intercept("DELETE", "/api/v1/applications/:id/api-keys/:id", (route, url) => {
        revoked = url.pathname;
        return route.fulfill(NO_CONTENT);
      });
    });
    await page.getByRole("button", { name: /Rotate key rag_live_ab/ }).click();
    await expect(page.getByRole("dialog", { name: "Rotate this key?" })).toContainText("stops working at once");
    await page.getByRole("dialog").getByRole("button", { name: "Rotate key" }).click();
    await expect(page.getByTestId("new-api-key")).toHaveText("rag_live_ROTATED");

    await page.getByRole("button", { name: /Revoke key rag_live_ab/ }).click();
    await page.getByRole("dialog", { name: "Revoke this key?" }).getByRole("button", { name: "Revoke key" }).click();
    await expect.poll(() => revoked).toBe("/api/v1/applications/app-policies/api-keys/key-1");
  });

  test("says so when there are no keys", async ({ page }) => {
    await openAdmin(page, "/applications/app-policies?tab=api", (a) => a.respond("GET", "/api/v1/applications/:id/api-keys", []));
    await expect(page.getByText("No active keys")).toBeVisible();
  });

  test("says what failed when keys cannot be loaded", async ({ page }) => {
    await openAdmin(page, "/applications/app-policies?tab=api", (a) => a.respond("GET", "/api/v1/applications/:id/api-keys", { detail: "Keys unavailable" }, 500));
    await expect(page.getByRole("alert").filter({ hasText: "Keys unavailable" })).toBeVisible();
  });
});
