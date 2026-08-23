# Phase 3 to Phase 4 handoff

Phase 3 ends the moment a `RepairPlan` is durably persisted. It never calls a provider adapter,
never opens a phone call, never writes a Calendar hold, and never books a ride. Everything after
approval belongs to Phase 4 dispatch and verification.

## What Phase 3 persists, and where

| Document | Collection | Written by |
|---|---|---|
| `ImpactAssessment` | `users/{uid}/impact_assessments/{id}` | `FirestoreRelayRepository.save_planning_result` |
| `RepairPlan` | `users/{uid}/repair_plans/{id}` | same, same transaction |
| `ActionRecord` (zero or more) | `users/{uid}/actions/{id}` | same, same transaction |
| `Approval` (zero or one) | `users/{uid}/approvals/{id}` | same, same transaction |

All four are written in one Firestore transaction, keyed by content-addressed IDs
(`app/domain/impact.py`: `make_assessment_id`, `make_repair_plan_id`, `make_action_record_id`,
`make_action_idempotency_key`). A repeat of the same disruption, graph, options, and policy is a
plain read of the existing `RepairPlan`, never a second write.

**There is no `ActionIntent`, `ActionBounds`, or `ApprovalBatch` type.** An earlier draft of the
Phase 3 plan proposed those three as new types; the actual implementation produces Phase 1's
already-canonical `ActionRecord`, `AuthorizationSnapshot` (the `voice_call` / `calendar_hold` /
`uber_deep_link` discriminated union), and `Approval` directly, to avoid a second overlapping
action/approval system. See `.superpowers/sdd/phase-03-impact-repair-planning/progress.md`,
Finding 3, for the full field-by-field mapping if you are comparing against the plan document.

## What an `ActionRecord` looks like coming out of Phase 3

- `state` is `AWAITING_APPROVAL` when any action in its batch was policy-decided `ASK` (every
  voice call and every `OPEN_UBER_HANDOFF` always is), or `AUTHORIZED` when every action in the
  batch was `AUTO` and none was `ASK`.
- `state` is never `BLOCKED`: a `NEVER`-policy action produces **no `ActionRecord` at all**. The
  block is explained only in the owning `RepairCandidate.invalid_reasons` / `explanation`.
- `repair_plan_id` and `repair_plan_version` identify the plan and version that authorized it.
- `idempotency_key` is `make_action_idempotency_key(repair_plan_version, kind, target_ref,
  authorization_snapshot)` — it changes if and only if the authorized bounds change.
- `authorization_snapshot` is already the correct discriminated variant for `type`
  (`voice_call` / `calendar_hold` / `uber_deep_link`); Phase 4 does not construct one.

## When an `Approval` exists

- Not created at all when the selected candidate needed no action (`action_kinds == ()` on every
  change, e.g. a plain `KEEP_AS_IS`), or when every action was `AUTO`-authorized directly.
- Not created when any action in the batch was policy-decided `NEVER` (the whole batch is blocked;
  `RepairPlan.approval_id` stays `None`).
- Created once, `state="awaiting_approval"`, when the batch contains at least one `ASK` action —
  which is every voice call and every Uber handoff, unconditionally. `action_ids` lists every
  `ActionRecord` in the batch, sorted by `(kind.value, target_ref)`. `expires_at` is shared by the
  whole batch (from `PlanningOptions.approval_expires_at`).
- The existing `/v1/approvals/{approval_id}/decision` endpoint (`app/routes/actions.py`, built in
  Phase 1) already transitions `Approval.state` from `awaiting_approval` to `approved` or
  `declined`. Phase 4 does not need a new approval-decision endpoint.

## Required Phase 4 dispatch contract

Before calling an adapter, Phase 4 must transactionally verify the chain and write the exact
`ActionRecord.idempotency_key`, rejecting duplicates:

```python
class AuthorizedActionLoader(Protocol):
    async def load_for_dispatch(self, action_id: str, user_id: str, now: datetime) -> ActionRecord: ...

async def authorize_and_dispatch(action_id: str, user_id: str) -> DispatchResult: ...
```

`load_for_dispatch` must reject an `ActionRecord` that:

- does not belong to `user_id`,
- is not `AUTHORIZED` (an `AWAITING_APPROVAL` action must go through the Phase 1 approval-decision
  endpoint first, which is what actually transitions it toward `AUTHORIZED`),
- has `expires_at` in the past,
- belongs to an `Approval` that is not `approved` (when one exists).

`authorize_and_dispatch` must transactionally move `AUTHORIZED -> DISPATCHED`, call the adapter
matching `authorization_snapshot.type`, and record the terminal state as `VERIFIED`,
`NEEDS_USER`, `RETRYABLE_FAILURE`, or `FAILED` (`ActionState` in `app/contracts.py` already has
all four). If the call outcome is expired, contradictory, unanswered, carries an unexpected fee,
or offers an option outside `authorized_options`/`must_not`, transition to `NEEDS_USER` — never
guess a successful repair. An `OPEN_UBER_HANDOFF` action's only real terminal states are
`HANDOFF_OPENED` or `NEEDS_USER`; `ActionState` has no `RIDE_BOOKED` value, and Phase 4 must not
add one — Relay opens Uber for the user, it never books on their behalf.

## What Phase 4 must not assume

- No route/Maps integration exists yet. Every `requires_travel` / `requires_location` edge Phase 3
  evaluated with an unresolved route is already visible as `ROUTE_UNKNOWN` / `AT_RISK` in the
  persisted `ImpactAssessment` and any candidate's `ConstraintTrace`, not silently treated as
  zero-travel-time. `MAPS_API_KEY` is not yet a real Secret Manager secret
  (`infra/gcp/secret-manifest.txt`).
- No per-user `UserActionPolicy` store exists yet. `/v1/disruptions/{id}/repair-plans`
  (`app/routes/repair_plans.py`) always plans against a bare default `UserActionPolicy()`, which
  is already maximally conservative (every voice call and Uber handoff is `ASK` regardless).
  Phase 4 must not assume a richer, persisted policy is being read from anywhere.
- Phase 3 may be re-run for the same disruption with different graph state, options, or policy —
  that produces a genuinely different `input_fingerprint` and therefore a new `RepairPlan.id` at
  version 1, not a version bump on the existing plan. There is currently no `PlanVersionConflict`
  and no version-2+ re-plan path; if Phase 4 needs one, it is new work, not something Phase 3
  already half-built.
