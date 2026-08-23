import {
  actionStateSchema,
  pickupContactCommandSchema,
  sourceEventEnvelopeSchema,
} from "../../../packages/contracts/src";

it("exposes the generated action states", () => {
  expect(actionStateSchema.safeParse("verified").success).toBe(true);
  expect(actionStateSchema.safeParse("ride_booked").success).toBe(false);
});

it("rejects source events without a stable idempotency key", () => {
  expect(
    sourceEventEnvelopeSchema.safeParse({
      source: "gmail",
      source_event_key: "",
      occurred_at: "2026-08-23T12:00:00Z",
      payload: {},
      correlation_id: "correlation-1",
    }).success,
  ).toBe(false);
});

it("keeps the pickup union exclusive in browser validation", () => {
  expect(
    pickupContactCommandSchema.safeParse({ selection: "manual", expected_version: 1 }).success,
  ).toBe(false);
  expect(
    pickupContactCommandSchema.safeParse({ selection: "no_pickup", expected_version: 1 }).success,
  ).toBe(true);
});
