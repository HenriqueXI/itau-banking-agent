import { describe, expect, it } from "vitest";
import { toAgentMessages } from "@/lib/conversation-history";

describe("toAgentMessages", () => {
  it("restores the persisted transcript with its source metadata", () => {
    const messages = toAgentMessages({
      thread_id: "thread-1",
      messages: [
        { role: "user", content: "Qual a taxa?", citations: [] },
        {
          role: "assistant",
          content: "A taxa e 1,49%.",
          citations: [{ document_id: "tarifas", title: "Tarifas", section: "Consignado", page: 2 }],
        },
      ],
    });

    expect(messages).toHaveLength(2);
    expect(messages[0]).toMatchObject({ id: "thread-1-0", role: "user" });
    expect(messages[1].citations?.[0]).toMatchObject({
      documentId: "tarifas",
      marker: "【Tarifas — p.2】",
    });
  });
});
