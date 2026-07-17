"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useContext, useMemo, useState } from "react";
import type { PropsWithChildren } from "react";
import type { Session } from "@/lib/agui-types";
import { clearSession, loadSession, saveSession } from "@/lib/auth";
import { ErrorBoundary } from "@/components/shared/error-boundary";

interface AuthContextValue { session: Session | null; setSession(session: Session): void; logout(): void; }
const AuthContext = createContext<AuthContextValue | null>(null);
export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider ausente");
  return value;
}
export function Providers({ children }: PropsWithChildren) {
  const [queryClient] = useState(() => new QueryClient());
  const [session, setSessionState] = useState<Session | null>(() =>
    typeof window === "undefined" ? null : loadSession(),
  );
  const value = useMemo<AuthContextValue>(() => ({
    session,
    setSession(next) { saveSession(next); setSessionState(next); },
    logout() { clearSession(); setSessionState(null); },
  }), [session]);
  return <ErrorBoundary><QueryClientProvider client={queryClient}><AuthContext.Provider value={value}>{children}</AuthContext.Provider></QueryClientProvider></ErrorBoundary>;
}
