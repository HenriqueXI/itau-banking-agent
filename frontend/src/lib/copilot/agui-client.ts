"use client";

import { runAgent } from "@/lib/api";
import type { AguiEvent, ResumePayload } from "@/lib/agui-types";

/**
 * The sole AG-UI transport seam.  UI components never parse SSE nor call the
 * backend directly; replacing this with CopilotKit's hook is contained here.
 */
export async function streamAgent(
  token: string,
  threadId: string,
  onEvent: (event: AguiEvent) => void,
  message?: string,
  resume?: ResumePayload,
  context?: { selected_card_id?: string; selected_account_id?: string },
): Promise<void> {
  const response = await runAgent(token, threadId, message, resume, context);
  if (!response.body) throw new Error("Resposta de streaming vazia");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const name = chunk.match(/^event: (.+)$/m)?.[1];
      const raw = chunk.match(/^data: (.+)$/m)?.[1];
      if (!name || !raw) continue;
      try {
        onEvent({ type: name, data: JSON.parse(raw) } as AguiEvent);
      } catch {
        console.warn("Evento AG-UI inválido", name);
      }
    }
    if (done) return;
  }
}
