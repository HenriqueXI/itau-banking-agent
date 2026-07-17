export type UserRole = "customer" | "manager" | "admin";

export interface Citation {
  documentId: string;
  title: string;
  section: string;
  page: number | null;
  marker: string;
}

export interface ConfirmationPayload {
  operationHash: string;
  operation: "alterar_limite" | "fazer_pix";
  currentAmount: string | null;
  requestedAmount: string;
  expiresAt: string;
  issuedAt?: string;
  recipientKeyMasked: string | null;
  accountId: string | null;
}

export interface StepUpPayload {
  operationHash: string;
  expiresAt: string;
}

export interface ResumePayload {
  operation_hash: string;
  response: "confirm" | "cancel" | string;
  stage: "confirmation" | "step_up";
  challenge_id?: string;
}

export interface AgentMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  citations?: Citation[];
}

export interface ToolStatus {
  id: string;
  name: string;
  state: "running" | "finished";
  summary?: string;
}

export type AguiEvent =
  | { type: "RUN_STARTED"; data: { threadId: string; runId: string } }
  | { type: "TEXT_MESSAGE_CONTENT"; data: { messageId: string; delta: string } }
  | { type: "TOOL_CALL_START"; data: { toolCallId: string; toolCallName: string; args: unknown } }
  | { type: "TOOL_CALL_END"; data: { toolCallId: string; result: string } }
  | { type: "confirmation_required"; data: ConfirmationPayload }
  | { type: "step_up_required"; data: StepUpPayload }
  | { type: "citations"; data: { citations: Citation[] } }
  | { type: "STATE_SNAPSHOT"; data: { snapshot: { pendingOperationHash: string | null; dataChanged?: boolean } } }
  | { type: "RUN_FINISHED"; data: { threadId: string; runId: string; route: string } }
  | { type: "RUN_ERROR"; data: { message: string; correlationId: string | null } };

export interface Session {
  token: string;
  role: UserRole;
  email: string;
}

export interface AuditEvent {
  id: string;
  event_id: string;
  user_ref: string;
  action: string;
  amount: string | null;
  occurred_at: string;
  resource: string;
  outcome: string;
  trace_id: string | null;
  details: Record<string, unknown>;
  actor: {
    id: string;
    name: string;
    email: string;
    role: UserRole;
  } | null;
}

export interface AuditFilters {
  user?: string;
  action?: string;
  from?: string;
  to?: string;
  page: number;
  pageSize: number;
}

export interface AuditPage {
  items: AuditEvent[];
  total: number;
}
