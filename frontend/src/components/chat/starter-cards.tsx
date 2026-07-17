"use client";

import { ArrowUpRight, CreditCard, HelpCircle, Landmark, Send } from "lucide-react";
import type { UserRole } from "@/lib/agui-types";
import { copy } from "@/lib/copy";

const icons = [Landmark, CreditCard, Send, HelpCircle];

export function StarterCards({ role, name, onPick }: { role: UserRole; name: string; onPick(question: string): void }) {
  const starters = copy.chat.starters[role];
  return (
    <div className="flex h-full flex-col items-center justify-center gap-8 py-8 text-center">
      <div className="animate-message-in">
        <span aria-hidden className="mx-auto mb-4 flex size-14 items-center justify-center rounded-2xl bg-primary/12 text-primary">
          <Landmark className="size-7" />
        </span>
        <h2 className="text-2xl font-semibold tracking-tight">
          {copy.chat.emptyGreeting}, {name}!
        </h2>
        <p className="mt-1 text-muted-foreground">{copy.chat.emptyHint}</p>
      </div>
      <div className="grid w-full max-w-lg gap-3 sm:grid-cols-2">
        {starters.map((starter, index) => {
          const Icon = icons[index % icons.length];
          return (
            <button
              key={starter}
              onClick={() => onPick(starter)}
              className="group flex animate-message-in items-center gap-3 rounded-xl border bg-card p-4 text-left text-sm shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              style={{ animationDelay: `${index * 60}ms` }}
            >
              <span aria-hidden className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground transition-colors group-hover:bg-primary/12 group-hover:text-primary">
                <Icon className="size-4" />
              </span>
              <span className="flex-1 font-medium">{starter}</span>
              <ArrowUpRight aria-hidden className="size-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
            </button>
          );
        })}
      </div>
    </div>
  );
}
