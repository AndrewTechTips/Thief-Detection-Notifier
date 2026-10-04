// A camera in the live grid: its video, its status and whether something is moving in front
// of it. The stream only runs while the camera is online, the tile is on screen and the tab is
// visible; everything else costs the hub nothing.

import { streamUrl } from "../api/devices.js";
import { createPlayer } from "../realtime/mjpeg.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { timeAgo } from "./time.js";

/** @typedef {import("../api/types.js").Device} Device */
/** @typedef {import("../realtime/mjpeg.js").PlayerState} PlayerState */

/** Grid tiles are small: a lower frame rate saves bandwidth on phones and work on the hub. */
const GRID_FPS = 8;

/**
 * What a tile says about its camera.
 * @type {Record<Device["status"], { tone?: string, label: string, note: string | null, busy?: boolean }>}
 */
const STATUS = {
  online: { tone: "signal", label: "Live", note: null },
  starting: { tone: "iris", label: "Starting", note: "Opening the camera…", busy: true },
  reconnecting: {
    tone: "sodium",
    label: "Reconnecting",
    note: "Lost the camera's signal. Retrying.",
    busy: true,
  },
  failed: {
    tone: "alarm",
    label: "Failed",
    note: "The hub gave up on this camera. Check its source.",
  },
  stopped: { label: "Stopped", note: "This camera is stopped." },
};

/**
 * @param {Device} initial
 */
export function cameraTile(initial) {
  let device = initial;
  let visible = false;
  let motion = false;
  /** @type {string | null} */
  let lastMotionAt = null;
  let hasFrame = false;
  /** @type {PlayerState} */
  let playerState = "idle";
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let restart;

  const canvas = h("canvas", { class: "camera-canvas", attrs: { role: "img" } });
  const placeholder = h("div", { class: "camera-placeholder" });
  const chipLight = h("span", { class: "dot", attrs: { "aria-hidden": "true" } });
  const chipLabel = h("span");
  const chip = h("span", { class: "camera-chip panel-solid" }, chipLight, chipLabel);
  const motionChip = h(
    "span",
    { class: "camera-chip camera-motion panel-solid", attrs: { "data-tone": "sodium" } },
    h("span", { class: "dot", attrs: { "aria-hidden": "true", "data-pulse": "" } }),
    "Motion",
  );
  const name = h("a", {
    class: "camera-link",
    attrs: { href: `/devices/${encodeURIComponent(device.id)}` },
  });
  const detail = h("p", { class: "text-sm text-haze" });
  const view = h("div", { class: "camera-view" }, canvas, placeholder, chip, motionChip);
  const element = h(
    "article",
    { class: "camera-tile card" },
    view,
    h("div", { class: "camera-meta" }, h("h2", { class: "font-medium" }, name), detail),
  );

  const player = createPlayer({
    canvas,
    source: () => streamUrl(device.id, { fps: GRID_FPS }),
    onState(state) {
      playerState = state;
      // The hub ends a stream when the camera stops. If it is still meant to be online, the
      // status update is on its way, or the stream will come back: check again shortly.
      if (state === "ended") restart = setTimeout(sync, 2_000);
      render();
    },
    onFrame(width, height) {
      if (!hasFrame) {
        hasFrame = true;
        view.style.aspectRatio = `${width} / ${height}`;
      }
    },
  });

  /** Plays or pauses the stream to match what the tile needs right now. */
  function sync() {
    clearTimeout(restart);
    if (device.status === "online" && visible && !document.hidden) player.play();
    else player.pause();
  }

  /** The status chip: the camera's status, or for a live camera, its stream's. */
  function chipLook() {
    if (device.status !== "online") return STATUS[device.status];
    if (playerState === "retrying") return { tone: "sodium", label: "Reconnecting" };
    if (playerState === "playing" || !visible) return { tone: "signal", label: "Live" };
    return { tone: "iris", label: "Connecting" };
  }

  function render() {
    const status = STATUS[device.status];
    const look = chipLook();
    if (look.tone) chip.dataset.tone = look.tone;
    else delete chip.dataset.tone;
    chipLight.toggleAttribute("data-pulse", look.label === "Live");
    chipLabel.textContent = look.label;

    name.textContent = device.name;
    canvas.setAttribute("aria-label", `Live view of ${device.name}`);
    element.toggleAttribute("data-motion", motion);
    // loading: shimmer until the first frame. retrying: the last frame, dimmed.
    element.dataset.state =
      device.status !== "online" ? device.status : hasFrame ? playerState : "loading";

    const note = status.note;
    placeholder.replaceChildren(
      ...(note
        ? [
            status.busy ? h("span", { class: "spinner size-4" }) : icon("camera"),
            h("span", { text: note }),
          ]
        : []),
    );
    placeholder.hidden = !note;
    renderDetail();
  }

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
  }

  fromDevice();
  render();

  return {
    element,
    get id() {
      return device.id;
    },
    /** @param {Device} next */
    update(next) {
      device = next;
      fromDevice();
      render();
      sync();
    },
    /** @param {Device["status"]} status */
    setStatus(status) {
      device = { ...device, status };
      render();
      sync();
    },
    /**
     * @param {boolean} active
     * @param {string} at when it started (active) or ended
     */
    setMotion(active, at) {
      motion = active;
      lastMotionAt = at;
      render();
    },
    /** @param {boolean} onScreen */
    setVisible(onScreen) {
      visible = onScreen;
      render();
      sync();
    },
    sync,
    /** Refreshes "Motion 4 minutes ago". */
    tick: renderDetail,
    destroy() {
      clearTimeout(restart);
      player.pause();
    },
  };
}
