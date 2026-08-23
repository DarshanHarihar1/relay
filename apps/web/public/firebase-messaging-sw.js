/* Relay notifications are intentionally generic; private details stay in the signed-in dashboard. */
importScripts("https://www.gstatic.com/firebasejs/11.10.0/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/11.10.0/firebase-messaging-compat.js");

self.addEventListener("push", (event) => {
  const data = event.data?.json?.() ?? {};
  const message = data.data ?? data;
  const kind = message.kind === "approval_needed" ? "Relay needs your attention" : "Relay has an update";
  const entityId = typeof message.entity_id === "string" ? message.entity_id : "dashboard";
  event.waitUntil(
    self.registration.showNotification(kind, {
      body: "Open Relay to review your current plan.",
      data: { url: `/?notification=${encodeURIComponent(entityId)}` },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url ?? "/";
  event.waitUntil(self.clients.openWindow(url));
});
