"use client";

import { useState } from "react";

import { decideApproval, type ApprovalDecision, type RelayAction } from "../lib/relay-api";

export type ApprovalBatchData = {
  id: string;
  state: "awaiting_approval" | "approved" | "declined";
  version: number;
  action_ids: string[];
  actions: RelayAction[];
};

type Props = {
  approval: ApprovalBatchData;
  onRefresh?: () => void;
};

export function ApprovalBatch({ approval, onRefresh }: Props) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(decision: ApprovalDecision) {
    if (pending || approval.state !== "awaiting_approval") {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await decideApproval(approval.id, decision, approval.version);
      onRefresh?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The approval could not be updated.");
      onRefresh?.();
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-label="Approval batch">
      <h2>Review proposed actions</h2>
      <ul>
        {approval.actions.map((action) => (
          <li key={action.id}>
            <strong>{action.type.replaceAll("_", " ")}</strong>
            {action.type !== "voice_call" && <span> — {action.target_ref}</span>}
            <p>{approvedBounds(action)}</p>
          </li>
        ))}
      </ul>
      {error !== null && <p role="alert">{error}</p>}
      <button type="button" onClick={() => submit("approve")} disabled={pending || approval.state !== "awaiting_approval"}>
        Approve repair
      </button>
      <button type="button" onClick={() => submit("decline")} disabled={pending || approval.state !== "awaiting_approval"}>
        Decline repair
      </button>
    </section>
  );
}

function approvedBounds(action: RelayAction): string {
  const snapshot = action.authorization_snapshot;
  if (snapshot.type === "voice_call") {
    const options = Array.isArray(snapshot.authorized_options)
      ? snapshot.authorized_options.join(", ")
      : "the listed options";
    return "Allowed options: " + options + ". No charge is authorized.";
  }
  if (snapshot.type === "calendar_hold") {
    return "Private Calendar hold from " + String(snapshot.start_at) + " to " + String(snapshot.end_at) + ".";
  }
  return "Pickup: " + String(snapshot.pickup) + ". Destination: " + String(snapshot.destination) + ".";
}
