"use client";

import { FormEvent, Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Briefcase, ShieldCheck, Sparkles, User } from "lucide-react";
import type { UserRole } from "@/lib/agui-types";
import { personas } from "@/lib/auth";
import { login } from "@/lib/api";
import { useAuth } from "@/app/providers";
import { copy } from "@/lib/copy";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";

const password = "demo123";

const roleIcons: Record<UserRole, typeof User> = { customer: User, manager: Briefcase, admin: ShieldCheck };
const roleTint: Record<UserRole, string> = {
  customer: "bg-role-customer/12 text-role-customer",
  manager: "bg-role-manager/12 text-role-manager",
  admin: "bg-role-admin/12 text-role-admin",
};

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { setSession } = useAuth();
  const [email, setEmail] = useState("");
  const [manualPassword, setPassword] = useState("");
  const [error, setError] = useState("");
  const destination = searchParams.get("next")?.startsWith("/") ? searchParams.get("next")! : "/";

  async function enter(targetEmail: string, targetPassword: string) {
    try {
      const result = await login(targetEmail, targetPassword);
      const persona = personas.find((item) => item.email === targetEmail);
      setSession({ token: result.access_token, email: targetEmail, role: persona?.role ?? "customer" });
      router.replace(destination);
    } catch {
      setError(copy.login.error);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void enter(email, manualPassword);
  }

  return (
    <main className="grid min-h-dvh lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden flex-col justify-between overflow-hidden bg-primary p-10 text-primary-foreground lg:flex">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,rgba(255,255,255,0.18),transparent_55%),radial-gradient(ellipse_at_bottom_left,rgba(0,0,0,0.22),transparent_50%)]"
        />
        <div className="relative flex items-center gap-2 font-semibold tracking-tight">
          <span aria-hidden className="flex size-8 items-center justify-center rounded-lg bg-primary-foreground/15 text-base font-bold">
            A
          </span>
          {copy.common.appName}
        </div>
        <div className="relative max-w-md">
          <Sparkles aria-hidden className="mb-4 size-8 opacity-80" />
          <h2 className="text-3xl font-semibold leading-tight tracking-tight">{copy.login.subtitle}</h2>
          <p className="mt-4 text-base/relaxed opacity-90">{copy.login.heroTagline}</p>
        </div>
        <Badge variant="secondary" className="relative w-fit border-primary-foreground/20 bg-primary-foreground/10 text-primary-foreground">
          {copy.common.demoBadge}
        </Badge>
      </section>

      <section className="flex items-center justify-center bg-background p-6">
        <div className="w-full max-w-md animate-card-in">
          <h1 className="text-2xl font-semibold tracking-tight">{copy.login.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{copy.login.chooseProfile}</p>

          <div className="mt-6 grid gap-3">
            {personas.map((persona) => {
              const Icon = roleIcons[persona.role];
              return (
                <button
                  key={persona.email}
                  onClick={() => void enter(persona.email, password)}
                  className="group flex w-full items-center gap-4 rounded-xl border bg-card p-4 text-left shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span aria-hidden className={`flex size-11 shrink-0 items-center justify-center rounded-full ${roleTint[persona.role]}`}>
                    <Icon className="size-5" />
                  </span>
                  <span className="min-w-0">
                    <span className="block font-semibold">
                      {persona.name}
                      <span className="ml-2 text-xs font-medium text-muted-foreground">{copy.roles[persona.role]}</span>
                    </span>
                    <span className="block truncate text-sm text-muted-foreground">
                      {copy.login.personaDescriptions[persona.role]}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>

          <div className="my-6 flex items-center gap-3 text-xs text-muted-foreground">
            <span className="h-px flex-1 bg-border" aria-hidden />
            {copy.login.manualDivider}
            <span className="h-px flex-1 bg-border" aria-hidden />
          </div>

          <form onSubmit={submit} className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="login-email">{copy.login.email}</Label>
              <Input id="login-email" required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="login-password">{copy.login.password}</Label>
              <Input id="login-password" required type="password" autoComplete="current-password" value={manualPassword} onChange={(event) => setPassword(event.target.value)} />
            </div>
            <Button type="submit" className="mt-1 w-full">
              {copy.login.enter}
            </Button>
          </form>

          {error && (
            <p role="alert" className="mt-4 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </p>
          )}

          <p className="mt-6 text-center text-xs text-muted-foreground lg:hidden">{copy.common.demoBadge}</p>
        </div>
      </section>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<main className="min-h-dvh bg-background" />}>
      <LoginForm />
    </Suspense>
  );
}
