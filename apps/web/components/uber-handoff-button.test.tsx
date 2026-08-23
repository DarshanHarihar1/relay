import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UberHandoffButton } from "./uber-handoff-button";
import { openUberHandoff } from "../lib/relay-api";

vi.mock("../lib/relay-api", () => ({ openUberHandoff: vi.fn() }));

describe("UberHandoffButton", () => {
  beforeEach(() => {
    vi.mocked(openUberHandoff).mockReset().mockResolvedValue({
      action_id: "ride-1",
      state: "handoff_opened",
      url: "https://m.uber.com/ul/?action=setPickup",
    });
  });

  it("records the handoff before navigating and never says the ride was booked", async () => {
    const assign = vi.fn();
    Object.defineProperty(window, "location", { value: { assign }, writable: true });
    render(<UberHandoffButton actionId="ride-1" />);

    fireEvent.click(screen.getByRole("button", { name: "Open Uber with this trip" }));

    await waitFor(() => expect(openUberHandoff).toHaveBeenCalledWith("ride-1"));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://m.uber.com/ul/?action=setPickup"));
    expect(screen.queryByText(/booked/i)).not.toBeInTheDocument();
  });
});
