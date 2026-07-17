import { expect, test } from "@playwright/test";

const login = async (
  page: import("@playwright/test").Page,
  conversations: (requestNumber: number) => Array<{ thread_id: string }> = () => [],
) => {
  await page.route("**/api/backend/auth/login", (route) => route.fulfill({ json: { access_token: "demo" } }));
  let conversationRequests = 0;
  await page.route("**/api/backend/conversations", (route) => {
    conversationRequests += 1;
    return route.fulfill({ json: conversations(conversationRequests) });
  });
  await page.route("**/api/backend/banking/summary", (route) => route.fulfill({ json: {
    account_id: "acc-1", available_balance: "28412.37",
    cards: [
      { card_id: "card-1", last4: "4242", total_limit: "5000.00", used_amount: "1834.90", available_amount: "3165.10", invoice_amount: "1834.90", due_date: "10", invoice_status: "OPEN" },
      { card_id: "card-2", last4: "8888", total_limit: "3000.00", used_amount: "420.00", available_amount: "2580.00", invoice_amount: "420.00", due_date: "20", invoice_status: "OPEN" },
    ],
    transactions: [],
  } }));
  await page.goto("/login"); await page.getByRole("button", { name: /Ana/ }).click(); await page.waitForURL("/");
};
const sse = (events: string) => ({ contentType: "text/event-stream", body: events });

test("UC-2 shows typed confirmation and resumes on Confirmar", async ({ page }) => {
  let calls = 0;
  await login(page);
  await page.route("**/api/backend/agui", (route) => {
    calls += 1;
    return route.fulfill(sse(calls === 1
      ? 'event: confirmation_required\ndata: {"operationHash":"limit-1","operation":"alterar_limite","currentAmount":"5000","requestedAmount":"15000","expiresAt":"2030-01-01T00:00:00Z","recipientKeyMasked":null,"accountId":null}\n\n'
      : 'event: TEXT_MESSAGE_CONTENT\ndata: {"messageId":"r","delta":"Limite atualizado."}\n\n'));
  });
  await page.getByLabel("Mensagem").fill("Aumente meu limite"); await page.getByRole("button", { name: "Enviar" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("R$ 5.000,00"); await page.getByRole("button", { name: "Confirmar" }).click();
  await expect(page.getByText("Limite atualizado.")).toBeVisible();
});

test("UC-2 keeps the confirmation card when the active history refreshes", async ({ page }) => {
  const threadId = "thread-confirmation";
  await page.addInitScript((id) => sessionStorage.setItem("itau-thread", id), threadId);
  await login(page, (requestNumber) => (requestNumber === 1 ? [] : [{ thread_id: threadId }]));
  await page.route(`**/api/backend/conversations/${threadId}`, (route) => route.fulfill({
    json: {
      thread_id: threadId,
      messages: [
        { role: "user", content: "FaÃ§a um PIX de R$ 100.", citations: [] },
        { role: "assistant", content: "Confirme o PIX exibido.", citations: [] },
      ],
    },
  }));
  await page.route("**/api/backend/agui", (route) => route.fulfill(sse(
    'event: confirmation_required\ndata: {"operationHash":"pix-1","operation":"fazer_pix","currentAmount":null,"requestedAmount":"100","expiresAt":"2030-01-01T00:00:00Z","recipientKeyMasked":"cha****","accountId":"acc-2"}\n\nevent: TEXT_MESSAGE_CONTENT\ndata: {"messageId":"answer","delta":"Confirme o PIX de R$ 100,00."}\n\n',
  )));

  await page.getByLabel("Mensagem").fill("FaÃ§a um PIX de R$ 100 para chave@exemplo.com.");
  await page.getByRole("button", { name: "Enviar" }).click();

  await expect(page.getByRole("alertdialog")).toContainText("PIX de R$ 100,00");
  await expect(page.getByRole("button", { name: "Confirmar" })).toBeVisible();
});

test("UC-3 stays a normal denial message", async ({ page }) => {
  await login(page);
  await page.route("**/api/backend/agui", (route) => route.fulfill(sse('event: TEXT_MESSAGE_CONTENT\ndata: {"messageId":"d","delta":"Não tenho permissão para essa consulta."}\n\n')));
  await page.getByLabel("Mensagem").fill("Saldo do João"); await page.getByRole("button", { name: "Enviar" }).click();
  await expect(page.getByText(/Não tenho permissão/)).toBeVisible();
});

test("UC-4 renders step-up before confirmation", async ({ page }) => {
  let calls = 0;
  await login(page);
  await page.route("**/api/backend/auth/step-up/request", (route) => route.fulfill({ json: { challenge_id: "challenge-1", expires_at: "2030-01-01T00:00:00Z", dev_code: "123456" } }));
  await page.route("**/api/backend/agui", (route) => {
    calls += 1;
    return route.fulfill(sse(calls === 1
      ? 'event: step_up_required\ndata: {"operationHash":"pix-1","expiresAt":"2030-01-01T00:00:00Z"}\n\n'
      : 'event: confirmation_required\ndata: {"operationHash":"pix-1","operation":"fazer_pix","currentAmount":null,"requestedAmount":"20000","expiresAt":"2030-01-01T00:00:00Z","recipientKeyMasked":"m***@exemplo.com","accountId":"acc-1"}\n\n'));
  });
  await page.getByLabel("Mensagem").fill("PIX de 20 mil"); await page.getByRole("button", { name: "Enviar" }).click();
  await expect(page.getByRole("dialog")).toBeVisible(); await page.getByLabel("Código").fill("123456");
  await expect(page.getByRole("alertdialog")).toContainText("PIX de R$ 20.000,00");
});

test("selected card is sent only as AG-UI reference context", async ({ page }) => {
  let payload: Record<string, unknown> | null = null;
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page);
  await page.route("**/api/backend/agui", async (route) => {
    payload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill(sse('event: TEXT_MESSAGE_CONTENT\ndata: {"messageId":"r","delta":"Fatura consultada."}\n\n'));
  });

  await page.getByRole("button", { name: /4242/ }).click();
  await page.getByLabel("Mensagem").fill("Qual a fatura desse cartão?");
  await page.getByRole("button", { name: "Enviar" }).click();

  await expect(page.getByText("Fatura consultada.")).toBeVisible();
  expect(payload?.context).toEqual({ selected_card_id: "card-1", selected_account_id: "acc-1" });
});
