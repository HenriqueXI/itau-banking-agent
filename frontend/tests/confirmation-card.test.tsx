import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConfirmationCard } from "@/components/chat/confirmation-card";

const payload = { operationHash: "op-1", operation: "alterar_limite" as const, currentAmount: "5000", requestedAmount: "15000", expiresAt: "2030-01-01T00:00:00Z", recipientKeyMasked: null, accountId: null };
describe("ConfirmationCard", () => {
  it("renders structured amounts and resumes only with confirm", async () => {
    const resolve = vi.fn().mockResolvedValue(undefined);
    render(<ConfirmationCard payload={payload} onResolve={resolve} />);
    expect(screen.getByText(/R\$ 5\.000,00/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));
    expect(resolve).toHaveBeenCalledWith("confirm");
  });
  it("throws if free text replaces the typed payload", () => {
    expect(() => render(<ConfirmationCard payload={null as never} onResolve={vi.fn()} />)).toThrow();
  });

  it("renders the masked PIX recipient and account", () => {
    render(
      <ConfirmationCard
        payload={{ ...payload, operation: "fazer_pix", currentAmount: null, requestedAmount: "20000", recipientKeyMasked: "m***@exemplo.com", accountId: "acc-1" }}
        onResolve={vi.fn()}
      />,
    );
    expect(screen.getByText(/PIX de R\$ 20\.000,00/)).toBeInTheDocument();
    expect(screen.getByText("m***@exemplo.com")).toBeInTheDocument();
    expect(screen.getByText("acc-1")).toBeInTheDocument();
  });

  it("disables actions once the confirmation expires", () => {
    render(<ConfirmationCard payload={{ ...payload, expiresAt: "2000-01-01T00:00:00Z" }} onResolve={vi.fn()} />);
    expect(screen.getByText(/Confirmação expirada/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirmar" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancelar" })).toBeDisabled();
  });

  it("uses the server-issued duration instead of a divergent browser clock", () => {
    render(
      <ConfirmationCard
        payload={{
          ...payload,
          expiresAt: "2026-07-15T12:05:00Z",
          issuedAt: "2026-07-15T12:00:00Z",
        }}
        onResolve={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Confirmar" })).toBeEnabled();
    expect(screen.queryByText(/Confirmação expirada/)).not.toBeInTheDocument();
  });

  it("collapses to a resolved summary after cancel", async () => {
    const resolve = vi.fn().mockResolvedValue(undefined);
    render(<ConfirmationCard payload={payload} onResolve={resolve} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(resolve).toHaveBeenCalledWith("cancel");
    expect(await screen.findByText("Operação resolvida.")).toBeInTheDocument();
  });
});
