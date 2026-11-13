import { FETCH_CREDENTIALS, authHeaders } from "@/lib/auth";
import { API_BASE, apiDownload, apiGet, apiSend, apiStreamEvents, apiUpload } from "./client";
import type {
  ApiKey,
  ApiKeyWithSecret,
  AuditEvent,
  AuditEventPage,
  AuditFilters,
  AuditStatus,
  Application,
  ApplicationAskResponse,
  ApplicationCreate,
  ApplicationRatings,
  ChatApplication,
  ChatCompletionResponse,
  ChatCounts,
  ChatDocument,
  ChatMessage,
  ChatStreamEvent,
  ConversationDetail,
  ConversationSummary,
  DashboardStats,
  DocumentItem,
  GPUResponse,
  IngestionJob,
  KnowledgeBase,
  KnowledgeBaseCreate,
  MessageFeedback,
  Provider,
  ProviderCreate,
  ProviderModelsResponse,
  ProviderTestResult,
  RatingItem,
  RetrievalConfig,
  SharedExchange,
  SystemHealth,
  SecurityStatus,
  Principal,
  KnowledgeBaseAccessCheck,
  KnowledgeBaseGrant,
  KnowledgeBaseGrantEntry,
} from "./types";

export const knowledgeBasesApi = {
  list: () => apiGet<KnowledgeBase[]>("/api/v1/knowledge-bases"),
  get: (id: string) => apiGet<KnowledgeBase>(`/api/v1/knowledge-bases/${id}`),
  create: (payload: KnowledgeBaseCreate) =>
    apiSend<KnowledgeBase>("/api/v1/knowledge-bases", "POST", payload),
  remove: (id: string) => apiSend<void>(`/api/v1/knowledge-bases/${id}`, "DELETE"),
  grants: (id: string) => apiGet<KnowledgeBaseGrant[]>(`/api/v1/knowledge-bases/${id}/grants`),
  replaceGrants: (id: string, grants: KnowledgeBaseGrantEntry[]) =>
    apiSend<KnowledgeBaseGrant[]>(`/api/v1/knowledge-bases/${id}/grants`, "PUT", { grants }),
  accessCheck: (subject: string, groups: string, roles: string) => {
    const params = new URLSearchParams({ subject });
    if (groups.trim()) params.set("groups", groups);
    if (roles.trim()) params.set("roles", roles);
    return apiGet<KnowledgeBaseAccessCheck>(`/api/v1/knowledge-bases/access-check?${params}`);
  },
};

export const documentsApi = {
  listForKb: (kbId: string) =>
    apiGet<DocumentItem[]>(`/api/v1/knowledge-bases/${kbId}/documents`),
  upload: (kbId: string, file: File, title?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (title) form.append("title", title);
    return apiUpload<DocumentItem>(`/api/v1/knowledge-bases/${kbId}/documents`, form);
  },
  remove: (id: string) => apiSend<void>(`/api/v1/documents/${id}`, "DELETE"),
  reprocess: (id: string) =>
    apiSend<IngestionJob>(`/api/v1/documents/${id}/reprocess`, "POST"),
  jobs: () => apiGet<IngestionJob[]>("/api/v1/ingestion-jobs"),
  /** Latest ingestion job for a document — drives per-file stage display. */
  job: (documentId: string) =>
    apiGet<IngestionJob>(`/api/v1/documents/${documentId}/ingestion-job`),
};

export const applicationsApi = {
  list: () => apiGet<Application[]>("/api/v1/applications"),
  get: (id: string) => apiGet<Application>(`/api/v1/applications/${id}`),
  create: (payload: ApplicationCreate) =>
    apiSend<Application>("/api/v1/applications", "POST", payload),
  remove: (id: string) => apiSend<void>(`/api/v1/applications/${id}`, "DELETE"),
  ask: (id: string, query: string, topK?: number) =>
    apiSend<ApplicationAskResponse>(`/api/v1/applications/${id}/ask`, "POST", {
      query,
      top_k: topK,
    }),
  update: (id: string, payload: Partial<ApplicationCreate>) =>
    apiSend<Application>(`/api/v1/applications/${id}`, "PATCH", payload),
  keys: (id: string) => apiGet<ApiKey[]>(`/api/v1/applications/${id}/api-keys`),
  createKey: (id: string, name?: string) =>
    apiSend<ApiKeyWithSecret>(`/api/v1/applications/${id}/api-keys`, "POST", { name }),
  rotateKey: (id: string, keyId: string) =>
    apiSend<ApiKeyWithSecret>(
      `/api/v1/applications/${id}/api-keys/${keyId}/rotate`,
      "POST",
    ),
  revokeKey: (id: string, keyId: string) =>
    apiSend<void>(`/api/v1/applications/${id}/api-keys/${keyId}`, "DELETE"),
};

/**
 * The OpenAI-compatible endpoint, mounted at bare `/v1` rather than `/api/v1`.
 * This is the route the Endpoint panel's snippets tell users to call, so the
 * "Send test request" button must exercise this and not `applicationsApi.ask`.
 */
export const openaiApi = {
  chatCompletionUrl: (baseUrl: string = API_BASE) =>
    `${baseUrl.replace(/\/$/, "")}/v1/chat/completions`,
  chatCompletion: async (
    baseUrl: string,
    model: string,
    messages: ChatMessage[],
    apiKey?: string,
  ): Promise<ChatCompletionResponse> => {
    // Mimics an external SDK client: the application key when one is given,
    // otherwise the signed-in admin's own credentials.
    const res = await fetch(openaiApi.chatCompletionUrl(baseUrl), {
      method: "POST",
      credentials: FETCH_CREDENTIALS,
      headers: {
        "Content-Type": "application/json",
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : await authHeaders()),
      },
      body: JSON.stringify({ model, messages }),
    });
    const body = await res.json();
    if (!res.ok) {
      throw new Error(body?.error?.message || `Request failed (${res.status})`);
    }
    return body as ChatCompletionResponse;
  },
};

export const providersApi = {
  list: (kind?: string) =>
    apiGet<Provider[]>(`/api/v1/providers${kind ? `?kind=${kind}` : ""}`),
  create: (payload: ProviderCreate) =>
    apiSend<Provider>("/api/v1/providers", "POST", payload),
  remove: (id: string) => apiSend<void>(`/api/v1/providers/${id}`, "DELETE"),
  test: (id: string) =>
    apiSend<ProviderTestResult>(`/api/v1/providers/${id}/test`, "POST"),
  models: (id: string) =>
    apiGet<ProviderModelsResponse>(`/api/v1/providers/${id}/models`),
};

export const retrievalApi = {
  list: () => apiGet<RetrievalConfig[]>("/api/v1/retrieval-configurations"),
  create: (payload: Partial<RetrievalConfig> & { name: string }) =>
    apiSend<RetrievalConfig>("/api/v1/retrieval-configurations", "POST", payload),
  remove: (id: string) =>
    apiSend<void>(`/api/v1/retrieval-configurations/${id}`, "DELETE"),
};

function auditQuery(params: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `?${text}` : "";
}

export const auditApi = {
  status: () => apiGet<AuditStatus>("/api/v1/audit/status"),
  events: (filters: AuditFilters, cursor?: string | null, limit = 50) =>
    apiGet<AuditEventPage>(
      `/api/v1/audit/events${auditQuery({ ...filters, limit, cursor: cursor ?? undefined })}`,
    ),
  event: (id: string) => apiGet<AuditEvent>(`/api/v1/audit/events/${id}`),
  export: (filters: AuditFilters, format: "ndjson" | "csv") =>
    apiDownload(`/api/v1/audit/export${auditQuery({ ...filters, format })}`),
};

export const chatApi = {
  /** Only Applications whose every Knowledge Base the caller holds a Grant on. */
  applications: () => apiGet<ChatApplication[]>("/api/v1/chat/applications"),
  document: (id: string) => apiGet<ChatDocument>(`/api/v1/chat/documents/${id}`),
  conversations: () => apiGet<ConversationSummary[]>("/api/v1/chat/conversations"),
  conversation: (id: string) => apiGet<ConversationDetail>(`/api/v1/chat/conversations/${id}`),
  startConversation: (applicationId: string) =>
    apiSend<ConversationSummary>("/api/v1/chat/conversations", "POST", { application_id: applicationId }),
  renameConversation: (id: string, title: string) =>
    apiSend<ConversationSummary>(`/api/v1/chat/conversations/${id}`, "PATCH", { title }),
  deleteConversation: (id: string) => apiSend<void>(`/api/v1/chat/conversations/${id}`, "DELETE"),
  ask: (
    conversationId: string,
    query: string,
    onEvent: (event: ChatStreamEvent) => void,
    signal?: AbortSignal,
  ) =>
    apiStreamEvents<ChatStreamEvent>(
      `/api/v1/chat/conversations/${conversationId}/messages`,
      { query },
      onEvent,
      signal,
    ),
  rate: (
    conversationId: string,
    messageId: string,
    feedback: { rating: "up" | "down"; comment: string | null; share_with_admins: boolean },
  ) =>
    apiSend<MessageFeedback>(
      `/api/v1/chat/conversations/${conversationId}/messages/${messageId}/feedback`,
      "PUT",
      feedback,
    ),
  withdrawRating: (conversationId: string, messageId: string) =>
    apiSend<void>(`/api/v1/chat/conversations/${conversationId}/messages/${messageId}/feedback`, "DELETE"),
};

/** The admin view of chat: counts, ratings and the exchanges users chose to share. Never Conversation text. */
export const chatAdminApi = {
  counts: () => apiGet<ChatCounts>("/api/v1/chat-admin/conversation-counts"),
  summary: () => apiGet<ApplicationRatings[]>("/api/v1/chat-admin/ratings/summary"),
  ratings: (applicationId?: string) =>
    apiGet<RatingItem[]>(
      `/api/v1/chat-admin/ratings${applicationId ? `?application_id=${applicationId}` : ""}`,
    ),
  shared: (applicationId?: string) =>
    apiGet<SharedExchange[]>(
      `/api/v1/chat-admin/shared-exchanges${applicationId ? `?application_id=${applicationId}` : ""}`,
    ),
  exportGolden: (applicationId: string) =>
    apiDownload(`/api/v1/chat-admin/shared-exchanges/export?application_id=${applicationId}`),
};

export const authApi = {
  me: () => apiGet<Principal>("/api/v1/auth/me"),
};

export const systemApi = {
  health: () => apiGet<SystemHealth>("/api/v1/system/health"),
  stats: () => apiGet<DashboardStats>("/api/v1/system/stats"),
  gpu: () => apiGet<GPUResponse>("/api/v1/system/gpu"),
  security: () => apiGet<SecurityStatus>("/api/v1/system/security"),
};
