// Connection indicator. While live events run (signed in), it shows their connection; otherwise,
// and whenever the hub itself is unwell, it shows the hub monitor's verdict.

import { realtime } from "../realtime/live.js";
import { hub } from "../state/hub.js";
import { h } from "./dom.js";
import { enter } from "./motion.js";

/** @typedef {import("../state/hub.js").HubStatus} HubStatus */
/** @typedef {import("../realtime/socket.js").RealtimeState} RealtimeState */
/**
 * @typedef {{
 *   key: string,
 *   tone: string,
 *   label: string,
 *   named: (subject: string) => string,
 *   pulse?: boolean,
 *   title: string,
 * }} Look
 */

/**
 * @param {HubStatus} status
 * @returns {Look}
 */
function hubLook(status) {
  const title = `${status.title}. ${status.detail}${status.command ? ` ${status.command}` : ""}`;
  switch (status.state) {
    case "checking":
      return {
        key: "hub:checking",
        tone: "iris",
        label: "Connecting",
        named: (subject) => `Connecting to ${subject.toLowerCase()}`,
        title,
      };
    case "online":
      return {
        key: "hub:online",
        tone: "signal",
        label: "Live",
        named: (subject) => `${subject} online`,
        pulse: true,
        title,
      };
    case "degraded":
      return {
        key: "hub:degraded",
        tone: "sodium",
        label: "Not ready",
        named: (subject) => `${subject} not ready`,
        title,
      };
    case "offline":
      return {
        key: "hub:offline",
        tone: "alarm",
        label: "Offline",
        named: (subject) => `${subject} offline`,
        title,
      };
  }
}

/**
 * @param {HubStatus} status
 * @param {RealtimeState} live
 * @returns {Look}
 */
function look(status, live) {
  if (status.state !== "online" || live.status === "stopped") return hubLook(status);
  switch (live.status) {
    case "live":
      return {
        key: "live",
        tone: "signal",
        label: "Live",
        named: () => "Live",
        pulse: true,
        title: "Live events connected.",
      };
    case "connecting":
    case "reconnecting":
      return {
        key: "reconnecting",
        tone: "iris",
        label: live.status === "connecting" ? "Connecting" : "Reconnecting",
        named: () => "Reconnecting",
        title: "Live events dropped. Reconnecting.",
      };
    case "offline":
      return {
        key: "offline",
        tone: "alarm",
        label: "Offline",
        named: () => "Offline",
        title: "This device is offline. Live events resume when it's back.",
      };
  }
}

/**
 * @param {{ subject?: string }} [options] `subject` names the thing ("Hub online"); without it
 *   the short labels are used where the context makes them clear (the sidebar's "Hub" row).
 */
export function connectionPill({ subject } = {}) {
  const light = h("span", { class: "dot", attrs: { "aria-hidden": "true" } });
  const label = h("span");
  const element = h("span", { class: "badge", attrs: { role: "status" } }, light, label);
  /** @type {string | undefined} */
  let shown;

  function render() {
    const current = look(hub.get(), realtime.state.get());
    element.title = current.title;
    if (shown === current.key) return;
    const changed = shown !== undefined;
    shown = current.key;
    element.dataset.tone = current.tone;
    light.toggleAttribute("data-pulse", Boolean(current.pulse));
    label.textContent = subject ? current.named(subject) : current.label;
    element.setAttribute("aria-label", current.title);
    if (changed) enter(label, [{ opacity: 0, transform: "translateY(4px)" }, { opacity: 1 }]);
  }

  const stops = [hub.subscribe(render), realtime.state.subscribe(render)];
  return { element, destroy: () => stops.forEach((stop) => stop()) };
}
