"use client";

// One pending block request. Two-step decision (choose, then confirm) because this is the
// HITL barrier: an approval makes tool_executor actually block a real card in the bank.

import { Check, CreditCard, Search, X } from "lucide-react";
import { useState } from "react";

import { clockTime, countdown, isPast } from "@/lib/time";
import type { PendingApproval } from "@/lib/types";

type Decision = "approved" | "rejected";

export function ApprovalCard({
  item,
  now,
  busy,
  onDecide,
  onInspect,
}: {
  item: PendingApproval;
  now: number;
  busy: boolean;
  onDecide: (decision: Decision, comment: string) => void;
  onInspect: () => void;
}) {
  const [confirming, setConfirming] = useState<Decision | null>(null);
  const [comment, setComment] = useState("");
  const expired = isPast(item.expires_at, now);
  const urgent = !expired && new Date(item.expires_at).getTime() - now < 2 * 60_000;
  const risk = item.risk_score ?? null;

  return (
    <article className="rounded-xl border border-andes-100 bg-white p-4 shadow-sm">
      <header className="flex flex-wrap items-center gap-2">
        <CreditCard className="h-4 w-4 text-andes-700" aria-hidden="true" />
        <h3 className="font-semibold text-andes-900">
          Bloqueo de tarjeta <span className="font-mono">{item.card}</span>
        </h3>
        <span
          className={`ml-auto rounded-full px-2 py-0.5 font-mono text-xs ${
            expired
              ? "bg-slate-100 text-slate-600"
              : urgent
                ? "bg-rose-100 text-rose-800"
                : "bg-sunrise-100 text-sunrise-700"
          }`}
        >
          {expired ? "vencida" : `vence en ${countdown(item.expires_at, now)}`}
        </span>
      </header>

      <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-slate-500">Motivo</dt>
        <dd>{item.reason}</dd>
        <dt className="text-slate-500">Solicitante</dt>
        <dd className="font-mono text-xs leading-5">{item.requester_id}</dd>
        <dt className="text-slate-500">Solicitada</dt>
        <dd>{clockTime(item.requested_at)}</dd>
        <dt className="text-slate-500">Riesgo</dt>
        <dd className="flex items-center gap-2">
          {risk === null ? (
            "—"
          ) : (
            <>
              <span className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
                <span
                  className={`block h-full ${risk > 0.7 ? "bg-rose-500" : "bg-andes-500"}`}
                  style={{ width: `${Math.round(risk * 100)}%` }}
                />
              </span>
              <span className="font-mono text-xs">{risk.toFixed(2)}</span>
            </>
          )}
        </dd>
      </dl>
      {item.summary && (
        <p className="mt-3 rounded-lg bg-mist px-3 py-2 text-sm text-slate-700">{item.summary}</p>
      )}

      <label htmlFor={`c-${item.thread_id}`} className="mt-3 block text-xs font-medium text-slate-600">
        Comentario (opcional, queda en la traza)
      </label>
      <input
        id={`c-${item.thread_id}`}
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        maxLength={500}
        className="mt-1 w-full rounded-lg border border-line px-3 py-1.5 text-sm"
      />

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {confirming ? (
          <>
            <span className="text-sm">
              {confirming === "approved"
                ? "¿Confirmas el bloqueo? Se ejecutará en el banco."
                : "¿Confirmas el rechazo? Se cancelará la solicitud."}
            </span>
            <button
              type="button"
              disabled={busy}
              autoFocus // the button that was clicked just unmounted; keep keyboard focus here
              onClick={() => onDecide(confirming, comment)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60 ${
                confirming === "approved" ? "bg-andes-700 hover:bg-andes-800" : "bg-rose-700 hover:bg-rose-800"
              }`}
            >
              {busy ? "Enviando…" : "Confirmar"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirming(null)}
              className="rounded-lg px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
            >
              Cancelar
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirming("approved")}
              className="flex items-center gap-1 rounded-lg bg-andes-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-andes-800 disabled:opacity-60"
            >
              <Check className="h-4 w-4" aria-hidden="true" />
              Aprobar
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirming("rejected")}
              className="flex items-center gap-1 rounded-lg border border-rose-300 px-3 py-1.5 text-sm font-medium text-rose-800 hover:bg-rose-50 disabled:opacity-60"
            >
              <X className="h-4 w-4" aria-hidden="true" />
              Rechazar
            </button>
          </>
        )}
        <button
          type="button"
          onClick={onInspect}
          className="ml-auto flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm text-andes-800 hover:bg-andes-50"
        >
          <Search className="h-4 w-4" aria-hidden="true" />
          Ver traza
        </button>
      </div>
    </article>
  );
}
