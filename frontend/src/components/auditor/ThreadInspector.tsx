"use client";

// Read-only view of GET /api/threads/{id}/state: the audit artifacts (sources, rules,
// execution records). Everything here is tokens or masked text, never raw PII, and never
// chain-of-thought.

import { RefreshCw, Search } from "lucide-react";
import { useEffect, useState } from "react";

import { api, describeError } from "@/lib/api";
import { clockTime } from "@/lib/time";
import type { ThreadState } from "@/lib/types";

export function ThreadInspector({
  token,
  threadId,
  onThreadChange,
  onForbidden,
}: {
  token: string;
  threadId: string;
  onThreadChange: (id: string) => void;
  onForbidden: () => void;
}) {
  const [input, setInput] = useState(threadId);
  const [state, setState] = useState<ThreadState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    setInput(threadId);
  }, [threadId]);

  useEffect(() => {
    if (!threadId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .threadState(token, threadId)
      .then((s) => !cancelled && setState(s))
      .catch((err) => {
        if (cancelled) return;
        if (err?.status === 403) onForbidden();
        setState(null);
        setError(describeError(err));
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [token, threadId, reload, onForbidden]);

  return (
    <section aria-labelledby="inspector-title" className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 id="inspector-title" className="text-lg font-semibold text-andes-900">
          Traza de la conversación
        </h2>
        {threadId && (
          <button
            type="button"
            onClick={() => setReload((n) => n + 1)}
            className="ml-auto rounded-lg p-1.5 text-slate-600 hover:bg-andes-50"
            title="Actualizar"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
            <span className="sr-only">Actualizar traza</span>
          </button>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (input.trim() === threadId) setReload((n) => n + 1);
          else onThreadChange(input.trim());
        }}
        className="flex gap-2"
      >
        <label htmlFor="thread-id" className="sr-only">
          ID de conversación
        </label>
        <input
          id="thread-id"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="th-…"
          maxLength={64}
          className="flex-1 rounded-lg border border-line px-3 py-1.5 font-mono text-sm"
        />
        <button
          type="submit"
          disabled={!input.trim()}
          className="flex items-center gap-1 rounded-lg bg-andes-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-andes-800 disabled:opacity-50"
        >
          <Search className="h-4 w-4" aria-hidden="true" />
          Ver
        </button>
      </form>

      {error && (
        <p role="alert" className="text-sm text-rose-700">
          {error}
        </p>
      )}
      {!threadId && !error && (
        <p className="text-sm text-slate-500">
          Elige “Ver traza” en una solicitud o pega el ID de una conversación (el chat lo muestra
          en Detalles técnicos).
        </p>
      )}
      {state && <StateView s={state} />}
    </section>
  );
}

function StateView({ s }: { s: ThreadState }) {
  const totals = s.usage.reduce(
    (t, u) => ({
      inTok: t.inTok + u.input_tokens,
      outTok: t.outTok + u.output_tokens,
      ms: t.ms + u.latency_ms,
    }),
    { inTok: 0, outTok: 0, ms: 0 },
  );

  return (
    <div className="space-y-4 text-sm">
      <dl className="grid grid-cols-3 gap-2">
        <Fact label="Intención" value={s.intent} />
        <Fact label="Fuente del triage" value={s.triage_source} />
        <Fact label="Ruta" value={s.route_reason} />
      </dl>

      {s.safety_violations.length > 0 && (
        <Block title="Violaciones de seguridad detectadas" tone="danger">
          <Json value={s.safety_violations} />
        </Block>
      )}
      {s.proposed_action && (
        <Block title="Acción propuesta">
          <Json value={s.proposed_action} />
        </Block>
      )}
      {s.human_decision && (
        <Block title="Decisión humana">
          <Json value={s.human_decision} />
        </Block>
      )}
      {s.action_result && (
        <Block title="Resultado verificado">
          <Json value={s.action_result} />
        </Block>
      )}
      {s.escalation && (
        <Block title="Traspaso a un asesor (handoff)">
          <Json value={s.escalation} />
        </Block>
      )}
      {s.citations.length > 0 && (
        <Block title="Citas de políticas">
          <Json value={s.citations} />
        </Block>
      )}

      <Block title={`Mensajes (${s.messages.length}) · texto saneado`}>
        <ol className="space-y-1.5">
          {s.messages.map((m, i) => (
            <li key={i} className="whitespace-pre-wrap">
              <span className="mr-1 font-medium text-andes-800">
                {m.role === "user" ? "Cliente:" : "Lía:"}
              </span>
              {m.content}
            </li>
          ))}
        </ol>
      </Block>

      <Block title={`Audit trail (${s.audit_trail.length} eventos, append-only)`}>
        <ol className="relative space-y-2 border-l border-andes-200 pl-4">
          {s.audit_trail.map((e, i) => (
            <li key={i} className="relative">
              <span
                aria-hidden="true"
                className="absolute top-1.5 -left-[21.5px] h-2.5 w-2.5 rounded-full bg-andes-500"
              />
              <details>
                <summary className="cursor-pointer">
                  <span className="font-mono text-xs text-slate-500">{clockTime(e.ts)}</span>{" "}
                  <span className="font-medium">{e.node}</span> ·{" "}
                  <span className="text-andes-800">{e.event}</span>
                </summary>
                <Json value={e.payload} />
              </details>
            </li>
          ))}
        </ol>
      </Block>

      {s.usage.length > 0 && (
        <Block title="Uso de modelos">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-slate-500">
                <tr>
                  <th className="py-1 pr-3 font-medium">Nodo</th>
                  <th className="py-1 pr-3 font-medium">Modelo</th>
                  <th className="py-1 pr-3 text-right font-medium">Tokens in</th>
                  <th className="py-1 pr-3 text-right font-medium">Tokens out</th>
                  <th className="py-1 text-right font-medium">Latencia</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {s.usage.map((u, i) => (
                  <tr key={i} className="border-t border-slate-100">
                    <td className="py-1 pr-3">{u.node}</td>
                    <td className="py-1 pr-3">{u.model}</td>
                    <td className="py-1 pr-3 text-right">{u.input_tokens}</td>
                    <td className="py-1 pr-3 text-right">{u.output_tokens}</td>
                    <td className="py-1 text-right">{(u.latency_ms / 1000).toFixed(2)} s</td>
                  </tr>
                ))}
                <tr className="border-t border-line font-semibold">
                  <td className="py-1 pr-3" colSpan={2}>
                    Total
                  </td>
                  <td className="py-1 pr-3 text-right">{totals.inTok}</td>
                  <td className="py-1 pr-3 text-right">{totals.outTok}</td>
                  <td className="py-1 text-right">{(totals.ms / 1000).toFixed(2)} s</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Block>
      )}
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="rounded-lg border border-andes-100 bg-white px-3 py-2">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="truncate font-mono text-xs">{value ?? "—"}</dd>
    </div>
  );
}

function Block({
  title,
  tone = "normal",
  children,
}: {
  title: string;
  tone?: "normal" | "danger";
  children: React.ReactNode;
}) {
  return (
    <div
      className={`rounded-xl border bg-white p-3 ${tone === "danger" ? "border-rose-300" : "border-andes-100"}`}
    >
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      {children}
    </div>
  );
}

function Json({ value }: { value: unknown }) {
  return (
    <pre className="mt-1 max-h-72 overflow-auto rounded-lg bg-mist p-2 font-mono text-xs leading-relaxed">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
