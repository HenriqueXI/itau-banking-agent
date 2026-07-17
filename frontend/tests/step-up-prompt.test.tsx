import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StepUpPrompt } from "@/components/chat/step-up-prompt";

const payload = { operationHash: "pix-1", expiresAt: "2030-01-01T00:00:00Z" };

describe("StepUpPrompt", () => {
  it("auto-submits on the sixth digit", async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<StepUpPrompt payload={payload} onSubmit={submit} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Código"), { target: { value: "123456" } });
    await waitFor(() => expect(submit).toHaveBeenCalledWith("123456"));
  });

  it("shows attempts left on a wrong code and cancels after the third failure", async () => {
    const submit = vi.fn().mockRejectedValue(new Error("invalid"));
    const cancel = vi.fn().mockResolvedValue(undefined);
    render(<StepUpPrompt payload={payload} onSubmit={submit} onCancel={cancel} />);
    const input = screen.getByLabelText("Código");

    fireEvent.change(input, { target: { value: "000000" } });
    await screen.findByText(/2 tentativas restantes/);
    fireEvent.change(input, { target: { value: "000000" } });
    await screen.findByText(/1 tentativa restante/);
    fireEvent.change(input, { target: { value: "000000" } });
    await waitFor(() => expect(cancel).toHaveBeenCalledOnce());
  });
});
