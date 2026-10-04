import "./styles/main.css";

import { ApiError } from "./api/errors.js";
import { createRouter } from "./router.js";
import { session } from "./state/auth.js";
import { checkHubNow, hub, startHubMonitor } from "./state/hub.js";
import { $ } from "./ui/dom.js";
import { enter, exit } from "./ui/motion.js";
import { dismissToast, toast } from "./ui/toast.js";
import { mountBoot } from "./views/boot.js";
import { createShell } from "./views/shell.js";

/** If the hub answers within this time, the boot screen never appears. */
const BOOT_DELAY_MS = 400;
/** Time to read "Hub online" before the boot screen gives way to the dashboard. */
const BOOT_LINGER_MS = 700;

/** @type {import("./router.js").Route[]} */
const routes = [
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

  const shell = createShell();
  app.replaceChildren(shell.element);
  const router = createRouter({
    routes,
    notFound: () => import("./views/not-found.js"),
    outlet: () => shell.outlet,
    onChange: shell.setActive,
    enter: (outlet) =>
      enter(outlet, [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1 }], {
        duration: 280,
      }),
  });
  await router.start();
  enter(shell.element, [{ opacity: 0 }, { opacity: 1 }], { duration: 400 });
  announceHubChanges();
  announceSessionEnd();
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

/** Until the sign-in page exists (roadmap 4.3), say when a session ends. */
function announceSessionEnd() {
  let signedIn = session.state.get().status === "signed-in";
  session.state.subscribe(({ status, reason }) => {
    if (status === "signed-in") signedIn = true;
    if (status !== "signed-out" || !signedIn) return;
    signedIn = false;
    toast(
      reason === "expired"
        ? { tone: "sodium", title: "Session expired", message: "Sign in again to continue." }
        : { tone: "iris", title: "Signed out" },
    );
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
