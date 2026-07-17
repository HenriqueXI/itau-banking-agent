"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, PanelRight, PanelLeft, X } from "lucide-react";
import { getAccountSummary, getConversation, listConversations } from "@/lib/api";
import { useAuth } from "@/app/providers";
import { copy } from "@/lib/copy";
import { toAgentMessages } from "@/lib/conversation-history";
import { personas } from "@/lib/auth";
import type { PanelAccount } from "@/lib/banking";
import { useAgentStream } from "@/hooks/use-agent-stream";
import { useAutoscroll } from "@/hooks/use-autoscroll";
import { AppShell } from "@/components/shared/app-shell";
import { AccountPanel } from "@/components/chat/account-panel";
import { Composer } from "@/components/chat/composer";
import { ConfirmationCard } from "@/components/chat/confirmation-card";
import { ConversationSidebar } from "@/components/chat/conversation-sidebar";
import { MessageList } from "@/components/chat/message-list";
import { StarterCards } from "@/components/chat/starter-cards";
import { StepUpPrompt } from "@/components/chat/step-up-prompt";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function ChatScreen() {
  const router = useRouter();
  const { session } = useAuth();
  const stream = useAgentStream();
  const restoredThreadId = useRef<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const conversations = useQuery({
    queryKey: ["conversations", session?.token],
    queryFn: () => listConversations(session!.token),
    enabled: Boolean(session),
  });
  const financialSummary = useQuery({
    queryKey: ["account-summary", session?.email],
    queryFn: () => getAccountSummary(session!.token),
    enabled: Boolean(session),
  });
  const history = useQuery({
    queryKey: ["conversation", session?.token, stream.threadId],
    queryFn: () => getConversation(session!.token, stream.threadId),
    enabled: Boolean(session && conversations.data?.some((item) => item.thread_id === stream.threadId)),
  });
  const { containerRef, onScroll, pinned, jumpToLatest } = useAutoscroll(
    `${stream.messages.length}:${stream.messages.at(-1)?.content.length ?? 0}:${stream.confirmation ? 1 : 0}:${stream.stepUp ? 1 : 0}`,
  );

  useEffect(() => {
    if (!session) router.replace("/login");
  }, [router, session]);
  useEffect(() => {
    if (!history.data || history.data.thread_id !== stream.threadId) return;
    if (restoredThreadId.current === history.data.thread_id) return;
    // A just-finished run may invalidate the conversation list, which enables
    // this history query for the current thread. Do not let that refresh erase
    // an active server-owned interrupt that arrived in the same SSE stream.
    if (stream.streaming || stream.confirmation || stream.stepUp) {
      restoredThreadId.current = history.data.thread_id;
      return;
    }
    restoredThreadId.current = history.data.thread_id;
    stream.restoreMessages(toAgentMessages(history.data));
  }, [history.data, stream.threadId, stream.streaming, stream.confirmation, stream.stepUp]);
  if (!session) return null;

  const persona = personas.find((item) => item.email === session.email);
  const account = financialSummary.data ? toPanelAccount(financialSummary.data) : null;
  const interrupted = Boolean(stream.confirmation) || Boolean(stream.stepUp);
  const selectConversation = (threadId: string) => {
    restoredThreadId.current = null;
    stream.selectThread(threadId);
  };
  const startNewConversation = () => {
    restoredThreadId.current = null;
    stream.selectThread(stream.newThread());
  };

  return (
    <AppShell>
      <div className="grid h-full lg:grid-cols-[280px_minmax(0,1fr)] xl:grid-cols-[280px_minmax(0,1fr)_340px]">
        <div className="hidden min-h-0 lg:block">
          <ConversationSidebar
            conversations={conversations.data ?? []}
            loading={conversations.isLoading}
            activeThreadId={stream.threadId}
            onSelect={selectConversation}
            onNew={startNewConversation}
          />
        </div>

        <section className="flex min-h-0 flex-col">
          <div className="flex items-center gap-2 border-b px-3 py-2 lg:hidden">
            <Button variant="ghost" size="icon" aria-label={copy.chat.conversations} onClick={() => setSidebarOpen(true)}>
              <PanelLeft />
            </Button>
            <span className="text-sm font-medium">{copy.chat.conversations}</span>
            <Button
              variant="ghost"
              size="icon"
              className="ml-auto xl:hidden"
              aria-label={copy.account.openPanel}
              onClick={() => setPanelOpen(true)}
            >
              <PanelRight />
            </Button>
          </div>

          <div ref={containerRef} onScroll={onScroll} className="relative min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col gap-4 px-4 py-6">
              {history.isLoading ? (
                <div className="py-8 text-center text-sm text-muted-foreground">Carregando conversa…</div>
              ) : history.isError ? (
                <div role="alert" className="py-8 text-center text-sm text-destructive">Não foi possível carregar esta conversa.</div>
              ) : !stream.messages.length && !interrupted ? (
                <StarterCards role={session.role} name={persona?.name ?? session.email.split("@")[0]} onPick={(question) => void stream.send(question)} />
              ) : (
                <MessageList messages={stream.messages} tools={stream.tools} streaming={stream.streaming} />
              )}
              {stream.confirmation && <ConfirmationCard payload={stream.confirmation} onResolve={stream.resolveConfirmation} />}
              {stream.stepUp && (
                <StepUpPrompt payload={stream.stepUp} onSubmit={stream.submitStepUp} onCancel={stream.cancelStepUp} />
              )}
              {stream.error && (
                <div role="alert" className="ml-11 flex animate-message-in items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/8 px-4 py-3 text-sm">
                  <AlertTriangle aria-hidden className="size-4 shrink-0 text-destructive" />
                  <span className="flex-1">{stream.error}</span>
                  <Button variant="outline" size="sm" onClick={stream.clearError}>
                    {copy.common.retry}
                  </Button>
                </div>
              )}
            </div>
            {!pinned && (
              <button
                onClick={jumpToLatest}
                className="sticky bottom-3 left-1/2 -translate-x-1/2 animate-fade-in rounded-full border bg-card px-4 py-1.5 text-xs font-medium shadow-md transition-colors hover:bg-accent"
              >
                {copy.chat.jumpToLatest}
              </button>
            )}
          </div>

          <div className="border-t bg-background/80 px-4 py-3 backdrop-blur">
            <div className="mx-auto max-w-3xl">
              <Composer
                disabled={stream.streaming || interrupted || history.isLoading}
                onSend={(message) => void stream.send(message)}
              />
            </div>
          </div>
        </section>

        <div className="hidden min-h-0 border-l bg-card/30 xl:block">
          {account ? <AccountPanel account={account} selectedCardId={stream.uiContext?.selected_card_id} onSelectCard={(cardId) => stream.setUiContext({ selected_card_id: cardId, selected_account_id: account.conta })} /> : <PanelState loading={financialSummary.isLoading} />}
        </div>
      </div>

      {sidebarOpen && (
        <MobileOverlay side="left" label={copy.chat.conversations} onClose={() => setSidebarOpen(false)}>
          <ConversationSidebar
            conversations={conversations.data ?? []}
            loading={conversations.isLoading}
            activeThreadId={stream.threadId}
            onSelect={(id) => {
              selectConversation(id);
              setSidebarOpen(false);
            }}
            onNew={() => {
              startNewConversation();
              setSidebarOpen(false);
            }}
          />
        </MobileOverlay>
      )}
      {panelOpen && (
        <MobileOverlay side="right" label={copy.account.title} onClose={() => setPanelOpen(false)}>
          {account ? <AccountPanel account={account} selectedCardId={stream.uiContext?.selected_card_id} onSelectCard={(cardId) => stream.setUiContext({ selected_card_id: cardId, selected_account_id: account.conta })} /> : <PanelState loading={financialSummary.isLoading} />}
        </MobileOverlay>
      )}
    </AppShell>
  );
}

function PanelState({ loading }: { loading: boolean }) {
  return <div className="p-4 text-sm text-muted-foreground">{loading ? "Carregando dados financeiros…" : "Não foi possível carregar os dados financeiros."}</div>;
}

function toPanelAccount(summary: import("@/lib/banking").AccountSummary): PanelAccount {
  return {
    agencia: "0341",
    conta: summary.account_id,
    saldo: summary.available_balance,
    cartoes: summary.cards.map((card) => ({
      id: card.card_id, maskedNumber: `••••${card.last4}`, brand: "Cartão", limite: card.total_limit,
      utilizado: card.used_amount, fatura: card.invoice_amount, vencimento: card.due_date,
    })),
    transacoes: summary.transactions.map((entry) => ({
      id: entry.transaction_id, descricao: entry.description, categoria: "transferencia",
      valor: entry.amount, data: entry.occurred_at, tipo: entry.kind === "credit" ? "credito" : "debito",
    })),
  };
}

function MobileOverlay({
  side,
  label,
  onClose,
  children,
}: {
  side: "left" | "right";
  label: string;
  onClose(): void;
  children: React.ReactNode;
}) {
  return (
    <div role="presentation" className="fixed inset-0 z-30 animate-fade-in bg-black/45" onMouseDown={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={label}
        onMouseDown={(event) => event.stopPropagation()}
        className={cn(
          "absolute inset-y-0 flex w-[min(20rem,85vw)] flex-col bg-background shadow-xl",
          side === "left" ? "left-0" : "right-0 animate-slide-in-right",
        )}
      >
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-semibold">{label}</span>
          <Button variant="ghost" size="icon" aria-label={copy.common.close} onClick={onClose}>
            <X />
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
  );
}
