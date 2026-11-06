// Shapes of what the FastAPI backend sends. Mirrors src/cardagent/api/{main,runner}.py.

export type Role = "user" | "assistant";

// --- auth -----------------------------------------------------------------------------

export interface LoginResponse {
  token: string;
  expires_at: string;
  thread_id: string;
  welcome: string; // rendered by the channel layer; the name never enters the graph
}

export interface AuditorLoginResponse {
  token: string;
  expires_at: string;
}

// --- SSE events (api/runner.py) -------------------------------------------------------

export interface FinalData {
  text: string;
  suggestions: string[];
  intent?: string | null;
  route: string;
  case_id?: string | null;
  latency_ms?: number;
  cost_usd?: number;
}

export type ChatEvent =
  | { type: "thread"; data: { thread_id: string; turn_id: string } }
  | { type: "node_start"; data: { node: string; message: string } }
  | { type: "interim"; data: { text: string } }
  | { type: "approval_required"; data: { status: string; card: string; expires_at: string } }
  | { type: "token"; data: { text: string } }
  | { type: "final"; data: FinalData }
  | { type: "error"; data: { code: string; message?: string } };

export type ChatEventType = ChatEvent["type"];

// Events after which the server closes the stream.
export const TERMINAL_EVENTS: ReadonlySet<string> = new Set(["final", "approval_required", "error"]);

// --- auditor --------------------------------------------------------------------------

export interface PendingApproval {
  type: "approval_required";
  thread_id: string;
  requester_id: string;
  action: string;
  card: string; // masked, e.g. "•••• 1382"
  reason: string;
  summary: string;
  risk_score: number | null;
  requested_at: string;
  expires_at: string;
}

export interface ApproveResponse {
  decision: string; // approved | rejected | expired
  note: string;
  verified: boolean;
  customer_message: string;
}

export interface AuditEvent {
  node: string;
  event: string;
  payload: Record<string, unknown>;
  ts: string;
}

export interface UsageRecord {
  node: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
}

export interface ThreadState {
  thread_id: string;
  messages: { role: Role; content: string }[];
  intent: string | null;
  triage_source: string | null;
  route_reason: string | null;
  proposed_action: Record<string, unknown> | null;
  human_decision: Record<string, unknown> | null;
  action_result: Record<string, unknown> | null;
  escalation: Record<string, unknown> | null;
  citations: unknown[];
  safety_violations: unknown[];
  audit_trail: AuditEvent[];
  usage: UsageRecord[];
}

export interface Health {
  status: string;
  prompt_version: string;
  models: Record<string, string>;
}
