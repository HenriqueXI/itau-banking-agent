"use client";

import { useRef, useState } from "react";
import { CheckCircle2, ShieldAlert, TimerOff } from "lucide-react";
import type { ConfirmationPayload } from "@/lib/agui-types";
import { copy } from "@/lib/copy";
import { brl } from "@/lib/format";
import { useCountdown } from "@/hooks/use-countdown";
import { useFocusTrap } from "@/hooks/use-focus-trap";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const RADIUS = 15;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

function CountdownRing({ fraction }: { fraction: number }) {
  return (
    <svg aria-hidden viewBox="0 0 36 36" className="size-9 -rotate-90">
      <circle cx="18" cy="18" r={RADIUS} fill="none" strokeWidth="3" className="stroke-muted" />
      <circle
        cx="18"
        cy="18"
        r={RADIUS}
        fill="none"
        strokeWidth="3"
        strokeLinecap="round"
        className="stroke-primary transition-[stroke-dashoffset] duration-1000 ease-linear"
        strokeDasharray={CIRCUMFERENCE}
        strokeDashoffset={(1 - fraction) * CIRCUMFERENCE}
      />
    </svg>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

export function ConfirmationCard({
  payload,
  onResolve,
}: {
  payload: ConfirmationPayload;
  onResolve(response: "confirm" | "cancel"): Promise<void>;
}) {
  if (!payload || typeof payload !== "object" || !payload.operationHash) throw new Error("ConfirmationCard exige payload tipado");
  return <ConfirmationCardInner payload={payload} onResolve={onResolve} />;
}

function ConfirmationCardInner({
  payload,
  onResolve,
}: {
  payload: ConfirmationPayload;
  onResolve(response: "confirm" | "cancel"): Promise<void>;
}) {
  const [state, setState] = useState<"active" | "resolved">("active");
  const [outcome, setOutcome] = useState<"confirm" | "cancel" | null>(null);
  const dialog = useRef<HTMLElement>(null);
  const countdown = useCountdown(payload.expiresAt, payload.issuedAt);
  useFocusTrap(dialog, state === "active" && !countdown.expired);

  async function resolve(response: "confirm" | "cancel") {
    await onResolve(response);
    setOutcome(response);
    setState("resolved");
  }

  if (state === "resolved")
    return (
      <section className="ml-11 flex animate-message-in items-center gap-3 rounded-xl border bg-card px-4 py-3 text-sm shadow-sm">
        <CheckCircle2 aria-hidden className="size-4 text-success" />
        {copy.confirmation.resolved}
        <Badge variant={outcome === "confirm" ? "success" : "secondary"}>
          {outcome === "confirm" ? copy.common.confirm : copy.common.cancel}
        </Badge>
      </section>
    );

  return (
    <section
      ref={dialog}
      tabIndex={-1}
      role="alertdialog"
      aria-label={copy.confirmation.title}
      className="ml-11 max-w-md animate-card-in rounded-xl border border-l-4 border-l-primary bg-card p-5 shadow-md focus:outline-none"
    >
      <header className="flex items-center gap-3">
        <span aria-hidden className="flex size-9 items-center justify-center rounded-full bg-primary/12 text-primary">
          <ShieldAlert className="size-4.5" />
        </span>
        <div className="flex-1">
          <h2 className="font-semibold leading-tight">{copy.confirmation.title}</h2>
          <p className="text-xs text-muted-foreground">{copy.confirmation.operationLabels[payload.operation] ?? payload.operation}</p>
        </div>
        {!countdown.expired && <CountdownRing fraction={countdown.fraction} />}
      </header>

      <dl className="mt-4 divide-y rounded-lg bg-muted/50 px-4 py-1.5">
        {payload.operation === "alterar_limite" ? (
          <>
            <Row label={copy.confirmation.card} value="••••4242" />
            <Row label={copy.confirmation.currentLimit} value={brl(payload.currentAmount)} />
            <Row label={copy.confirmation.requestedLimit} value={brl(payload.requestedAmount)} />
          </>
        ) : (
          <>
            <Row label={copy.confirmation.pixAmount} value={`PIX de ${brl(payload.requestedAmount)}`} />
            {payload.recipientKeyMasked && <Row label={copy.confirmation.pixRecipient} value={payload.recipientKeyMasked} />}
            {payload.accountId && <Row label={copy.confirmation.pixAccount} value={payload.accountId} />}
          </>
        )}
      </dl>

      {countdown.expired ? (
        <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
          <TimerOff aria-hidden className="size-4" />
          {copy.confirmation.expired}. {copy.confirmation.expiredHint}
        </p>
      ) : (
        <p role="timer" className="mt-3 text-sm text-muted-foreground">
          {copy.confirmation.expiresIn(countdown.minutes, countdown.seconds)}
        </p>
      )}

      <footer className="mt-4 grid grid-cols-2 gap-3">
        <Button disabled={countdown.expired} onClick={() => void resolve("confirm")}>
          {copy.common.confirm}
        </Button>
        <Button variant="outline" disabled={countdown.expired} onClick={() => void resolve("cancel")}>
          {copy.common.cancel}
        </Button>
      </footer>
    </section>
  );
}
