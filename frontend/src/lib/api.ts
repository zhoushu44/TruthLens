function resolveApiBase(): string {
  const envBase = (import.meta as any).env?.VITE_API_BASE as string | undefined;
  if (envBase) return envBase;
  if (typeof window !== "undefined") {
    if (window.location.port === "5173") return "http://localhost:8000/api/v1";
    if (window.location.port === "6655") return `${window.location.origin}/api/v1`;
    if (window.location.origin.startsWith("http")) return `${window.location.origin}/api/v1`;
  }
  return "http://localhost:6655/api/v1";
}
const API_BASE = resolveApiBase();
export function getApiBase(): string { return API_BASE; }
export function getServerOrigin(): string {
  try {
    const u = new URL(API_BASE);
    return `${u.protocol}//${u.host}`;
  } catch { return window.location.origin; }
}
export function getStoredApiKey(): string { try { return localStorage.getItem("truthlens_api_key") || ""; } catch { return ""; } }
export function setStoredApiKey(k: string): void { try { k ? localStorage.setItem("truthlens_api_key", k) : localStorage.removeItem("truthlens_api_key"); } catch {} }
export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const k = getStoredApiKey();
  return k ? { ...extra, Authorization: `Bearer ${k}` } : { ...extra };
}
export function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const merged: RequestInit = { ...init, headers: { ...authHeaders(), ...(init.headers || {}) } };
  return fetch(input, merged);
}

export interface BackendModel {
  id: string;
  name: string;
  provider: string;
  tier: number;
  available: boolean;
  free: boolean;
  description: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface Warning {
  type: string;
  message: string;
  claim_id: string;
  source_url?: string | null;
}

export interface VerificationDetails {
  entailment_score?: number;
  contradiction_score?: number;
  neutral_score?: number;
  source_coverage?: number;
  source_agreement_variance?: number;
  evidence?: Array<{
    source_type: string;
    source_tier?: string;
    source_url?: string | null;
    source_title?: string | null;
    document_name?: string | null;
    chunk_index?: number | null;
    message_index?: number | null;
    snippet?: string;
    nli_label?: string;
    nli_scores?: Record<string, number> | null;
  }>;
  sources_checked?: string[];
}

export interface DetectionClaim {
  id: string;
  text: string;
  exact_quote?: string;
  domain: string;
  risk_score: number;
  status: "VERIFIED" | "PARTIALLY_VERIFIED" | "UNVERIFIED" | "CONTRADICTED" | "UNVERIFIABLE_SOURCE" | "OPINION" | "SKIPPED";
  confidence: number;
  reasoning?: string;
  suggestion?: string;
  suggested_sources: string[];
  note: string;
  citations: string[];
  verification_details?: VerificationDetails;
}

export interface DetectionMetadata {
  processing_time_ms: number;
  claims_extracted: number;
  claims_verified: number;
  claims_skipped: number;
  sources_queried: string[];
  platform?: string | null;
  conversation_id?: string | null;
}

export interface HighlightClaim {
  text: string;
  exact_quote?: string;
  domain?: string;
  score: number;
  note?: string;
  citations?: string[];
}

export interface MessageDetectionResult {
  messageId?: string;
  messageIndex?: number;
  assistantRoleIndex?: number;
  role: string;
  risk_score: number;
  risk_level: string;
  claims: HighlightClaim[];
}

export interface DetectionResult {
  response_id: string;
  overall_risk_score: number;
  risk_level: "LOW" | "MODERATE" | "HIGH" | "CRITICAL";
  risk_color: string;
  warning_message: string;
  warnings: Warning[];
  claims: DetectionClaim[];
  results?: MessageDetectionResult[] | null;
  metadata?: DetectionMetadata;
}

// ---- Detection Request Models ----

export interface ConversationMessage {
  role: "user" | "assistant";
  content: string;
  model_id?: string;
}

export interface DetectionConfig {
  check_web?: boolean;
  check_documents?: boolean;
  check_conversation?: boolean;
  claim_threshold?: number;
}

export interface DetectionRequest {
  conversation_id?: string;
  assistant_message_id?: string;
  document_ids?: string[];
  config?: DetectionConfig;
  model_id?: string;
  model_response?: string;
  conversation_history?: ConversationMessage[];
}

export interface Conversation {
  id: string;
  external_id?: string;
  platform?: string;
  title?: string;
  external_url?: string;
  messages: Array<{
    id: string;
    role: "user" | "assistant";
    content: string;
    model_id?: string;
    created_at: string;
  }>;
  metadata: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface AnalyticsSummary {
  total_analyses: number;
  total_claims: number;
  total_hallucinations: number;
  average_confidence: number;
}

export interface AnalyticsModelStat {
  id: string;
  name: string;
  confidence: number;
  hallucinations: number;
  sources: number;
  analyses: number;
  claims: number;
}

export interface AnalyticsTimelinePoint {
  date: string;
  label: string;
  hallucinations: number;
  confidence: number;
}

export interface AnalyticsApiKeyStat {
  id: string;
  name: string;
  prefix: string;
  is_active: boolean;
  usage_count: number;
  last_used_at: string | null;
}

export interface AnalyticsProviderStat {
  id: string;
  label?: string | null;
  calls: number;
  last_used_at: string | null;
}

export interface AnalyticsOverview {
  days: number;
  generated_at: string;
  summary: AnalyticsSummary;
  models: AnalyticsModelStat[];
  timeline: AnalyticsTimelinePoint[];
  api_keys?: AnalyticsApiKeyStat[];
  providers?: AnalyticsProviderStat[];
}

export async function fetchConversations(): Promise<Conversation[]> {
  const res = await apiFetch(`${API_BASE}/conversations`);
  if (!res.ok) throw new Error("Failed to fetch conversations");
  return res.json() as Promise<Conversation[]>;
}

export async function getConversation(id: string): Promise<Conversation> {
  const res = await apiFetch(`${API_BASE}/conversations/${id}`);
  if (!res.ok) throw new Error("Failed to load conversation");
  return res.json() as Promise<Conversation>;
}

export async function createConversation(title: string = "New Session"): Promise<Conversation> {
  const res = await apiFetch(`${API_BASE}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ metadata: { title } })
  });
  if (!res.ok) throw new Error("Failed to create conversation");
  return res.json() as Promise<Conversation>;
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/conversations/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error(`Failed to delete conversation (${res.status})`);
  }
}

export async function fetchAnalyticsOverview(days: number = 7): Promise<AnalyticsOverview> {
  const res = await apiFetch(`${API_BASE}/analytics/overview?days=${days}`);
  if (!res.ok) throw new Error("Failed to fetch analytics overview");
  return res.json() as Promise<AnalyticsOverview>;
}

export async function addMessageToConversation(
  convId: string,
  role: "user" | "assistant",
  content: string,
  modelId?: string
): Promise<Conversation["messages"][number]> {
  const res = await apiFetch(`${API_BASE}/conversations/${convId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, content, model_id: modelId })
  });
  if (!res.ok) throw new Error("Failed to add message");
  return res.json() as Promise<Conversation["messages"][number]>;
}

/** Fetch all available models from the backend. (MOCKED) */
export async function fetchModels(): Promise<BackendModel[]> {
  const res = await apiFetch(`${API_BASE}/models`);
  if (!res.ok) throw new Error("Failed to fetch models");
  const data = await res.json();
  const modelsList = Array.isArray(data) ? data : data.models || [];
  return modelsList.map((m: any) => ({
    id: m.id,
    name: m.name,
    provider: m.provider,
    tier: m.tier,
    available: true,
    free: true,
    description: m.description,
  }));
}

// ---- Document API ---- //

export interface DocumentResponse {
  id: string;
  conversation_id?: string;
  filename: string;
  file_type: string;
  file_size_bytes: number;
  chunk_count: number;
  created_at: string;
}

export async function uploadDocument(file: File, conversationId?: string): Promise<DocumentResponse> {
  const formData = new FormData();
  formData.append("file", file);
  if (conversationId) formData.append("conversation_id", conversationId);
  
  const res = await apiFetch(`${API_BASE}/documents/upload`, {
    method: "POST",
    body: formData
  });
  if (!res.ok) throw new Error("Failed to upload document");
  return res.json();
}

export async function getDocument(docId: string): Promise<DocumentResponse> {
  const res = await apiFetch(`${API_BASE}/documents/${docId}`);
  if (!res.ok) throw new Error("Failed to get document");
  return res.json();
}

export async function getGlobalDocuments(): Promise<DocumentResponse[]> {
  const res = await apiFetch(`${API_BASE}/documents?global_only=true`);
  if (!res.ok) throw new Error("Failed to fetch global documents");
  const data = await res.json();
  return data.documents;
}

export async function deleteDocument(docId: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/documents/${docId}`, {
    method: "DELETE"
  });
  if (!res.ok) throw new Error("Failed to delete document");
}

/** Send a chat message (non-streaming). Returns the AI response text. */
export async function sendChatMessage(
  modelId: string,
  message: string,
  history: ChatMessage[]
): Promise<string> {
  const res = await apiFetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model_id: modelId,
      message,
      conversation_history: history,
      stream: false,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `Chat failed: ${res.status}`);
  }
  const data = await res.json();
  return data.response as string;
}

/** Streaming chat message */
export async function sendChatMessageStream(
  modelId: string,
  message: string,
  history: ChatMessage[],
  onChunk: (chunk: string) => void
): Promise<string> {
  const res = await apiFetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model_id: modelId,
      message,
      conversation_history: history,
      stream: true,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `Chat failed: ${res.status}`);
  }
  if (!res.body) throw new Error("No response body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let fullText = "";
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const eventStr = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      
      if (eventStr.startsWith("data: ")) {
        const data = eventStr.slice(6);
        if (data.trim() === "[DONE]") {
          return fullText;
        } else if (data.startsWith("[ERROR]")) {
          throw new Error(data);
        } else {
          fullText += data;
          onChunk(data);
        }
      }
      
      boundary = buffer.indexOf("\n\n");
    }
  }
  return fullText;
}

/** Run hallucination detection on an AI response. */
export async function detectHallucinations(
  modelId: string,
  modelResponse: string,
  history: ChatMessage[],
  documentIds: string[] = [],
  conversationId?: string,
  assistantMessageId?: string,
): Promise<DetectionResult> {
  const res = await apiFetch(`${API_BASE}/detect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model_id: modelId,
      model_response: modelResponse,
      conversation_history: history,
      conversation_id: conversationId,
      assistant_message_id: assistantMessageId,
      document_ids: documentIds,
      config: { check_web: true, check_documents: true, check_conversation: true },
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `Detection failed: ${res.status}`);
  }
  return res.json() as Promise<DetectionResult>;
}

/** Map a numeric risk_score (0-100) to the frontend RiskLevel string. */
export function scoreToRisk(score: number): "none" | "green" | "amber" | "red" {
  if (score <= 30) return "green";
  if (score <= 65) return "amber";
  return "red";
}


export interface ApiKeyItem { id: string; name: string; prefix: string; is_active: boolean; expires_at: string | null; usage_count: number; last_used_at: string | null; created_at: string | null; }
export interface ApiKeyStatus { server_port: number; master_configured: boolean; master_prefix: string | null; api_key_required: boolean; auth_header: string; detect_url: string; openai_chat_url: string; openai_models_url: string; swagger_url: string; }
export async function fetchApiKeyStatus(): Promise<ApiKeyStatus> {
  const res = await apiFetch(`${API_BASE}/apikeys/status`);
  if (!res.ok) throw new Error("无法获取服务状态");
  return res.json();
}
export async function fetchApiKeys(): Promise<ApiKeyItem[]> {
  const res = await apiFetch(`${API_BASE}/apikeys`);
  if (!res.ok) throw new Error("获取 Key 列表失败");
  const data = await res.json();
  return data.keys || [];
}
export async function createApiKey(name: string): Promise<{ id: string; name: string; prefix: string; api_key: string; tip: string }> {
  const res = await apiFetch(`${API_BASE}/apikeys`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
  if (!res.ok) throw new Error("创建 Key 失败");
  return res.json();
}
export async function revokeApiKey(id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/apikeys/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("吊销失败");
}
export async function tryDetectWithKey(apiKey: string, text: string): Promise<any> {
  const origin = getServerOrigin();
  const res = await fetch(`${origin}/api/v1/detect`, { method: "POST", headers: { "Content-Type": "application/json", ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}) }, body: JSON.stringify({ model_response: text, conversation_history: [], config: { check_web: true, check_documents: true, check_conversation: true } }) });
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error((e as any).detail || `调用失败 ${res.status}`); }
  return res.json();
}

/* ---- Provider Keys / URLs（模型 Key 与搜索 Key 设置页） ---- */
export interface ProviderSettingItem { key: string; label: string; category: string; required: string; required_level: string; description: string; placeholder: string; secret: boolean; configured: boolean; masked: string; value: string; }
export interface EffectiveSummary { claim_ready: boolean; claim_channel: string; adjudicator: string; chat_models: string[]; chat_models_count: number; web_search: boolean; web_providers: string[]; }
export async function fetchProviderSettings(): Promise<{ items: ProviderSettingItem[]; effective: EffectiveSummary }> {
  const res = await apiFetch(`${API_BASE}/settings/status`);
  if (!res.ok) throw new Error("获取 Key 配置状态失败");
  return res.json();
}
export async function saveProviderSettings(values: Record<string, string>): Promise<{ ok: boolean; updated: string[]; effective: EffectiveSummary }> {
  const res = await apiFetch(`${API_BASE}/settings`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values }) });
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error((e as any).detail || "保存失败"); }
  return res.json();
}
export function openProviderSettings(): void { try { window.dispatchEvent(new CustomEvent("open-provider-settings")); } catch {} }

export interface ProviderTestResult { ok: boolean; detail: string; latency_ms: number; tested_with: string; }
export async function testProviderKey(key: string, value?: string): Promise<ProviderTestResult> {
  const res = await apiFetch(`${API_BASE}/settings/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key, value: value ?? null }) });
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error((e as any).detail || "测试请求失败"); }
  return res.json();
}

