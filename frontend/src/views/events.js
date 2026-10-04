// Events: what the cameras recorded, newest first, grouped by day. Live events appear at the
// top as they start and fill in (duration, snapshot) when they end. Events replayed after a
// reconnect are marked as missed; nothing is listed twice.

import { describeError } from "../api/errors.js";
import { getEvent, listEvents } from "../api/events.js";
import { realtime } from "../realtime/live.js";
import { devices, loadDevices } from "../state/devices.js";
import { h } from "../ui/dom.js";
import { eventRow } from "../ui/event-row.js";
import { icon } from "../ui/icons.js";
import { enter, flip } from "../ui/motion.js";
import { pageHeader } from "../ui/page.js";
import { formatDay } from "../ui/time.js";

/** @typedef {import("../api/types.js").MotionEvent} MotionEvent */
/** @typedef {import("../ui/event-row.js").ListedEvent} ListedEvent */
/** @typedef {ReturnType<typeof eventRow>} Row */

const FIRST_PAGE = 50;

/** @type {import("../router.js").View} */
export default {
  title: "Events",
  mount(outlet) {
    const list = h("div", { class: "event-days", attrs: { "aria-busy": "true" } });
    outlet.append(
      pageHeader({ title: "Events", description: "Motion your cameras recorded, newest first." }),
      list,
    );

    /** @type {Map<string, Row>} */
    const rows = new Map();
    const controller = new AbortController();
    const highlight = new URLSearchParams(location.search).get("event");
    let ready = false;

    showSkeleton();
    load();

    async function load() {
      try {
        const [page] = await Promise.all([
          listEvents({ limit: FIRST_PAGE, signal: controller.signal }),
          devices.get().size ? null : loadDevices().catch(() => {}),
        ]);
        for (const event of page.items) upsert(event);
        if (highlight && !rows.has(highlight)) {
          const event = await getEvent(highlight, { signal: controller.signal }).catch(() => null);
          if (event) upsert(event);
        }
        ready = true;
        render({ animate: false });
        if (highlight) focus(highlight);
      } catch (error) {
        if (!controller.signal.aborted) showError(error);
      }
    }

    /**
     * Adds or updates an event; returns true if it is new to the list.
     * @param {ListedEvent} event
     */
    function upsert(event) {
      const row = rows.get(event.id);
      if (row) {
        row.update(event);
        return false;
      }
      rows.set(event.id, eventRow(event));
      return true;
    }

    /** Lays rows out by day, newest first, reusing their elements (images stay loaded). */
    function render({ animate = true } = {}) {
      list.removeAttribute("aria-busy");
      if (rows.size === 0) {
        list.replaceChildren(emptyState());
        return;
      }
      const sorted = [...rows.values()].sort((a, b) =>
        b.event.started_at.localeCompare(a.event.started_at),
      );
      /** @type {Map<string, Row[]>} */
      const days = new Map();
      for (const row of sorted) {
        const day = formatDay(row.event.started_at);
        days.set(day, [...(days.get(day) ?? []), row]);
      }
      const existing = [...list.querySelectorAll(".event-row")];
      const layout = () =>
        list.replaceChildren(
          ...[...days].map(([day, dayRows]) =>
            h(
              "section",
              { class: "event-day", attrs: { "aria-label": day } },
              h("h2", { class: "event-day-title", text: day }),
              h(
                "ol",
                { class: "event-list card" },
                dayRows.map((row) => row.element),
              ),
            ),
          ),
        );
      if (animate) flip(existing, layout);
      else layout();
    }

    /** @param {string} id */
    function focus(id) {
      const row = rows.get(id);
      if (!row) return;
      row.element.scrollIntoView({ block: "center" });
      row.element.setAttribute("data-highlight", "");
      setTimeout(() => row.element.removeAttribute("data-highlight"), 2_400);
    }

    /** @param {ListedEvent} event */
    function arrive(event) {
      const fresh = upsert(event);
      if (!ready) return;
      render();
      const row = rows.get(event.id);
      if (fresh && row) {
        enter(row.element, [{ opacity: 0, transform: "translateY(-8px)" }, { opacity: 1 }], {
          duration: 360,
        });
      }
    }

    /** Fills in the snapshot and final numbers once the hub has stored them.
     * @param {string} id */
    async function complete(id) {
      const event = await getEvent(id, { signal: controller.signal }).catch(() => null);
      if (event && rows.has(id)) rows.get(id)?.update(event);
    }

    const stopListening = realtime.listen((message) => {
      switch (message.type) {
        case "motion.started": {
          if (rows.has(message.data.event_id)) return;
          arrive({
            id: message.data.event_id,
            device_id: message.device_id,
            started_at: message.data.started_at,
            ended_at: null,
            duration_seconds: null,
            complete: false,
            interrupted: false,
            peak_area_ratio: 0,
            motion_frames: 0,
            boxes: [],
            snapshots: [],
            missed: message.replay,
          });
          return;
        }
        case "motion.ended": {
          const { event_id: id, started_at, ended_at } = message.data;
          const known = rows.get(id)?.event;
          arrive({
            ...(known ?? { snapshots: [], interrupted: false, missed: message.replay }),
            id,
            device_id: message.device_id,
            started_at,
            ended_at,
            duration_seconds: ended_at
              ? (Date.parse(ended_at) - Date.parse(started_at)) / 1000
              : null,
            complete: true,
            peak_area_ratio: message.data.peak_area_ratio,
            motion_frames: message.data.motion_frames,
            boxes: message.data.boxes,
          });
          complete(id);
          return;
        }
        case "replay.done":
          // More was missed than the hub replays: start over from the history.
          if (message.data.truncated) {
            rows.clear();
            load();
          }
          return;
      }
    });

    // Camera names arrive with the directory; durations and "4 minutes ago" move with time.
    const stopDevices = devices.subscribe(() => rows.forEach((row) => row.update(row.event)));
    const fast = window.setInterval(() => {
      rows.forEach((row) => {
        if (!row.event.complete && !row.event.interrupted) row.tick();
      });
    }, 1_000);
    const slow = window.setInterval(() => rows.forEach((row) => row.tick()), 30_000);

    function showSkeleton() {
      list.replaceChildren(
        h(
          "div",
          { class: "event-list card", attrs: { "aria-hidden": "true" } },
          ...Array.from({ length: 4 }, () =>
            h(
              "div",
              { class: "event-row" },
              h("div", { class: "event-thumb skeleton" }),
              h(
                "div",
                { class: "flex-1 space-y-2" },
                h("div", { class: "skeleton h-4 w-1/3" }),
                h("div", { class: "skeleton h-3 w-1/2" }),
              ),
            ),
          ),
        ),
      );
    }

    /** @param {unknown} error */
    function showError(error) {
      const { title, detail } = describeError(error);
      list.removeAttribute("aria-busy");
      list.replaceChildren(
        h(
          "div",
          { class: "camera-empty card" },
          h("span", { class: "camera-empty-icon" }, icon("events")),
          h("p", { class: "font-medium", text: title }),
          h("p", { class: "text-sm text-haze", text: detail }),
          h("button", {
            class: "btn btn-secondary btn-sm mt-3",
            attrs: { type: "button" },
            text: "Try again",
            on: {
              click: () => {
                showSkeleton();
                load();
              },
            },
          }),
        ),
      );
    }

    return () => {
      controller.abort();
      stopListening();
      stopDevices();
      window.clearInterval(fast);
      window.clearInterval(slow);
    };
  },
};

function emptyState() {
  return h(
    "div",
    { class: "camera-empty card" },
    h("span", { class: "camera-empty-icon" }, icon("events")),
    h("p", { class: "font-medium", text: "No motion recorded yet" }),
    h("p", {
      class: "text-sm text-haze",
      text: "Events appear here, live, as your cameras see movement.",
    }),
  );
}
