import { expect, test } from "@playwright/test";

const auditRow = { id: "audit-1", event_id: "source-1", user_ref: "ana@demo", action: "PIX", amount: "20000", occurred_at: "2026-07-15T18:21:00Z", resource: "pix:m***", outcome: "executed", trace_id: "trace-1", details: { recipient: "m***@exemplo.com" } };

async function login(page: import("@playwright/test").Page, persona: "Carla" | "Bruno") {
  await page.route("**/api/backend/auth/login", (route) => route.fulfill({ json: { access_token: "demo" } }));
  await page.goto("/login");
  await page.getByRole("button", { name: new RegExp(persona) }).click();
  await page.waitForURL("/");
}

test("Carla filters PIX, opens the audit detail and trace link", async ({ page }) => {
  await page.route("**/api/backend/conversations", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/backend/admin/audit?*", (route) => route.fulfill({ headers: { "X-Total-Count": "1" }, json: [auditRow] }));
  await page.route("**/api/backend/admin/audit/audit-1", (route) => route.fulfill({ json: auditRow }));
  await login(page, "Carla");
  await page.goto("/admin");
  await page.getByLabel("Ação").selectOption("PIX");
  await expect(page.getByRole("cell", { name: "PIX" })).toBeVisible();
  await page.getByRole("button", { name: /15\/07\/2026/ }).click();
  const link = page.getByRole("link", { name: "Abrir trace no Langfuse" });
  await expect(link).toHaveAttribute("href", "http://localhost:3001/trace/trace-1");
  await page.getByRole("button", { name: "Fechar" }).press("Enter");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("Bruno is redirected to the not-authorized screen", async ({ page }) => {
  await page.route("**/api/backend/conversations", (route) => route.fulfill({ json: [] }));
  await login(page, "Bruno");
  await page.goto("/admin");
  await expect(page).toHaveURL("/403");
  await expect(page.getByRole("heading", { name: "Acesso não autorizado" })).toBeVisible();
});

test("an empty audit filter is a friendly state", async ({ page }) => {
  await page.route("**/api/backend/conversations", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/backend/admin/audit?*", (route) => route.fulfill({ headers: { "X-Total-Count": "0" }, json: [] }));
  await login(page, "Carla");
  await page.goto("/admin");
  await expect(page.getByText("Nenhum evento foi encontrado com estes filtros.")).toBeVisible();
});

test("a logged-out visitor returns through login before opening admin", async ({ page }) => {
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/login\?next=\/admin/);
});
