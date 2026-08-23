# Relay release checklist

This checklist is evidence-oriented. Replace only the opaque IDs and timestamps
below; never paste tokens, phone numbers, provider references, or secret values.

## Pre-release gates

- [ ] `pnpm check:contracts` and generated-contract diff are clean.
- [ ] Node 22/pnpm 9 web lint, unit tests, and Next build pass.
- [ ] API Ruff, unit tests, emulator tests, and deterministic E2E tests pass.
- [ ] Secret Manager names were verified separately; absent provider/FCM
      secrets remain a release blocker.
- [ ] Firebase demo user, staging origin, Gmail label, Calendar write scope,
      and pre-consented recipient are confirmed by the operator.

## Build and deployment evidence

- Cloud Build ID: `TBD`
- Commit SHA: `TBD`
- API Cloud Run revision: `TBD`
- Worker Cloud Run revision: `TBD`
- Region: `asia-south1`
- Smoke correlation ID: `TBD`
- Smoke timestamp (UTC): `TBD`
- Alert policy status: `TBD`

The build must run contract generation, web checks, API checks, immutable
SHA-tagged image builds, and only then deploy the API and private worker. The
smoke script reads a short-lived Firebase ID token from stdin and never sends an
approval or provider command.

## Rollback

1. Stop the demo operator and disable the demo window alert acknowledgement.
2. Route traffic back to the previous known-good API revision.
3. Pin the worker to the previous known-good revision and pause new Pub/Sub
   deliveries if a dispatch or verification error is active.
4. Capture only Cloud Build, revision, and correlation IDs in the incident note.
5. Re-run the smoke script against the restored API before resuming the demo.

## Demo-window alert handling

The operator acknowledges only the alert policy and timestamp, not its private
payload. Any webhook signature failure, dead-letter work, action error-rate
breach, or stuck approved voice action pauses the demo until reviewed.
