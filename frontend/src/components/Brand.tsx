import Link from "next/link";

/** Mountain-range mark for the fictional Banco Cordillera. */
export function Logo({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <rect width="32" height="32" rx="8" className="fill-andes-700" />
      <path d="M4 24 L12 12 L16 18 L20 10 L28 24 Z" className="fill-andes-100" />
      <path d="M20 10 L22.6 14 L20 13 L17.6 14.2 Z" className="fill-white" />
      <circle cx="9" cy="8" r="2.4" className="fill-sunrise-400" />
    </svg>
  );
}

export function Header({ subtitle, children }: { subtitle: string; children?: React.ReactNode }) {
  return (
    <header className="border-b border-andes-100 bg-white">
      <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3">
        <Link href="/" className="flex items-center gap-3 rounded-lg">
          <Logo />
          <div className="leading-tight">
            <p className="font-semibold text-andes-900">Banco Cordillera</p>
            <p className="text-xs text-slate-500">{subtitle}</p>
          </div>
        </Link>
        <div className="ml-auto flex items-center gap-2">{children}</div>
      </div>
    </header>
  );
}

/** Required disclosure: fictional bank, synthetic data, demo. */
export function Disclaimer() {
  return (
    <p className="px-4 py-3 text-center text-xs text-slate-600">
      Banco Cordillera es un banco <strong>ficticio</strong>. Datos y políticas{" "}
      <strong>sintéticos</strong> · Prototipo para el Factored AI &amp; Data Hackathon 2026.
    </p>
  );
}
