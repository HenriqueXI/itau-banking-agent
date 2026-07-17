"use client";

import { MessageSquare, Plus } from "lucide-react";
import { copy } from "@/lib/copy";
import { loadThreadMeta } from "@/lib/thread-meta";
import { relativeTime } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function ConversationSidebar({
  conversations,
  loading,
  activeThreadId,
  onSelect,
  onNew,
}: {
  conversations: Array<{ thread_id: string }>;
  loading: boolean;
  activeThreadId: string;
  onSelect(threadId: string): void;
  onNew(): void;
}) {
  const meta = loadThreadMeta();
  return (
    <aside className="flex h-full flex-col gap-3 border-r bg-card/50 p-3">
      <Button onClick={onNew} className="w-full justify-start">
        <Plus aria-hidden />
        {copy.chat.newConversation}
      </Button>
      <h2 className="px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{copy.chat.conversations}</h2>
      <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto" aria-label={copy.chat.conversations}>
        {loading &&
          Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-12 w-full" />)}
        {conversations.map((conversation) => {
          const item = meta[conversation.thread_id];
          const active = conversation.thread_id === activeThreadId;
          return (
            <button
              key={conversation.thread_id}
              onClick={() => onSelect(conversation.thread_id)}
              aria-current={active ? "true" : undefined}
              className={cn(
                "flex w-full items-start gap-2.5 rounded-lg border-l-2 border-transparent px-3 py-2.5 text-left text-sm transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                active && "border-primary bg-accent",
              )}
            >
              <MessageSquare aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0">
                <span className="block truncate font-medium">
                  {item?.title ?? `${copy.chat.conversationFallback} ${conversation.thread_id.slice(0, 8)}`}
                </span>
                {item && <span className="block text-xs text-muted-foreground">{relativeTime(item.updatedAt)}</span>}
              </span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
