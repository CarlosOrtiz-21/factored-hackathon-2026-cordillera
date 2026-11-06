"use client";

// Auditor panel: the human in human-in-the-loop. Lists pending card blocks, approves or
// rejects them (POST /api/approve), and inspects any conversation's audit trail.
//
// Segregation of duties is enforced by the backend, not here: the approver id comes from
// the auditor's token, and an approver equal to the requester is refused by the graph.

import { CheckCircle2, Inbox, LogIn, LogOut, RefreshCw, ShieldAlert, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ApprovalCard } from "@/components/auditor/ApprovalCard";
import { ThreadInspector } from "@/components/auditor/ThreadInspector";
import { Disclaimer, Header } from "@/components/Brand";
import { ApiError, api, describeError } from "@/lib/api";
import { useNow } from "@/lib/time";
import type { ApproveResponse, Health, PendingApproval } from "@/lib/types";

const POLL_MS = 5000;

interface StaffSession {
  token: string;
  auditorId: string;
}

interface DecisionLog extends ApproveResponse {
  threadId: string;
  card: string;
}

export default function AuditorPage() {
  const [session, setSession] = useState<StaffSession | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const forbidden = useCallback(() => {
    setSession(null);
    setNotice("Tu sesión de auditor terminó. Vuelve a ingresar.");
  }, []);

  return (
    <div className="flex min-h-dvh flex-col">
      <Header subtitle="Panel de auditoría · aprobación humana">
        {health && (
          <span className="hidden text-xs text-slate-500 md:inline" title={JSON.stringify(health.models)}>
            prompts {health.prompt_version}
          </span>
        )}
        {session && (
          <>
            <span className="font-mono text-xs text-slate-600">{session.auditorId}</span>
            <button
              type="button"
              onClick={() => setSession(null)}
              className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm text-slate-600 hover:bg-andes-50"
            >
              <LogOut className="h-4 w-4" aria-hidden="true" />
              Salir
            </button>
          </>
        )}
      </Header>
      <main className="flex-1">
        {session ? (
          <Dashboard session={session} onForbidden={forbidden} />
        ) : (
          <AuditorLogin
            notice={notice}
            onLogin={(s) => {
              setNotice(null);
              setSession(s);
            }}
          />
        )}
      </main>
      <Disclaimer />
    </div>
  );
}

function AuditorLogin({
  notice,
  onLogin,
}: {
  notice: string | null;
  onLogin: (s: StaffSession) => void;
}) {
  const [auditorId, setAuditorId] = useState("auditor-01");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.auditorLogin(auditorId.trim(), code.trim());
      onLogin({ token: res.token, auditorId: auditorId.trim() });
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401 ? "Credenciales inválidas." : describeError(err),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mx-auto mt-10 w-full max-w-sm space-y-4 rounded-2xl border border-andes-100 bg-white p-6 shadow-sm"
    >
      <div className="flex items-center gap-2">
        <ShieldAlert className="h-5 w-5 text-andes-700" aria-hidden="true" />
        <h1 className="text-lg font-semibold text-andes-900">Ingreso de auditor</h1>
      </div>
      {notice && (
        <p role="status" className="rounded-lg bg-sunrise-100 px-3 py-2 text-sm">
          {notice}
        </p>
      )}
      <div>
        <label htmlFor="auditor" className="mb-1 block text-sm font-medium">
          ID de auditor
        </label>
        <input
          id="auditor"
          value={auditorId}
          onChange={(e) => setAuditorId(e.target.value)}
          required
          maxLength={64}
          autoComplete="username"
          className="w-full rounded-lg border border-line px-3 py-2 font-mono text-sm"
        />
      </div>
      <div>
        <label htmlFor="acode" className="mb-1 block text-sm font-medium">
          Código de auditor
        </label>
        <input
          id="acode"
          type="password"
          inputMode="numeric"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          required
          maxLength={32}
          autoComplete="current-password"
          className="w-full rounded-lg border border-line px-3 py-2 text-sm"
        />
      </div>
      {error && (
        <p role="alert" className="text-sm text-rose-700">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={busy}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-andes-700 px-4 py-2.5 font-medium text-white hover:bg-andes-800 disabled:opacity-60"
      >
        <LogIn className="h-4 w-4" aria-hidden="true" />
        {busy ? "Verificando…" : "Ingresar"}
      </button>
      <p className="text-xs text-slate-500">
        Rol simulado para la demo. Quien aprueba nunca puede ser quien solicitó la acción.
      </p>
    </form>
  );
}

function Dashboard({ session, onForbidden }: { session: StaffSession; onForbidden: () => void }) {
  const [pending, setPending] = useState<PendingApproval[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyThread, setBusyThread] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<DecisionLog[]>([]);
  const [inspect, setInspect] = useState("");
  const [tick, setTick] = useState(0);
  const now = useNow();

  // Poll pending approvals. Each call also makes the backend expire stale ones.
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .pending(session.token)
        .then((items) => {
          if (cancelled) return;
          setPending(items);
          setLoadError(null);
        })
        .catch((err) => {
          if (cancelled) return;
          if (err instanceof ApiError && err.status === 403) onForbidden();
          else setLoadError(describeError(err));
        });
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [session.token, onForbidden, tick]);

  async function decide(item: PendingApproval, decision: "approved" | "rejected", comment: string) {
    setBusyThread(item.thread_id);
    try {
      const res = await api.approve(session.token, item.thread_id, decision, comment);
      setDecisions((d) => [{ ...res, threadId: item.thread_id, card: item.card }, ...d].slice(0, 10));
      setPending((p) => p.filter((x) => x.thread_id !== item.thread_id));
      setInspect(item.thread_id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) onForbidden();
      else setLoadError(describeError(err));
    } finally {
      setBusyThread(null);
      setTick((t) => t + 1);
    }
  }

  return (
    <div className="mx-auto grid max-w-6xl gap-6 px-4 py-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <div className="space-y-6">
        <section aria-labelledby="pending-title" className="space-y-3">
          <div className="flex items-center gap-2">
            <h2 id="pending-title" className="text-lg font-semibold text-andes-900">
              Solicitudes pendientes
            </h2>
            <span className="rounded-full bg-andes-100 px-2 text-xs font-medium text-andes-800">
              {pending.length}
            </span>
            <button
              type="button"
              onClick={() => setTick((t) => t + 1)}
              className="ml-auto rounded-lg p-1.5 text-slate-600 hover:bg-andes-50"
              title={`Se actualiza cada ${POLL_MS / 1000} s`}
            >
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              <span className="sr-only">Actualizar</span>
            </button>
          </div>
          {loadError && (
            <p role="alert" className="text-sm text-rose-700">
              {loadError}
            </p>
          )}
          {pending.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-andes-200 bg-white px-4 py-8 text-center text-sm text-slate-500">
              <Inbox className="h-6 w-6" aria-hidden="true" />
              No hay bloqueos esperando aprobación. Pídele a Lía en el chat que bloquee una
              tarjeta para ver uno aquí.
            </div>
          ) : (
            pending.map((item) => (
              <ApprovalCard
                key={`${item.thread_id}-${item.requested_at}`}
                item={item}
                now={now}
                busy={busyThread === item.thread_id}
                onDecide={(d, c) => decide(item, d, c)}
                onInspect={() => setInspect(item.thread_id)}
              />
            ))
          )}
        </section>

        {decisions.length > 0 && (
          <section aria-labelledby="decided-title" aria-live="polite" className="space-y-2">
            <h2 id="decided-title" className="text-sm font-semibold text-slate-600">
              Decisiones de esta sesión
            </h2>
            {decisions.map((d, i) => (
              <DecisionRow key={i} d={d} onInspect={() => setInspect(d.threadId)} />
            ))}
          </section>
        )}
      </div>

      <ThreadInspector
        token={session.token}
        threadId={inspect}
        onThreadChange={setInspect}
        onForbidden={onForbidden}
      />
    </div>
  );
}

const DECISION_LABEL: Record<string, string> = {
  approved: "Aprobada",
  rejected: "Rechazada",
  expired: "Vencida",
};

function DecisionRow({ d, onInspect }: { d: DecisionLog; onInspect: () => void }) {
  const ok = d.decision === "approved";
  return (
    <div className="rounded-xl border border-andes-100 bg-white p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {ok ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />
        ) : (
          <XCircle className="h-4 w-4 text-rose-600" aria-hidden="true" />
        )}
        <span className="font-medium">{DECISION_LABEL[d.decision] ?? d.decision}</span>
        <span className="font-mono text-xs">{d.card}</span>
        {ok && (
          <span
            className={`rounded-full px-2 py-0.5 text-xs ${
              d.verified ? "bg-emerald-50 text-emerald-800" : "bg-rose-50 text-rose-800"
            }`}
          >
            {d.verified ? "verificado en el banco" : "NO verificado"}
          </span>
        )}
        <button type="button" onClick={onInspect} className="ml-auto text-xs text-andes-800 underline">
          Ver traza
        </button>
      </div>
      {d.note && <p className="mt-1 text-xs text-rose-800">Nota del sistema: {d.note}</p>}
      <p className="mt-2 rounded-lg bg-mist px-3 py-2 text-xs text-slate-700">
        <span className="font-medium">El cliente ve:</span> {d.customer_message}
      </p>
    </div>
  );
}
