# Relay four-minute demo runbook

This runbook keeps the demo honest: Relay may propose bounded actions and show
verified outcomes, but it does not claim a booking or a provider result before
the corresponding evidence exists.

## Before the demo

- Operator: confirm the signed-in Firebase demo user, staging origin, Gmail
  label, Calendar write/read-back access, FCM permission, and the recipient's
  explicit pre-consented voice-call boundary.
- Confirm the Vapi/Twilio readiness check and callback signatures without
  printing credentials or phone numbers. Use the `rehearsal` seed with fake
  adapters first; it is a visibly labelled mock.
- Check Cloud Run API/worker revision IDs and the alert dashboard. Pause the
  demo on any webhook signature failure, dead-letter work, or stuck action.

## Four-minute sequence

### 0:00-0:30 — consent and scope

Identify Relay, the signed-in user, the pre-consented recipient, and the
bounded action policy. State that the user must explicitly choose pickup and
approve the action batch. Explain that outcomes are only called verified after
independent evidence.

### 0:30-1:10 — inject the delay

Inject the labelled Gmail flight-delay message. Show the flight arrival at
22:05, the optional pickup prompt, dinner, and hotel timeline. Keep the
unrelated protected commitment out of the repair plan.

### 1:10-1:45 — answer pickup explicitly

Choose `No`, select one current picker contact, or enter one manual number. Do
not pre-fill a contact or imply that a directory was synced. Show that the
dashboard refreshes after the versioned command.

### 1:45-2:20 — inspect and approve once

Open the approval batch. Point out the goal, allowed options, zero fee cap,
identity disclosure, prohibition on payment/transfer, and expiry. Check the
voice acknowledgement and approve once. A rapid second click must not create a
second dispatch.

### 2:20-3:10 — call and Calendar verification

Use the real pre-consented Vapi/Twilio call only after the operator confirms
consent. Show the identity disclosure and bounded tool result. Read the private
Calendar hold back independently. If using the fake adapter, say that it is a
visibly labelled mock; never present it as a real call.

### 3:10-3:35 — Uber handoff

Click the user-controlled Uber handoff. It opens Uber for the user to review.
Never say the ride is booked. Relay does not pay, book, or cancel.

### 3:35-4:00 — evidence and unresolved state

Show the redacted audit projection and opaque correlation ID. Inject a no-answer
fixture and point out `Needs your attention`. Explain that no-answer,
unexpected fee, contradiction, or missing verification is not success.

## Reset and rollback

- Reset rehearsal data with the idempotent `rehearsal` seed for the demo user;
  never reset a real user's collection.
- If the demo window is unhealthy, stop approvals, pause the worker's new
  deliveries, route the API and worker back to the previous known-good revision,
  then re-run `scripts/smoke-demo.sh` with a short-lived token on stdin.
- Record only Cloud Build ID, revision IDs, smoke timestamp, and correlation
  IDs in `docs/release-checklist.md`. Mask screenshots and do not retain
  provider references, contact details, transcripts, or tokens.
