import { expect, test } from "@playwright/test";

test("UC-1 renders streamed answer and a structured citation", async ({ page }) => {
  await page.route("**/api/backend/auth/login", (route) => route.fulfill({ json: { access_token: "demo" } }));
  await page.route("**/api/backend/conversations", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/backend/agui", (route) => route.fulfill({ contentType: "text/event-stream", body: "event: TEXT_MESSAGE_CONTENT\ndata: {\"messageId\":\"a\",\"delta\":\"A taxa é 1,49%.\"}\n\nevent: citations\ndata: {\"citations\":[{\"documentId\":\"d\",\"title\":\"Tarifas\",\"section\":\"1\",\"page\":null,\"marker\":\"【Tarifas — 1】\"}]}\n\n" }));
  await page.goto("/login"); await page.getByRole("button", { name: /Ana/ }).click(); await page.waitForURL("/");
  await page.getByLabel("Mensagem").fill("Qual a taxa?"); await page.getByRole("button", { name: "Enviar" }).click();
  await expect(page.getByText("A taxa é 1,49%.")).toBeVisible(); await expect(page.getByText("Tarifas — 1", { exact: true })).toBeVisible();
});
