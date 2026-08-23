"use client";

import type { ActionOutcomeView } from "../../../packages/contracts/src";

import { UberHandoffButton } from "./uber-handoff-button";

const statusCopy: Record<ActionOutcomeView["status"], string> = {
  verified: "Verified",
  in_progress: "Calling within the approved limits",
  retrying: "Retrying safely",
  needs_user: "Needs your attention",
  failed: "Could not complete",
  handoff: "Uber opened. Confirm fare and booking in Uber",
};

export function ActionOutcomes({ outcomes }: { outcomes: readonly ActionOutcomeView[] }) {
  return (
    <section aria-labelledby="action-outcomes-heading">
      <h2 id="action-outcomes-heading" tabIndex={-1}>Action outcomes</h2>
      <ul aria-live="polite">
        {outcomes.map((outcome) => (
          <li key={outcome.action_id}>
            <p>{statusCopy[outcome.status]}</p>
            <p>{outcome.summary}</p>
            {outcome.evidence_label !== undefined && outcome.evidence_label !== null && (
              <p>{outcome.evidence_label}</p>
            )}
            {outcome.status === "handoff" && <UberHandoffButton actionId={outcome.action_id} />}
          </li>
        ))}
      </ul>
    </section>
  );
}
