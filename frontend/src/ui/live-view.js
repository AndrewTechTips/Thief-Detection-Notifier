// A camera's live picture with its status chip and motion chip. Used by grid tiles and the
// camera page. The stream only runs while the camera is online, the view is on screen and the
// tab is visible; everything else costs the hub nothing.
//
// The host element (a tile, the camera page) receives data-state and data-motion, so styles can
// dim a stalled picture or light up a motion ring around whatever contains the view.

import { streamUrl } from "../api/devices.js";
import { createPlayer } from "../realtime/mjpeg.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";

/** @typedef {import("../api/types.js").Device} Device */
/** @typedef {import("../realtime/mjpeg.js").PlayerState} PlayerState */

/**
 * What the view says about its camera.
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
 * @param {{
 *   device: Device,
 *   host: HTMLElement,
 *   fps?: number,
 *   onFrameSize?: (width: number, height: number) => void,
 * }} options
 * fps: frame-rate cap for the stream (omit for the hub's maximum).
 */
export function liveView({ device: initial, host, fps, onFrameSize }) {
  let device = initial;
  let visible = false;
  let motion = false;
  let hasFrame = false;
  /** @type {PlayerState} */
  let playerState = "idle";
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let restart;

  host.classList.add("live-host");
  // The canvas shows the live picture; the poster under it holds the first frame (see mjpeg.js).
  const canvas = h("canvas", { class: "camera-canvas", attrs: { role: "img" } });
  const poster = h("img", {
    class: "camera-canvas camera-poster",
    attrs: { alt: "", "aria-hidden": "true", decoding: "async" },
  });
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
  const element = h("div", { class: "camera-view" }, poster, canvas, placeholder, chip, motionChip);

  // Start at the camera's real shape when the hub knows it, so the first frame doesn't resize
  // the view and push the page around (layout shift).
  if (initial.stream)
    element.style.aspectRatio = `${initial.stream.width} / ${initial.stream.height}`;

  const player = createPlayer({
    canvas,
    poster,
    source: () => streamUrl(device.id, { fps }),
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
        element.style.aspectRatio = `${width} / ${height}`;
        onFrameSize?.(width, height);
      }
    },
  });

  /** Plays or pauses the stream to match what the view needs right now. */
  function sync() {
    clearTimeout(restart);
    if (device.status === "online" && visible && !document.hidden) player.play();
    else player.pause();
  }

  /** The status chip: the camera's status, or for a live camera whose stream is connecting or
   * retrying, the stream's. A paused stream (off screen, hidden tab) still shows "Live". */
  function chipLook() {
    if (device.status !== "online") return STATUS[device.status];
    if (playerState === "retrying") return { tone: "sodium", label: "Reconnecting" };
    if (playerState === "loading") return { tone: "iris", label: "Connecting" };
    return STATUS.online;
  }

  function render() {
    const status = STATUS[device.status];
    const look = chipLook();
    if (look.tone) chip.dataset.tone = look.tone;
    else delete chip.dataset.tone;
    chipLight.toggleAttribute("data-pulse", look.label === "Live");
    chipLabel.textContent = look.label;

    canvas.setAttribute("aria-label", `Live view of ${device.name}`);
    host.toggleAttribute("data-motion", motion);
    // loading: shimmer until the first frame. retrying: the last frame, dimmed.
    host.dataset.state =
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
  }

  render();

  return {
    element,
    /** @param {Device} next */
    update(next) {
      device = next;
      render();
      sync();
    },
    /** @param {Device["status"]} status */
    setStatus(status) {
      device = { ...device, status };
      render();
      sync();
    },
    /** @param {boolean} active */
    setMotion(active) {
      motion = active;
      render();
    },
    /** @param {boolean} onScreen */
    setVisible(onScreen) {
      visible = onScreen;
      render();
      sync();
    },
    sync,
    destroy() {
      clearTimeout(restart);
      player.pause();
    },
  };
}
