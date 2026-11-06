"use client";

// The customer's chat. One turn = one POST /api/chat/stream (SSE). If the turn proposes a
// card block, the stream ends at `approval_required`; we then open a second stream,
// GET /api/chat/{thread_id}/events, and wait there for the auditor's decision.
//
// What the customer sees is always text the backend already reviewed: `token` events are
// pieces of the critic-approved final answer, never raw LLM output.

import { Clock, LogOut, RotateCw, Send, ShieldCheck, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Disclaimer, Header } from "@/components/Brand";
import { ApiError, describeError } from "@/lib/api";
import { streamEvents } from "@/lib/sse";
import { countdown, isPast, useNow } from "@/lib/time";
import type { ChatEvent, FinalData, Role } from "@/lib/types";

export interface CustomerSession {
  token: string;
  expiresAt: string;
  threadId: string;
  customerId: string;
}

interface Msg {
  id: string;
  role: Role;
  text: string;
  status: "streaming" | "done" | "error";
  progress?: string;
  pendingApproval?: boolean;
  meta?: FinalData;
}

type Phase = "idle" | "streaming" | "waiting";

const STARTERS = [
  "¿Mi tarjeta está activa?",
  "Muéstrame mis últimos movimientos",
  "Perdí mi tarjeta de crédito",
  "¿Cuánto cuesta la reposición?",
];
const MAX_CHARS = 2000; // same limit as the API (MAX_MESSAGE_CHARS)
const WAITING_TEXT = "Esperando la confirmación de un supervisor…";

let seq = 0;
const newId = () => `m${Date.now().toString(36)}${(seq++).toString(36)}`;

export function ChatView({
  session,
  welcome,
  onSessionEnd,
}: {
  session: CustomerSession;
  welcome: string;
  onSessionEnd: (reason: "logout" | "expired") => void;
}) {
  const [messages, setMessages] = useState<Msg[]>([
    { id: "welcome", role: "assistant", text: welcome, status: "done" },
  ]);
  const [suggestions, setSuggestions] = useState<string[]>(STARTERS);
  const [phase, setPhase] = useState<Phase>("idle");
  const [approval, setApproval] = useState<{ card: string; expiresAt: string } | null>(null);
  const [waitFailed, setWaitFailed] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [draft, setDraft] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const now = useNow();

  useEffect(() => () => abortRef.current?.abort(), []); // close streams on unmount
  // Braces matter: newer browsers make scrollIntoView() return a Promise, and an effect may
  // only return a cleanup function (or nothing).
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);
  // The composer is disabled while Lía works, which drops keyboard focus; give it back.
  useEffect(() => {
    if (phase === "idle") composerRef.current?.focus();
  }, [phase]);

  const update = useCallback((id: string, patch: Partial<Msg> | ((m: Msg) => Partial<Msg>)) => {
    setMessages((ms) =>
      ms.map((m) => (m.id === id ? { ...m, ...(typeof patch === "function" ? patch(m) : patch) } : m)),
    );
  }, []);

  /** Runs one SSE stream into the assistant message `id`. Returns the terminal event type. */
  const runStream = useCallback(
    async (
      id: string,
      opts: { path: string; method: "GET" | "POST"; body?: unknown },
    ): Promise<ChatEvent["type"] | "failed"> => {
      let terminal: ChatEvent["type"] | "failed" = "failed";
      const controller = new AbortController();
      abortRef.current = controller;

      const onEvent = (ev: ChatEvent) => {
        switch (ev.type) {
          case "node_start":
            update(id, { progress: ev.data.message });
            break;
          case "interim": // "requested, pending supervisor": never claims it's done
            update(id, { text: ev.data.text, progress: undefined });
            break;
          case "approval_required":
            terminal = "approval_required";
            setApproval({ card: ev.data.card, expiresAt: ev.data.expires_at });
            update(id, (m) => ({
              status: "done",
              progress: undefined,
              pendingApproval: true,
              text: m.text || "Tu solicitud quedó pendiente de la aprobación de un supervisor.",
            }));
            break;
          case "token":
            update(id, (m) => ({ text: m.text + ev.data.text }));
            break;
          case "final":
            terminal = "final";
            update(id, (m) => ({
              text: ev.data.text || m.text, // `final` carries the full authoritative text
              status: "done",
              progress: undefined,
              meta: ev.data,
            }));
            setSuggestions(ev.data.suggestions ?? []);
            setApproval(null);
            break;
          case "error":
            terminal = "error";
            update(id, {
              text: ev.data.message ?? describeError(null),
              status: "error",
              progress: undefined,
            });
            break;
        }
      };

      try {
        await streamEvents({ ...opts, token: session.token, signal: controller.signal, onEvent });
      } catch (err) {
        if (controller.signal.aborted) return "failed";
        if (err instanceof ApiError && err.status === 401) onSessionEnd("expired");
        update(id, { text: describeError(err), status: "error", progress: undefined });
        return "failed";
      }
      return terminal;
    },
    [onSessionEnd, session.token, update],
  );

  const waitForDecision = useCallback(async () => {
    const id = newId();
    setMessages((ms) => [
      ...ms,
      { id, role: "assistant", text: "", status: "streaming", progress: WAITING_TEXT },
    ]);
    setWaitFailed(false);
    const outcome = await runStream(id, {
      path: `/api/chat/${encodeURIComponent(session.threadId)}/events`,
      method: "GET",
    });
    if (outcome === "final") {
      setPhase("idle");
    } else {
      setWaitFailed(true); // keep `waiting`: the block is still pending on the server
    }
  }, [runStream, session.threadId]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || phase !== "idle") return;
    const id = newId();
    setDraft("");
    setSuggestions([]);
    setMessages((ms) => [
      ...ms,
      { id: newId(), role: "user", text: message, status: "done" },
      { id, role: "assistant", text: "", status: "streaming", progress: "Entendiendo tu mensaje…" },
    ]);
    setPhase("streaming");
    const outcome = await runStream(id, {
      path: "/api/chat/stream",
      method: "POST",
      body: { message, thread_id: session.threadId },
    });
    if (outcome === "approval_required") {
      setPhase("waiting");
      await waitForDecision();
    } else {
      setPhase("idle");
    }
  }

  const sessionOver = isPast(session.expiresAt, now);
  const placeholder =
    phase === "waiting"
      ? "Esperando la decisión del supervisor…"
      : phase === "streaming"
        ? "Lía está respondiendo…"
        : "Escribe tu mensaje (Enter para enviar)";

  return (
    <div className="flex h-dvh flex-col">
      <Header subtitle="Lía · asistente de tarjetas">
        <span
          className={`hidden items-center gap-1 text-xs sm:flex ${sessionOver ? "text-rose-700" : "text-slate-500"}`}
          title="Las sesiones de la demo duran 15 minutos"
        >
          <Clock className="h-3.5 w-3.5" aria-hidden="true" />
          {sessionOver ? "Sesión vencida" : `Sesión ${countdown(session.expiresAt, now)}`}
        </span>
        <button
          type="button"
          onClick={() => setShowDetails((v) => !v)}
          aria-pressed={showDetails}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-600 hover:bg-andes-50"
          title="Detalles técnicos (para la demo)"
        >
          <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
          <span className="sr-only">Detalles técnicos</span>
        </button>
        <button
          type="button"
          onClick={() => onSessionEnd("logout")}
          className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm text-slate-600 hover:bg-andes-50"
        >
          <LogOut className="h-4 w-4" aria-hidden="true" />
          Salir
        </button>
      </Header>

      {showDetails && (
        <div className="border-b border-andes-100 bg-andes-50 px-4 py-2 text-xs text-andes-900">
          <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-x-4 gap-y-1">
            <span>
              Conversación: <code className="font-mono">{session.threadId}</code>
            </span>
            <span>
              Cliente: <code className="font-mono">{session.customerId}</code>
            </span>
            <Link href="/auditor/" target="_blank" className="font-medium underline">
              Abrir panel de auditor
            </Link>
          </div>
        </div>
      )}

      <main className="flex-1 overflow-y-auto">
        <div
          className="mx-auto max-w-3xl space-y-4 px-4 py-6"
          role="log"
          aria-live="polite"
          aria-busy={phase === "streaming"} // announce the finished answer, not every chunk
          aria-label="Conversación con Lía"
        >
          {messages.map((m) => (
            <Bubble key={m.id} msg={m} showDetails={showDetails} />
          ))}
          <div ref={endRef} />
        </div>
      </main>

      <footer className="border-t border-andes-100 bg-white">
        <div className="mx-auto max-w-3xl px-4 pt-3">
          {approval && (
            <div
              className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-sunrise-400 bg-sunrise-100 px-3 py-2 text-sm"
            >
              <ShieldCheck className="h-4 w-4 text-sunrise-700" aria-hidden="true" />
              <span role="status">
                Bloqueo de la tarjeta <strong>{approval.card}</strong> pendiente de aprobación
                humana.
              </span>
              {/* Outside the live region: a ticking countdown must not be announced every second. */}
              <span className="ml-auto font-mono text-xs">
                {isPast(approval.expiresAt, now)
                  ? "vencida"
                  : `vence en ${countdown(approval.expiresAt, now)}`}
              </span>
              {waitFailed && (
                <button
                  type="button"
                  onClick={waitForDecision}
                  className="flex items-center gap-1 rounded-lg bg-white px-2 py-1 text-xs font-medium text-andes-800 hover:bg-andes-50"
                >
                  <RotateCw className="h-3.5 w-3.5" aria-hidden="true" />
                  Volver a consultar
                </button>
              )}
            </div>
          )}

          {suggestions.length > 0 && phase === "idle" && (
            <div className="mb-3 flex flex-wrap gap-2" aria-label="Sugerencias">
              {suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => send(s)}
                  className="rounded-full border border-andes-200 bg-white px-3 py-1 text-sm text-andes-800 hover:bg-andes-50"
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(draft);
            }}
            className="flex items-end gap-2 pb-1"
          >
            <label htmlFor="composer" className="sr-only">
              Mensaje para Lía
            </label>
            <textarea
              ref={composerRef}
              id="composer"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send(draft);
                }
              }}
              rows={1}
              maxLength={MAX_CHARS}
              disabled={phase !== "idle"}
              placeholder={placeholder}
              className="max-h-40 min-h-11 flex-1 resize-y rounded-xl border border-line px-3 py-2.5 text-sm disabled:bg-slate-50"
            />
            <button
              type="submit"
              disabled={phase !== "idle" || !draft.trim()}
              className="flex h-11 w-11 items-center justify-center rounded-xl bg-andes-700 text-white hover:bg-andes-800 disabled:opacity-50"
            >
              <Send className="h-4 w-4" aria-hidden="true" />
              <span className="sr-only">Enviar</span>
            </button>
          </form>
          {draft.length > MAX_CHARS * 0.8 && (
            <p className="text-right text-xs text-slate-500">
              {draft.length}/{MAX_CHARS}
            </p>
          )}
        </div>
        <Disclaimer />
      </footer>
    </div>
  );
}

function Bubble({ msg, showDetails }: { msg: Msg; showDetails: boolean }) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-andes-700 px-4 py-2.5 text-sm text-white">
          <span className="sr-only">Tú: </span>
          {msg.text}
        </p>
      </div>
    );
  }

  const working = msg.status === "streaming" && !msg.text;
  return (
    <div className="flex items-start gap-2">
      <div
        aria-hidden="true"
        className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-andes-100 text-sm font-semibold text-andes-800"
      >
        L
      </div>
      <div className="max-w-[85%] space-y-1.5">
        <div
          className={`rounded-2xl rounded-tl-sm border px-4 py-2.5 text-sm ${
            msg.status === "error"
              ? "border-rose-200 bg-rose-50 text-rose-900"
              : "border-andes-100 bg-white"
          }`}
        >
          {working ? (
            <p className="flex items-center gap-2 text-slate-500">
              <span className="flex gap-0.5" aria-hidden="true">
                <span className="lia-dot h-1.5 w-1.5 rounded-full bg-andes-500" />
                <span className="lia-dot h-1.5 w-1.5 rounded-full bg-andes-500" />
                <span className="lia-dot h-1.5 w-1.5 rounded-full bg-andes-500" />
              </span>
              {msg.progress ?? "Pensando…"}
            </p>
          ) : (
            <p className="whitespace-pre-wrap leading-relaxed">
              <span className="sr-only">Lía: </span>
              {msg.text}
            </p>
          )}
          {msg.status === "streaming" && msg.text && msg.progress && (
            <p className="mt-1 text-xs text-slate-500">{msg.progress}</p>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5 text-xs">
          {msg.pendingApproval && (
            <span className="rounded-full bg-sunrise-100 px-2 py-0.5 text-sunrise-700">
              Pendiente de aprobación humana
            </span>
          )}
          {msg.meta?.case_id && (
            <span className="rounded-full bg-andes-50 px-2 py-0.5 font-mono text-andes-800">
              Caso {msg.meta.case_id}
            </span>
          )}
          {showDetails && msg.meta && (
            <span className="text-slate-500">
              intención: {msg.meta.intent ?? "—"} · ruta: {msg.meta.route}
              {msg.meta.latency_ms !== undefined && ` · ${(msg.meta.latency_ms / 1000).toFixed(1)} s`}
              {msg.meta.cost_usd !== undefined && ` · US$ ${msg.meta.cost_usd.toFixed(4)}`}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
