import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalBatch } from "./approval-batch";
import { decideApproval } from "../lib/relay-api";

vi.mock("../lib/relay-api", async () => {
  const actual = await vi.importActual<typeof import("../lib/relay-api")>("../lib/relay-api");
  return { ...actual, decideApproval: vi.fn() };
});

const approval = {
  id: "approval-1",
  state: "awaiting_approval" as const,
  version: 1,
  action_ids: ["call-1"],
  actions: [
    {
      id: "call-1",
      type: "voice_call" as const,
      state: "awaiting_approval" as const,
      target_ref: "place:toscano",
      authorization_snapshot: { type: "voice_call", max_fee_inr: 0 },
      retry_count: 0,
    },
  ],
};

describe("ApprovalBatch", () => {
  beforeEach(() => {
    vi.mocked(decideApproval).mockReset().mockResolvedValue({
      approval_id: "approval-1",
      state: "approved",
      action_ids: ["call-1"],
    });
  });

  it("submits one versioned approval while the request is pending", async () => {
    render(<ApprovalBatch approval={approval} />);
    const button = screen.getByRole("button", { name: "Approve repair" });
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() => expect(decideApproval).toHaveBeenCalledTimes(1));
    expect(decideApproval).toHaveBeenCalledWith("approval-1", "approve", 1);
  });
});
