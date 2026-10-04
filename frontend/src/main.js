import "./styles/main.css";

import { ApiError } from "./api/errors.js";
import { startAlerts } from "./realtime/alerts.js";
import { realtime } from "./realtime/live.js";
import { createRouter, safeRedirect } from "./router.js";
import { session } from "./state/auth.js";
import { checkHubNow, hub, startHubMonitor } from "./state/hub.js";
import { $, h } from "./ui/dom.js";
import { enter, exit } from "./ui/motion.js";
import { dismissToast, toast } from "./ui/toast.js";
import { mountBoot } from "./views/boot.js";
import { createShell } from "./views/shell.js";

/** If the hub answers within this time, the boot screen never appears. */
const BOOT_DELAY_MS = 400;
/** Time to read "Hub online" before the boot screen gives way to the dashboard. */
const BOOT_LINGER_MS = 700;

const LOGIN = "/login";

/** @type {import("./router.js").Route[]} */
const routes = [
  { path: LOGIN, load: () => import("./views/login.js"), public: true, layout: "bare" },
  { path: "/", load: () => import("./views/live.js") },
  { path: "/events", load: () => import("./views/events.js") },
  { path: "/activity", load: () => import("./views/activity.js") },
  { path: "/devices/:id", load: () => import("./views/device.js") },
];

const app = $(document, "#app");
const bootScreen = $(app, "[data-boot-screen]");

startHubMonitor();
const stopBoot = mountBoot($(bootScreen, "[data-boot]"));
const reveal = window.setTimeout(() => bootScreen.removeAttribute("data-pending"), BOOT_DELAY_MS);

let started = false;
const stopWaiting = hub.subscribe((status) => {
  if (status.state === "online" && !started) {
    started = true;
    queueMicrotask(start);
  }
});

async function start() {
  stopWaiting();
  window.clearTimeout(reveal);
  if (!bootScreen.hasAttribute("data-pending")) {
    await new Promise((resolve) => setTimeout(resolve, BOOT_LINGER_MS));
    await exit(bootScreen, [{ opacity: 1 }, { opacity: 0, transform: "scale(0.98)" }], {
      duration: 240,
    });
  }
  stopBoot();
  await restoreSession();

  /** @type {ReturnType<typeof createShell> | null} */
  let shell = null;
  const bare = h("div", { class: "bare-layout" });
  const router = createRouter({
    routes,
    notFound: () => import("./views/not-found.js"),
    guard: guardRoute,
    outlet(route) {
      if (route?.layout === "bare") {
        if (!bare.isConnected) app.replaceChildren(bare);
        return bare;
      }
      shell ??= createShell();
      if (!shell.element.isConnected) {
        app.replaceChildren(shell.element);
        enter(shell.element, [{ opacity: 0 }, { opacity: 1 }], { duration: 400 });
      }
      return shell.outlet;
    },
    onChange: (pathname) => shell?.setActive(pathname),
    enter: (outlet) =>
      enter(outlet, [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1 }], {
        duration: 280,
      }),
  });
  await router.start();
  announceHubChanges();
  connectRealtime();
  startAlerts(realtime, { navigate: router.navigate });

  // Signing in or out (here or in another tab) re-runs the guard on the current page.
  let status = session.state.get().status;
  session.state.subscribe((next) => {
    if (next.status === status) return;
    status = next.status;
    router.refresh();
  });
}

/** Live events run while signed in. The hub monitor and the socket help each other: a dropped
 * socket triggers a health check, and a hub that is back skips the socket's backoff. */
function connectRealtime() {
  session.state.subscribe(({ status }) => {
    if (status === "signed-in") realtime.start();
    else realtime.stop();
  });
  let previous = realtime.state.get().status;
  realtime.state.subscribe(({ status }) => {
    if (previous === "live" && status === "reconnecting") checkHubNow();
    // Live again proves the hub is back: confirm now rather than at the next scheduled check.
    if (status === "live" && previous !== "live" && hub.get().state !== "online") checkHubNow();
    previous = status;
  });
  let hubState = hub.get().state;
  hub.subscribe(({ state }) => {
    // Only when the hub comes back: other updates (a check starting) must not skip the backoff.
    if (state === "online" && hubState !== "online") realtime.retryNow();
    hubState = state;
  });
}

/**
 * Signed out: every page except sign-in sends you there, remembering where you were going.
 * Signed in: the sign-in page sends you on.
 * @param {import("./router.js").Route | null} route
 * @param {URL} url
 */
function guardRoute(route, url) {
  const signedIn = session.state.get().status === "signed-in";
  if (route?.public) {
    return signedIn && url.pathname === LOGIN
      ? safeRedirect(url.searchParams.get("next"), location.origin, [LOGIN])
      : null;
  }
  if (signedIn) return null;
  const here = url.pathname + url.search;
  return here === "/" ? LOGIN : `${LOGIN}?next=${encodeURIComponent(here)}`;
}

/** Resumes a stored session. If the hub drops out meanwhile, tries again once it is back; if
 * it answers with an error (rate limit, server error), waits as told and tries again. */
async function restoreSession() {
  for (;;) {
    try {
      await session.restore();
      return;
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      if (error.kind === "http") {
        const seconds = error.retryAfter ?? 5;
        await new Promise((resolve) => setTimeout(resolve, seconds * 1000));
      } else {
        await nextOnline();
      }
    }
  }
}

/** Resolves the next time the hub monitor reports the hub online after being away. */
function nextOnline() {
  return new Promise((resolve) => {
    let away = false;
    const stop = hub.subscribe(({ state }) => {
      if (state !== "online") away = true;
      else if (away) {
        queueMicrotask(stop);
        resolve(undefined);
      }
    });
    checkHubNow();
  });
}

/** After startup, losing the hub is a toast, not a full-screen takeover. */
function announceHubChanges() {
  /** @type {string | null} */
  let notified = null;
  let lost = false;
  hub.subscribe(({ state, title, checking }) => {
    if (state === "online") {
      if (lost) {
        lost = false;
        notified = null;
        dismissToast("hub");
        toast({ tone: "signal", title: "Connected to the hub again", duration: 3000 });
      }
      return;
    }
    if (state === "checking" || checking) return;
    lost = true;
    // Once per state: a toast someone closed stays closed until something changes.
    if (notified === state) return;
    notified = state;
    toast({
      key: "hub",
      tone: state === "offline" ? "alarm" : "sodium",
      title,
      message: "Live view and alerts pause until it's back. Retrying in the background.",
      duration: 0,
      action: {
        label: "Retry now",
        run: () => {
          notified = null;
          checkHubNow();
        },
      },
    });
  });
}
