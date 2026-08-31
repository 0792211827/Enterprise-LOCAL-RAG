// Types mirroring the backend OpenAPI contract (src/schemas/api/domain.py).

export type RetrievalMode = "bm25" | "vector" | "hybrid";
export type RAGStrategy = "traditional" | "agentic";
export type ProviderKind = "llm" | "embedding" | "vlm";
export type ProviderType = "ollama" | "openai-compatible" | "huggingface";

export interface KnowledgeBase {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  index_name: string;
  embedding_provider: string;
  embedding_model: string;
  embedding_dimension: number;
  retrieval_mode: string;
  default_top_k: number;
  document_count: number;
  chunk_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface KnowledgeBaseCreate {
  name: string;
  description?: string;
  embedding_provider?: string;
  embedding_model?: string;
  embedding_dimension?: number;
  retrieval_mode?: RetrievalMode;
  default_top_k?: number;
}

export interface DocumentItem {
  id: string;
  knowledge_base_id: string;
  title: string;
  source_uri: string | null;
  content_type: string | null;
  file_size_bytes: number | null;
  status: string;
  error: string | null;
  chunk_count: number;
  parser_used: string | null;
  processed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface IngestionJob {
  id: string;
  document_id: string;
  knowledge_base_id: string;
  status: string;
  stage: string | null;
  error: string | null;
  stats: Record<string, number> | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
}

export interface Provider {
  id: string;
  name: string;
  kind: string;
  provider_type: string;
  endpoint: string | null;
  model: string;
  dimension: string | null;
  capabilities: Record<string, unknown> | null;
  enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface ProviderCreate {
  name: string;
  kind: ProviderKind;
  provider_type: ProviderType;
  endpoint?: string;
  model: string;
  api_key_ref?: string;
  dimension?: string;
  enabled?: boolean;
}

export interface ProviderTestResult {
  connected: boolean;
  model_available: boolean | null;
  generation_ok: boolean | null;
  embedding_ok: boolean | null;
  dimension: number | null;
  latency_ms: number | null;
  message: string;
  detail: string | null;
}

export interface RetrievalConfig {
  id: string;
  name: string;
  mode: string;
  top_k: number;
  hybrid_size_multiplier: number;
  rrf_rank_constant: number;
  score_threshold: number | null;
  filters: Record<string, unknown> | null;
}

export interface ModelConfiguration {
  id: string;
  llm_provider: string;
  llm_endpoint: string | null;
  llm_model: string;
  embedding_provider: string;
  embedding_model: string;
  vlm_enabled: boolean;
  vlm_provider: string | null;
  vlm_model: string | null;
  generation_params: Record<string, unknown> | null;
}

export interface Application {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  system_prompt: string | null;
  rag_strategy: string;
  streaming_enabled: boolean;
  citations_enabled: boolean;
  knowledge_bases: KnowledgeBase[];
  model_configuration: ModelConfiguration | null;
  retrieval_configuration: RetrievalConfig | null;
  created_at: string | null;
  updated_at: string | null;
  /** Present only on the create response; the plaintext is shown once. */
  api_key?: string | null;
}

export interface ApplicationCreate {
  name: string;
  description?: string;
  system_prompt?: string;
  rag_strategy?: RAGStrategy;
  knowledge_base_ids?: string[];
  llm_provider?: string;
  llm_endpoint?: string;
  llm_model?: string;
  embedding_provider?: string;
  embedding_model?: string;
  vlm_enabled?: boolean;
  vlm_provider?: string;
  vlm_model?: string;
  retrieval_mode?: RetrievalMode;
  top_k?: number;
  score_threshold?: number;
  temperature?: number;
  max_tokens?: number;
  streaming_enabled?: boolean;
  citations_enabled?: boolean;
}

export interface RetrievedSource {
  document_id: string | null;
  document_title: string | null;
  chunk_text: string;
  score: number;
  section_title: string | null;
  retrieval_method: string | null;
}

export interface ApplicationAskResponse {
  query: string;
  answer: string;
  sources: RetrievedSource[];
  search_mode: string;
  chunks_used: number;
}

export interface ComponentHealth {
  name: string;
  status: string;
  latency_ms: number | null;
  version: string | null;
  detail: string | null;
}

export interface SystemHealth {
  status: string;
  components: ComponentHealth[];
}

export interface GPUInfo {
  index: number;
  name: string;
  memory_total_mb: number | null;
  memory_used_mb: number | null;
  utilization_percent: number | null;
  temperature_c: number | null;
}

export interface GPUResponse {
  available: boolean;
  cuda_version: string | null;
  driver_version: string | null;
  gpus: GPUInfo[];
  message: string | null;
}

export interface DashboardStats {
  knowledge_bases: number;
  documents: number;
  applications: number;
  providers: number;
  chunks: number;
  ingestion_jobs: number;
  ingestion_jobs_by_status: Record<string, number>;
}

export interface ApiKey {
  id: string;
  application_id: string;
  name: string | null;
  key_prefix: string;
  key_last4: string;
  is_active: boolean;
  created_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** Returned once at creation/rotation. `key` is never recoverable afterwards. */
export interface ApiKeyWithSecret extends ApiKey {
  key: string;
}

export interface ProviderModel {
  id: string;
  name: string;
}

export interface ProviderModelsResponse {
  reachable: boolean;
  models: ProviderModel[];
  detail: string | null;
}

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface ChatCompletionResponse {
  id: string;
  object: string;
  created: number;
  model: string;
  choices: {
    index: number;
    message: ChatMessage;
    finish_reason: string;
  }[];
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}
