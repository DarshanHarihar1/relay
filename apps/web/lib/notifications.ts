"use client";

import { getFirebaseApp } from "./firebase";
import { registerDevice } from "./relay-api";

export type NotificationStatus = "enabled" | "denied" | "unsupported" | "unavailable";

export type NotificationDependencies = {
  requestPermission?: () => Promise<NotificationPermission>;
  getToken?: () => Promise<string | null>;
  registerDevice?: (token: string) => Promise<unknown>;
};

export type ForegroundNotification = {
  data?: Record<string, unknown>;
};

export type ForegroundHandlers = {
  invalidateDashboard: () => void;
  announce: (message: string) => void;
};

const notificationKinds = new Set(["approval_needed", "outcome_updated"]);

export async function initializeNotifications(
  dependencies: NotificationDependencies = {},
): Promise<NotificationStatus> {
  if (dependencies.requestPermission === undefined && typeof Notification === "undefined") {
    return "unsupported";
  }
  const requestPermission = dependencies.requestPermission ?? (() => Notification.requestPermission());
  let permission: NotificationPermission;
  try {
    permission = await requestPermission();
  } catch {
    return "unavailable";
  }
  if (permission !== "granted") {
    return permission === "denied" ? "denied" : "unavailable";
  }

  const getToken = dependencies.getToken ?? getFirebaseMessagingToken;
  const token = await getToken();
  if (token === null || token.length === 0) {
    return "unavailable";
  }
  await (dependencies.registerDevice ?? registerDevice)(token);
  return "enabled";
}

export function handleForegroundMessage(
  message: ForegroundNotification,
  handlers: ForegroundHandlers,
): void {
  const data = message.data;
  if (
    data === undefined
    || typeof data.kind !== "string"
    || !notificationKinds.has(data.kind)
    || typeof data.entity_id !== "string"
    || typeof data.correlation_id !== "string"
  ) {
    return;
  }
  handlers.invalidateDashboard();
  handlers.announce("Relay has an update.");
}

export async function subscribeToForegroundMessages(
  handlers: ForegroundHandlers,
): Promise<() => void> {
  try {
    const vapidKey = process.env.NEXT_PUBLIC_FIREBASE_VAPID_KEY;
    if (typeof window === "undefined" || vapidKey === undefined) {
      return () => undefined;
    }
    const { getMessaging, onMessage } = await import("firebase/messaging");
    const unsubscribe = onMessage(getMessaging(getFirebaseApp()), (message) => {
      handleForegroundMessage(message, handlers);
    });
    return unsubscribe;
  } catch {
    return () => undefined;
  }
}

async function getFirebaseMessagingToken(): Promise<string | null> {
  const vapidKey = process.env.NEXT_PUBLIC_FIREBASE_VAPID_KEY;
  if (typeof window === "undefined" || vapidKey === undefined) {
    return null;
  }
  try {
    const registration = await navigator.serviceWorker.register("/firebase-messaging-sw.js");
    const { getMessaging, getToken } = await import("firebase/messaging");
    return (await getToken(getMessaging(getFirebaseApp()), {
      vapidKey,
      serviceWorkerRegistration: registration,
    })) || null;
  } catch {
    return null;
  }
}
