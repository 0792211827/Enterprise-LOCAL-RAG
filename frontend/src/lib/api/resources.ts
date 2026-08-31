import { API_BASE, apiGet, apiSend, apiUpload } from "./client";
import type {
  ApiKey,
  ApiKeyWithSecret,
  Application,
  ApplicationAskResponse,
  ApplicationCreate,
  ChatCompletionResponse,
  ChatMessage,
  DashboardStats,
  DocumentItem,
  GPUResponse,
  IngestionJob,
  KnowledgeBase,
  KnowledgeBaseCreate,
  Provider,
  ProviderCreate,
  ProviderModelsResponse,
  ProviderTestResult,
  RetrievalConfig,
  SystemHealth,
} from "./types";

export const knowledgeBasesApi = {
  list: () => apiGet<KnowledgeBase[]>("/api/v1/knowledge-bases"),
  get: (id: string) => apiGet<KnowledgeBase>(`/api/v1/knowledge-bases/${id}`),
  create: (payload: KnowledgeBaseCreate) =>
    apiSend<KnowledgeBase>("/api/v1/knowledge-bases", "POST", payload),
  remove: (id: string) => apiSend<void>(`/api/v1/knowledge-bases/${id}`, "DELETE"),
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
    const res = await fetch(openaiApi.chatCompletionUrl(baseUrl), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
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

export const systemApi = {
  health: () => apiGet<SystemHealth>("/api/v1/system/health"),
  stats: () => apiGet<DashboardStats>("/api/v1/system/stats"),
  gpu: () => apiGet<GPUResponse>("/api/v1/system/gpu"),
};
