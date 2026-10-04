// Live: every camera, with its video and status, updated by live events.

import { listDevices } from "../api/devices.js";
import { describeError } from "../api/errors.js";
import { realtime } from "../realtime/live.js";
import { rememberDevices } from "../state/devices.js";
import { cameraTile } from "../ui/camera-tile.js";
import { h } from "../ui/dom.js";
import { icon } from "../ui/icons.js";
import { enter } from "../ui/motion.js";
import { pageHeader } from "../ui/page.js";

/** @typedef {import("../api/types.js").Device} Device */
/** @typedef {ReturnType<typeof cameraTile>} Tile */

/** @type {import("../router.js").View} */
export default {
  title: "Live",
  mount(outlet) {
    const summary = h("p", { text: "Loading cameras…" });
    const header = pageHeader({ title: "Live" });
    header.querySelector("div")?.append(summary);
    const grid = h("div", { class: "camera-grid", attrs: { "aria-busy": "true" } });
    outlet.append(header, grid);

    /** @type {Map<string, Tile>} */
    const tiles = new Map();
    /** @type {Map<string, Device["status"]>} for the summary line */
    const statuses = new Map();
    let loaded = false;
    const controller = new AbortController();

    // Streams run only for tiles on screen (with some margin, so scrolling feels instant).
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const id = /** @type {HTMLElement} */ (entry.target).dataset.device;
          if (id) tiles.get(id)?.setVisible(entry.isIntersecting);
        }
      },
      { rootMargin: "200px 0px" },
    );

    showSkeletons();
    load();

    async function load() {
      try {
        const devices = await listDevices({ signal: controller.signal });
        rememberDevices(devices);
        loaded = true;
        show(devices);
      } catch (error) {
        if (controller.signal.aborted) return;
        showError(error);
      }
    }

    /** @param {Device[]} devices */
    function show(devices) {
      grid.removeAttribute("aria-busy");
      if (devices.length === 0) {
        clearTiles();
        grid.replaceChildren(
          empty("No cameras yet", "Cameras added to the hub appear here, live."),
        );
        summary.textContent = "Live video and status for every camera.";
        return;
      }
      const ids = new Set(devices.map((device) => device.id));
      for (const [id, tile] of tiles) {
        if (!ids.has(id)) {
          observer.unobserve(tile.element);
          tile.destroy();
          tiles.delete(id);
        }
      }
      const elements = devices.map((device) => {
        const existing = tiles.get(device.id);
        if (existing) {
          existing.update(device);
          return existing.element;
        }
        const tile = cameraTile(device);
        tile.element.dataset.device = device.id;
        tiles.set(device.id, tile);
        observer.observe(tile.element);
        return tile.element;
      });
      const fresh = !grid.querySelector(".camera-tile");
      grid.replaceChildren(...elements);
      if (fresh) {
        elements.forEach((element, i) =>
          enter(element, [{ opacity: 0, transform: "translateY(10px)" }, { opacity: 1 }], {
            duration: 420,
            delay: Math.min(i, 8) * 45,
          }),
        );
      }
      statuses.clear();
      devices.forEach((device) => statuses.set(device.id, device.status));
      summarise();
    }

    function summarise() {
      const total = statuses.size;
      const online = [...statuses.values()].filter((status) => status === "online").length;
      const cameras = total === 1 ? "camera" : "cameras";
      summary.textContent =
        online === total
          ? `${total === 1 ? "Your" : `All ${total}`} ${cameras} live.`
          : `${online} of ${total} ${cameras} live.`;
    }

    function showSkeletons() {
      grid.replaceChildren(
        ...Array.from({ length: 3 }, () =>
          h(
            "div",
            { class: "card overflow-hidden", attrs: { "aria-hidden": "true" } },
            h("div", { class: "skeleton aspect-video rounded-none" }),
            h(
              "div",
              { class: "space-y-2 p-4" },
              h("div", { class: "skeleton h-4 w-1/2" }),
              h("div", { class: "skeleton h-3 w-1/3" }),
            ),
          ),
        ),
      );
    }

    /** @param {unknown} error */
    function showError(error) {
      const { title, detail } = describeError(error);
      grid.removeAttribute("aria-busy");
      grid.replaceChildren(
        empty(title, detail, {
          label: "Try again",
          run: () => {
            showSkeletons();
            load();
          },
        }),
      );
      summary.textContent = "Couldn't load your cameras.";
    }

    function clearTiles() {
      for (const tile of tiles.values()) {
        observer.unobserve(tile.element);
        tile.destroy();
      }
      tiles.clear();
      statuses.clear();
    }

    // Live events keep the grid current without reloading.
    const stopListening = realtime.listen((message) => {
      const tile = "device_id" in message ? tiles.get(message.device_id) : undefined;
      switch (message.type) {
        case "device.status":
          if (!tile) return;
          tile.setStatus(message.data.status);
          statuses.set(message.device_id, message.data.status);
          summarise();
          return;
        case "motion.started":
          tile?.setMotion(true, message.data.started_at);
          return;
        case "motion.ended":
          tile?.setMotion(false, message.data.ended_at ?? message.data.started_at);
          return;
      }
    });

    // Status changes are not replayed after a reconnect: reload the list to catch up.
    let realtimeStatus = realtime.state.get().status;
    const stopWatching = realtime.state.subscribe(({ status }) => {
      if (status === "live" && realtimeStatus !== "live" && loaded) load();
      realtimeStatus = status;
    });

    const onVisibility = () => tiles.forEach((tile) => tile.sync());
    document.addEventListener("visibilitychange", onVisibility);
    const ticker = window.setInterval(() => tiles.forEach((tile) => tile.tick()), 30_000);

    return () => {
      controller.abort();
      stopListening();
      stopWatching();
      document.removeEventListener("visibilitychange", onVisibility);
      window.clearInterval(ticker);
      observer.disconnect();
      clearTiles();
    };
  },
};

/**
 * @param {string} title
 * @param {string} detail
 * @param {{ label: string, run: () => void }} [action]
 */
function empty(title, detail, action) {
  return h(
    "div",
    { class: "camera-empty card" },
    h("span", { class: "camera-empty-icon" }, icon("camera")),
    h("p", { class: "font-medium", text: title }),
    h("p", { class: "text-sm text-haze", text: detail }),
    action
      ? h("button", {
          class: "btn btn-secondary btn-sm mt-3",
          attrs: { type: "button" },
          text: action.label,
          on: { click: action.run },
        })
      : null,
  );
}
