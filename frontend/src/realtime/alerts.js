// Motion alerts: one toast per camera, from "motion now" to "ended, with a snapshot".
// Cameras that alert on people only toast once the event is over, and only if a person was
// seen (the hub decides: `alert` on the ended event). Events replayed after a reconnect are
// summed up in a single toast instead of one each.
// In a background tab, the page title counts unseen alerts: "(2) Live – Vision Hub".

import { getEvent, snapshotUrl } from "../api/events.js";
import { alertsOn, deviceName } from "../state/devices.js";
import { formatClock, formatDuration } from "../ui/time.js";
import { dismissToast, toast } from "../ui/toast.js";

/** @typedef {import("../api/types.js").MessageOf<"motion.started">} MotionStarted */
/** @typedef {import("../api/types.js").MessageOf<"motion.ended">} MotionEnded */

/** How long an "ended" alert stays (in-progress alerts stay until the motion ends). */
const ENDED_MS = 9_000;
const SNAPSHOT_TIMEOUT_MS = 3_000;

/**
 * @param {import("./socket.js").Realtime} realtime
 * @param {{ navigate: (path: string) => void }} options
 * @returns {() => void} stop
 */
export function startAlerts(realtime, { navigate }) {
  /** @type {Map<string, string>} device id -> event id of its in-progress alert */
  const active = new Map();
  let missed = 0;
  const attention = titleCounter();

  const stop = realtime.listen((message) => {
    switch (message.type) {
      case "motion.started":
        // People-only cameras: nothing to say until the hub knows whether it was a person.
        if (alertsOn(message.device_id) === "person") return;
        if (message.replay) missed += 1;
        else started(message);
        return;
      case "motion.ended":
        if (!message.data.alert) {
          quiet(message);
          return;
        }
        // An alert still says "motion now" for this event: finish it, replayed or not.
        if (active.get(message.device_id) === message.data.event_id) ended(message);
        else if (message.replay) missed += 1;
        else ended(message);
        return;
      case "replay.done":
        if (missed) summarise(missed, message.data.truncated);
        missed = 0;
        return;
    }
  });

  /** @param {MotionStarted} message */
  function started(message) {
    const device = message.device_id;
    active.set(device, message.data.event_id);
    toast({
      key: `motion:${device}`,
      tone: "sodium",
      title: `Motion on ${deviceName(device)}`,
      message: `Started at ${formatClock(message.data.started_at)}.`,
      duration: 0, // until the motion ends
      action: { label: "Watch", run: () => navigate(`/devices/${encodeURIComponent(device)}`) },
    });
    attention.bump();
  }

  /** No alert after all (the camera's setting changed mid-event): take back "motion now".
   * @param {MotionEnded} message */
  function quiet(message) {
    if (active.get(message.device_id) !== message.data.event_id) return;
    active.delete(message.device_id);
    dismissToast(`motion:${message.device_id}`);
  }

  /** @param {MotionEnded} message */
  async function ended(message) {
    const device = message.device_id;
    const { event_id: id, started_at: start, ended_at: end } = message.data;
    if (active.get(device) === id) active.delete(device);
    const lasted = end ? (Date.parse(end) - Date.parse(start)) / 1000 : null;
    const media = await thumbnail(id);
    // A newer motion on the same camera owns the toast now: don't overwrite it.
    if (active.has(device)) return;
    toast({
      key: `motion:${device}`,
      tone: "sodium",
      title: message.data.person
        ? `Person at ${deviceName(device)}`
        : `Motion on ${deviceName(device)}`,
      message:
        lasted === null
          ? `At ${formatClock(start)}.`
          : `At ${formatClock(start)}, for ${formatDuration(lasted)}.`,
      duration: ENDED_MS,
      media,
      action: { label: "View", run: () => navigate(`/events?event=${encodeURIComponent(id)}`) },
    });
    if (!message.replay) attention.bump();
  }

  /**
   * @param {number} count
   * @param {boolean} truncated more were missed than the hub replayed
   */
  function summarise(count, truncated) {
    const events = truncated
      ? `${count}+ motion events`
      : `${count} motion ${count === 1 ? "event" : "events"}`;
    toast({
      key: "missed",
      tone: "iris",
      title: "While you were away",
      message: `${events} on your cameras.`,
      duration: 12_000,
      action: { label: "See events", run: () => navigate("/events") },
    });
  }

  return () => {
    stop();
    attention.reset();
  };
}

/**
 * The event's thumbnail link, or null if it takes too long (the alert must not wait).
 * @param {string} id
 */
async function thumbnail(id) {
  try {
    const event = await getEvent(id, { signal: AbortSignal.timeout(SNAPSHOT_TIMEOUT_MS) });
    return snapshotUrl(event, "thumbnail");
  } catch {
    return null;
  }
}

/** Counts alerts that arrive while the tab is hidden, in the page title. */
function titleCounter() {
  let unseen = 0;
  /** @type {string | null} */
  let base = null;

  function reset() {
    if (base !== null) document.title = base;
    base = null;
    unseen = 0;
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) reset();
  });

  return {
    bump() {
      if (!document.hidden) return;
      base ??= document.title;
      unseen += 1;
      document.title = `(${unseen}) ${base}`;
    },
    reset,
  };
}
