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

function render(source) {
  for (const fragment of requiredFragments) {
    if (!source.includes(fragment)) {
      throw new Error(`OpenAPI source is missing required contract fragment: ${fragment}`);
    }
  }

  const digest = createHash("sha256").update(source).digest("hex");
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
