import type { RelayAction } from "../lib/relay-api";

const statusCopy: Record<RelayAction["state"], string> = {
  planned: "Planned",
  awaiting_approval: "Awaiting your approval",
  authorized: "Authorized",
  dispatched: "Dispatched",
  in_progress: "In progress",
  succeeded: "Confirmation received. Verifying it now.",
  needs_user: "Needs your attention",
  retryable_failure: "Retrying safely",
  failed: "Could not complete",
  verified: "Verified",
  handoff_opened: "Uber opened. Confirm the ride in Uber.",
};

export function RelayActionStatus({ action }: { action: RelayAction }) {
  const label =
    action.type === "voice_call" && action.state === "needs_user"
      ? "Call outcome needs your attention"
      : statusCopy[action.state];
  return <p data-state={action.state}>{label}</p>;
}

export function RepairActions({ actions }: { actions: RelayAction[] }) {
  return (
    <ul aria-label="Action outcomes">
      {actions.map((action) => (
        <li key={action.id}>
          <RelayActionStatus action={action} />
        </li>
      ))}
    </ul>
  );
}

export const RelayActions = RepairActions;
