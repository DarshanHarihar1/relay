# Implementation plan handoff review

Read this before implementing Phases 2 through 5. The detailed phase plans are kept in the workspace `docs/` directory alongside the technical specification.

## Preflight

Run the completed foundation checks before changing application code:

```bash
CI=true FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 UV_CACHE_DIR=/private/tmp/relay-uv-cache bash scripts/verify-foundation.sh
```

The emulator must be local. Never make an emulator test pass by using production Firestore.

## Contract compatibility gate

The completed Phase 1 models in `services/relay-api/app/contracts.py` are intentionally small. Later plans require richer planning fields.

| Model | Current foundation | Required before Phase 3 |
|---|---|---|
| `Commitment` | summary and start/end timestamps | title/type, schedule windows, location, flexibility, criticality, buffers, participants, protection |
| `Edge` | `from_ref`, `to_ref`, `relation` | typed edge kind, `from_id`/`to_id`, minimum gap, confidence, travel/location requirements |
| `Disruption` | source and timing metadata | changed commitment ID, changed schedule, confidence, provenance, encrypted evidence |

Phase 2 must extend these canonical models compatibly, or perform a documented migration with serialization tests. Do not create a second persisted model in the planner. Phase 3 domain code belongs in `app.domain.impact`; do not create `app.impact.*` packages.

## Fixed product decisions

- A pickup is not inferred. The user chooses `No`, selects one current Google People result, or enters one manual number. A seeded prompt is not a callable contact until confirmed.
- Vapi is Relay's only voice adapter. Twilio is only the imported carrier number for Vapi. Do not add direct Twilio dispatch, Sarvam, Pipecat, Exotel, or a provider fallback.
- Every voice action has `max_fee_inr = 0`, a short expiry, a fixed identity disclosure, finite approved options, a selected recipient reference, and explicit approval.
- Use Vertex AI through ADC locally and the Cloud Run service account in deployment. Do not add a Gemini Developer API key to browser code, `.env.example`, tests, or Cloud Run. Reconcile the legacy `GEMINI_API_KEY` names still present in the Phase 1 infrastructure before implementing Gemini extraction.
- The Gmail notification email address is input data. Resolve it to exactly one active connection server-side. Never let Pub/Sub choose a user ID or another user's cursor.
- Phase 2 reads bounded Calendar free/busy only. Phase 4 owns Calendar writes. Phase 3 is pure and makes no provider calls.
- FCM is optional. Dashboard correctness must not depend on notification delivery.
- Uber is always a user handoff. `handoff_opened` is never a booking state.

## Reliability requirements

- Treat Gmail, Pub/Sub, provider callbacks, and Calendar reads as at-least-once.
- Use idempotent source-event claims, monotonic cursors, deterministic plan IDs, and one approval batch.
- Reconcile an ambiguous provider create before retrying. Do not assume Vapi or Twilio deduplicates a request.
- `succeeded` is not independently verified. Only bounded Vapi outcome evidence or a matching Calendar read-back can produce `verified`.
- Keep phone numbers, booking references, tokens, message content, transcripts, and provider payloads out of logs, browser state, notifications, and projections.

## Demo boundary

Keep the four-minute path narrow: labelled Gmail delay, explicit pickup choice, deterministic blast radius, one approval batch, one pre-consented Vapi call, private Calendar read-back, Uber handoff, and honest audit state. Tests use fakes and visibly label fake calls. Never use a real call as a retry experiment.

The workspace review also removed duplicate instructions, corrected Phase 3 module examples, removed interactive prompts from the plan files, and aligned the foundation voice validator with the documented zero-fee bound.
