import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { listAudit } from "@/lib/api";
import { actorLabel, AuditDetailDrawer, brl, dateTime, securityEvent } from "@/components/admin/admin-dashboard";

describe("audit utilities", () => {
  it("formats money, local dates and security events safely", () => {
    expect(brl(null)).toBe("—");
    expect(brl("20000")).toContain("20.000,00");
    expect(dateTime("2026-07-15T12:00:00Z")).toMatch(/15\/07\/2026/);
    expect(securityEvent({ action: "AUTH_DENIED", outcome: "denied" } as never)).toBe(true);
    expect(actorLabel({ actor: { name: "Carla Souza", email: "admin@demo.local" } } as never)).toBe("Carla Souza — admin@demo.local");
    expect(actorLabel({ actor: null } as never)).toBe("Sistema");
  });

  it("serializes server-side audit filters and pagination", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("[]", { headers: { "X-Total-Count": "3" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(listAudit("token", { user: "ana@demo", action: "PIX", from: "2026-07-01T00:00:00Z", to: "2026-07-15T00:00:00Z", page: 2, pageSize: 25 })).resolves.toEqual({ items: [], total: 3 });
    expect(fetchMock.mock.calls[0][0]).toContain("user=ana%40demo");
    expect(fetchMock.mock.calls[0][0]).toContain("page=2");
  });
});

it("shows the immutable detail, external trace link and closes the drawer", () => {
  const close = vi.fn();
  render(<AuditDetailDrawer loading={false} error={false} onClose={close} event={{ id: "1", event_id: "event-1", user_ref: "ana", action: "PIX", amount: "20000", occurred_at: "2026-07-15T12:00:00Z", resource: "pix:m***", outcome: "executed", trace_id: "trace one", details: { recipient: "m***" }, actor: { id: "actor-1", name: "Ana", email: "ana@demo.local", role: "customer" } }} />);
  expect(screen.getByRole("link", { name: "Abrir trace no Langfuse" })).toHaveAttribute("href", "http://localhost:3001/trace/trace%20one");
  expect(screen.getByText("Ana — ana@demo.local")).toBeVisible();
  expect(screen.getByText(/recipient/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Fechar" }));
  expect(close).toHaveBeenCalledOnce();
});
