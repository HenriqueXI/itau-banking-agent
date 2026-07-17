import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ runAgent: vi.fn() }));
import { runAgent } from "@/lib/api";
import { streamAgent } from "@/lib/copilot/agui-client";

describe("AG-UI client", () => {
  it("decodes structured SSE events", async () => {
    const encoded = new TextEncoder().encode('event: confirmation_required\ndata: {"operationHash":"op","operation":"fazer_pix","currentAmount":null,"requestedAmount":"10","expiresAt":"2030-01-01T00:00:00Z","recipientKeyMasked":"a***","accountId":"acc"}\n\n');
    vi.mocked(runAgent).mockResolvedValue(new Response(new ReadableStream({ start(controller) { controller.enqueue(encoded); controller.close(); } })));
    const events: unknown[] = []; await streamAgent("token", "thread", (event) => events.push(event), "oi");
    expect(events).toHaveLength(1);
  });
});
