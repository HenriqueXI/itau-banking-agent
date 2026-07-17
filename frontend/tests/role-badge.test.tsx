import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RoleBadge } from "@/components/shared/role-badge";

describe("RoleBadge", () => {
  it.each([
    ["Ana", "customer", "Cliente"],
    ["Bruno", "manager", "Gerente"],
    ["Carla", "admin", "Admin"],
  ] as const)("labels %s with the textual role, never color-only", (name, role, label) => {
    render(<RoleBadge name={name} role={role} />);
    expect(screen.getByText(name)).toBeInTheDocument();
    expect(screen.getByText(`· ${label}`)).toBeInTheDocument();
  });
});
