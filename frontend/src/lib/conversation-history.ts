import type { AgentMessage } from "@/lib/agui-types";

export interface ConversationHistory {
  thread_id: string;
  messages: Array<{
    role: "user" | "assistant";
    content: string;
    citations: Array<{ document_id: string; title: string; section: string; page: number | null }>;
  }>;
}

export function toAgentMessages(history: ConversationHistory): AgentMessage[] {
  return history.messages.map((message, index) => ({
    id: `${history.thread_id}-${index}`,
    role: message.role,
    content: message.content,
    citations: message.citations.length
      ? message.citations.map((citation) => ({
          documentId: citation.document_id,
          title: citation.title,
          section: citation.section,
          page: citation.page,
          marker: `【${citation.title} — ${citation.page === null ? citation.section : `p.${citation.page}`}】`,
        }))
      : undefined,
  }));
}
