// Web push: motion alerts as system notifications, also while the dashboard is closed.
//
// - The browser subscribes with the hub's public key and gives the hub its subscription; the hub
//   encrypts each alert for this browser alone, and the service worker shows it (sw/).
// - One subscription per browser, under whoever turned it on. Signing out removes it, so a
//   shared computer stops getting alerts.
// - Browsers can drop a subscription (Safari does after unshown pushes), and a new hub key makes
//   old ones useless. Each start checks and quietly subscribes again where it can.

import { api } from "./api/client.js";
import { ApiError } from "./api/errors.js";
import { Store } from "./state/store.js";

/** @typedef {import("./api/types.js").PushConfig} PushConfig */
/** @typedef {import("./api/types.js").PushTest} PushTest */
/**
 * unsupported: this browser can't, or a dev build (no service worker)
 * install: iPhone and iPad allow push only for apps added to the Home Screen
 * hub-off: turned off on the hub
 * denied: the person blocked notifications for this site
 * @typedef {"unsupported" | "install" | "hub-off" | "denied" | "off" | "on"} PushState
 */

/** Whether this browser should have a subscription: survives the browser dropping it. */
const WANTED_KEY = "vision-hub.push";

export const push = new Store(
  /** @type {{ state: PushState, busy: boolean }} */ ({ state: "unsupported", busy: false }),
);

function supported() {
  return (
    import.meta.env.PROD &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** Safari on iPhone/iPad outside an installed app: push exists, but only after installing. */
function needsInstall() {
  const ios =
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const installed =
    matchMedia("(display-mode: standalone)").matches ||
    /** @type {{ standalone?: boolean }} */ (navigator).standalone === true;
  return ios && !installed;
}

/** @param {PushState} state */
function setState(state) {
  push.update((current) => ({ ...current, state }));
}

/**
 * Runs `task` with the switch marked busy.
 * @template T
 * @param {() => Promise<T>} task
 */
async function busy(task) {
  push.update((current) => ({ ...current, busy: true }));
  try {
    return await task();
  } finally {
    push.update((current) => ({ ...current, busy: false }));
  }
}

/** The hub's key, or null when push is turned off there. */
async function hubKey() {
  try {
    /** @type {PushConfig} */
    const config = await api.get("/push");
    return config.public_key;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

/**
 * Brings the switch up to date after signing in, and repairs the subscription: one the browser
 * dropped is made again (if the person wanted it), one for an old hub key is replaced, and the
 * hub hears about it again (it may have been subscribed under another account).
 */
export async function syncPush() {
  if (!supported()) return setState(needsInstall() ? "install" : "unsupported");
  await busy(async () => {
    const key = await hubKey();
    if (!key) return setState("hub-off");
    if (Notification.permission === "denied") return setState("denied");
    const registration = await navigator.serviceWorker.ready;
    let subscription = await registration.pushManager.getSubscription();
    if (subscription && !sameKey(subscription, key)) {
      await subscription.unsubscribe();
      subscription = null;
    }
    if (!subscription && wanted() && Notification.permission === "granted") {
      subscription = await subscribe(registration, key).catch(() => null);
    }
    if (!subscription) return setState("off");
    await save(subscription);
    setState("on");
  });
}

/** Asks for permission (call from a click: browsers require it) and subscribes. */
export async function enablePush() {
  return busy(async () => {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      setState(permission === "denied" ? "denied" : "off");
      return false;
    }
    const key = await hubKey();
    if (!key) {
      setState("hub-off");
      return false;
    }
    const registration = await navigator.serviceWorker.ready;
    const existing = await registration.pushManager.getSubscription();
    const subscription =
      existing && sameKey(existing, key) ? existing : await subscribe(registration, key);
    try {
      await save(subscription);
    } catch (error) {
      await subscription.unsubscribe();
      throw error;
    }
    setWanted(true);
    setState("on");
    return true;
  });
}

/** Unsubscribes this browser here and on the hub. Safe to call when there is nothing to undo. */
export async function disablePush() {
  if (!supported()) return;
  await busy(async () => {
    setWanted(false);
    const registration = await navigator.serviceWorker.getRegistration();
    const subscription = await registration?.pushManager.getSubscription();
    if (subscription) {
      // If the hub can't be told now, its next alert finds the subscription gone and drops it.
      await api
        .delete(`/push/subscriptions/${await subscriptionId(subscription.endpoint)}`)
        .catch(() => {});
      await subscription.unsubscribe();
    }
    if (push.get().state === "on") setState("off");
  });
}

/** A test notification to every browser of this account. */
export async function sendTestPush() {
  /** @type {PushTest} */
  const result = await api.post("/push/test");
  return result;
}

/**
 * @param {ServiceWorkerRegistration} registration
 * @param {string} key
 */
function subscribe(registration, key) {
  return registration.pushManager.subscribe({
    userVisibleOnly: true, // every push shows a notification (required by Chrome and Safari)
    applicationServerKey: base64UrlBytes(key),
  });
}

/** @param {PushSubscription} subscription */
async function save(subscription) {
  const { endpoint, keys } = subscription.toJSON();
  await api.post("/push/subscriptions", { json: { endpoint, keys } });
}

/**
 * @param {PushSubscription} subscription
 * @param {string} key
 */
function sameKey(subscription, key) {
  const current = subscription.options.applicationServerKey;
  if (!current) return false;
  const a = new Uint8Array(current);
  const b = base64UrlBytes(key);
  return a.length === b.length && a.every((byte, i) => byte === b[i]);
}

/** The hub's id for a subscription: SHA-256 of its endpoint, hex.
 * @param {string} endpoint */
export async function subscriptionId(endpoint) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** @param {string} text */
export function base64UrlBytes(text) {
  const base64 = text.replaceAll("-", "+").replaceAll("_", "/");
  return Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
}

function wanted() {
  try {
    return localStorage.getItem(WANTED_KEY) === "on";
  } catch {
    return false;
  }
}

/** @param {boolean} value */
function setWanted(value) {
  try {
    if (value) localStorage.setItem(WANTED_KEY, "on");
    else localStorage.removeItem(WANTED_KEY);
  } catch {
    // storage blocked: the browser's own subscription still says it all
  }
}
