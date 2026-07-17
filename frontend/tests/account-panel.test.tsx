import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AccountPanel } from "@/components/chat/account-panel";
import type { PanelAccount } from "@/lib/banking";

const account: PanelAccount = {
  agencia: "0341",
  conta: "acc-1",
  saldo: "28412.37",
  cartoes: [
    {
      id: "card-1",
      maskedNumber: "••••4242",
      brand: "Cartão",
      limite: "5000.00",
      utilizado: "1834.90",
      fatura: "1834.90",
      vencimento: "10",
    },
  ],
  transacoes: [],
};

describe("AccountPanel", () => {
  it("renders the authoritative account values passed by the summary API", () => {
    render(<AccountPanel account={account} />);
    expect(screen.getByText("••••4242")).toBeInTheDocument();
    expect(screen.getByText(/R\$\s*28\.412,37/)).toBeInTheDocument();
  });

  it("hides amounts behind the eye toggle", () => {
    render(<AccountPanel account={account} />);
    fireEvent.click(screen.getByRole("button", { name: "Ocultar saldo" }));
    expect(screen.queryByText(/R\$ 8\.412,37/)).not.toBeInTheDocument();
    expect(screen.getByText("R$ ••••••")).toBeInTheDocument();
  });
});
