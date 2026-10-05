// Vision Hub service worker. Built into dist/sw.js by vite.config.js, which fills in the
// version and the list of files to precache.
//
// What it caches: the app itself (the page shell and its hashed files), so the dashboard opens
// instantly and still starts when the hub is unreachable (it then says so).
// What it never touches: anything under /api/ (data, snapshots, live video, the WebSocket).
// Camera data must always be live, and private images must not linger in a cache.

/** @type {string} */
const VERSION = "__VERSION__";
/* global __PRECACHE__ */
/** @type {string[]} */
const PRECACHE = __PRECACHE__;

const CACHE = `vision-hub-${VERSION}`;
const SHELL = "/index.html";
/** A page load waits this long for the hub before falling back to the cached shell. */
const NAVIGATION_TIMEOUT_MS = 3000;
/** The hub's own pages and endpoints (see api/dashboard.py RESERVED): never the dashboard. */
const BYPASS = ["api", "docs", "redoc", "openapi.json", "metrics"];

const worker = /** @type {ServiceWorkerGlobalScope} */ (/** @type {unknown} */ (self));
/**
 * The hub's responses say `Vary: Origin` (CORS) and `Vary: Accept-Encoding`. Neither changes
 * these files, but honouring Vary would make a script requested with `crossorigin` (it sends an
 * Origin header) miss the copy the worker cached without one: offline, nothing would load.
 */
const MATCH = { ignoreVary: true };

worker.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll([SHELL, ...PRECACHE])));
  // No skipWaiting here: open pages may still need the old files. The page offers a reload
  // ("Update ready") and tells this worker to take over when the person chooses.
});

worker.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") worker.skipWaiting();
});

worker.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("vision-hub-") && key !== CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => worker.clients.claim()),
  );
});

worker.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== worker.location.origin) return;
  if (BYPASS.includes(url.pathname.split("/")[1])) return; // always the network, never cached

  if (request.mode === "navigate") {
    event.respondWith(page(request));
  } else if (url.pathname.startsWith("/assets/")) {
    event.respondWith(cacheFirst(request)); // hashed names: a cached copy is never stale
  } else {
    event.respondWith(staleWhileRevalidate(request));
  }
});

/** Pages: the hub's answer if it comes in time, else the cached shell (the app routes). */
async function page(/** @type {Request} */ request) {
  try {
    const response = await withTimeout(fetch(request), NAVIGATION_TIMEOUT_MS);
    if (response.ok) {
      const cache = await caches.open(CACHE);
      cache.put(SHELL, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(SHELL, MATCH);
    return cached ?? Response.error();
  }
}

async function cacheFirst(/** @type {Request} */ request) {
  const cached = await caches.match(request, MATCH);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) (await caches.open(CACHE)).put(request, response.clone());
  return response;
}

async function staleWhileRevalidate(/** @type {Request} */ request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request, MATCH);
  const network = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => cached ?? Response.error());
  return cached ?? network;
}

// ── Notifications ───────────────────────────────────────────
// The hub sends each alert encrypted for this browser; the browser decrypts it before `push`.

/**
 * @typedef {{
 *   kind: "motion",
 *   event_id: string,
 *   device_id: string,
 *   device_name: string,
 *   started_at: string,
 *   duration_seconds: number | null,
 *   image: string | null,
 * } | { kind: "test" }} PushAlert
 */

const ICON = "/icons/icon-192.png";
const BADGE = "/icons/badge-96.png"; // Android's status bar: a white mark on transparent

worker.addEventListener("push", (event) => {
  /** @type {PushAlert | null} */
  let alert = null;
  try {
    alert = event.data?.json() ?? null;
  } catch {
    // not ours: still show something (browsers require a notification for every push)
  }
  event.waitUntil(notify(alert));
});

/** @param {PushAlert | null} alert */
async function notify(alert) {
  const registration = worker.registration;
  if (alert?.kind === "test") {
    return registration.showNotification("Notifications are on", {
      body: "Motion alerts will appear like this.",
      icon: ICON,
      badge: BADGE,
      tag: "test",
    });
  }
  if (alert?.kind !== "motion") {
    return registration.showNotification("Vision Hub", {
      body: "Open to see what's new.",
      icon: ICON,
    });
  }
  // Someone looking at the dashboard already sees the alert there.
  const windows = await worker.clients.matchAll({ type: "window" });
  if (windows.some((client) => client.focused && client.visibilityState === "visible")) return;

  const lasted = alert.duration_seconds;
  return registration.showNotification(`Motion on ${alert.device_name}`, {
    body: lasted
      ? `At ${clock(alert.started_at)}, for ${duration(lasted)}.`
      : `At ${clock(alert.started_at)}.`,
    icon: ICON,
    badge: BADGE,
    // Shown large on Android and Windows; the link is signed, so no sign-in is needed.
    ...(alert.image && { image: new URL(alert.image, worker.location.origin).href }),
    tag: alert.event_id, // a repeat of the same alert replaces it quietly
    timestamp: Date.parse(alert.started_at),
    data: { url: `/events?event=${encodeURIComponent(alert.event_id)}` },
  });
}

worker.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path = event.notification.data?.url ?? "/";
  event.waitUntil(open(path));
});

/** Brings the dashboard forward on the event (in place if it's open), or opens it there.
 * @param {string} path */
async function open(path) {
  const windows = await worker.clients.matchAll({ type: "window", includeUncontrolled: true });
  const existing = windows.find((client) => new URL(client.url).origin === worker.location.origin);
  if (existing) {
    existing.postMessage({ type: "open", url: path });
    await existing.focus();
    return;
  }
  await worker.clients.openWindow(path);
}

// The page's formats (src/ui/time.js), repeated here: the worker is a single plain file.
const clockFormat = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

/** @param {string} when */
function clock(when) {
  return clockFormat.format(new Date(when));
}

/** @param {number} seconds */
function duration(seconds) {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return total % 60 ? `${minutes} min ${total % 60} s` : `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
}

/**
 * @template T
 * @param {Promise<T>} promise
 * @param {number} ms
 * @returns {Promise<T>}
 */
function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms)),
  ]);
}
