// Plain JSON calls to the backend. Streaming lives in ./sse.ts.

import { API_URL } from "./config";
import type {
  ApproveResponse,
  AuditorLoginResponse,
  Health,
  LoginResponse,
  PendingApproval,
  ThreadState,
} from "./types";

/** An HTTP error with the backend's status, so the UI can react (401 -> log in again). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
  }
}

export async function toApiError(res: Response): Promise<ApiError> {
  let detail = res.statusText || "request failed";
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") detail = body.detail;
  } catch {
    // body was not JSON; keep the status text
  }
  return new ApiError(res.status, detail);
}

async function request<T>(
  path: string,
  opts: { method?: "GET" | "POST"; token?: string; body?: unknown } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
  } catch {
    throw new ApiError(0, "network"); // backend down, CORS, or offline
  }
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),

  login: (customer_id: string, code: string) =>
    request<LoginResponse>("/api/auth/login", { method: "POST", body: { customer_id, code } }),

  auditorLogin: (auditor_id: string, code: string) =>
    request<AuditorLoginResponse>("/api/auth/auditor-login", {
      method: "POST",
      body: { auditor_id, code },
    }),

  pending: (token: string) => request<PendingApproval[]>("/api/approvals/pending", { token }),

  approve: (token: string, thread_id: string, decision: "approved" | "rejected", comment: string) =>
    request<ApproveResponse>("/api/approve", {
      method: "POST",
      token,
      body: { thread_id, decision, comment },
    }),

  threadState: (token: string, thread_id: string) =>
    request<ThreadState>(`/api/threads/${encodeURIComponent(thread_id)}/state`, { token }),
};

/** Customer-facing Spanish text for an API failure. Details stay out of the UI. */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.status) {
      case 0:
        return "No pudimos conectar con el servidor. Revisa tu conexión e inténtalo de nuevo.";
      case 401:
        return "Tu sesión terminó. Vuelve a ingresar para continuar.";
      case 403:
        return "No tienes permiso para esta acción.";
      case 404:
        return "No encontramos esa conversación.";
      case 409:
        return "Hay una solicitud de esta conversación esperando aprobación. Te aviso apenas haya respuesta.";
      case 422:
        return "Tu mensaje es demasiado largo. Intenta resumirlo.";
      case 429:
        return "Estás enviando mensajes muy rápido. Espera un momento.";
      case 503:
        return "La demo alcanzó su límite de uso de hoy. Vuelve a intentarlo mañana.";
    }
  }
  return "Tuve un problema técnico. Intenta de nuevo en unos minutos; si es urgente, llama a la línea #2222.";
}
