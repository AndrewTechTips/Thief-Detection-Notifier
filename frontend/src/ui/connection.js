// Connection indicator: a status pill that follows the hub monitor.

import { hub } from "../state/hub.js";
import { h } from "./dom.js";
import { enter } from "./motion.js";

/** @typedef {import("../state/hub.js").HubStatus} HubStatus */

/** @type {Record<HubStatus["state"], { tone: string, label: string, pulse?: boolean }>} */
const LOOK = {
  checking: { tone: "iris", label: "Connecting" },
  online: { tone: "signal", label: "Live", pulse: true },
  degraded: { tone: "sodium", label: "Not ready" },
  offline: { tone: "alarm", label: "Offline" },
};

export function connectionPill() {
  const light = h("span", { class: "dot", attrs: { "aria-hidden": "true" } });
  const label = h("span");
  const element = h("span", { class: "badge", attrs: { role: "status" } }, light, label);
  /** @type {string | undefined} */
  let shown;

  const unsubscribe = hub.subscribe((status) => {
    const look = LOOK[status.state];
    element.title = `${status.title}. ${status.detail}${status.command ? ` ${status.command}` : ""}`;
    if (shown === status.state) return;
    const changed = shown !== undefined;
    shown = status.state;
    element.dataset.tone = look.tone;
    light.toggleAttribute("data-pulse", Boolean(look.pulse));
    label.textContent = look.label;
    element.setAttribute("aria-label", `Hub: ${status.title}`);
    if (changed) enter(label, [{ opacity: 0, transform: "translateY(4px)" }, { opacity: 1 }]);
  });

  return { element, destroy: unsubscribe };
}
