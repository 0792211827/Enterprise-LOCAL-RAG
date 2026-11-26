// Types mirroring the backend OpenAPI contract (src/schemas/api/domain.py).

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
}

export type KnowledgeBaseUpdate = Partial<Pick<KnowledgeBaseCreate, "name" | "description">>;

export type ConnectorSourceType = "path" | "s3";

/** One folder or bucket mirrored into a Knowledge Base. Credentials appear in `source` only as `env:NAME` references. */
export interface Connector {
  id: string;
  name: string;
  knowledge_base_id: string;
  source_type: ConnectorSourceType;
  source: Record<string, string>;
  enabled: boolean;
  running: boolean;
  last_run_at: string | null;
  /** ok | partial | error; null until the first run. */
  last_run_status: string | null;
  last_run_summary: Record<string, unknown> | null;
  files_synced: number;
  files_failed: number;
  created_at: string | null;
}

export interface ConnectorCreate {
  name: string;
  knowledge_base_id: string;
  source_type: ConnectorSourceType;
  enabled?: boolean;
  path?: string;
  endpoint_url?: string;
  bucket?: string;
  prefix?: string;
  region?: string;
  access_key_ref?: string;
  secret_key_ref?: string;
}

export type ConnectorUpdate = Partial<Omit<ConnectorCreate, "name" | "knowledge_base_id" | "source_type">>;

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
  /** A replacement being ingested (queued, processing) or that failed; the Document keeps answering from its current content. */
  replacement?: { state: "queued" | "processing" | "failed"; filename: string | null; error: string | null } | null;
  /** Things that did not stop ingestion but are worth knowing (for example images that were not described). */
  warnings?: string[];
  /** `url` for a web page the server fetched; its Citations link to `source_url`. */
  source_kind?: "file" | "url";
  source_url?: string | null;
}

/** The outcome of refreshing a URL Document: `changed` is false when the page holds what the Document already does. */
export interface DocumentRefresh {
  changed: boolean;
  document: DocumentItem;
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
  /** `env:NAME` references are shown as is; a literal key is masked. */
  api_key_ref?: string | null;
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

export type ProviderUpdate = Partial<Pick<ProviderCreate, "name" | "endpoint" | "model" | "api_key_ref" | "dimension" | "enabled">>;

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

export interface ModelConfiguration {
  id: string;
  llm_provider: string;
  llm_endpoint: string | null;
  /** `env:NAME` references are shown as is; a literal key is masked. */
  llm_api_key_ref?: string | null;
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
  /** `env:NAME` reads the key from the server's environment at request time. */
  llm_api_key_ref?: string;
  llm_model?: string;
  embedding_provider?: string;
  embedding_model?: string;
  vlm_enabled?: boolean;
  vlm_provider?: string;
  vlm_model?: string;
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
  /** Where in the source the passage sits, in words ("slide 7", "sheet Codes, rows 12-30"); null for plain text. */
  locator_text?: string | null;
  /** The page of a URL Document, which the Citation links to. */
  source_url?: string | null;
}

export interface ApplicationAskResponse {
  query: string;
  answer: string;
  sources: RetrievedSource[];
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

export type GrantPrincipalType = "group" | "role" | "subject" | "everyone";

export interface KnowledgeBaseGrantEntry {
  principal_type: GrantPrincipalType;
  principal_value: string;
}

export interface KnowledgeBaseGrant extends KnowledgeBaseGrantEntry {
  id: string;
  permission: string;
  created_at: string | null;
  created_by: string | null;
}

export interface DocumentRestrictionWarning {
  principal_type: GrantPrincipalType;
  principal_value: string;
  message: string;
}

export interface DocumentRestrictions {
  document_id: string;
  restrictions: KnowledgeBaseGrantEntry[];
  warnings: DocumentRestrictionWarning[];
  chunks_updated: number | null;
}

export interface DocumentAccessCheck {
  document_id: string;
  can_read: boolean;
  reason: string;
  knowledge_base_granted: boolean;
  restricted: boolean;
  restriction_matched: boolean | null;
}

export interface KnowledgeBaseAccessCheck {
  unrestricted: boolean;
  reason: string;
  knowledge_bases: { id: string; name: string; slug: string }[];
}

// Employee chat. An Application as an end user sees it: no prompt or knowledge bases.
export interface ChatApplication {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  citations_enabled: boolean;
}

export interface ChatCitation {
  index: number;
  document_id: string | null;
  title: string;
  section_title: string | null;
  /** Where in the source the passage sits, in words ("slide 7", "sheet Codes, rows 12-30"); null for plain text. */
  locator_text?: string | null;
  /** The page of a URL Document, which the Citation links to. */
  source_url?: string | null;
  chunk_id: string | null;
  passage: string;
  score: number;
  /** Set for a passage of an Attachment: it names the Attachment (`title` is its file name), not a Document. */
  attachment_id?: string | null;
}

/** What the chat UI needs to know about this deployment. */
export interface ChatConfig {
  images_enabled: boolean;
  image_max_mb: number;
  attachments_per_message: number;
}

export interface ChatDocument {
  id: string;
  title: string;
  source_uri: string | null;
  content_type: string | null;
  text: string;
}

/** One frame of a streamed answer. */
export type ChatStreamEvent =
  | { type: "delta"; content: string }
  | { type: "citations"; citations: ChatCitation[] }
  | { type: "error"; message: string }
  | { type: "done"; message_id: string | null };

export interface ConversationSummary {
  id: string;
  application_id: string;
  title: string;
  created_at: string;
  last_activity_at: string;
  message_count: number;
}

/** A document attached to a Conversation. `attached_to` is null until the next message is sent. */
export interface ChatAttachment {
  id: string;
  filename: string;
  kind: string;
  size_bytes: number;
  token_estimate: number;
  /** True for a document too long for the prompt: it is searched passage by passage, not sent in full. */
  searched?: boolean;
  attached_to: string | null;
  created_at: string;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: ChatCitation[];
  created_at: string;
  attachments: ChatAttachment[];
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
  pending_attachments: ChatAttachment[];
}

export interface Principal {
  subject: string;
  roles: ("admin" | "querier" | "auditor")[];
  groups: string[];
  raw_roles: string[];
  auth_method: "disabled" | "jwt" | "trusted_header" | "api_key";
  api_key_id: string | null;
  application_id: string | null;
}

export interface SecurityStatus {
  auth_mode: "disabled" | "jwt" | "trusted_header";
  api_keys_enforced: boolean;
  user_token_header: string | null;
  /** Whether a Knowledge Base with no Grants is readable by everyone ("allow") or by no one ("deny"). */
  ungranted_kb_access: "deny" | "allow";
  admin_bypass_retrieval: boolean;
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

export type AuditAction =
  | "query.answer"
  | "query.search"
  | "admin.mutation"
  | "access.denied"
  | "audit.export"
  | "audit.purge"
  | "uploads.cleanup"
  | "chat.purge"
  | "connector.sync";

export type AuditOutcome = "ok" | "denied" | "error";

export interface AuditStatus {
  level: "off" | "metadata" | "full";
  fail_mode: "log" | "deny";
  retention_days: number;
}

export interface AuditRetrievedChunk {
  chunk_id?: string | null;
  document_id?: string | null;
  knowledge_base_id?: string | null;
  score?: number | null;
}

export interface AuditEvent {
  id: string;
  occurred_at: string;
  request_id: string | null;
  action: AuditAction;
  outcome: AuditOutcome;
  status_code: number | null;
  actor_subject: string | null;
  actor_roles: string[] | null;
  actor_groups: string[] | null;
  auth_method: string | null;
  api_key_id: string | null;
  application_id: string | null;
  source_ip: string | null;
  user_agent: string | null;
  http_method: string | null;
  route: string | null;
  resource_type: string | null;
  resource_id: string | null;
  /** null: a global search (every knowledge base). */
  requested_kb_ids: string[] | null;
  /** null: unrestricted; []: none. */
  permitted_kb_ids: string[] | null;
  dropped_kb_ids: string[] | null;
  /** null: no retrieval ran for this request (e.g. a cache hit). */
  retrieved: AuditRetrievedChunk[] | null;
  cache_hit: boolean | null;
  model: string | null;
  query_text: string | null;
  answer_text: string | null;
  query_sha256: string | null;
  answer_sha256: string | null;
  latency_ms: number | null;
  error_code: string | null;
  details: Record<string, unknown> | null;
}

export interface AuditEventPage {
  items: AuditEvent[];
  next_cursor: string | null;
}

export interface AuditFilters {
  actor?: string;
  action?: AuditAction | "";
  outcome?: AuditOutcome | "";
  application_id?: string;
  knowledge_base_id?: string;
  request_id?: string;
  since?: string;
  until?: string;
}
