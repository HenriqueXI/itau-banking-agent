"use client";

import { useEffect, useRef, useState } from "react";
import { KeyRound } from "lucide-react";
import type { StepUpPayload } from "@/lib/agui-types";
import { copy } from "@/lib/copy";
import { useCountdown } from "@/hooks/use-countdown";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const MAX_ATTEMPTS = 3;
const SLOTS = 6;

export function StepUpPrompt({
  payload,
  onSubmit,
  onCancel,
}: {
  payload: StepUpPayload;
  onSubmit(code: string): Promise<void>;
  onCancel(): Promise<void>;
}) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [attempts, setAttempts] = useState(0);
  const [shaking, setShaking] = useState(false);
  const [focused, setFocused] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const countdown = useCountdown(payload.expiresAt);

  useEffect(() => {
    input.current?.focus();
  }, []);

  async function change(value: string) {
    const digits = value.replace(/\D/g, "").slice(0, SLOTS);
    setCode(digits);
    if (digits.length !== SLOTS) return;
    try {
      await onSubmit(digits);
    } catch {
      const failed = attempts + 1;
      setAttempts(failed);
      setCode("");
      setShaking(true);
      window.setTimeout(() => setShaking(false), 400);
      if (failed >= MAX_ATTEMPTS) {
        setError(copy.stepUp.cancelled);
        await onCancel();
        return;
      }
      setError(`${copy.stepUp.invalid} ${copy.stepUp.attemptsLeft(MAX_ATTEMPTS - failed)}.`);
      input.current?.focus();
    }
  }

  return (
    <section
      role="dialog"
      aria-label={copy.stepUp.title}
      className="ml-11 max-w-md animate-card-in rounded-xl border border-l-4 border-l-role-admin bg-card p-5 shadow-md"
    >
      <header className="flex items-center gap-3">
        <span aria-hidden className="flex size-9 items-center justify-center rounded-full bg-role-admin/12 text-role-admin">
          <KeyRound className="size-4.5" />
        </span>
        <div>
          <h2 className="font-semibold leading-tight">{copy.stepUp.title}</h2>
          <p className="text-xs text-muted-foreground">{copy.stepUp.instruction}</p>
        </div>
      </header>

      <label htmlFor="stepup" className="sr-only">
        {copy.stepUp.codeLabel}
      </label>
      <div className={cn("relative mt-4 w-fit", shaking && "animate-shake")}>
        <input
          ref={input}
          id="stepup"
          inputMode="numeric"
          autoComplete="one-time-code"
          value={code}
          maxLength={SLOTS}
          onChange={(event) => void change(event.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          aria-describedby="stepup-error"
          className="absolute inset-0 z-10 w-full opacity-0"
        />
        <div aria-hidden className="flex gap-2">
          {Array.from({ length: SLOTS }, (_, index) => {
            const active = focused && index === Math.min(code.length, SLOTS - 1);
            return (
              <span
                key={index}
                className={cn(
                  "flex h-11 w-9 items-center justify-center rounded-lg border bg-muted/40 text-lg font-semibold tabular-nums transition-all",
                  active && "border-primary ring-2 ring-ring",
                )}
              >
                {code[index] ?? ""}
              </span>
            );
          })}
        </div>
      </div>

      <p id="stepup-error" role="status" className="mt-2 min-h-5 text-sm text-destructive">
        {error}
      </p>
      <div className="flex items-center justify-between gap-3">
        <p role="timer" className="text-xs text-muted-foreground">
          {countdown.expired ? copy.confirmation.expired : copy.confirmation.expiresIn(countdown.minutes, countdown.seconds)}
        </p>
        <Button variant="outline" size="sm" onClick={() => void onCancel()}>
          {copy.common.cancel}
        </Button>
      </div>
    </section>
  );
}
