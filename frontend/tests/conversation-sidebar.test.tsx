import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConversationSidebar } from "@/components/chat/conversation-sidebar";

describe("ConversationSidebar", () => {
  it("selects the clicked persisted conversation", () => {
    const onSelect = vi.fn();
    render(
      <ConversationSidebar
        conversations={[{ thread_id: "thread-123" }]}
        loading={false}
        activeThreadId="thread-current"
        onSelect={onSelect}
        onNew={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Conversa thread-1" }));
    expect(onSelect).toHaveBeenCalledWith("thread-123");
  });
});
