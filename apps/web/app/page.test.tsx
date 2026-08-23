import { render, screen } from "@testing-library/react";
import Home from "./page";

it("renders the Relay development shell", () => {
  render(<Home />);
  expect(screen.getByRole("heading", { name: "Relay" })).toBeVisible();
});
