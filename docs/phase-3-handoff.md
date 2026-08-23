# Phase 2 to Phase 3 handoff

Phase 2 ends the moment a disruption is durably created. Everything after that belongs to
Phase 3 impact assessment.

## The single handoff command

```python
class AssessDisruption(ContractModel):
    disruption_id: str
    commitment_id: str
    correlation_id: str
    source_event_key: str
```

Defined in `services/relay-api/app/services/retention.py`. It carries identifiers only. No
subject, sender, body, evidence excerpt, booking reference, phone number, or mailbox address
appears in it, and `test_the_handoff_command_carries_only_identifiers` enforces that.

## When it is emitted

`GmailIngestionService._match` enqueues exactly one `AssessDisruption` and only when all of the
following are true, in order:

1. The Pub/Sub push carried a valid Google OIDC token for the configured service account.
2. The mailbox resolved server-side to exactly one active Google connection.
3. The notification was newer than the stored history cursor.
4. The message was inside the configured Gmail label, both in history and on re-check.
5. `claim_source_event` succeeded, so this is the first time this source event was processed.
6. Gemini returned a candidate that passed schema, evidence, changed-time, and confidence checks.
7. `ConservativeCommitmentMatcher.match` returned `status="matched"`.
8. `create_disruption_if_absent` returned `True`, meaning this disruption did not already exist.

## When it is never emitted

| Situation | Phase 3 command |
|---|---|
| `needs_review` from extraction (schema, safety, evidence, low confidence, missing new time) | None |
| `needs_review` from matching (duplicate reference, ambiguous, weak score) | None |
| `no_match` | None |
| Duplicate or stale notification | None |
| Message outside the configured label | None |
| `create_disruption_if_absent` returned `False` | None |

A review case creates no disruption, mutates no commitment, and enqueues nothing. That is the
Phase 2 safety boundary: Relay would rather do nothing than act on a guess.

## Idempotency guarantee

`disruption_id = sha256("{source_event_key}|{commitment_id}")`. The same Gmail message and the
same commitment always resolve to the same disruption document, and Firestore `create` fails if
it already exists. A redelivered Pub/Sub message therefore yields `created=False` and no second
command. `test_a_redelivered_notification_never_enqueues_a_second_command` covers this.

The Phase 3 consumer must still deduplicate by `disruption_id`, because the enqueue itself is
at-least-once.

## What Phase 3 reads

`Disruption` (`app/contracts.py`) is the one canonical record. Phase 2 populates:

`commitment_id`, `kind`, `occurred_at`, `previous_time`, `new_time`, `provider`,
`location_text`, `encrypted_booking_reference`, `evidence_excerpt`, `gmail_source`,
`model_version`, `match_score`, `match_reasons`, and `provenance`.

`evidence_excerpt` and `gmail_source` are raw evidence and are cleared by the 30-day retention
job. Phase 3 must not depend on them being present indefinitely.

Read-only context is available through `CommitmentContextReader.read_commitment_context`
(`app/services/context_readers.py`), which returns a `CommitmentContext` with an explicit
`unavailable_reasons` list. Phase 2 builds this reader but never calls it; Phase 3 owns when
context is fetched.

## What Phase 3 must not assume

- The user's friends do not necessarily use Gmail or Google Calendar. Calendar is optional
  context, never the coordination layer.
- A pickup contact exists only when the user explicitly selected it.
- Phase 2 made no outbound call and mutated no external commitment. Vapi is the only planned
  voice provider and is not called before Phase 4.
