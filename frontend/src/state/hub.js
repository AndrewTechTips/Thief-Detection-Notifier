// Hub reachability, polled from the readiness probe: slowly while online, with backoff while
// not. Paused in hidden tabs; checks at once when the tab returns or the network comes back.

import { checkHub } from "../api/health.js";
import { Store } from "./store.js";

/** @typedef {import("../api/health.js").HubReport} HubReport */
/** @typedef {HubReport & { checking: boolean, nextCheckAt: number | null }} HubStatus */

const RECHECK_ONLINE_MS = 10_000;
const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 15_000;

export const hub = new Store(
  /** @type {HubStatus} */ ({
    state: "checking",
    title: "Connecting to your hub",
    detail: "This takes a second.",
    checking: true,
    nextCheckAt: null,
  }),
);

let started = false;
let inFlight = false;
let failures = 0;
/** @type {number | undefined} */
let timer;

export function startHubMonitor() {
  if (started) return;
  started = true;
  window.addEventListener("online", checkHubNow);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      window.clearTimeout(timer);
      hub.update((status) => ({ ...status, nextCheckAt: null }));
    } else {
      checkHubNow();
    }
  });
  checkHubNow();
}

export async function checkHubNow() {
  if (inFlight) return;
  inFlight = true;
  window.clearTimeout(timer);
  hub.update((status) => ({ ...status, checking: true, nextCheckAt: null }));

  const report = await checkHub();
  inFlight = false;
  failures = report.state === "online" ? 0 : failures + 1;
  const delay = report.state === "online" ? RECHECK_ONLINE_MS : backoff(failures);
  // Hidden tabs don't poll; the visibilitychange handler checks again on return.
  const nextCheckAt = document.hidden ? null : Date.now() + delay;
  if (nextCheckAt) timer = window.setTimeout(checkHubNow, delay);
  hub.set({ ...report, checking: false, nextCheckAt });
}

/** Exponential backoff with ±20 % jitter, so many tabs don't retry in lockstep.
 * @param {number} failures */
export function backoff(failures) {
  const base = Math.min(RETRY_MAX_MS, RETRY_MIN_MS * 2 ** (failures - 1));
  return Math.round(base * (0.8 + Math.random() * 0.4));
}
