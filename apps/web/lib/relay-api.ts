import { getFirebaseAuth } from "./firebase";
import {
  approvalDecisionResponseSchema,
  dashboardViewSchema,
  pickupContactCommandSchema,
  pickupContactResponseSchema,
  pickerSessionViewSchema,
  type ApprovalDecisionRequest,
  type ApprovalDecisionResponse as GeneratedApprovalDecisionResponse,
  type DashboardView,
  type PickupContactCommand,
  type PickupContactResponse,
  type PickerSessionView,
} from "../../../packages/contracts/src";

const relayApiUrl = process.env.NEXT_PUBLIC_RELAY_API_URL ?? "";

export type HandoffResponse = {
  action_id: string;
  state: "handoff_opened";
  url: string;
};

export type ApprovalDecision = ApprovalDecisionRequest["decision"];

export type ApprovalDecisionResponse = GeneratedApprovalDecisionResponse;

export type RelayActionState =
  | "planned"
  | "awaiting_approval"
  | "authorized"
  | "dispatched"
  | "in_progress"
  | "succeeded"
  | "needs_user"
  | "retryable_failure"
  | "failed"
  | "verified"
  | "handoff_opened";

export type RelayAction = {
  id: string;
  type: "voice_call" | "calendar_hold" | "uber_deep_link";
  state: RelayActionState;
  target_ref: string;
  authorization_snapshot: Record<string, unknown>;
  retry_count: number;
};

function correlationId(): string {
  return globalThis.crypto.randomUUID();
}

export async function relayFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const user = getFirebaseAuth().currentUser;
  if (user === null) {
    throw new Error("Sign in is required");
  }

  const token = await user.getIdToken();
  const requestHeaders = new Headers(init.headers);
  const contentType = requestHeaders.get("Content-Type") ?? "application/json";
  const response = await fetch(`${relayApiUrl}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": contentType,
      "X-Correlation-ID": correlationId(),
    },
  });

  if (!response.ok) {
    throw new Error(`Relay API request failed with status ${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export async function openUberHandoff(actionId: string): Promise<HandoffResponse> {
  return relayFetch<HandoffResponse>("/v1/actions/" + encodeURIComponent(actionId) + "/open-handoff", {
    method: "POST",
  });
}

export async function decideApproval(
  approvalId: string,
  decision: ApprovalDecision,
  expectedVersion: number,
): Promise<ApprovalDecisionResponse> {
  const response = await relayFetch<unknown>(
    "/v1/approvals/" + encodeURIComponent(approvalId) + "/decision",
    {
      method: "POST",
      body: JSON.stringify({
        approval_id: approvalId,
        decision,
        expected_version: expectedVersion,
      }),
    },
  );
  return approvalDecisionResponseSchema.parse(response);
}

export async function getDashboard(): Promise<DashboardView> {
  return dashboardViewSchema.parse(await relayFetch<unknown>("/v1/dashboard"));
}

export async function submitPickup(
  commitmentId: string,
  command: PickupContactCommand,
): Promise<PickupContactResponse> {
  const validCommand = pickupContactCommandSchema.parse(command);
  return pickupContactResponseSchema.parse(
    await relayFetch<unknown>("/v1/commitments/" + encodeURIComponent(commitmentId) + "/pickup-contact", {
      method: "POST",
      body: JSON.stringify(validCommand),
    }),
  );
}

export async function searchPickerContacts(query: string): Promise<PickerSessionView> {
  return pickerSessionViewSchema.parse(
    await relayFetch<unknown>("/v1/google/contact-picker?query=" + encodeURIComponent(query)),
  );
}

export async function getAction(actionId: string): Promise<RelayAction> {
  return relayFetch<RelayAction>("/v1/actions/" + encodeURIComponent(actionId));
}
