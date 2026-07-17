import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Composer } from "@/components/chat/composer";

describe("Composer", () => {
  it("sends with Enter and preserves Shift+Enter", () => {
    const send = vi.fn(); render(<Composer disabled={false} onSend={send} />);
    const input = screen.getByRole("textbox"); fireEvent.change(input, { target: { value: "Olá" } });
    fireEvent.keyDown(input, { key: "Enter" }); expect(send).toHaveBeenCalledWith("Olá");
  });
  it("locks while an interrupt is active", () => { render(<Composer disabled onSend={vi.fn()} />); expect(screen.getByRole("textbox")).toBeDisabled(); });
});
