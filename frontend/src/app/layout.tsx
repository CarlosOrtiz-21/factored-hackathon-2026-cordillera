import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Lía · Banco Cordillera",
  description:
    "Asistente de tarjetas con aprobación humana. Banco ficticio y datos sintéticos (Factored AI & Data Hackathon 2026).",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body className="min-h-dvh font-sans antialiased">{children}</body>
    </html>
  );
}
