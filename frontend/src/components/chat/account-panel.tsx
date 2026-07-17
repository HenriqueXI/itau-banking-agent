"use client";

import { useState } from "react";
import {
  ArrowDownLeft,
  ArrowUpRight,
  Bus,
  CreditCard,
  Eye,
  EyeOff,
  Landmark,
  ReceiptText,
  ShoppingCart,
  Tv,
  UtensilsCrossed,
} from "lucide-react";
import type { PanelAccount, PanelTransaction } from "@/lib/banking";
import { copy } from "@/lib/copy";
import { brl, shortDate } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

const categoryIcons: Record<PanelTransaction["categoria"], typeof ShoppingCart> = {
  mercado: ShoppingCart,
  transferencia: ArrowDownLeft,
  assinatura: Tv,
  salario: Landmark,
  restaurante: UtensilsCrossed,
  transporte: Bus,
};

function Progress({ value, max }: { value: number; max: number }) {
  const percent = Math.min(100, Math.round((value / max) * 100));
  return (
    <div aria-hidden className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${percent}%` }} />
    </div>
  );
}

export function AccountPanel({ account, selectedCardId, onSelectCard }: { account: PanelAccount; selectedCardId?: string; onSelectCard?(cardId: string): void }) {
  const [hidden, setHidden] = useState(false);

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      <Card className="animate-message-in">
        <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
          <div>
            <CardTitle className="text-sm text-muted-foreground">{copy.account.balance}</CardTitle>
            <p className="text-xs text-muted-foreground/80">{copy.account.branchAccount(account.agencia, account.conta)}</p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            aria-pressed={hidden}
            aria-label={hidden ? copy.account.showBalance : copy.account.hideBalance}
            onClick={() => setHidden((value) => !value)}
          >
            {hidden ? <Eye /> : <EyeOff />}
          </Button>
        </CardHeader>
        <CardContent>
          <p className="text-2xl font-semibold tabular-nums tracking-tight">{hidden ? "R$ ••••••" : brl(account.saldo)}</p>
        </CardContent>
      </Card>

      <Card className="animate-message-in" style={{ animationDelay: "60ms" }}>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm text-muted-foreground">
            <CreditCard aria-hidden className="size-4" />
            {copy.account.cards}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {account.cartoes.map((cartao) => {
            const cardId = cartao.id;
            return <button type="button" key={cartao.maskedNumber} onClick={() => onSelectCard?.(cardId)} aria-pressed={selectedCardId === cardId} className={cn("w-full space-y-2 rounded-md p-1 text-left", selectedCardId === cardId && "bg-muted")}>
              <div className="flex items-center justify-between">
                <span className="font-medium tabular-nums">{cartao.maskedNumber}</span>
                <Badge variant="secondary">{cartao.brand}</Badge>
              </div>
              <Progress value={Number(cartao.utilizado)} max={Number(cartao.limite)} />
              <p className="text-xs text-muted-foreground">
                {copy.account.limitUsed}: <span className="tabular-nums">{hidden ? "R$ ••••" : brl(cartao.utilizado)}</span> /{" "}
                <span className="tabular-nums">{brl(cartao.limite)}</span>
              </p>
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <ReceiptText aria-hidden className="size-3.5" />
                {copy.account.invoice}: <span className="tabular-nums">{hidden ? "R$ ••••" : brl(cartao.fatura)}</span> · {copy.account.due}{" "}
                {cartao.vencimento}
              </p>
            </button>;
          })}
        </CardContent>
      </Card>

      <Card className="animate-message-in" style={{ animationDelay: "120ms" }}>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm text-muted-foreground">{copy.account.transactions}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1">
          {account.transacoes.map((transacao, index) => {
            const Icon = transacao.tipo === "credito" ? ArrowDownLeft : categoryIcons[transacao.categoria] ?? ArrowUpRight;
            return (
              <div key={transacao.id}>
                {index > 0 && <Separator className="my-1.5" />}
                <div className="flex items-center gap-2.5 py-1">
                  <span
                    aria-hidden
                    className={cn(
                      "flex size-8 shrink-0 items-center justify-center rounded-full",
                      transacao.tipo === "credito" ? "bg-success/12 text-success" : "bg-muted text-muted-foreground",
                    )}
                  >
                    <Icon className="size-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{transacao.descricao}</span>
                    <span className="block text-xs text-muted-foreground">{shortDate(transacao.data)}</span>
                  </span>
                  <span
                    className={cn("text-sm font-semibold tabular-nums", transacao.tipo === "credito" ? "text-success" : "text-foreground")}
                  >
                    {hidden ? "••••" : `${transacao.tipo === "credito" ? "+" : ""}${brl(transacao.valor)}`}
                  </span>
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>

    </div>
  );
}
