import { render, screen } from "@testing-library/react";

import RepairPlanPage from "./page";

it("renders a truthful repair-plan shell", async () => {
  render(await RepairPlanPage({ params: Promise.resolve({ repairPlanId: "plan-1" }) }));

  expect(screen.getByRole("heading", { name: "Repair plan" })).toBeVisible();
  expect(screen.queryByText("Ride booked")).not.toBeInTheDocument();
});
