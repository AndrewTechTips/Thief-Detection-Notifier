// One motion event in a list: snapshot, camera, when, how long, and how much moved.

import { snapshotUrl } from "../api/events.js";
import { deviceName } from "../state/devices.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { formatClock, formatDuration, timeAgo } from "./time.js";

/** @typedef {import("../api/types.js").MotionEvent} MotionEvent */
/**
 * missed: arrived by replay after a reconnect, i.e. it happened while this page was offline.
 * @typedef {MotionEvent & { missed?: boolean }} ListedEvent
 */

/** @param {ListedEvent} initial */
export function eventRow(initial) {
  let event = initial;
  const thumb = h("div", { class: "event-thumb" });
  const name = h("p", { class: "truncate font-medium" });
  const facts = h("p", { class: "event-facts" });
  const tags = h("div", { class: "event-tags" });
  const clock = h("time", { class: "text-sm tabular-nums" });
  const ago = h("p", { class: "text-xs text-haze" });
  const element = h(
    "li",
    { class: "event-row" },
    thumb,
    h("div", { class: "min-w-0 flex-1" }, name, facts, tags),
    h("div", { class: "event-when" }, clock, ago),
  );

  function render() {
    const camera = deviceName(event.device_id);
    element.dataset.state = event.complete
      ? "complete"
      : event.interrupted
        ? "interrupted"
        : "live";
    name.textContent = camera;
    clock.textContent = formatClock(event.started_at, { seconds: true });
    clock.dateTime = event.started_at;

    const url = snapshotUrl(event, "thumbnail");
    const image = thumb.querySelector("img");
    if (url && image?.dataset.event !== event.id) {
      thumb.replaceChildren(
        h("img", {
          attrs: {
            src: url,
            alt: `Snapshot from ${camera} at ${formatClock(event.started_at)}`,
            loading: "lazy",
            decoding: "async",
          },
          data: { event: event.id },
        }),
      );
    } else if (!url) {
      // No snapshot yet while motion goes on; none at all if it could not be saved.
      const live = !event.complete && !event.interrupted;
      if (thumb.dataset.placeholder !== String(live)) {
        thumb.dataset.placeholder = String(live);
        thumb.replaceChildren(
          live
            ? h("span", { class: "dot", attrs: { "data-tone": "sodium", "data-pulse": "" } })
            : icon("camera"),
        );
      }
    }

    renderFacts();

    tags.replaceChildren(
      ...[
        !event.complete && !event.interrupted && tag("sodium", "In progress", true),
        event.interrupted &&
          tag("alarm", "Interrupted", false, "The hub stopped before this event ended."),
        event.missed && tag("iris", "Missed", false, "Happened while this page was offline."),
      ].filter((item) => item instanceof HTMLElement),
    );
    tick();
  }

  function renderFacts() {
    const lines = [];
    if (event.complete && event.duration_seconds !== null) {
      lines.push(`Lasted ${formatDuration(event.duration_seconds)}`);
    } else if (!event.complete && !event.interrupted) {
      const seconds = (Date.now() - Date.parse(event.started_at)) / 1000;
      lines.push(seconds < 1 ? "Just started" : `Going on for ${formatDuration(seconds)}`);
    }
    if (event.peak_area_ratio > 0) {
      lines.push(`up to ${Math.round(event.peak_area_ratio * 100)} % of the frame`);
    }
    facts.textContent = lines.join(", ");
  }

  /** Refreshes the parts that change with time. */
  function tick() {
    ago.textContent = timeAgo(event.started_at);
    if (!event.complete && !event.interrupted) renderFacts();
  }

  render();
  return {
    element,
    get event() {
      return event;
    },
    /** @param {ListedEvent} next */
    update(next) {
      event = { ...next, missed: next.missed ?? event.missed };
      render();
    },
    tick,
  };
}

/**
 * @param {string} tone
 * @param {string} label
 * @param {boolean} [pulse]
 * @param {string} [title]
 */
function tag(tone, label, pulse = false, title) {
  return h(
    "span",
    { class: "badge", data: { tone }, attrs: { title } },
    h("span", { class: "dot", attrs: pulse ? { "data-pulse": "" } : {} }),
    label,
  );
}
