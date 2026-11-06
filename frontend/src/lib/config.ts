// Build-time configuration. NEXT_PUBLIC_* values are inlined into the static bundle.

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(
  /\/+$/,
  "",
);

export const DEMO_CUSTOMERS: string[] = (process.env.NEXT_PUBLIC_DEMO_CUSTOMERS ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);
