import { render, screen } from "@testing-library/react";
import { vi } from "vitest";

import Home from "./page";

vi.mock("../lib/firebase", () => ({
  getFirebaseAuth: () => ({
    currentUser: null,
  }),
}));

vi.mock("firebase/auth", () => ({
  GoogleAuthProvider: class {},
  onAuthStateChanged: (_auth: unknown, callback: (user: null) => void) => {
    callback(null);
    return vi.fn();
  },
  signInWithPopup: vi.fn(),
  signOut: vi.fn(),
}));

it("renders the Relay development shell", () => {
  render(<Home />);
  expect(screen.getByRole("heading", { name: "Relay" })).toBeVisible();
});
