"use client";

import type { PropsWithChildren } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LogOut, ScrollText } from "lucide-react";
import { useAuth } from "@/app/providers";
import { personas } from "@/lib/auth";
import { copy } from "@/lib/copy";
import { Button } from "@/components/ui/button";
import { RoleBadge } from "@/components/shared/role-badge";
import { ThemeToggle } from "@/components/shared/theme-toggle";
import { cn } from "@/lib/utils";

export function AppShell({ children, className }: PropsWithChildren<{ className?: string }>) {
  const router = useRouter();
  const { session, logout } = useAuth();
  const persona = personas.find((item) => item.email === session?.email);
  const name = persona?.name ?? session?.email.split("@")[0] ?? "";

  return (
    <div className="flex h-dvh flex-col bg-background">
      <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b bg-card/80 px-4 backdrop-blur">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span aria-hidden className="flex size-7 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
            A
          </span>
          <span className="hidden sm:inline">{copy.common.appName}</span>
        </Link>
        <div className="ml-auto flex items-center gap-2">
          {session?.role === "admin" && (
            <Button variant="ghost" size="sm" onClick={() => router.push("/admin")}>
              <ScrollText aria-hidden />
              {copy.admin.title}
            </Button>
          )}
          {session && <RoleBadge name={name} role={session.role} />}
          <ThemeToggle />
          {session && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                logout();
                router.replace("/login");
              }}
            >
              <LogOut aria-hidden />
              {copy.common.logout}
            </Button>
          )}
        </div>
      </header>
      <div className={cn("min-h-0 flex-1", className)}>{children}</div>
    </div>
  );
}
