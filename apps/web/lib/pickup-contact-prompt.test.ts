import { describe, expect, it } from "vitest";

import { pickupContactCommandSchema } from "../../../packages/contracts/src";

describe("pickup command boundary", () => {
  it("requires an explicit selection", () => {
    expect(pickupContactCommandSchema.safeParse({ selection: "no_pickup", expected_version: 1 }).success).toBe(true);
    expect(pickupContactCommandSchema.safeParse({ selection: "manual", expected_version: 1 }).success).toBe(false);
  });
});
