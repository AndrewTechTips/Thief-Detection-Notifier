// A camera in the live grid: its live view, a link to the camera page and when it last saw
// motion.

import { href } from "../paths.js";
import { liveView } from "./live-view.js";
import { h } from "./dom.js";
import { timeAgo } from "./time.js";

/** @typedef {import("../api/types.js").Device} Device */

/** Grid tiles are small: a lower frame rate saves bandwidth on phones and work on the hub. */
const GRID_FPS = 8;

/**
 * @param {Device} initial
 */
export function cameraTile(initial) {
  let device = initial;
  let motion = false;
  /** @type {string | null} */
  let lastMotionAt = null;

  const name = h("a", {
    class: "camera-link",
    attrs: { href: href(`/devices/${encodeURIComponent(device.id)}`) },
  });
  const detail = h("p", { class: "text-sm text-haze" });
  const element = h("article", { class: "camera-tile card" });
  const view = liveView({ device, host: element, fps: GRID_FPS });
  element.append(
    view.element,
    h("div", { class: "camera-meta" }, h("h2", { class: "font-medium" }, name), detail),
  );

  function renderDetail() {
    detail.textContent = motion
      ? "Motion now"
      : lastMotionAt
        ? `Motion ${timeAgo(lastMotionAt)}`
        : "No motion yet";
  }

  function fromDevice() {
    const last = device.last_event;
    motion = Boolean(last && !last.ended_at);
    lastMotionAt = last ? (last.ended_at ?? last.started_at) : null;
    name.textContent = device.name;
    view.setMotion(motion);
    renderDetail();
  }

  fromDevice();

  return {
    element,
    get id() {
      return device.id;
    },
    /** @param {Device} next */
    update(next) {
      device = next;
      fromDevice();
      view.update(next);
    },
    /** @param {Device["status"]} status */
    setStatus(status) {
      device = { ...device, status };
      view.setStatus(status);
    },
    /**
     * @param {boolean} active
     * @param {string} at when it started (active) or ended
     */
    setMotion(active, at) {
      motion = active;
      lastMotionAt = at;
      view.setMotion(active);
      renderDetail();
    },
    /** @param {boolean} onScreen */
    setVisible(onScreen) {
      view.setVisible(onScreen);
    },
    sync: view.sync,
    /** Refreshes "Motion 4 minutes ago". */
    tick: renderDetail,
    destroy: view.destroy,
  };
}
