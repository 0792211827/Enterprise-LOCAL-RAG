import { expect, test, type Page } from "@playwright/test";
import { stubApi, type ApiStub, type CallerKind } from "./support/api-stub";
import { captureAll, VIEWPORTS } from "./support/screenshots";

const NOW = "2026-11-14T09:30:00Z";
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

async function open(page: Page, caller: CallerKind, path: string, setup?: (api: ApiStub) => void) {
  const api = await stubApi(page, caller);
  setup?.(api);
  await page.goto(path);
  return api;
}

const FILE_HEADERS = { "access-control-allow-origin": "*", "access-control-expose-headers": "content-disposition" };

test.describe("Feedback", () => {
  const COUNTS = {
    conversations: 41,
    messages: 212,
    by_application: [
      { application_id: "app-policies", application_name: "HR policies", conversations: 30, messages: 160 },
      { application_id: "app-finance", application_name: "Finance desk", conversations: 11, messages: 52 },
    ],
  };
  const SUMMARY = [
    { application_id: "app-policies", application_name: "HR policies", up: 18, down: 2 },
    { application_id: "app-finance", application_name: "Finance desk", up: 3, down: 7 },
  ];
  const RATINGS = [
    { message_id: "msg-aaaa1111", application_id: "app-finance", rating: "down", comment: "Quoted last year's budget.", shared: true, rated_at: NOW },
    { message_id: "msg-bbbb2222", application_id: "app-policies", rating: "up", comment: null, shared: false, rated_at: NOW },
  ];
  const SHARED = [
    {
      message_id: "msg-aaaa1111",
      application_id: "app-finance",
      rating: "down",
      comment: "Quoted last year's budget.",
      question: "What is the travel budget this year?",
      answer: "The travel budget is 40,000 [1].",
      sources: ["Budget 2025.pdf"],
    },
  ];

  const seed = (api: ApiStub) => {
    api.respond("GET", "/api/v1/chat-admin/conversation-counts", COUNTS);
    api.respond("GET", "/api/v1/chat-admin/ratings/summary", SUMMARY);
    api.respondWith("GET", "/api/v1/chat-admin/ratings", (url) =>
      RATINGS.filter((r) => !url.searchParams.get("application_id") || r.application_id === url.searchParams.get("application_id")),
    );
    api.respondWith("GET", "/api/v1/chat-admin/shared-exchanges", (url) =>
      SHARED.filter((r) => !url.searchParams.get("application_id") || r.application_id === url.searchParams.get("application_id")),
    );
  };

  test("summarises Ratings per Application, weakest first, and says what Admins cannot see", async ({ page }) => {
    await open(page, "admin", "/feedback", seed);
    await expect(page.getByText("No Admin can read a Conversation beyond the exchanges shared with them.")).toBeVisible();
    const rows = page.getByRole("table", { name: /Ratings per Application/ }).getByRole("row");
    await expect(rows.nth(1)).toContainText("Finance desk");
    await expect(rows.nth(1)).toContainText("30%");
    await expect(rows.nth(2)).toContainText("HR policies");
    await expect(rows.nth(2)).toContainText("90%");
    const ratings = page.getByRole("table", { name: "Ratings", exact: true });
    await expect(ratings).toContainText("Quoted last year's budget.");
    await expect(ratings).toContainText("Not helpful");
    await expect(page.getByRole("list", { name: "Shared exchanges" })).toContainText("What is the travel budget this year?");
    await captureAll(page, "feedback");
  });

  test("scopes Ratings and Shared exchanges to one Application", async ({ page }) => {
    await open(page, "admin", "/feedback", seed);
    await page.getByRole("button", { name: "Show only HR policies" }).click();
    await expect(page.getByLabel("Application", { exact: true })).toHaveValue("app-policies");
    await expect(page.getByText("Nothing shared")).toBeVisible();
    await expect(page.getByRole("table", { name: "Ratings", exact: true })).not.toContainText("Quoted last year's budget.");
  });

  test("exports one Application's Shared exchanges as Golden set candidates", async ({ page }) => {
    let asked = "";
    await open(page, "admin", "/feedback", (api) => {
      seed(api);
      api.intercept("GET", "/api/v1/chat-admin/shared-exchanges/export", (route, url) => {
        asked = url.searchParams.get("application_id") ?? "";
        return route.fulfill({
          status: 200,
          headers: { ...FILE_HEADERS, "content-disposition": 'attachment; filename="finance-desk-golden-candidates.yaml"' },
          contentType: "application/x-yaml",
          body: "- question: What is the travel budget this year?\n",
        });
      });
    });
    const exportButton = page.getByRole("button", { name: "Export as Golden set candidates" });
    await expect(exportButton).toBeDisabled();
    await expect(page.getByText("Choose one Application above to export its exchanges.")).toBeVisible();
    await page.getByLabel("Application", { exact: true }).selectOption("app-finance");
    await expect(exportButton).toBeEnabled();
    const download = page.waitForEvent("download");
    await exportButton.click();
    expect((await download).suggestedFilename()).toBe("finance-desk-golden-candidates.yaml");
    expect(asked).toBe("app-finance");
  });

  test("shows designed empty states", async ({ page }) => {
    await open(page, "admin", "/feedback", (api) => {
      api.respond("GET", "/api/v1/chat-admin/conversation-counts", { conversations: 0, messages: 0, by_application: [] });
      api.respond("GET", "/api/v1/chat-admin/ratings", []);
      api.respond("GET", "/api/v1/chat-admin/shared-exchanges", []);
    });
    await expect(page.getByText("No Ratings yet")).toBeVisible();
    await expect(page.getByText("Nothing shared")).toBeVisible();
    await captureAll(page, "feedback-empty");
  });

  test("names what failed and retries", async ({ page }) => {
    let healthy = false;
    await open(page, "admin", "/feedback", (api) => {
      seed(api);
      api.intercept("GET", "/api/v1/chat-admin/shared-exchanges", (route) =>
        healthy
          ? route.fulfill({ status: 200, contentType: "application/json", headers: FILE_HEADERS, body: JSON.stringify(SHARED) })
          : route.fulfill({ status: 500, contentType: "application/json", headers: FILE_HEADERS, body: JSON.stringify({ detail: "The feedback store is down" }) }),
      );
    });
    const alert = page.getByRole("alert").filter({ hasText: "Could not load Shared exchanges" });
    await expect(alert).toContainText("The feedback store is down");
    await captureAll(page, "feedback-error");
    healthy = true;
    await alert.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("list", { name: "Shared exchanges" })).toBeVisible();
  });

  test("keeps tables inside their container on a phone", async ({ page }) => {
    await open(page, "admin", "/feedback", seed);
    await page.setViewportSize(VIEWPORTS.phone);
    await expect(page.getByRole("table", { name: "Ratings", exact: true })).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
  });
});

test.describe("Audit Log", () => {
  const event = (over: Record<string, unknown> = {}) => ({
    id: "evt-1",
    occurred_at: NOW,
    request_id: "req-0001",
    action: "query.answer",
    outcome: "ok",
    status_code: 200,
    actor_subject: "maria.santos@example.test",
    actor_roles: ["querier"],
    actor_groups: ["finance"],
    auth_method: "jwt",
    api_key_id: null,
    application_id: "app-policies",
    source_ip: "10.0.0.7",
    user_agent: "Mozilla/5.0",
    http_method: "POST",
    route: "/api/v1/chat/conversations/c1/messages",
    resource_type: null,
    resource_id: null,
    requested_kb_ids: ["kb-hr"],
    permitted_kb_ids: ["kb-hr"],
    dropped_kb_ids: [],
    retrieved: [{ chunk_id: "chunk-abcdef123", document_id: "doc-leave-1234", knowledge_base_id: "kb-hr", score: 0.8123 }],
    cache_hit: false,
    model: "llama3.2:1b",
    query_text: "How many days of leave do I get?",
    answer_text: "25 days.",
    query_sha256: "a".repeat(64),
    answer_sha256: "b".repeat(64),
    latency_ms: 840,
    error_code: null,
    details: null,
    ...over,
  });
  const EVENTS = [
    event(),
    event({ id: "evt-2", action: "admin.mutation", outcome: "ok", actor_subject: "sam.okafor@example.test", http_method: "PATCH", route: "/api/v1/applications/app-policies", retrieved: null, details: { changed: ["name"] } }),
    event({ id: "evt-3", action: "access.denied", outcome: "denied", status_code: 403, actor_subject: "pat.guest@example.test", http_method: "GET", route: "/api/v1/applications", retrieved: null }),
  ];

  const seed = (api: ApiStub) => {
    api.respondWith("GET", "/api/v1/audit/events", (url) => {
      const action = url.searchParams.get("action");
      const items = EVENTS.filter((e) => !action || e.action === action);
      return { items, next_cursor: null };
    });
  };

  test("lists events as a dense table and filters them", async ({ page }) => {
    const api = await open(page, "auditor", "/audit", seed);
    const table = page.getByRole("table", { name: /Audit events/ });
    await expect(table.getByRole("row")).toHaveCount(4);
    await page.getByLabel("Action", { exact: true }).selectOption("access.denied");
    await page.getByRole("button", { name: "Apply filters" }).click();
    await expect(table.getByRole("row")).toHaveCount(2);
    await expect(table).toContainText("pat.guest@example.test");
    expect(api.calls.filter((c) => c.endsWith("/audit/events")).length).toBeGreaterThan(1);
    await page.getByRole("button", { name: "Clear" }).click();
    await expect(table.getByRole("row")).toHaveCount(4);
  });

  test("opens one event in a drawer showing all of its fields", async ({ page }) => {
    await open(page, "auditor", "/audit", seed);
    await page.getByRole("button", { name: /Open event from/ }).first().click();
    const drawer = page.getByRole("dialog", { name: "Audit event" });
    for (const text of ["req-0001", "maria.santos@example.test", "finance", "10.0.0.7", "Mozilla/5.0", "llama3.2:1b", "How many days of leave do I get?", "25 days.", "0.812", "840 ms", "a".repeat(64)]) {
      await expect(drawer).toContainText(text);
    }
    await captureAll(page, "audit-event");
    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
  });

  test("shows what was changed for an Admin change", async ({ page }) => {
    await open(page, "auditor", "/audit", seed);
    await page.getByRole("row", { name: /Admin change/ }).click();
    const drawer = page.getByRole("dialog", { name: "Audit event" });
    await expect(drawer).toContainText("PATCH /api/v1/applications/app-policies");
    await expect(drawer).toContainText('"changed"');
  });

  test("exports CSV using the applied filters, not the unapplied ones", async ({ page }) => {
    let asked: URLSearchParams | null = null;
    await open(page, "auditor", "/audit", (api) => {
      seed(api);
      api.intercept("GET", "/api/v1/audit/export", (route, url) => {
        asked = url.searchParams;
        return route.fulfill({
          status: 200,
          headers: { ...FILE_HEADERS, "content-disposition": 'attachment; filename="audit-events.csv"' },
          contentType: "text/csv",
          body: "id,action\nevt-3,access.denied\n",
        });
      });
    });
    await page.getByLabel("Action", { exact: true }).selectOption("access.denied");
    await page.getByLabel("Actor", { exact: true }).fill("typed but never applied");
    await page.getByRole("button", { name: "Apply filters" }).click();
    await page.getByLabel("Actor", { exact: true }).fill("someone else");
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export CSV" }).click();
    expect((await download).suggestedFilename()).toBe("audit-events.csv");
    expect(asked!.get("format")).toBe("csv");
    expect(asked!.get("action")).toBe("access.denied");
    expect(asked!.get("actor")).toBe("typed but never applied");
  });

  test("pages through older events", async ({ page }) => {
    await open(page, "auditor", "/audit", (api) => {
      api.respondWith("GET", "/api/v1/audit/events", (url) =>
        url.searchParams.get("cursor") ? { items: [EVENTS[2]], next_cursor: null } : { items: [EVENTS[0]], next_cursor: "next" },
      );
    });
    const table = page.getByRole("table", { name: /Audit events/ });
    await expect(table.getByRole("row")).toHaveCount(2);
    await page.getByRole("button", { name: "Load older events" }).click();
    await expect(table.getByRole("row")).toHaveCount(3);
    await expect(page.getByRole("button", { name: "Load older events" })).toHaveCount(0);
  });

  test("distinguishes an empty log from filters that match nothing", async ({ page }) => {
    await open(page, "auditor", "/audit");
    await expect(page.getByText("No audit events yet")).toBeVisible();
    await captureAll(page, "audit-empty");
    await page.getByLabel("Actor", { exact: true }).fill("nobody");
    await page.getByRole("button", { name: "Apply filters" }).click();
    await expect(page.getByText("No events match these filters")).toBeVisible();
  });

  test("says audit logging is off", async ({ page }) => {
    await open(page, "auditor", "/audit", (api) => api.respond("GET", "/api/v1/audit/status", { level: "off", fail_mode: "log", retention_days: 0 }));
    await expect(page.getByText("Audit logging is off")).toBeVisible();
  });

  test("names what failed and retries", async ({ page }) => {
    let healthy = false;
    await open(page, "auditor", "/audit", (api) => {
      api.intercept("GET", "/api/v1/audit/events", (route) =>
        healthy
          ? route.fulfill({ status: 200, contentType: "application/json", headers: FILE_HEADERS, body: JSON.stringify({ items: EVENTS, next_cursor: null }) })
          : route.fulfill({ status: 500, contentType: "application/json", headers: FILE_HEADERS, body: JSON.stringify({ detail: "The audit store is unreachable" }) }),
      );
    });
    const alert = page.getByRole("alert").filter({ hasText: "Could not load the Audit Log" });
    await expect(alert).toContainText("The audit store is unreachable");
    healthy = true;
    await alert.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("table", { name: /Audit events/ })).toBeVisible();
  });

  test("scrolls the table inside its container at phone width", async ({ page }) => {
    await open(page, "auditor", "/audit", seed);
    await page.setViewportSize(VIEWPORTS.phone);
    await expect(page.getByRole("table", { name: /Audit events/ })).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
    await captureAll(page, "audit-log");
  });

  test("an Admin without the auditor role never reaches it", async ({ page }) => {
    await open(page, "admin", "/audit");
    await expect(page).toHaveURL(/\/$/);
  });
});
