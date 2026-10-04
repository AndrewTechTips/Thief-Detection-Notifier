// Snapshot viewer: a modal dialog with the event's picture, with or without the motion boxes.
// Browse with the arrows, the arrow keys or a swipe. Escape closes it and focus returns to
// where it was. Signed snapshot links expire, so a broken image fetches fresh links once.

import { snapshotUrl } from "../api/events.js";
import { deviceName } from "../state/devices.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { enter, exit } from "./motion.js";
import { formatClock, formatDay, formatDuration } from "./time.js";

/** @typedef {import("./event-row.js").ListedEvent} ListedEvent */

const BOXES_KEY = "vision-hub.snapshot-boxes";
const SWIPE_PX = 60;

/**
 * @param {{
 *   items: ListedEvent[],
 *   index: number,
 *   refresh: (id: string) => Promise<ListedEvent | null>,
 *   onClose?: (id: string) => void,
 * }} options
 * `items`: the events to browse, in list order (newest first). `refresh`: fresh signed links.
 * `onClose` gets the event shown last, so the list can put focus back on it.
 */
export function openLightbox({ items, index, refresh, onClose }) {
  let current = index;
  let boxes = readBoxes();
  /** @type {Set<string>} events whose links were already refreshed once */
  const refreshed = new Set();

  const image = h("img", { class: "lightbox-image", attrs: { alt: "", decoding: "async" } });
  const status = h("p", { class: "lightbox-status", attrs: { role: "status" } });
  const previous = navButton("back", "Previous event", () => go(current - 1));
  const next = navButton("forward", "Next event", () => go(current + 1));
  const title = h("h2", { class: "font-medium", attrs: { id: "lightbox-title" } });
  const facts = h("p", { class: "text-sm text-haze" });
  const position = h("p", { class: "text-xs text-haze tabular-nums" });
  const boxesSwitch = h("input", {
    class: "switch",
    attrs: { type: "checkbox", role: "switch", id: "lightbox-boxes" },
  });
  boxesSwitch.checked = boxes;
  const download = h(
    "a",
    { class: "btn btn-secondary btn-sm", attrs: { download: "" } },
    icon("download"),
    "Download",
  );
  const close = h(
    "button",
    {
      class: "lightbox-close btn btn-ghost btn-icon btn-sm",
      attrs: { type: "button", "aria-label": "Close" },
    },
    icon("close"),
  );

  const frame = h("div", { class: "lightbox-frame" }, image, status, previous, next);
  const dialog = h(
    "dialog",
    { class: "lightbox", attrs: { "aria-labelledby": "lightbox-title" } },
    frame,
    h(
      "div",
      { class: "lightbox-bar panel-solid" },
      h("div", { class: "min-w-0 flex-1" }, title, facts, position),
      h(
        "div",
        { class: "flex flex-wrap items-center gap-3" },
        h(
          "label",
          {
            class: "flex cursor-pointer items-center gap-2 text-sm",
            attrs: { for: "lightbox-boxes" },
          },
          boxesSwitch,
          "Motion boxes",
        ),
        download,
        close,
      ),
    ),
  );

  function show() {
    const event = items[current];
    const camera = deviceName(event.device_id);
    const kind = boxes ? "annotated" : "clean";
    const url = snapshotUrl(event, kind) ?? snapshotUrl(event, boxes ? "clean" : "annotated");
    title.textContent = camera;
    facts.textContent = [
      `${formatDay(event.started_at)}, ${formatClock(event.started_at, { seconds: true })}`,
      event.duration_seconds !== null ? `lasted ${formatDuration(event.duration_seconds)}` : null,
      event.peak_area_ratio > 0
        ? `up to ${Math.round(event.peak_area_ratio * 100)} % of the frame`
        : null,
    ]
      .filter(Boolean)
      .join(", ");
    position.textContent = items.length > 1 ? `${current + 1} of ${items.length}` : "";
    previous.disabled = current === 0;
    next.disabled = current === items.length - 1;

    const original = snapshotUrl(event, "clean");
    download.hidden = !original;
    if (original) {
      download.href = original;
      download.setAttribute("download", fileName(event));
    }

    if (!url) {
      image.hidden = true;
      status.textContent = event.complete
        ? "No snapshot was saved for this event."
        : "The snapshot is ready when the motion ends.";
      return;
    }
    image.hidden = false;
    image.alt = `${camera}, ${formatClock(event.started_at)}${boxes ? ", motion boxed" : ""}`;
    if (image.getAttribute("src") !== url) {
      frame.dataset.loading = "";
      status.textContent = "";
      image.src = url;
    }
    preload(current - 1);
    preload(current + 1);
  }

  /** @param {number} at */
  function preload(at) {
    const event = items[at];
    const url = event && snapshotUrl(event, boxes ? "annotated" : "clean");
    if (url) new Image().src = url;
  }

  /** @param {number} at */
  function go(at) {
    if (at < 0 || at >= items.length || at === current) return;
    const direction = Math.sign(at - current);
    current = at;
    show();
    enter(image, [{ opacity: 0, transform: `translateX(${direction * 24}px)` }, { opacity: 1 }], {
      duration: 260,
    });
  }

  image.addEventListener("load", () => delete frame.dataset.loading);
  image.addEventListener("error", async () => {
    const event = items[current];
    if (refreshed.has(event.id)) {
      delete frame.dataset.loading;
      image.hidden = true;
      status.textContent = "This snapshot couldn't be loaded. It may have been deleted.";
      return;
    }
    // Most likely an expired signed link: get fresh ones and try again.
    refreshed.add(event.id);
    const fresh = await refresh(event.id);
    if (fresh && items[current]?.id === event.id) {
      items[current] = { ...fresh, missed: event.missed };
      show();
    }
  });

  boxesSwitch.addEventListener("change", () => {
    boxes = boxesSwitch.checked;
    writeBoxes(boxes);
    show();
  });

  dialog.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") go(current - 1);
    else if (event.key === "ArrowRight") go(current + 1);
  });

  // Swipe between events on touch screens.
  let startX = /** @type {number | null} */ (null);
  frame.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "mouse") startX = event.clientX;
  });
  frame.addEventListener("pointerup", (event) => {
    if (startX === null) return;
    const dx = event.clientX - startX;
    startX = null;
    if (Math.abs(dx) >= SWIPE_PX) go(current + (dx < 0 ? 1 : -1));
  });

  // Clicking the dim area around the picture closes, like Escape.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) shut();
  });
  close.addEventListener("click", shut);
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault(); // Escape: animate out instead of vanishing
    shut();
  });

  let closing = false;
  async function shut() {
    if (closing) return;
    closing = true;
    await exit(dialog, [{ opacity: 1 }, { opacity: 0, transform: "scale(0.98)" }], {
      duration: 160,
    });
    dialog.close();
    dialog.remove();
    onClose?.(items[current].id);
  }

  document.body.append(dialog);
  show();
  dialog.showModal();
  close.focus();
  enter(dialog, [{ opacity: 0, transform: "scale(0.97)" }, { opacity: 1 }], { duration: 240 });
  return { close: shut };
}

/**
 * @param {"back" | "forward"} name
 * @param {string} label
 * @param {() => void} run
 */
function navButton(name, label, run) {
  return h(
    "button",
    {
      class: `lightbox-nav lightbox-nav-${name} btn btn-secondary btn-icon`,
      attrs: { type: "button", "aria-label": label },
      on: { click: run },
    },
    icon(name),
  );
}

/** e.g. "demo-porch-2026-10-04-19-55-38.jpg", in local time. @param {ListedEvent} event */
function fileName(event) {
  const date = new Date(event.started_at);
  const pad = (/** @type {number} */ n) => String(n).padStart(2, "0");
  const stamp = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}-${pad(date.getHours())}-${pad(date.getMinutes())}-${pad(date.getSeconds())}`;
  return `${event.device_id}-${stamp}.jpg`;
}

function readBoxes() {
  try {
    return localStorage.getItem(BOXES_KEY) !== "false";
  } catch {
    return true;
  }
}

/** @param {boolean} value */
function writeBoxes(value) {
  try {
    localStorage.setItem(BOXES_KEY, String(value));
  } catch {
    // preference not remembered
  }
}
