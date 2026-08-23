"use client";

import { useState } from "react";

import type { ApprovalBatchView } from "../../../packages/contracts/src";

import { decideApproval } from "../lib/relay-api";

type Props = {
  approval: ApprovalBatchView;
  onRefresh?: () => void;
  onApproved?: () => void;
};

export function ApprovalBatchCard({ approval, onRefresh, onApproved }: Props) {
  const [pending, setPending] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasVoice = approval.actions.some((action) => action.kind === "voice_call");
  const expired = Date.parse(approval.expires_at) <= Date.now();
  const disabled = pending || expired || approval.state !== "awaiting_approval";

  async function submit(decision: "approve" | "decline") {
    if (disabled || (decision === "approve" && hasVoice && !acknowledged)) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await decideApproval(approval.approval_id, decision, approval.version);
      onRefresh?.();
      if (decision === "approve") {
        onApproved?.();
      }
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "The approval could not be updated.";
      setError(message.includes("409") ? "This plan changed. Review the current version." : message);
      onRefresh?.();
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="approval-batch-heading">
      <h2 id="approval-batch-heading">Review and approve these limited actions</h2>
      <p>{approval.reason}</p>
      <p>Expires {new Date(approval.expires_at).toLocaleString()}</p>
      {expired && <p role="status">This approval has expired.</p>}
      <ul>
        {approval.actions.map((action) => (
          <li key={action.action_id}>
            <h3>{action.goal}</h3>
            <p>Allowed options: {action.authorized_options.join(", ") || "the approved hold or handoff"}</p>
            <p>Fee cap: INR {action.max_fee_inr}</p>
            {action.disclosure !== null && action.disclosure !== undefined && <p>{action.disclosure}</p>}
            <p>Will not {action.must_not.map((item) => item === "make payment" ? "make a payment" : item).join(", ")}</p>
          </li>
        ))}
      </ul>
      {hasVoice && (
        <label>
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
            disabled={disabled}
          />
          I approve this limited call to a pre-consented recipient.
        </label>
      )}
      {error !== null && <p role="alert">{error}</p>}
      <button
        type="button"
        onClick={() => void submit("approve")}
        disabled={disabled || (hasVoice && !acknowledged)}
      >
        Approve {approval.actions.length} actions
      </button>
      <button type="button" onClick={() => void submit("decline")} disabled={disabled}>
        Decline actions
      </button>
    </section>
  );
}
