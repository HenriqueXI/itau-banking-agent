import type { AuditEvent, AuditFilters, AuditPage, ResumePayload } from "@/lib/agui-types";
import type { AccountSummary } from "@/lib/banking";
import type { ConversationHistory } from "@/lib/conversation-history";

const API = "/api/backend";

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...init.headers },
  });
  if (!response.ok) throw new ApiError(response.status, await response.text());
  return response.json() as Promise<T>;
}

export function login(email: string, password: string) {
  return fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  }).then(async (response) => {
    if (!response.ok) throw new ApiError(response.status, await response.text());
    return response.json() as Promise<{ access_token: string }>;
  });
}

export function listConversations(token: string) {
  return request<Array<{ thread_id: string }>>("/conversations", token);
}

export function getConversation(token: string, threadId: string) {
  return request<ConversationHistory>(`/conversations/${encodeURIComponent(threadId)}`, token);
}

export function getAccountSummary(token: string) {
  return request<AccountSummary>("/banking/summary", token);
}

export async function listAudit(token: string, filters: AuditFilters): Promise<AuditPage> {
  const params = new URLSearchParams({ page: String(filters.page), page_size: String(filters.pageSize) });
  if (filters.user) params.set("user", filters.user);
  if (filters.action) params.set("action", filters.action);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  const response = await fetch(`${API}/admin/audit?${params}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!response.ok) throw new ApiError(response.status, await response.text());
  return { items: await response.json() as AuditEvent[], total: Number(response.headers.get("X-Total-Count") ?? 0) };
}

export function getAudit(token: string, auditId: string) {
  return request<AuditEvent>(`/admin/audit/${auditId}`, token);
}

export function requestStepUp(token: string, operationHash: string) {
  return request<{ challenge_id: string; expires_at: string; dev_code: string | null }>(
    "/auth/step-up/request",
    token,
    { method: "POST", body: JSON.stringify({ operation_hash: operationHash }) },
  );
}

export async function runAgent(
  token: string,
  threadId: string,
  message?: string,
  resume?: ResumePayload,
  context?: { selected_card_id?: string; selected_account_id?: string },
): Promise<Response> {
  const payload = resume
    ? { thread_id: threadId, resume }
    : { thread_id: threadId, messages: [{ role: "user", content: message }], context };
  const response = await fetch(`${API}/agui`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new ApiError(response.status, await response.text());
  return response;
}
