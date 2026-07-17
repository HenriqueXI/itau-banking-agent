"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, TriangleAlert, X } from "lucide-react";
import { ApiError, getAudit, listAudit } from "@/lib/api";
import { useAuth } from "@/app/providers";
import { copy } from "@/lib/copy";
import { brl, dateTime } from "@/lib/format";
import { traceUrl } from "@/lib/langfuse";
import type { AuditEvent, AuditFilters } from "@/lib/agui-types";
import { AppShell } from "@/components/shared/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;
const actions = [
  "PIX",
  "CARD_LIMIT_CHANGE",
  "STEP_UP",
  "OPERATION_CONFIRMATION",
  "AUTHORIZATION_DENIED",
  "GUARDRAIL_TRIGGERED",
  "CONVERSATION_TURN",
];

function securityEvent(event: AuditEvent): boolean {
  return /denied|guardrail|failed|forbidden/i.test(`${event.action} ${event.outcome}`);
}

function actorLabel(event: AuditEvent): string {
  return event.actor ? `${event.actor.name} — ${event.actor.email}` : "Sistema";
}

const selectClass =
  "flex h-9 w-full rounded-md border border-input bg-card px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function AdminDashboard() {
  const router = useRouter();
  const { session, logout } = useAuth();
  const [user, setUser] = useState("");
  const [debouncedUser, setDebouncedUser] = useState("");
  const [action, setAction] = useState("");
  const [period, setPeriod] = useState("7d");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    if (!session) router.replace("/login?next=/admin");
    else if (session.role !== "admin") router.replace("/403");
  }, [router, session]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedUser(user.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [user]);

  const filters = useMemo<AuditFilters>(() => {
    const now = new Date();
    const sevenDaysAgo = new Date(now);
    sevenDaysAgo.setDate(now.getDate() - 7);
    return {
      user: debouncedUser || undefined,
      action: action || undefined,
      from: period === "7d" ? sevenDaysAgo.toISOString() : from ? new Date(`${from}T00:00:00`).toISOString() : undefined,
      to: period === "7d" ? now.toISOString() : to ? new Date(`${to}T23:59:59`).toISOString() : undefined,
      page,
      pageSize: PAGE_SIZE,
    };
  }, [action, debouncedUser, from, page, period, to]);
  const audit = useQuery({ queryKey: ["audit", session?.token, filters], queryFn: () => listAudit(session!.token, filters), enabled: session?.role === "admin" });
  const detail = useQuery({ queryKey: ["audit-detail", session?.token, selectedId], queryFn: () => getAudit(session!.token, selectedId!), enabled: Boolean(selectedId && session?.role === "admin") });

  useEffect(() => {
    if (audit.error instanceof ApiError && audit.error.status === 401) { logout(); router.replace("/login?next=/admin"); }
  }, [audit.error, logout, router]);

  if (!session || session.role !== "admin") return null;
  const totalPages = Math.max(1, Math.ceil((audit.data?.total ?? 0) / PAGE_SIZE));

  return (
    <AppShell className="overflow-y-auto">
      <main className="mx-auto w-full max-w-6xl space-y-4 p-4">
        <header>
          <h1 className="text-xl font-semibold tracking-tight">{copy.admin.title}</h1>
          <p className="text-sm text-muted-foreground">{copy.admin.subtitle}</p>
        </header>

        <Card aria-label={copy.admin.filters} className="animate-message-in">
          <CardContent className="flex flex-wrap items-end gap-3 p-4">
            <label className="grid min-w-40 gap-1.5 text-sm font-medium">
              {copy.admin.user}
              <Input aria-label={copy.admin.user} value={user} onChange={(event) => { setUser(event.target.value); setPage(1); }} />
            </label>
            <label className="grid min-w-40 gap-1.5 text-sm font-medium">
              {copy.admin.action}
              <select aria-label={copy.admin.action} className={selectClass} value={action} onChange={(event) => { setAction(event.target.value); setPage(1); }}>
                <option value="">{copy.admin.allActions}</option>
                {actions.map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            </label>
            <label className="grid min-w-40 gap-1.5 text-sm font-medium">
              {copy.admin.period}
              <select aria-label={copy.admin.period} className={selectClass} value={period} onChange={(event) => { setPeriod(event.target.value); setPage(1); }}>
                <option value="7d">{copy.admin.last7days}</option>
                <option value="custom">{copy.admin.customRange}</option>
              </select>
            </label>
            {period === "custom" && (
              <>
                <label className="grid gap-1.5 text-sm font-medium">
                  {copy.admin.from}
                  <Input aria-label={copy.admin.from} type="date" value={from} onChange={(event) => { setFrom(event.target.value); setPage(1); }} />
                </label>
                <label className="grid gap-1.5 text-sm font-medium">
                  {copy.admin.to}
                  <Input aria-label={copy.admin.to} type="date" value={to} onChange={(event) => { setTo(event.target.value); setPage(1); }} />
                </label>
              </>
            )}
          </CardContent>
        </Card>

        <Card aria-live="polite" className="animate-message-in overflow-hidden" style={{ animationDelay: "60ms" }}>
          {audit.isLoading && (
            <CardContent className="space-y-2 p-4">
              {Array.from({ length: 6 }, (_, index) => (
                <Skeleton key={index} className="h-10 w-full" />
              ))}
            </CardContent>
          )}
          {audit.isError && !(audit.error instanceof ApiError && audit.error.status === 401) && (
            <CardContent className="p-4">
              <p role="alert" className="flex items-center gap-3 text-sm">
                {copy.admin.error}
                <Button variant="outline" size="sm" onClick={() => void audit.refetch()}>
                  {copy.common.retry}
                </Button>
              </p>
            </CardContent>
          )}
          {!audit.isLoading && !audit.isError && audit.data?.items.length === 0 && (
            <CardContent className="p-8 text-center text-sm text-muted-foreground">{copy.admin.empty}</CardContent>
          )}
          {audit.data && audit.data.items.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">{copy.admin.events}</caption>
                <thead>
                  <tr className="border-b bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="px-4 py-3 font-medium">{copy.admin.when}</th>
                    <th className="px-4 py-3 font-medium">{copy.admin.user}</th>
                    <th className="px-4 py-3 font-medium">{copy.admin.action}</th>
                    <th className="px-4 py-3 font-medium">{copy.admin.amount}</th>
                    <th className="px-4 py-3 font-medium">{copy.admin.outcome}</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.data.items.map((event) => {
                    const security = securityEvent(event);
                    return (
                      <tr
                        key={event.id}
                        className={cn("border-b transition-colors last:border-0 hover:bg-accent/50", security && "bg-destructive/8")}
                      >
                        <td className="whitespace-nowrap px-4 py-2.5">
                          <button
                            className="inline-flex items-center gap-1.5 underline decoration-muted-foreground/40 underline-offset-4 hover:decoration-foreground"
                            onClick={() => setSelectedId(event.id)}
                          >
                            {security && <TriangleAlert aria-label={copy.admin.securityEvent} className="size-3.5 text-destructive" />}
                            {dateTime(event.occurred_at)}
                          </button>
                        </td>
                        <td className="whitespace-nowrap px-4 py-2.5">{actorLabel(event)}</td>
                        <td className="whitespace-nowrap px-4 py-2.5 font-medium">{event.action}</td>
                        <td className="whitespace-nowrap px-4 py-2.5 tabular-nums">{brl(event.amount)}</td>
                        <td className="whitespace-nowrap px-4 py-2.5">
                          <Badge variant={security ? "destructive" : "success"}>{event.outcome}</Badge>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {audit.data && audit.data.total > PAGE_SIZE && (
            <nav aria-label="Paginação" className="flex items-center justify-end gap-3 border-t px-4 py-3">
              <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage((current) => current - 1)}>
                {copy.admin.previous}
              </Button>
              <span className="text-sm text-muted-foreground">{copy.admin.pageOf(page, totalPages)}</span>
              <Button variant="outline" size="sm" disabled={page === totalPages} onClick={() => setPage((current) => current + 1)}>
                {copy.admin.next}
              </Button>
            </nav>
          )}
        </Card>

        {selectedId && (
          <AuditDetailDrawer event={detail.data} loading={detail.isLoading} error={detail.isError} onClose={() => setSelectedId(null)} />
        )}
      </main>
    </AppShell>
  );
}

export function AuditDetailDrawer({ event, loading, error, onClose }: { event?: AuditEvent; loading: boolean; error: boolean; onClose(): void }) {
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeButton.current?.focus();
  }, []);
  return (
    <div role="presentation" className="fixed inset-0 z-30 flex animate-fade-in justify-end bg-black/45 p-3" onMouseDown={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={copy.admin.detailTitle}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex w-full max-w-xl animate-slide-in-right flex-col overflow-y-auto rounded-xl border bg-card p-5 text-card-foreground shadow-xl"
      >
        {loading && <p className="text-sm text-muted-foreground">{copy.admin.loadingDetail}</p>}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {copy.admin.error}
          </p>
        )}
        {event && (
          <>
            <header className="flex items-center justify-between gap-4">
              <h2 className="text-lg font-semibold tracking-tight">{copy.admin.detailTitle}</h2>
              <Button ref={closeButton} variant="ghost" size="sm" onClick={onClose}>
                <X aria-hidden />
                {copy.common.close}
              </Button>
            </header>
            <dl className="mt-4 space-y-3 text-sm">
              <div>
                <dt className="font-medium text-muted-foreground">{copy.admin.user}</dt>
                <dd className="mt-0.5">{actorLabel(event)}</dd>
                <dd className="mt-0.5 break-all font-mono text-xs text-muted-foreground">
                  {event.user_ref}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-muted-foreground">{copy.admin.resource}</dt>
                <dd className="mt-0.5 break-all font-mono text-xs">{event.resource}</dd>
              </div>
              <div>
                <dt className="font-medium text-muted-foreground">{copy.admin.outcome}</dt>
                <dd className="mt-0.5">
                  <Badge variant={/denied|guardrail|failed|forbidden/i.test(event.outcome) ? "destructive" : "success"}>{event.outcome}</Badge>
                </dd>
              </div>
              <div>
                <dt className="font-medium text-muted-foreground">{copy.admin.eventId}</dt>
                <dd className="mt-0.5 break-all font-mono text-xs">{event.event_id}</dd>
              </div>
            </dl>
            <h3 className="mt-5 text-sm font-semibold">{copy.admin.details}</h3>
            <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-4 font-mono text-xs">
              {JSON.stringify(event.details, null, 2)}
            </pre>
            {event.trace_id ? (
              <a
                href={traceUrl(event.trace_id)}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-4 inline-flex h-9 w-fit items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
              >
                <ExternalLink aria-hidden className="size-4" />
                {copy.admin.openTrace}
              </a>
            ) : (
              <p className="mt-4 text-sm text-muted-foreground">{copy.admin.traceUnavailable}</p>
            )}
          </>
        )}
      </aside>
    </div>
  );
}

export { actorLabel, brl, dateTime, securityEvent };
