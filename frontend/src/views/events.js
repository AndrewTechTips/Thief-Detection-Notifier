// Events: what the cameras recorded, newest first, grouped by day. Filter by camera and time
// range (kept in the address bar), scroll for older events, open any of them full size.
// Live events appear at the top as they start and fill in (duration, snapshot) when they end;
// events replayed after a reconnect are marked as missed; nothing is listed twice.

import { describeError } from "../api/errors.js";
import { getEvent, listEvents } from "../api/events.js";
import { realtime } from "../realtime/live.js";
import { devices, directoryLoaded, loadDevices } from "../state/devices.js";
import { h } from "../ui/dom.js";
import { eventRow } from "../ui/event-row.js";
import { icon } from "../ui/icons.js";
import { openLightbox } from "../ui/lightbox.js";
import { enter, flip } from "../ui/motion.js";
import { pageHeader } from "../ui/page.js";
import { formatDay } from "../ui/time.js";

/** @typedef {import("../api/types.js").MotionEvent} MotionEvent */
/** @typedef {import("../ui/event-row.js").ListedEvent} ListedEvent */
/** @typedef {ReturnType<typeof eventRow>} Row */
/** @typedef {"all" | "today" | "24h" | "7d"} Range */

const PAGE_SIZE = 30;

/** @type {{ value: Range, label: string }[]} */
const RANGES = [
  { value: "all", label: "All time" },
  { value: "today", label: "Today" },
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
];

/**
 * Where a range starts, from now.
 * @param {Range} range
 * @returns {Date | null}
 */
export function rangeStart(range, now = new Date()) {
  switch (range) {
    case "today":
      return new Date(now.getFullYear(), now.getMonth(), now.getDate());
    case "24h":
      return new Date(now.getTime() - 86_400_000);
    case "7d":
      return new Date(now.getTime() - 7 * 86_400_000);
    default:
      return null;
  }
}

/** @type {import("../router.js").View} */
export default {
  title: "Events",
  mount(outlet) {
    const query = new URLSearchParams(location.search);
    const filters = {
      device: query.get("device") || null,
      range: /** @type {Range} */ (
        RANGES.some((r) => r.value === query.get("range")) ? query.get("range") : "all"
      ),
    };
    const highlight = query.get("event");

    const camera = h("select", {
      class: "select",
      attrs: { "aria-label": "Camera" },
    });
    const ranges = h(
      "div",
      { class: "segmented", attrs: { role: "radiogroup", "aria-label": "Time range" } },
      RANGES.map(({ value, label }) =>
        h(
          "label",
          {},
          h("input", {
            attrs: { type: "radio", name: "range", value, checked: value === filters.range },
          }),
          label,
        ),
      ),
    );
    const list = h("div", { class: "event-days", attrs: { "aria-busy": "true" } });
    const more = h("div", { class: "event-more", attrs: { "aria-live": "polite" } });
    outlet.append(
      pageHeader({ title: "Events", description: "Motion your cameras recorded, newest first." }),
      h("div", { class: "event-filters" }, camera, ranges),
      list,
      more,
    );

    /** @type {Map<string, Row>} */
    const rows = new Map();
    /** @type {string | null} */
    let cursor = null;
    let done = false;
    let loading = false;
    let ready = false;
    /** Bumps whenever the filters change, so answers to old requests are dropped. */
    let generation = 0;
    let controller = new AbortController();

    // Older pages load as the end of the list comes into view.
    const sentinel = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadMore();
      },
      { rootMargin: "600px 0px" },
    );
    sentinel.observe(more);

    fillCameras();
    reset();

    // ── Loading ─────────────────────────────────────────────

    function reset() {
      generation += 1;
      controller.abort();
      controller = new AbortController();
      rows.clear();
      cursor = null;
      done = false;
      ready = false;
      loading = false; // a request for the old filters may still be out; its answer is dropped
      showSkeleton();
      loadMore();
    }

    async function loadMore() {
      if (loading || done) return;
      loading = true;
      const mine = generation;
      if (ready) more.replaceChildren(h("span", { class: "spinner" }), "Loading older events…");
      try {
        const [page] = await Promise.all([
          listEvents({
            deviceId: filters.device,
            since: rangeStart(filters.range),
            cursor,
            limit: PAGE_SIZE,
            signal: controller.signal,
          }),
          // Usually loaded at sign-in already; this covers a first visit straight to Events.
          directoryLoaded() ? null : loadDevices().catch(() => {}),
        ]);
        if (mine !== generation) return;
        for (const event of page.items) upsert(event);
        cursor = page.next_cursor;
        done = !cursor;
        if (!ready && highlight && !rows.has(highlight)) {
          const event = await getEvent(highlight, { signal: controller.signal }).catch(() => null);
          if (event && matches(event)) upsert(event);
        }
        const first = !ready;
        ready = true;
        render({ animate: false });
        if (first && highlight) reveal(highlight);
      } catch (error) {
        if (mine !== generation || controller.signal.aborted) return;
        if (!ready) showError(error);
        else {
          more.replaceChildren(
            "Couldn't load older events. ",
            h("button", {
              class: "btn btn-ghost btn-sm",
              attrs: { type: "button" },
              text: "Try again",
              on: { click: () => loadMore() },
            }),
          );
        }
        return;
      } finally {
        if (mine === generation) loading = false;
      }
      // The list may still be shorter than the screen: keep going while the end is visible.
      renderFooter();
      if (!done && more.getBoundingClientRect().top < innerHeight + 600) loadMore();
    }

    /** @param {ListedEvent} event */
    function matches(event) {
      if (filters.device && event.device_id !== filters.device) return false;
      const since = rangeStart(filters.range);
      return !since || Date.parse(event.started_at) >= since.getTime();
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
      rows.set(event.id, eventRow(event, { onOpen: open }));
      return true;
    }

    // ── Rendering ───────────────────────────────────────────

    function sortedRows() {
      return [...rows.values()].sort((a, b) =>
        b.event.started_at.localeCompare(a.event.started_at),
      );
    }

    /** Lays rows out by day, newest first, reusing their elements (images stay loaded). */
    function render({ animate = true } = {}) {
      list.removeAttribute("aria-busy");
      if (rows.size === 0) {
        list.replaceChildren(emptyState());
        renderFooter();
        return;
      }
      /** @type {Map<string, Row[]>} */
      const days = new Map();
      for (const row of sortedRows()) {
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
      renderFooter();
    }

    function renderFooter() {
      if (!ready || loading) return;
      more.replaceChildren(done && rows.size > 0 ? "That's everything." : "");
    }

    function emptyState() {
      const filtered = filters.device || filters.range !== "all";
      return h(
        "div",
        { class: "camera-empty card" },
        h("span", { class: "camera-empty-icon" }, icon("events")),
        h("p", {
          class: "font-medium",
          text: filtered ? "No events match these filters" : "No motion recorded yet",
        }),
        h("p", {
          class: "text-sm text-haze",
          text: filtered
            ? "Try another camera or a longer time range."
            : "Events appear here, live, as your cameras see movement.",
        }),
        filtered
          ? h("button", {
              class: "btn btn-secondary btn-sm mt-3",
              attrs: { type: "button" },
              text: "Show all events",
              on: { click: () => setFilters({ device: null, range: "all" }) },
            })
          : null,
      );
    }

    // ── Filters ─────────────────────────────────────────────

    function fillCameras() {
      const known = [...devices.get().values()].sort((a, b) => a.name.localeCompare(b.name));
      const options = [
        h("option", { attrs: { value: "" }, text: "All cameras" }),
        ...known.map((device) => h("option", { attrs: { value: device.id }, text: device.name })),
      ];
      // A camera from the address bar the directory doesn't know (yet): keep it selectable.
      if (filters.device && !devices.get().has(filters.device)) {
        options.push(h("option", { attrs: { value: filters.device }, text: filters.device }));
      }
      camera.replaceChildren(...options);
      camera.value = filters.device ?? "";
    }

    /** @param {{ device?: string | null, range?: Range }} next */
    function setFilters(next) {
      Object.assign(filters, next);
      camera.value = filters.device ?? "";
      for (const input of ranges.querySelectorAll("input")) {
        input.checked = input.value === filters.range;
      }
      const params = new URLSearchParams();
      if (filters.device) params.set("device", filters.device);
      if (filters.range !== "all") params.set("range", filters.range);
      const search = params.size ? `?${params}` : "";
      // Same page, new filters: replace the entry so Back leaves the page rather than undoing.
      history.replaceState(history.state, "", `${location.pathname}${search}`);
      reset();
    }

    camera.addEventListener("change", () => setFilters({ device: camera.value || null }));
    ranges.addEventListener("change", (event) => {
      const input = /** @type {HTMLInputElement} */ (event.target);
      setFilters({ range: /** @type {Range} */ (input.value) });
    });

    // ── Snapshot viewer ─────────────────────────────────────

    /** @param {string} id */
    function open(id) {
      const items = sortedRows()
        .map((row) => row.event)
        .filter((event) => event.complete || event.interrupted);
      const index = items.findIndex((event) => event.id === id);
      if (index < 0) return;
      openLightbox({
        items,
        index,
        refresh: async (eventId) => {
          const fresh = await getEvent(eventId).catch(() => null);
          if (fresh) upsert(fresh);
          return fresh;
        },
        // Back to the list where you left off: the event shown last, not the one first opened.
        onClose: (lastId) => rows.get(lastId)?.focus(),
      });
    }

    /** @param {string} id */
    function reveal(id) {
      const row = rows.get(id);
      if (!row) return;
      row.element.scrollIntoView({ block: "center" });
      row.element.setAttribute("data-highlight", "");
      setTimeout(() => row.element.removeAttribute("data-highlight"), 2_400);
      if (row.event.complete || row.event.interrupted) open(id);
    }

    // ── Live updates ────────────────────────────────────────

    /** @param {ListedEvent} event */
    function arrive(event) {
      if (!matches(event)) return;
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
        case "motion.started":
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
            clip: null,
            missed: message.replay,
          });
          return;
        case "motion.ended": {
          const { event_id: id, started_at, ended_at } = message.data;
          const known = rows.get(id)?.event;
          arrive({
            ...(known ?? { snapshots: [], clip: null, interrupted: false, missed: message.replay }),
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
          if (rows.has(id)) complete(id);
          return;
        }
        case "replay.done":
          // More was missed than the hub replays: start over from the history.
          if (message.data.truncated) reset();
          return;
      }
    });

    // Camera names arrive with the directory; durations and "4 minutes ago" move with time.
    const stopDevices = devices.subscribe(() => {
      fillCameras();
      rows.forEach((row) => row.update(row.event));
    });
    const fast = window.setInterval(() => {
      rows.forEach((row) => {
        if (!row.event.complete && !row.event.interrupted) row.tick();
      });
    }, 1_000);
    const slow = window.setInterval(() => rows.forEach((row) => row.tick()), 30_000);

    function showSkeleton() {
      more.replaceChildren();
      list.setAttribute("aria-busy", "true");
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
            on: { click: reset },
          }),
        ),
      );
    }

    return () => {
      controller.abort();
      sentinel.disconnect();
      stopListening();
      stopDevices();
      window.clearInterval(fast);
      window.clearInterval(slow);
    };
  },
};
