import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalBatchCard } from "./approval-batch-card";
import { decideApproval } from "../lib/relay-api";

vi.mock("../lib/relay-api", () => ({
  decideApproval: vi.fn(),
}));

const approval = {
  approval_id: "approval-1",
  version: 1,
  state: "awaiting_approval" as const,
  expires_at: "2026-08-30T17:00:00Z",
  reason: "Review these actions.",
  actions: [
    {
      action_id: "call-1",
      kind: "voice_call" as const,
      goal: "Confirm the limited dinner timing",
      authorized_options: ["confirm_new_time"],
      max_fee_inr: 0,
      expires_at: "2026-08-30T17:00:00Z",
      disclosure: "Relay will identify itself.",
      must_not: ["make payment"],
    },
  ],
};

describe("ApprovalBatchCard", () => {
  beforeEach(() => {
    vi.mocked(decideApproval).mockReset().mockResolvedValue({
      approval_id: "approval-1",
      state: "approved",
      action_ids: ["call-1"],
    });
  });

  it("shows disclosure, bounds, and expiry before approval", () => {
    render(<ApprovalBatchCard approval={approval} />);
    expect(screen.getByText(/Relay will identify itself/i)).toBeVisible();
    expect(screen.getByText(/Fee cap: INR 0/i)).toBeVisible();
    expect(screen.getByText(/Expires/i)).toBeVisible();
  });

  it("submits one approval request while the request is pending", async () => {
    let release: (() => void) | undefined;
    vi.mocked(decideApproval).mockImplementation(
      () => new Promise((resolve) => {
        release = () => resolve({ approval_id: "approval-1", state: "approved", action_ids: ["call-1"] });
      }),
    );
    render(<ApprovalBatchCard approval={approval} />);
    fireEvent.click(screen.getByRole("checkbox"));
    const button = screen.getByRole("button", { name: "Approve 1 actions" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(decideApproval).toHaveBeenCalledTimes(1));
    release?.();
  });
});
