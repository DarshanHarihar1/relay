import { describe, expect, it } from "vitest";

import { approvalBatchViewSchema } from "../../../packages/contracts/src";

describe("approval batch view boundary", () => {
  it("requires the approval expiry and bounded action details", () => {
    const result = approvalBatchViewSchema.safeParse({
      approval_id: "approval-1",
      version: 1,
      state: "awaiting_approval",
      expires_at: "2026-08-22T17:00:00Z",
      reason: "Review these actions.",
      actions: [],
    });
    expect(result.success).toBe(true);
  });
});
