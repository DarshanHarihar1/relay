import { render, screen } from "@testing-library/react";

import { RelayActions } from "./relay-action-status";

it("does not present unresolved or handoff states as successful repairs", () => {
  render(
    <RelayActions
      actions={[
        {
          id: "call-1",
          type: "voice_call",
          state: "needs_user",
          target_ref: "venue",
          authorization_snapshot: {},
          retry_count: 0,
        },
        {
          id: "ride-1",
          type: "uber_deep_link",
          state: "handoff_opened",
          target_ref: "dinner",
          authorization_snapshot: {},
          retry_count: 0,
        },
      ]}
    />,
  );

  expect(screen.getByText("Call outcome needs your attention")).toBeVisible();
  expect(screen.getByText("Uber opened. Confirm the ride in Uber.")).toBeVisible();
  expect(screen.queryByText("Ride booked")).not.toBeInTheDocument();
});
