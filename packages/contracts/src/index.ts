// Generated from openapi/relay.yaml (sha256:daa53dfbf39da2273abf2964cf270ddd116bb84780006c47c3cee5474f53847d). Do not edit manually.
import { z } from "zod";

export const actionStates = ["planned", "awaiting_approval", "authorized", "dispatched", "in_progress", "succeeded", "needs_user", "retryable_failure", "failed", "verified", "handoff_opened"] as const;
export const actionTypes = ["voice_call", "calendar_hold", "uber_deep_link"] as const;
export const callOutcomeKinds = ["confirmed", "declined", "no_answer", "voicemail", "transfer_requested", "contradiction", "unexpected_fee", "provider_error"] as const;
export const providerKinds = ["vapi", "twilio", "calendar", "uber"] as const;
export const actionStateSchema = z.enum(actionStates);
export const actionTypeSchema = z.enum(actionTypes);
export const callOutcomeKindSchema = z.enum(callOutcomeKinds);
export const providerKindSchema = z.enum(providerKinds);
export type ActionState = z.infer<typeof actionStateSchema>;
export type ActionType = z.infer<typeof actionTypeSchema>;
export type CallOutcomeKind = z.infer<typeof callOutcomeKindSchema>;
export type ProviderKind = z.infer<typeof providerKindSchema>;
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export const jsonValueSchema: z.ZodType<JsonValue> = z.lazy(() => z.union([z.string(), z.number(), z.boolean(), z.null(), z.array(jsonValueSchema), z.record(jsonValueSchema)]));
const nonEmptyString = z.string().trim().min(1);
const awareDateTime = z.string().datetime({ offset: true });
const stringList = z.array(nonEmptyString);

export const voiceCallAuthorizationSnapshotSchema = z.object({
  type: z.literal("voice_call"),
  goal: nonEmptyString,
  recipient_ref: nonEmptyString,
  identity_disclosure: nonEmptyString,
  authorized_options: stringList.min(1),
  max_fee_inr: z.literal(0),
  must_not: stringList,
  required_evidence: stringList.min(1),
  expires_at: awareDateTime,
}).strict();
export const calendarHoldAuthorizationSnapshotSchema = z.object({
  type: z.literal("calendar_hold"),
  calendar_id: nonEmptyString,
  start_at: awareDateTime,
  end_at: awareDateTime,
  visibility: z.literal("private"),
}).strict();
export const uberDeepLinkAuthorizationSnapshotSchema = z.object({
  type: z.literal("uber_deep_link"),
  pickup: nonEmptyString,
  destination: nonEmptyString,
  handoff_label: z.literal("Open Uber"),
}).strict();
export const authorizationSnapshotSchema = z.discriminatedUnion("type", [
  voiceCallAuthorizationSnapshotSchema,
  calendarHoldAuthorizationSnapshotSchema,
  uberDeepLinkAuthorizationSnapshotSchema,
]);
export type AuthorizationSnapshot = z.infer<typeof authorizationSnapshotSchema>;

export const callContractSchema = z.object({
  action_id: nonEmptyString,
  goal: nonEmptyString,
  recipient_ref: nonEmptyString,
  identity_disclosure: nonEmptyString,
  authorized_options: stringList.min(1).max(3),
  max_fee_inr: z.literal(0),
  must_not: stringList,
  required_evidence: stringList,
  expires_at: awareDateTime,
}).strict();
export type CallContract = z.infer<typeof callContractSchema>;
export const recordCallOutcomeInputSchema = z.object({
  action_id: nonEmptyString,
  outcome: callOutcomeKindSchema,
  venue: nonEmptyString.nullable().optional(),
  date: z.string().date().nullable().optional(),
  party_size: z.number().int().min(1).max(20).nullable().optional(),
  confirmed_time: z.string().time().nullable().optional(),
  fee_inr: z.number().min(0).nullable().optional(),
  requested_transfer: z.boolean().default(false),
  redacted_excerpt: z.string().max(280).nullable().optional(),
}).strict();
export type RecordCallOutcomeInput = z.infer<typeof recordCallOutcomeInputSchema>;
export const outcomeValidationSchema = z.object({
  state: actionStateSchema,
  reason: nonEmptyString,
  missing_evidence: stringList,
}).strict();
export type OutcomeValidation = z.infer<typeof outcomeValidationSchema>;
export const actionStatusResponseSchema = z.object({
  action_id: nonEmptyString,
  state: actionStateSchema,
  retry_count: z.number().int().min(0),
  verification_evidence: z.record(jsonValueSchema).nullable().optional(),
  correlation_id: nonEmptyString,
}).strict();
export type ActionStatusResponse = z.infer<typeof actionStatusResponseSchema>;
export const handoffResponseSchema = z.object({
  action_id: nonEmptyString,
  state: z.literal("handoff_opened"),
  url: nonEmptyString,
}).strict();
export type HandoffResponse = z.infer<typeof handoffResponseSchema>;

export const actionRecordSchema = z.object({
  id: nonEmptyString,
  user_id: nonEmptyString,
  repair_plan_id: nonEmptyString,
  repair_plan_version: z.number().int().min(1),
  type: actionTypeSchema,
  target_ref: nonEmptyString,
  idempotency_key: nonEmptyString,
  authorization_snapshot: authorizationSnapshotSchema,
  provider_ref: nonEmptyString.nullable().optional(),
  state: actionStateSchema,
  retry_count: z.number().int().min(0).default(0),
  verification_evidence: z.record(jsonValueSchema).nullable().optional(),
  correlation_id: nonEmptyString,
  expires_at: awareDateTime.nullable().optional(),
  dispatched_at: awareDateTime.nullable().optional(),
  version: z.number().int().min(1).default(1),
}).strict().superRefine((action, context) => {
  if (action.type !== action.authorization_snapshot.type) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ["authorization_snapshot", "type"], message: "Snapshot type must match action type" });
  }
});
export type ActionRecord = z.infer<typeof actionRecordSchema>;
export const dispatchClaimSchema = z.object({
  claimed: z.boolean(),
  action: actionRecordSchema.nullable().optional(),
  idempotency_key: nonEmptyString.nullable().optional(),
  reconciliation_required: z.boolean().default(false),
}).strict();
export type DispatchClaim = z.infer<typeof dispatchClaimSchema>;

export const approvalSchema = z.object({
  id: nonEmptyString,
  user_id: nonEmptyString,
  action_ids: z.array(nonEmptyString).min(1),
  state: z.enum(["awaiting_approval", "approved", "declined"]),
  version: z.number().int().min(1),
  correlation_id: nonEmptyString,
  expires_at: awareDateTime.nullable().optional(),
}).strict();
export type Approval = z.infer<typeof approvalSchema>;
export const approvalDecisionRequestSchema = z.object({
  approval_id: nonEmptyString,
  decision: z.enum(["approve", "decline"]),
  expected_version: z.number().int().min(1),
}).strict();
export type ApprovalDecisionRequest = z.infer<typeof approvalDecisionRequestSchema>;
export const approvalDecisionResponseSchema = z.object({
  approval_id: nonEmptyString,
  state: z.enum(["approved", "declined"]),
  action_ids: z.array(nonEmptyString),
}).strict();
export type ApprovalDecisionResponse = z.infer<typeof approvalDecisionResponseSchema>;
export const actionDispatchRecordSchema = z.object({
  id: nonEmptyString,
  user_id: nonEmptyString,
  action_id: nonEmptyString,
  status: z.enum(["pending", "claimed", "completed"]),
  correlation_id: nonEmptyString,
  attempts: z.number().int().min(0),
  provider_ref: nonEmptyString.nullable().optional(),
  created_at: awareDateTime,
  updated_at: awareDateTime,
  version: z.number().int().min(1),
}).strict();
export type ActionDispatchRecord = z.infer<typeof actionDispatchRecordSchema>;

export const commitmentSchema = z.object({ id: nonEmptyString, user_id: nonEmptyString, source_event_key: nonEmptyString, summary: nonEmptyString, starts_at: awareDateTime, ends_at: awareDateTime, pickup_selection: z.enum(["no_pickup", "selected"]).nullable().optional(), pickup_command_fingerprint: nonEmptyString.nullable().optional() }).strict();
export type Commitment = z.infer<typeof commitmentSchema>;
export const edgeSchema = z.object({ id: nonEmptyString, from_ref: nonEmptyString, to_ref: nonEmptyString, relation: nonEmptyString }).strict();
export type Edge = z.infer<typeof edgeSchema>;
export const disruptionSchema = z.object({ id: nonEmptyString, user_id: nonEmptyString, source_event_key: nonEmptyString, kind: nonEmptyString, occurred_at: awareDateTime }).strict();
export type Disruption = z.infer<typeof disruptionSchema>;
export const providerEventSchema = z.object({
  id: nonEmptyString,
  action_id: nonEmptyString,
  provider: providerKindSchema,
  provider_event_key: nonEmptyString,
  event_type: nonEmptyString.nullable().optional(),
  provider_ref: nonEmptyString.nullable().optional(),
  payload_hash: nonEmptyString.nullable().optional(),
  occurred_at: awareDateTime,
  correlation_id: nonEmptyString,
}).strict();
export type ProviderEvent = z.infer<typeof providerEventSchema>;
export const sourceEventEnvelopeSchema = z.object({
  source: z.enum(["gmail", "calendar", "vapi"]),
  source_event_key: nonEmptyString,
  occurred_at: awareDateTime,
  payload: z.record(jsonValueSchema),
  correlation_id: nonEmptyString,
}).strict();
export type SourceEventEnvelope = z.infer<typeof sourceEventEnvelopeSchema>;
export const problemSchema = z.object({ code: nonEmptyString, message: nonEmptyString, correlation_id: nonEmptyString }).strict();
export type Problem = z.infer<typeof problemSchema>;

export const timelineStatuses = ["changed", "at_risk", "repaired", "unresolved", "protected"] as const;
export const outcomeStatuses = ["verified", "in_progress", "retrying", "needs_user", "failed", "handoff"] as const;
export const timelineStatusSchema = z.enum(timelineStatuses);
export const outcomeStatusSchema = z.enum(outcomeStatuses);
export type TimelineStatus = z.infer<typeof timelineStatusSchema>;
export type OutcomeStatus = z.infer<typeof outcomeStatusSchema>;
const utcDateTime = awareDateTime.refine((value) => value.endsWith("Z") || value.endsWith("+00:00"), "UTC timestamp required");

export const planTimelineItemSchema = z.object({
  commitment_id: nonEmptyString,
  title: z.string().trim().min(1).max(140),
  starts_at: utcDateTime,
  ends_at: utcDateTime,
  status: timelineStatusSchema,
  explanation: z.string().trim().min(1).max(360),
  is_pickup_prompt: z.boolean().default(false),
}).strict().superRefine((item, context) => {
  if (new Date(item.ends_at).getTime() <= new Date(item.starts_at).getTime()) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ["ends_at"], message: "Timeline item must end after it starts" });
  }
});
export type PlanTimelineItem = z.infer<typeof planTimelineItemSchema>;

export const approvalActionSummarySchema = z.object({
  action_id: nonEmptyString,
  kind: actionTypeSchema,
  goal: z.string().trim().min(1).max(160),
  authorized_options: stringList,
  max_fee_inr: z.number().int().min(0),
  expires_at: utcDateTime.nullable(),
  disclosure: z.string().trim().max(240).nullable().optional(),
  must_not: stringList,
}).strict();
export type ApprovalActionSummary = z.infer<typeof approvalActionSummarySchema>;

export const approvalBatchViewSchema = z.object({
  approval_id: nonEmptyString,
  version: z.number().int().min(1),
  state: z.enum(["awaiting_approval", "approved", "declined", "expired", "blocked"]),
  expires_at: utcDateTime,
  reason: z.string().trim().min(1).max(360),
  actions: z.array(approvalActionSummarySchema),
}).strict();
export type ApprovalBatchView = z.infer<typeof approvalBatchViewSchema>;

export const actionOutcomeViewSchema = z.object({
  action_id: nonEmptyString,
  kind: actionTypeSchema,
  status: outcomeStatusSchema,
  summary: z.string().trim().min(1).max(360),
  occurred_at: utcDateTime,
  evidence_label: z.string().trim().max(180).nullable().optional(),
  retry_at: utcDateTime.nullable().optional(),
  handoff_url: z.string().url().nullable().optional(),
}).strict();
export type ActionOutcomeView = z.infer<typeof actionOutcomeViewSchema>;

export const auditEventViewSchema = z.object({
  occurred_at: utcDateTime,
  event_code: nonEmptyString,
  summary: z.string().trim().min(1).max(360),
}).strict();
export type AuditEventView = z.infer<typeof auditEventViewSchema>;
export const actionAuditViewSchema = z.object({
  outcome: actionOutcomeViewSchema,
  events: z.array(auditEventViewSchema),
}).strict();
export type ActionAuditView = z.infer<typeof actionAuditViewSchema>;

export const pickupContactCommandSchema = z.object({
  selection: z.enum(["no_pickup", "google_picker", "manual"]),
  picker_session_id: nonEmptyString.nullable().optional(),
  picker_contact_index: z.number().int().min(0).max(19).nullable().optional(),
  manual_display_name: z.string().trim().min(1).max(200).nullable().optional(),
  manual_phone_number: z.string().trim().min(7).max(32).nullable().optional(),
  expected_version: z.number().int().min(1),
}).strict().superRefine((command, context) => {
  const picker = command.picker_session_id !== undefined || command.picker_contact_index !== undefined;
  const manual = command.manual_display_name !== undefined || command.manual_phone_number !== undefined;
  if (command.selection === "no_pickup" && (picker || manual)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "no_pickup cannot include contact fields" });
  }
  if (command.selection === "google_picker" && (!command.picker_session_id || command.picker_contact_index === undefined || manual)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "google_picker requires a current picker result" });
  }
  if (command.selection === "manual" && (!command.manual_display_name || !command.manual_phone_number || picker)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "manual selection requires a name and phone number" });
  }
});
export type PickupContactCommand = z.infer<typeof pickupContactCommandSchema>;

export const pickupContactResponseSchema = z.object({
  commitment_id: nonEmptyString,
  version: z.number().int().min(1),
  selection: z.enum(["no_pickup", "selected"]),
  display_name: z.string().nullable().optional(),
}).strict();
export type PickupContactResponse = z.infer<typeof pickupContactResponseSchema>;

export const dashboardViewSchema = z.object({
  repair_plan_id: nonEmptyString,
  repair_plan_version: z.number().int().min(1),
  generated_at: utcDateTime,
  timeline: z.array(planTimelineItemSchema),
  approval: approvalBatchViewSchema.nullable(),
  outcomes: z.array(actionOutcomeViewSchema),
  last_event_id: nonEmptyString.nullable().optional(),
}).strict();
export type DashboardView = z.infer<typeof dashboardViewSchema>;

export const registerDeviceRequestSchema = z.object({
  token: z.string().min(32).max(4096),
  platform: z.literal("web").default("web"),
}).strict();
export type RegisterDeviceRequest = z.infer<typeof registerDeviceRequestSchema>;
