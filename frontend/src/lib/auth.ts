import type { Session, UserRole } from "@/lib/agui-types";

const KEY = "itau-agent-session";

export const personas: Array<{ name: string; email: string; role: UserRole; icon: string }> = [
  { name: "Ana", email: "ana@demo", role: "customer", icon: "👤" },
  { name: "Bruno", email: "bruno@demo", role: "manager", icon: "👔" },
  { name: "Carla", email: "carla@demo", role: "admin", icon: "🛡" },
];

export function saveSession(session: Session): void {
  sessionStorage.setItem(KEY, JSON.stringify(session));
}
export function loadSession(): Session | null {
  const stored = sessionStorage.getItem(KEY);
  if (!stored) return null;
  try { return JSON.parse(stored) as Session; } catch { sessionStorage.removeItem(KEY); return null; }
}
export function clearSession(): void { sessionStorage.removeItem(KEY); }
