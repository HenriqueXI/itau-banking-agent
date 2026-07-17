import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StarterCards } from "@/components/chat/starter-cards";
import { copy } from "@/lib/copy";

describe("StarterCards", () => {
  it("greets the persona and sends the picked starter question", () => {
    const pick = vi.fn();
    render(<StarterCards role="customer" name="Ana" onPick={pick} />);
    expect(screen.getByText(/Olá, Ana!/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Qual a taxa do consignado\?/ }));
    expect(pick).toHaveBeenCalledWith("Qual a taxa do consignado?");
  });

  it("shows role-specific starters", () => {
    render(<StarterCards role="manager" name="Bruno" onPick={vi.fn()} />);
    for (const starter of copy.chat.starters.manager) {
      expect(screen.getByText(starter)).toBeInTheDocument();
    }
  });

  it("does not advertise card-limit changes to customers", () => {
    render(<StarterCards role="customer" name="Ana" onPick={vi.fn()} />);
    expect(screen.getByText("Qual é o meu limite?")).toBeInTheDocument();
    expect(screen.queryByText(/Aumente meu limite/)).not.toBeInTheDocument();
  });
});
