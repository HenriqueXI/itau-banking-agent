import { Bot } from "lucide-react";
import { CitationChips } from "@/components/chat/citation-chips";
import { ToolStatusRow } from "@/components/chat/tool-status-row";
import type { AgentMessage, ToolStatus } from "@/lib/agui-types";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

function Bubble({ message, streaming }: { message: AgentMessage; streaming: boolean }) {
  const isUser = message.role === "user";
  return (
    <article className={cn("flex w-full animate-message-in gap-3", isUser && "justify-end")}>
      {!isUser && (
        <span aria-hidden className="mt-1 flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/12 text-primary">
          <Bot className="size-4" />
        </span>
      )}
      <div className={cn("max-w-[75%]", isUser && "flex flex-col items-end")}>
        <span className="sr-only">{isUser ? copy.chat.you : copy.chat.agent}</span>
        <div
          className={cn(
            "rounded-2xl px-4 py-2.5 text-sm/relaxed shadow-sm",
            isUser ? "rounded-br-md bg-primary text-primary-foreground" : "rounded-bl-md border bg-card text-card-foreground",
          )}
        >
          <p className="whitespace-pre-wrap">
            {message.content}
            {streaming && (
              <span aria-hidden className="ml-0.5 inline-block h-4 w-2 animate-caret bg-primary align-text-bottom" />
            )}
          </p>
        </div>
        {message.citations && <CitationChips citations={message.citations} />}
      </div>
    </article>
  );
}

export function MessageList({ messages, tools, streaming }: { messages: AgentMessage[]; tools: ToolStatus[]; streaming: boolean }) {
  if (!messages.length) return null;
  const lastAssistant = [...messages].reverse().find((message) => message.role === "assistant");
  return (
    <section aria-live="polite" aria-busy={streaming} className="flex flex-col gap-4">
      {messages.map((message) => (
        <Bubble key={message.id} message={message} streaming={streaming && message.id === lastAssistant?.id} />
      ))}
      {tools.length > 0 && (
        <div className="flex flex-wrap gap-2 pl-11">
          {tools.map((tool) => (
            <ToolStatusRow key={tool.id} tool={tool} />
          ))}
        </div>
      )}
    </section>
  );
}
