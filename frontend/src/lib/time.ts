"use client";

import { useEffect, useState } from "react";

/** Re-renders every `ms` and returns the current time (for countdowns). */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

/** "8:05" for a future instant, "0:00" once it has passed. */
export function countdown(iso: string, now: number): string {
  const left = Math.max(0, Math.floor((new Date(iso).getTime() - now) / 1000));
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

export function isPast(iso: string, now: number): boolean {
  return new Date(iso).getTime() <= now;
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("es-CO", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
