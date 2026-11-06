"use client";

// Customer side. The session token lives only in memory (React state): a reload means
// logging in again. We deliberately don't persist the conversation in browser storage,
// because the customer's own messages may contain raw personal data.

import { useCallback, useState } from "react";

import { Disclaimer, Header } from "@/components/Brand";
import { ChatView, type CustomerSession } from "@/components/chat/ChatView";
import { LoginCard } from "@/components/chat/LoginCard";

export default function CustomerPage() {
  const [session, setSession] = useState<CustomerSession | null>(null);
  const [welcome, setWelcome] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const end = useCallback((reason: "logout" | "expired") => {
    setSession(null);
    setNotice(reason === "expired" ? "Tu sesión terminó. Vuelve a ingresar para continuar." : null);
  }, []);

  if (session) {
    return <ChatView key={session.threadId} session={session} welcome={welcome} onSessionEnd={end} />;
  }
  return (
    <div className="flex min-h-dvh flex-col">
      <Header subtitle="Lía · asistente de tarjetas" />
      <main className="flex-1">
        <LoginCard
          notice={notice}
          onLogin={(customerId, res) => {
            setWelcome(res.welcome);
            setNotice(null);
            setSession({
              token: res.token,
              expiresAt: res.expires_at,
              threadId: res.thread_id,
              customerId,
            });
          }}
        />
      </main>
      <Disclaimer />
    </div>
  );
}
