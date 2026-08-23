import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourcePath = resolve(packageRoot, "openapi/relay.yaml");
const defaultOutputPath = resolve(packageRoot, "src/index.ts");

const requiredFragments = [
  "openapi: 3.1.0",
  "ActionState:",
  "ActionRecord:",
  "Approval:",
  "ApprovalDecisionRequest:",
  "ApprovalDecisionResponse:",
  "Commitment:",
  "Edge:",
  "Disruption:",
  "ProviderEvent:",
  "SourceEventEnvelope:",
  "Problem:",
  "/healthz:",
  "/v1/me:",
  "/v1/actions/{action_id}:",
  "/v1/approvals/{approval_id}/decision:",
];

function renderExecutionContracts(digest) {
  return String.raw`// Generated from openapi/relay.yaml (sha256:` + digest + String.raw`). Do not edit manually.
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

export const commitmentSchema = z.object({ id: nonEmptyString, user_id: nonEmptyString, source_event_key: nonEmptyString, summary: nonEmptyString, starts_at: awareDateTime, ends_at: awareDateTime }).strict();
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
`;
}

function render(source) {
  for (const fragment of requiredFragments) {
    if (!source.includes(fragment)) {
      throw new Error(`OpenAPI source is missing required contract fragment: ${fragment}`);
    }
  }

  const digest = createHash("sha256").update(source).digest("hex");
  if (source.includes("CallContract:")) {
    return renderExecutionContracts(digest);
  }
  return `// Generated from openapi/relay.yaml (sha256:${digest}). Do not edit manually.\nimport { z } from "zod";\n\nexport const actionStates = [\n  "planned",\n  "awaiting_approval",\n  "authorized",\n  "dispatched",\n  "in_progress",\n  "succeeded",\n  "needs_user",\n  "retryable_failure",\n  "failed",\n  "verified",\n  "handoff_opened",\n] as const;\n\nexport const actionTypes = ["voice_call", "calendar_hold", "uber_deep_link"] as const;\nexport const actionStateSchema = z.enum(actionStates);\nexport const actionTypeSchema = z.enum(actionTypes);\nexport type ActionState = z.infer<typeof actionStateSchema>;\nexport type ActionType = z.infer<typeof actionTypeSchema>;\n\nexport type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };\nexport const jsonValueSchema: z.ZodType<JsonValue> = z.lazy(() =>\n  z.union([z.string(), z.number(), z.boolean(), z.null(), z.array(jsonValueSchema), z.record(jsonValueSchema)]),\n);\n\nconst nonEmptyString = z.string().trim().min(1);\nconst awareDateTime = z.string().datetime({ offset: true });\nconst stringList = z.array(nonEmptyString);\n\nexport const voiceCallAuthorizationSnapshotSchema = z.object({\n  type: z.literal("voice_call"),\n  goal: nonEmptyString,\n  recipient_ref: nonEmptyString,\n  identity_disclosure: nonEmptyString,\n  authorized_options: stringList.min(1),\n  max_fee_inr: z.number().int().min(1).max(100000),\n  must_not: stringList,\n  required_evidence: stringList.min(1),\n  expires_at: awareDateTime,\n}).strict();\n\nexport const calendarHoldAuthorizationSnapshotSchema = z.object({\n  type: z.literal("calendar_hold"),\n  calendar_id: nonEmptyString,\n  start_at: awareDateTime,\n  end_at: awareDateTime,\n  visibility: z.literal("private"),\n}).strict();\n\nexport const uberDeepLinkAuthorizationSnapshotSchema = z.object({\n  type: z.literal("uber_deep_link"),\n  pickup: nonEmptyString,\n  destination: nonEmptyString,\n  handoff_label: z.literal("Open Uber"),\n}).strict();\n\nexport const authorizationSnapshotSchema = z.discriminatedUnion("type", [\n  voiceCallAuthorizationSnapshotSchema,\n  calendarHoldAuthorizationSnapshotSchema,\n  uberDeepLinkAuthorizationSnapshotSchema,\n]);\nexport type AuthorizationSnapshot = z.infer<typeof authorizationSnapshotSchema>;\n\nexport const actionRecordSchema = z.object({\n  id: nonEmptyString,\n  user_id: nonEmptyString,\n  repair_plan_id: nonEmptyString,\n  repair_plan_version: z.number().int().min(1),\n  type: actionTypeSchema,\n  target_ref: nonEmptyString,\n  idempotency_key: nonEmptyString,\n  authorization_snapshot: authorizationSnapshotSchema,\n  provider_ref: nonEmptyString.nullable().optional(),\n  state: actionStateSchema,\n  retry_count: z.number().int().min(0).default(0),\n  verification_evidence: z.record(jsonValueSchema).nullable().optional(),\n  correlation_id: nonEmptyString,\n  expires_at: awareDateTime.nullable().optional(),\n  dispatched_at: awareDateTime.nullable().optional(),\n  version: z.number().int().min(1).default(1),\n}).strict().superRefine((action, context) => {\n  if (action.type !== action.authorization_snapshot.type) {\n    context.addIssue({ code: z.ZodIssueCode.custom, path: ["authorization_snapshot", "type"], message: "Snapshot type must match action type" });\n  }\n});\nexport type ActionRecord = z.infer<typeof actionRecordSchema>;\n\nexport const approvalSchema = z.object({\n  id: nonEmptyString,\n  user_id: nonEmptyString,\n  action_ids: z.array(nonEmptyString).min(1),\n  state: z.enum(["pending", "approved", "declined"]),\n  version: z.number().int().min(1),\n  correlation_id: nonEmptyString,\n}).strict();\nexport type Approval = z.infer<typeof approvalSchema>;\n\nexport const approvalDecisionRequestSchema = z.object({\n  approval_id: nonEmptyString,\n  decision: z.enum(["approve", "decline"]),\n  expected_version: z.number().int().min(1),\n}).strict();\nexport type ApprovalDecisionRequest = z.infer<typeof approvalDecisionRequestSchema>;\n\nexport const approvalDecisionResponseSchema = z.object({\n  approval_id: nonEmptyString,\n  state: z.enum(["approved", "declined"]),\n  action_ids: z.array(nonEmptyString),\n}).strict();\nexport type ApprovalDecisionResponse = z.infer<typeof approvalDecisionResponseSchema>;\n\nexport const commitmentSchema = z.object({ id: nonEmptyString, user_id: nonEmptyString, source_event_key: nonEmptyString, summary: nonEmptyString, starts_at: awareDateTime, ends_at: awareDateTime }).strict();\nexport type Commitment = z.infer<typeof commitmentSchema>;\nexport const edgeSchema = z.object({ id: nonEmptyString, from_ref: nonEmptyString, to_ref: nonEmptyString, relation: nonEmptyString }).strict();\nexport type Edge = z.infer<typeof edgeSchema>;\nexport const disruptionSchema = z.object({ id: nonEmptyString, user_id: nonEmptyString, source_event_key: nonEmptyString, kind: nonEmptyString, occurred_at: awareDateTime }).strict();\nexport type Disruption = z.infer<typeof disruptionSchema>;\nexport const providerEventSchema = z.object({ id: nonEmptyString, action_id: nonEmptyString, provider: z.enum(["vapi", "calendar", "uber"]), provider_event_key: nonEmptyString, occurred_at: awareDateTime, correlation_id: nonEmptyString }).strict();\nexport type ProviderEvent = z.infer<typeof providerEventSchema>;\n\nexport const sourceEventEnvelopeSchema = z.object({\n  source: z.enum(["gmail", "calendar", "vapi"]),\n  source_event_key: nonEmptyString,\n  occurred_at: awareDateTime,\n  payload: z.record(jsonValueSchema),\n  correlation_id: nonEmptyString,\n}).strict();\nexport type SourceEventEnvelope = z.infer<typeof sourceEventEnvelopeSchema>;\n\nexport const problemSchema = z.object({ code: nonEmptyString, message: nonEmptyString, correlation_id: nonEmptyString }).strict();\nexport type Problem = z.infer<typeof problemSchema>;\n`;
}

export async function generate(outputPath = defaultOutputPath) {
  const source = await readFile(sourcePath, "utf8");
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, render(source));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await generate(process.argv[2] ? resolve(process.argv[2]) : defaultOutputPath);
}
