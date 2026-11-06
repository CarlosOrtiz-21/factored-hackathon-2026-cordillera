"use client";

import { LogIn } from "lucide-react";
import { useState } from "react";

import { Logo } from "@/components/Brand";
import { ApiError, api, describeError } from "@/lib/api";
import { DEMO_CUSTOMERS } from "@/lib/config";
import type { LoginResponse } from "@/lib/types";

export function LoginCard({
  notice,
  onLogin,
}: {
  notice?: string | null;
  onLogin: (customerId: string, res: LoginResponse) => void;
}) {
  const [customerId, setCustomerId] = useState(DEMO_CUSTOMERS[0] ?? "");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.login(customerId.trim(), code.trim());
      onLogin(customerId.trim(), res);
    } catch (err) {
      // The backend answers the same 401 for every failure (no account enumeration).
      setError(
        err instanceof ApiError && err.status === 401
          ? "No pudimos verificar tus datos. Revisa el ID de cliente y el código."
          : describeError(err),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto mt-10 w-full max-w-md px-4">
      <div className="rounded-2xl border border-andes-100 bg-white p-6 shadow-sm">
        <div className="mb-5 flex items-center gap-3">
          <Logo className="h-10 w-10" />
          <div>
            <h1 className="text-lg font-semibold text-andes-900">Habla con Lía</h1>
            <p className="text-sm text-slate-600">Tu asistente de tarjetas de Banco Cordillera</p>
          </div>
        </div>

        {notice && (
          <p role="status" className="mb-4 rounded-lg bg-sunrise-100 px-3 py-2 text-sm text-ink">
            {notice}
          </p>
        )}

        <form onSubmit={submit} className="space-y-4">
          <div>
            <label htmlFor="customer" className="mb-1 block text-sm font-medium">
              ID de cliente
            </label>
            <input
              id="customer"
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value)}
              autoComplete="username"
              required
              maxLength={64}
              placeholder="CLI-XXXXXXXXXXXX"
              className="w-full rounded-lg border border-line px-3 py-2 font-mono text-sm"
            />
            {DEMO_CUSTOMERS.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Clientes de demo">
                {DEMO_CUSTOMERS.map((id) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setCustomerId(id)}
                    className="rounded-full border border-andes-200 px-3 py-1 font-mono text-xs text-andes-800 hover:bg-andes-50"
                  >
                    {id}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div>
            <label htmlFor="code" className="mb-1 block text-sm font-medium">
              Código de verificación
            </label>
            <input
              id="code"
              type="password"
              inputMode="numeric"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              autoComplete="one-time-code"
              required
              maxLength={32}
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
        </form>

        <p className="mt-4 text-xs text-slate-500">
          Identidad simulada para la demo: un ID de cliente por sí solo nunca prueba quién eres,
          por eso pedimos también un código. La sesión dura 15 minutos.
        </p>
      </div>
    </div>
  );
}
