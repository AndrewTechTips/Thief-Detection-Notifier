// The dashboard's realtime connection, wired to the browser. main.js starts it on sign-in and
// stops it on sign-out.

import { api } from "../api/client.js";
import { createRealtime } from "./socket.js";

export const realtime = createRealtime({
  client: api,
  url(ticket) {
    const url = new URL("/api/v1/ws/events", location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.searchParams.set("ticket", ticket);
    return url.href;
  },
});

// The browser knows about lost networks long before a socket times out.
window.addEventListener("offline", () => realtime.drop());
window.addEventListener("online", () => realtime.retryNow());
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) realtime.retryNow();
});
