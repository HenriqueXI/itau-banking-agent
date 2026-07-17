"use client";

import { useEffect, useRef, useState } from "react";

export interface Countdown {
  minutes: number;
  seconds: number;
  expired: boolean;
  /** Remaining fraction of the interval observed at mount, for progress rings. */
  fraction: number;
}

function monotonicNow(): number {
  return typeof performance === "undefined" ? Date.now() : performance.now();
}

export function useCountdown(expiresAt: string, issuedAt?: string): Countdown {
  const [, setTick] = useState(0);
  const reference = useRef<{
    expiresAt: string;
    issuedAt?: string;
    receivedAt: number;
    initialDuration: number;
  } | null>(null);
  if (reference.current?.expiresAt !== expiresAt || reference.current?.issuedAt !== issuedAt) {
    const expiresAtMs = new Date(expiresAt).getTime();
    const issuedAtMs = issuedAt ? new Date(issuedAt).getTime() : Number.NaN;
    const serverDuration = expiresAtMs - issuedAtMs;
    const fallbackDuration = expiresAtMs - Date.now();
    reference.current = {
      expiresAt,
      issuedAt,
      receivedAt: monotonicNow(),
      initialDuration: Number.isFinite(serverDuration) && serverDuration >= 0
        ? serverDuration
        : Number.isFinite(fallbackDuration) ? fallbackDuration : 0,
    };
  }

  useEffect(() => {
    const timer = window.setInterval(() => setTick((current) => current + 1), 1000);
    return () => clearInterval(timer);
  }, []);

  const current = reference.current;
  const elapsed = monotonicNow() - current.receivedAt;
  const remaining = Math.max(0, current.initialDuration - elapsed);
  const initial = Math.max(1, current.initialDuration);

  return {
    minutes: Math.floor(remaining / 60000),
    seconds: Math.floor((remaining % 60000) / 1000),
    expired: remaining <= 0,
    fraction: Math.min(1, remaining / initial),
  };
}
