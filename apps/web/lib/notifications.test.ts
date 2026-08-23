import { describe, expect, it, vi } from "vitest";

import { handleForegroundMessage, initializeNotifications } from "./notifications";

describe("Relay notifications", () => {
  it("registers a token only after notification permission is granted", async () => {
    const registerDevice = vi.fn().mockResolvedValue(undefined);
    const getToken = vi.fn().mockResolvedValue("f".repeat(32));
    const requestPermission = vi.fn().mockResolvedValueOnce("default").mockResolvedValueOnce("granted");

    await initializeNotifications({ requestPermission, getToken, registerDevice });
    expect(registerDevice).not.toHaveBeenCalled();

    await initializeNotifications({ requestPermission, getToken, registerDevice });
    expect(registerDevice).toHaveBeenCalledWith("f".repeat(32));
  });

  it("invalidates the dashboard for a safe foreground data message", () => {
    const invalidateDashboard = vi.fn();
    const announce = vi.fn();

    handleForegroundMessage(
      { data: { kind: "outcome_updated", entity_id: "act_1", correlation_id: "corr_1" } },
      { invalidateDashboard, announce },
    );

    expect(invalidateDashboard).toHaveBeenCalledTimes(1);
    expect(announce).toHaveBeenCalledWith("Relay has an update.");
    expect(document.body.textContent).not.toContain("restaurant");
  });
});
