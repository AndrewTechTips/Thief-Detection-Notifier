// The dashboard's session, wired to the browser and to the API client.

import { api } from "../api/client.js";
import { Session } from "./session.js";

/** @param {() => Storage} get */
function storage(get) {
  try {
    return get();
  } catch {
    return null; // storage blocked: the session lives in memory only
  }
}

export const session = new Session({
  client: api,
  local: storage(() => localStorage),
  ephemeral: storage(() => sessionStorage),
  channel: "BroadcastChannel" in globalThis ? new BroadcastChannel("vision-hub.session") : null,
  locks: navigator.locks ?? null,
  visibility: document,
});

api.setAuth(session.hooks);
