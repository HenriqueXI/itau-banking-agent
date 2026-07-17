export interface AccountSummary {
  account_id: string;
  available_balance: string;
  cards: Array<{
    card_id: string; last4: string; total_limit: string; used_amount: string;
    available_amount: string; invoice_amount: string; due_date: string; invoice_status: string;
  }>;
  transactions: Array<{ transaction_id: string; description: string; amount: string; occurred_at: string; kind: string }>;
}

export interface PanelTransaction {
  id: string;
  descricao: string;
  categoria: "mercado" | "transferencia" | "assinatura" | "salario" | "restaurante" | "transporte";
  valor: string;
  data: string;
  tipo: "credito" | "debito";
}

export interface PanelAccount {
  agencia: string;
  conta: string;
  saldo: string;
  cartoes: Array<{
    id: string;
    maskedNumber: string;
    brand: string;
    limite: string;
    utilizado: string;
    fatura: string;
    vencimento: string;
  }>;
  transacoes: PanelTransaction[];
}
