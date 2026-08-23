import { beforeEach, describe, expect, it, vi } from "vitest";

const firebase = vi.hoisted(() => ({
  getIdToken: vi.fn(),
  currentUser: { getIdToken: vi.fn() } as { getIdToken: () => Promise<string> } | null,
}));

vi.mock("./firebase", () => ({
  getFirebaseAuth: () => ({ currentUser: firebase.currentUser }),
}));

import { relayFetch } from "./relay-api";

describe("relayFetch", () => {
  beforeEach(() => {
    firebase.getIdToken.mockReset().mockResolvedValue("test-token");
    firebase.currentUser = { getIdToken: firebase.getIdToken };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ uid: "u1" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })));
  });

  it("attaches a Firebase ID token and never exposes provider credentials", async () => {
    await relayFetch("/v1/me");

    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/v1/me"), expect.objectContaining({
      headers: expect.objectContaining({ Authorization: "Bearer test-token" }),
    }));
    expect(JSON.stringify(vi.mocked(fetch).mock.calls)).not.toContain("VAPI_PRIVATE_KEY");
  });

  it("rejects an authenticated request when no Firebase user is available", async () => {
    firebase.currentUser = null;

    await expect(relayFetch("/v1/me")).rejects.toThrow("Sign in is required");
  });
});
