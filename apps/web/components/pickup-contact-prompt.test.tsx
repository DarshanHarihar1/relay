import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PickupContactPrompt } from "./pickup-contact-prompt";
import { submitPickup } from "../lib/relay-api";

vi.mock("../lib/relay-api", () => ({
  searchPickerContacts: vi.fn(),
  submitPickup: vi.fn().mockResolvedValue({
    commitment_id: "pickup-1",
    version: 2,
    selection: "no_pickup",
    display_name: null,
  }),
}));

it("does not create a pickup contact until the user explicitly chooses one", async () => {
  render(<PickupContactPrompt commitmentId="pickup-1" version={1} />);
  expect(submitPickup).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "No" }));
  await waitFor(() => expect(submitPickup).toHaveBeenCalledWith("pickup-1", {
    selection: "no_pickup",
    expected_version: 1,
  }));
});

it("clears a manually entered phone after the command resolves", async () => {
  render(<PickupContactPrompt commitmentId="pickup-1" version={2} />);
  fireEvent.click(screen.getByRole("button", { name: "Enter number" }));
  const phone = screen.getByLabelText("Phone number");
  fireEvent.change(phone, { target: { value: "+919999999999" } });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Asha" } });
  fireEvent.click(screen.getByRole("button", { name: "Save pickup contact" }));
  await waitFor(() => expect(phone).toHaveValue(""));
});
