import type { Page, Route } from "@playwright/test";

// The API stub: answers every call the UI makes at the HTTP boundary, so the production build runs
// with no backend. A test says who is signed in and, where the default fixtures don't suit, adds or
// overrides an endpoint. Anything the UI asks for that nobody answered is a 404 and is recorded in
// `unhandled`, so a missing fixture fails loudly instead of rendering an empty page.

export const API_ORIGIN = "http://api.stub.test";

export type CallerKind = "end-user" | "admin" | "auditor" | "no-role" | "signed-out";

const PRINCIPALS: Record<Exclude<CallerKind, "signed-out">, { subject: string; roles: string[]; groups: string[] }> = {
  "end-user": { subject: "maria.santos@example.test", roles: ["querier"], groups: ["finance"] },
  admin: { subject: "sam.okafor@example.test", roles: ["admin"], groups: ["platform"] },
  auditor: { subject: "li.wei@example.test", roles: ["auditor"], groups: ["compliance"] },
  "no-role": { subject: "pat.guest@example.test", roles: [], groups: [] },
};

export interface Citation {
  index: number;
  document_id: string | null;
  title: string;
  section_title: string | null;
  chunk_id: string | null;
  passage: string;
  score: number;
  attachment_id?: string | null;
}

export interface StreamedAnswer {
  /** Answer text, sent as a few deltas. */
  text: string;
  citations?: Citation[];
  /** Sent instead of a clean finish: the stream carries an error event. */
  error?: string;
}

export const APPLICATIONS = [
  { id: "app-policies", name: "HR policies", slug: "hr-policies", description: "Leave, expenses and conduct.", citations_enabled: true },
  { id: "app-finance", name: "Finance desk", slug: "finance-desk", description: "Budgets and approvals.", citations_enabled: true },
];

export const SAMPLE_CITATION: Citation = {
  index: 1,
  document_id: "doc-leave",
  title: "Leave policy",
  section_title: "Annual leave",
  chunk_id: "chunk-1",
  passage: "Full-time employees receive 25 days of paid annual leave per calendar year.",
  score: 0.82,
};

type Handler = (route: Route, url: URL) => unknown | Promise<unknown>;
type Build = (url: URL, request: { body: unknown }) => unknown;

export interface ApiStub {
  /** Calls nobody answered, as "GET /path". */
  unhandled: string[];
  /** Every request that reached the API, as "GET /path". */
  calls: string[];
  /** Answer an endpoint with fixed JSON. `:id` in the path matches one path segment. */
  respond(method: string, path: string, body: unknown, status?: number): void;
  /** Answer an endpoint with JSON built from the request. */
  respondWith(method: string, path: string, build: Build, status?: number): void;
  /** The answer streamed back for the next question asked in a conversation. */
  streamAnswer(answer: StreamedAnswer): void;
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: "application/json",
    headers: { "access-control-allow-origin": "*" },
    body: body === undefined ? "" : JSON.stringify(body),
  });

function sse(answer: StreamedAnswer): string {
  const frames: unknown[] = [];
  const words = answer.text.split(/(?<=\s)/);
  const chunk = Math.max(1, Math.ceil(words.length / 4));
  for (let i = 0; i < words.length; i += chunk) frames.push({ type: "delta", content: words.slice(i, i + chunk).join("") });
  if (answer.citations) frames.push({ type: "citations", citations: answer.citations });
  if (answer.error) frames.push({ type: "error", message: answer.error });
  frames.push({ type: "done", message_id: answer.error ? null : "msg-1" });
  return frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join("");
}

export async function stubApi(page: Page, caller: CallerKind): Promise<ApiStub> {
  const routes = new Map<string, Handler>();
  const stub: ApiStub = {
    unhandled: [],
    calls: [],
    respond(method, path, body, status = 200) {
      stub.respondWith(method, path, () => body, status);
    },
    respondWith(method, path, build, status = 200) {
      routes.set(`${method} ${path}`, async (route, url) => {
        let posted: unknown;
        try {
          posted = route.request().postDataJSON();
        } catch {
          posted = undefined;
        }
        return json(route, build(url, { body: posted }), status);
      });
    },
    streamAnswer(answer) {
      routes.set("POST /api/v1/chat/conversations/:id/messages", (route) =>
        route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          headers: { "access-control-allow-origin": "*" },
          body: sse(answer),
        }),
      );
    },
  };

  // Defaults: enough for each role's landing surface to render.
  if (caller === "signed-out") {
    stub.respond("GET", "/api/v1/auth/me", { detail: "Not authenticated" }, 401);
  } else {
    const p = PRINCIPALS[caller];
    stub.respond("GET", "/api/v1/auth/me", {
      ...p,
      raw_roles: p.roles,
      auth_method: "jwt",
      api_key_id: null,
      application_id: null,
    });
  }
  const now = new Date().toISOString();
  stub.respond("GET", "/api/v1/chat/applications", APPLICATIONS);
  stub.respond("GET", "/api/v1/chat/conversations", []);
  stub.respond("GET", "/api/v1/chat/config", { images_enabled: false });
  stub.respondWith("POST", "/api/v1/chat/conversations", (_url, req) => ({
    id: "conv-1",
    application_id: (req.body as { application_id?: string })?.application_id ?? APPLICATIONS[0].id,
    title: "New conversation",
    created_at: now,
    last_activity_at: now,
    message_count: 0,
  }));
  stub.respond("GET", "/api/v1/chat/conversations/:id/attachments", []);
  stub.streamAnswer({ text: "You get 25 days of annual leave [1].", citations: [SAMPLE_CITATION] });

  stub.respond("GET", "/api/v1/system/stats", {
    knowledge_bases: 2,
    documents: 14,
    applications: 2,
    providers: 1,
    chunks: 380,
    ingestion_jobs: 14,
    ingestion_jobs_by_status: { completed: 14 },
  });
  stub.respond("GET", "/api/v1/system/health", {
    status: "healthy",
    components: [{ name: "postgres", status: "healthy", latency_ms: 3, version: "16", detail: null }],
  });
  stub.respond("GET", "/api/v1/system/gpu", { available: false, cuda_version: null, driver_version: null, gpus: [], message: "No GPU" });
  stub.respond("GET", "/api/v1/ingestion-jobs", []);
  stub.respond("GET", "/api/v1/audit/status", { level: "metadata", fail_mode: "log", retention_days: 90 });
  stub.respond("GET", "/api/v1/audit/events", { items: [], next_cursor: null });

  const find = (method: string, path: string): Handler | undefined => {
    const exact = routes.get(`${method} ${path}`);
    if (exact) return exact;
    for (const [key, handler] of routes) {
      const [m, pattern] = key.split(" ");
      if (m !== method || !pattern.includes(":")) continue;
      const re = new RegExp(`^${pattern.replace(/:[^/]+/g, "[^/]+")}$`);
      if (re.test(path)) return handler;
    }
    return undefined;
  };

  await page.route(`${API_ORIGIN}/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") {
      return route.fulfill({
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
          "access-control-allow-headers": "authorization,content-type",
        },
      });
    }
    const label = `${request.method()} ${url.pathname}`;
    stub.calls.push(label);
    const handler = find(request.method(), url.pathname);
    if (!handler) {
      stub.unhandled.push(label);
      return json(route, { detail: `no stub for ${label}` }, 404);
    }
    return handler(route, url);
  });

  return stub;
}
