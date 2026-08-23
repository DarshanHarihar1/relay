import { render, screen } from "@testing-library/react";

import { ActionOutcomes } from "./action-outcomes";
import { PlanTimeline } from "./plan-timeline";

const TIME = "2026-08-22T22:00:00Z";

it("renders the time-ordered plan with a text status for every item", () => {
  render(
    <PlanTimeline
      items={[
        {
          commitment_id: "dinner",
          title: "Dinner",
          starts_at: "2026-08-22T23:00:00Z",
          ends_at: "2026-08-23T00:00:00Z",
          status: "at_risk",
          explanation: "Timing changed.",
          is_pickup_prompt: false,
        },
        {
          commitment_id: "flight",
          title: "Flight arrival",
          starts_at: TIME,
          ends_at: "2026-08-22T22:20:00Z",
          status: "changed",
          explanation: "Arrival changed.",
          is_pickup_prompt: false,
        },
      ]}
    />,
  );

  expect(screen.getAllByRole("listitem").map((node) => node.textContent)).toEqual([
    expect.stringContaining("Flight arrival"),
    expect.stringContaining("Dinner"),
  ]);
  expect(screen.getByText("At risk")).toBeVisible();
});

it("labels an Uber click as a handoff, not a booking", () => {
  render(
    <ActionOutcomes
      outcomes={[
        {
          action_id: "ride-1",
          kind: "uber_deep_link",
          status: "handoff",
          summary: "Uber opened. Confirm fare and booking in Uber",
          occurred_at: TIME,
        },
      ]}
    />,
  );
  expect(screen.getByRole("button", { name: "Open Uber with this trip" })).toBeVisible();
  expect(screen.queryByText(/ride booked/i)).not.toBeInTheDocument();
});
