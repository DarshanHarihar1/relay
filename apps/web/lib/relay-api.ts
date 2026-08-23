import { getFirebaseAuth } from "./firebase";

const relayApiUrl = process.env.NEXT_PUBLIC_RELAY_API_URL ?? "";

export type HandoffResponse = {
  action_id: string;
  state: "handoff_opened";
  url: string;
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
