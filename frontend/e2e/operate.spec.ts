import { expect, test, type Page } from "@playwright/test";
import { provider, security } from "./support/admin-fixtures";
import { stubApi, type ApiStub } from "./support/api-stub";
import { captureAll, VIEWPORTS } from "./support/screenshots";

const NO_CONTENT = { status: 204, headers: { "access-control-allow-origin": "*" } };

async function open(page: Page, path: string, setup?: (api: ApiStub) => void) {
  const api = await stubApi(page, "admin");
  setup?.(api);
  await page.goto(path);
  return api;
}

const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test.describe("Models and Providers", () => {
  const withProviders = (a: ApiStub, list: unknown[] = [provider()]) => a.respond("GET", "/api/v1/providers", list);

  test("lists Inference endpoints with their address, model and status", async ({ page }) => {
    await open(page, "/providers", (a) =>
      withProviders(a, [
        provider(),
        provider({ id: "prov-2", name: "Embedder", kind: "embedding", provider_type: "huggingface", endpoint: null, model: "nomic-embed-text", enabled: false }),
      ]),
    );
    const table = page.getByRole("table", { name: "Inference endpoints" });
    const row = table.getByRole("row", { name: /Local Ollama/ });
    await expect(row).toContainText("http://ollama:11434");
    await expect(row).toContainText("llama3.2:1b");
    await expect(row).toContainText("Active");
    await expect(table.getByRole("row", { name: /Embedder/ })).toContainText("Disabled");
    await captureAll(page, "providers-list");
    await page.setViewportSize(VIEWPORTS.phone);
    expect(await noHorizontalOverflow(page)).toBe(true);
  });

  test("a successful test says it connected and how fast", async ({ page }) => {
    await open(page, "/providers", (a) => {
      withProviders(a);
      a.respond("POST", "/api/v1/providers/:id/test", { connected: true, model_available: true, generation_ok: null, embedding_ok: null, dimension: null, latency_ms: 41.6, message: "Connected", detail: null });
    });
    await page.getByRole("button", { name: "Test Local Ollama" }).click();
    const result = page.getByText("Connected", { exact: true });
    await expect(result).toBeVisible();
    await expect(page.getByText("42 ms")).toBeVisible();
    await captureAll(page, "providers-test-ok");
  });

  test("a failed test shows the specific error and what to check", async ({ page }) => {
    await open(page, "/providers", (a) => {
      withProviders(a);
      a.respond("POST", "/api/v1/providers/:id/test", {
        connected: false,
        model_available: null,
        generation_ok: null,
        embedding_ok: null,
        dimension: null,
        latency_ms: 3012,
        message: "Unable to connect to the configured endpoint.",
        detail: "Cannot connect to host ollama:11434 ssl:default [Connection refused]",
      });
    });
    await page.getByRole("button", { name: "Test Local Ollama" }).click();
    await expect(page.getByText("Unable to connect to the configured endpoint.")).toBeVisible();
    await expect(page.getByText("Cannot connect to host ollama:11434 ssl:default [Connection refused]")).toBeVisible();
    await expect(page.getByText("Check the address and key, then test again.")).toBeVisible();
    await captureAll(page, "providers-test-failed");
  });

  test("warns when the endpoint is reachable but does not serve the configured model", async ({ page }) => {
    await open(page, "/providers", (a) => {
      withProviders(a);
      a.respond("POST", "/api/v1/providers/:id/test", { connected: true, model_available: false, generation_ok: null, embedding_ok: null, dimension: null, latency_ms: 20, message: "Connected", detail: null });
    });
    await page.getByRole("button", { name: "Test Local Ollama" }).click();
    await expect(page.getByText("Connected, but the endpoint does not serve llama3.2:1b")).toBeVisible();
  });

  test("shows the API's refusal when a test cannot run", async ({ page }) => {
    await open(page, "/providers", (a) => {
      withProviders(a);
      a.respond("POST", "/api/v1/providers/:id/test", { detail: "Endpoint host 'ollama' is not in the allowed list" }, 400);
    });
    await page.getByRole("button", { name: "Test Local Ollama" }).click();
    await expect(page.getByText("The test could not run")).toBeVisible();
    await expect(page.getByText("Endpoint host 'ollama' is not in the allowed list")).toBeVisible();
  });

  test("lists the models an endpoint serves and notes whether the configured one is among them", async ({ page }) => {
    await open(page, "/providers", (a) => {
      withProviders(a);
      a.respond("GET", "/api/v1/providers/:id/models", { reachable: true, models: [{ id: "llama3.2:1b", name: "llama3.2:1b" }, { id: "qwen2.5:7b", name: "qwen2.5:7b" }], detail: null });
    });
    await page.getByRole("button", { name: "List models on Local Ollama" }).click();
    const drawer = page.getByRole("dialog", { name: "Models on Local Ollama" });
    await expect(drawer.getByRole("list", { name: "Models" })).toContainText("qwen2.5:7b");
    await expect(drawer).toContainText("2 models served");
    await expect(drawer).toContainText("is among them");
  });

  test("says when the endpoint could not list its models", async ({ page }) => {
    await open(page, "/providers", (a) => {
      withProviders(a);
      a.respond("GET", "/api/v1/providers/:id/models", { reachable: false, models: [], detail: "Connection refused" });
    });
    await page.getByRole("button", { name: "List models on Local Ollama" }).click();
    await expect(page.getByRole("dialog", { name: "Models on Local Ollama" })).toContainText("Connection refused");
  });

  test("adds an endpoint", async ({ page }) => {
    let posted: Record<string, unknown> = {};
    await open(page, "/providers", (a) => {
      withProviders(a, []);
      a.respondWith("POST", "/api/v1/providers", (_url, req) => {
        posted = req.body as Record<string, unknown>;
        return provider({ name: "Gateway" });
      }, 201);
    });
    await expect(page.getByText("No Inference endpoints yet")).toBeVisible();
    await page.getByRole("button", { name: "Add Inference endpoint" }).first().click();
    const dialog = page.getByRole("dialog", { name: "Add Inference endpoint" });
    const add = dialog.getByRole("button", { name: "Add endpoint" });
    await expect(add).toBeDisabled();
    await dialog.getByLabel("Name").fill("Gateway");
    await dialog.getByLabel("Type").selectOption("openai-compatible");
    await expect(dialog.getByLabel("Address")).toHaveValue("http://inference.internal:8000/v1");
    await dialog.getByLabel("Address").fill("inference.internal");
    await expect(dialog).toContainText("Start with http:// or https://");
    await expect(add).toBeDisabled();
    await dialog.getByLabel("Address").fill("https://inference.internal/v1");
    await dialog.getByLabel("Model").fill("qwen2.5:7b");
    await dialog.getByLabel("API key (optional)").fill("env:GATEWAY_KEY");
    await captureAll(page, "providers-new");
    await add.click();
    await expect.poll(() => posted).toEqual({
      name: "Gateway",
      kind: "llm",
      provider_type: "openai-compatible",
      endpoint: "https://inference.internal/v1",
      model: "qwen2.5:7b",
      enabled: true,
      api_key_ref: "env:GATEWAY_KEY",
    });
  });

  test("edits an endpoint without resending a key it did not change", async ({ page }) => {
    let patched: Record<string, unknown> = {};
    await open(page, "/providers", (a) => {
      withProviders(a, [provider({ api_key_ref: "(set)" })]);
      a.respondWith("PATCH", "/api/v1/providers/:id", (_url, req) => {
        patched = req.body as Record<string, unknown>;
        return provider({ name: "Primary Ollama" });
      });
    });
    await page.getByRole("button", { name: "Edit Local Ollama" }).click();
    const dialog = page.getByRole("dialog", { name: "Edit Local Ollama" });
    await expect(dialog).toContainText("cannot change once an endpoint exists");
    await expect(dialog.getByLabel("API key (optional)")).toHaveAttribute("placeholder", /Currently \(set\)/);
    await dialog.getByLabel("Name").fill("Primary Ollama");
    await dialog.getByRole("switch", { name: "Enabled" }).click();
    await dialog.getByRole("button", { name: "Save changes" }).click();
    await expect.poll(() => patched).toEqual({ name: "Primary Ollama", endpoint: "http://ollama:11434", model: "llama3.2:1b", enabled: false });
  });

  test("names what is removed before deleting an endpoint", async ({ page }) => {
    let deleted = "";
    await open(page, "/providers", (a) => {
      withProviders(a);
      a.intercept("DELETE", "/api/v1/providers/:id", (route, url) => {
        deleted = url.pathname;
        return route.fulfill(NO_CONTENT);
      });
    });
    await page.getByRole("button", { name: "Delete Local Ollama" }).click();
    const dialog = page.getByRole("dialog", { name: "Delete Local Ollama?" });
    await expect(dialog).toContainText("The model server itself is untouched");
    await dialog.getByRole("button", { name: "Delete endpoint" }).click();
    await expect.poll(() => deleted).toBe("/api/v1/providers/prov-1");
  });

  test("says what failed when endpoints cannot be loaded", async ({ page }) => {
    await open(page, "/providers", (a) => a.respond("GET", "/api/v1/providers", { detail: "Providers unavailable" }, 500));
    await expect(page.getByRole("alert").filter({ hasText: "Providers unavailable" })).toBeVisible();
  });
});

test.describe("System", () => {
  test("shows the health of each dependency, with the reason for an unhealthy one", async ({ page }) => {
    await open(page, "/system", (a) => {
      a.respond("GET", "/api/v1/system/security", security());
      a.respond("GET", "/api/v1/system/health", {
        status: "degraded",
        components: [
          { name: "postgres", status: "healthy", latency_ms: 3, version: "16.2", detail: null },
          { name: "opensearch", status: "unhealthy", latency_ms: null, version: null, detail: "Connection refused" },
        ],
      });
    });
    const table = page.getByRole("table", { name: "Dependency health" });
    await expect(table.getByRole("row", { name: /postgres/ })).toContainText("16.2");
    const bad = table.getByRole("row", { name: /opensearch/ });
    await expect(bad).toContainText("Unhealthy");
    await expect(bad).toContainText("Connection refused");
    await captureAll(page, "system");
  });

  test("states the security posture in plain words", async ({ page }) => {
    await open(page, "/system", (a) => a.respond("GET", "/api/v1/system/security", security({ user_token_header: "X-User-Token" })));
    const posture = page.locator("dl").filter({ hasText: "Authentication" });
    await expect(posture).toContainText("Sign-in with an identity provider");
    await expect(posture).toContainText("Required");
    await expect(posture).toContainText("Unreadable until granted");
    await expect(posture).toContainText("Admins need a Grant like anyone else");
    await expect(posture).toContainText("X-User-Token");
    await expect(page.getByText("Authentication is off", { exact: true })).toHaveCount(0);
  });

  test("warns loudly when authentication is off", async ({ page }) => {
    await open(page, "/system", (a) => a.respond("GET", "/api/v1/system/security", security({ auth_mode: "disabled", api_keys_enforced: false, ungranted_kb_access: "allow", admin_bypass_retrieval: true })));
    await expect(page.getByText("Authentication is off", { exact: true })).toBeVisible();
    await expect(page.getByText("Anyone who can reach the API can use every admin route")).toBeVisible();
    await expect(page.getByText("Admins read every Knowledge Base")).toBeVisible();
    await captureAll(page, "system-open");
  });

  test("says what failed when health or posture cannot be read", async ({ page }) => {
    await open(page, "/system", (a) => {
      a.respond("GET", "/api/v1/system/health", { detail: "Health unavailable" }, 500);
      a.respond("GET", "/api/v1/system/security", { detail: "Security unavailable" }, 500);
    });
    await expect(page.getByRole("alert").filter({ hasText: "Health unavailable" })).toBeVisible();
    await expect(page.getByRole("alert").filter({ hasText: "Security unavailable" })).toBeVisible();
  });

  test("scrolls the health table at tablet width", async ({ page }) => {
    await open(page, "/system", (a) => a.respond("GET", "/api/v1/system/security", security()));
    await page.setViewportSize(VIEWPORTS.phone);
    await expect(page.getByRole("table", { name: "Dependency health" })).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
  });
});
