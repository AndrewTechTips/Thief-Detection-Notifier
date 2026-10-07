// A camera's page: its live picture at full frame rate, the facts about it and its latest
// motion. Admins also start and stop it, test its connection and tune detection: sensitivity
// and the areas of the picture it watches. Saved settings reload the running camera.

import { api } from "../api/client.js";
import { describeError } from "../api/errors.js";
import { listEvents, snapshotUrl } from "../api/events.js";
import { realtime } from "../realtime/live.js";
import { session } from "../state/auth.js";
import { devices as directory, rememberDevice } from "../state/devices.js";
import { detectionForm } from "../ui/detection-form.js";
import { h } from "../ui/dom.js";
import { icon } from "../ui/icons.js";
import { liveView } from "../ui/live-view.js";
import { pageHeader } from "../ui/page.js";
import { describeAreas, roiEditor } from "../ui/roi-editor.js";
import { formatClock, formatDuration, timeAgo } from "../ui/time.js";
import { toast } from "../ui/toast.js";

/** @typedef {import("../api/types.js").Device} Device */
/** @typedef {import("../api/types.js").DetectionConfig} DetectionConfig */
/** @typedef {import("../api/types.js").SourceTestResult} SourceTestResult */
/** @typedef {import("../api/types.js").MotionEvent} MotionEvent */

const RECENT = 6;

/** @type {import("../router.js").View} */
export default {
  title: (params) => directory.get().get(params.id)?.name ?? "Camera",
  mount(outlet, params) {
    const id = params.id;
    const path = `/devices/${encodeURIComponent(id)}`;
    const controller = new AbortController();
    const admin = session.state.get().user?.role === "admin";
    /** @type {Array<() => void>} */
    const cleanups = [() => controller.abort()];

    outlet.append(
      backLink(),
      h(
        "div",
        { class: "device-loading", attrs: { "aria-busy": "true" } },
        h("div", { class: "skeleton h-8 w-48" }),
        h("div", { class: "skeleton mt-6 aspect-video w-full rounded-[var(--radius-card)]" }),
      ),
    );

    api
      .get(path, { signal: controller.signal })
      .then((/** @type {Device} */ device) => build(device))
      .catch((error) => {
        if (controller.signal.aborted) return;
        outlet.replaceChildren(backLink(), problem(error, id));
      });

    /** @param {Device} initial */
    function build(initial) {
      let device = initial;
      /** @type {DetectionConfig} the settings being edited (saved ones are on device) */
      let draft = structuredClone(device.detection);
      document.title = `${device.name} – Vision Hub`;
      rememberDevice(device);

      // ── Header ──────────────────────────────────────────
      const statusBadge = h("span", { class: "badge" });
      const power = h("button", { class: "btn btn-sm", attrs: { type: "button" } });
      const header = pageHeader({ title: device.name, actions: admin ? [power] : [] });
      header
        .querySelector("div")
        ?.append(h("p", { class: "flex items-center gap-2" }, statusBadge));

      // ── Live picture and watch areas ────────────────────
      const stage = h("section", {
        class: "device-stage card",
        attrs: { "aria-label": "Live view" },
      });
      const view = liveView({ device, host: stage });
      const areasText = h("p", { class: "text-sm text-haze" });
      const editAreas = h("button", {
        class: "btn btn-secondary btn-sm",
        attrs: { type: "button", "aria-pressed": "false" },
        text: "Edit areas",
      });
      const roi = roiEditor({
        surface: view.element,
        areas: device.detection.roi,
        onChange: (areas) => {
          draft = { ...draft, roi: areas };
          renderDraft();
        },
      });
      stage.append(
        view.element,
        h(
          "div",
          { class: "device-stage-bar" },
          h(
            "div",
            { class: "flex flex-wrap items-center justify-between gap-3" },
            areasText,
            admin ? editAreas : null,
          ),
          admin ? roi.toolbar : null,
        ),
      );
      view.setVisible(true);
      view.setMotion(Boolean(device.last_event && !device.last_event.ended_at));

      // ── Side panels ─────────────────────────────────────
      const facts = h("dl", { class: "device-facts" });
      const recent = h("div", { class: "device-recent" });
      const side = h(
        "div",
        { class: "grid content-start gap-4" },
        h(
          "section",
          { class: "card p-5" },
          h("h2", { class: "device-section-title", text: "Details" }),
          facts,
        ),
      );

      /** @type {ReturnType<typeof detectionForm> | null} */
      let form = null;
      const save = h("button", {
        class: "btn btn-primary btn-sm",
        attrs: { type: "button" },
        text: "Save settings",
      });
      const reset = h("button", {
        class: "btn btn-ghost btn-sm",
        attrs: { type: "button" },
        text: "Discard changes",
      });
      const testHint = h("p", { class: "hint mb-3" });
      const testButton = h("button", {
        class: "btn btn-secondary btn-sm",
        attrs: { type: "button" },
        text: "Test connection",
      });
      const testResult = h("p", { class: "text-sm", attrs: { role: "status" } });

      if (admin) {
        form = detectionForm({
          config: draft,
          onChange: (config) => {
            draft = { ...config, roi: draft.roi };
            renderDraft();
          },
        });
        side.append(
          h(
            "section",
            { class: "card p-5" },
            h("h2", { class: "device-section-title", text: "Connection" }),
            testHint,
            h("div", { class: "flex flex-wrap items-center gap-3" }, testButton),
            testResult,
          ),
          h(
            "section",
            { class: "card p-5" },
            h("h2", { class: "device-section-title", text: "Detection" }),
            form.element,
            h("div", { class: "mt-5 flex flex-wrap items-center gap-2" }, save, reset),
          ),
        );
      }
      side.append(
        h(
          "section",
          { class: "card p-5" },
          h(
            "div",
            { class: "mb-3 flex items-center justify-between" },
            h("h2", { class: "device-section-title mb-0", text: "Latest motion" }),
            h("a", {
              class: "btn btn-ghost btn-sm",
              attrs: { href: `/events?device=${encodeURIComponent(id)}` },
              text: "See all",
            }),
          ),
          recent,
        ),
      );

      outlet.replaceChildren(backLink(), header, h("div", { class: "device-layout" }, stage, side));

      // ── Rendering ───────────────────────────────────────

      function renderDevice() {
        const look = STATUS[device.status];
        statusBadge.dataset.tone = look.tone;
        statusBadge.replaceChildren(
          h("span", { class: "dot", attrs: look.tone === "signal" ? { "data-pulse": "" } : {} }),
          look.label,
        );
        power.textContent = device.running ? "Stop camera" : "Start camera";
        power.className = `btn btn-sm ${device.running ? "btn-secondary" : "btn-primary"}`;

        const stream = device.stream;
        const rows = /** @type {[string, string][]} */ ([
          ["Source", describeSource(device)],
          ["Picture", stream ? `${stream.width} × ${stream.height}` : "No frames yet"],
          [
            "Analysed",
            device.target_fps ? `${device.target_fps} frames a second` : "Hub default rate",
          ],
          ["Keeps events", device.retention_days ? `${device.retention_days} days` : "Hub default"],
          ["Starts with the hub", device.enabled ? "Yes" : "No"],
        ]);
        facts.replaceChildren(
          ...rows.flatMap(([term, value]) => [h("dt", { text: term }), h("dd", { text: value })]),
        );
        // A running camera is already connected, and some sources (USB cameras) can't be
        // opened twice: the test is for cameras that aren't streaming.
        testButton.disabled = device.status === "online";
        // Said in the text, not a tooltip: phones never show tooltips.
        testHint.textContent =
          device.status === "online"
            ? "The camera is streaming, so its connection works. Stop it to test the source on its own."
            : "Opens the camera's source and reads one frame, using its saved credentials.";
      }

      function renderDraft() {
        const changed = JSON.stringify(draft) !== JSON.stringify(device.detection);
        save.disabled = !changed;
        reset.hidden = !changed;
        areasText.textContent = describeAreas(draft.roi);
      }

      // ── Recent events ───────────────────────────────────

      async function loadRecent() {
        try {
          const page = await listEvents({ deviceId: id, limit: RECENT, signal: controller.signal });
          renderRecent(page.items);
        } catch (error) {
          if (!controller.signal.aborted)
            recent.replaceChildren(h("p", { class: "hint", text: describeError(error).title }));
        }
      }

      /** @param {MotionEvent[]} events */
      function renderRecent(events) {
        if (!events.length) {
          recent.replaceChildren(h("p", { class: "hint", text: "No motion recorded yet." }));
          return;
        }
        recent.replaceChildren(
          h(
            "ul",
            { class: "device-recent-grid" },
            events.map((event) => {
              const url = snapshotUrl(event, "thumbnail");
              return h(
                "li",
                {},
                h(
                  "a",
                  {
                    class: "device-recent-item",
                    attrs: {
                      href: `/events?device=${encodeURIComponent(id)}&event=${encodeURIComponent(event.id)}`,
                      "aria-label": `Snapshot at ${formatClock(event.started_at)}`,
                    },
                  },
                  url
                    ? h("img", { attrs: { src: url, alt: "", loading: "lazy", decoding: "async" } })
                    : h("span", {
                        class: "dot",
                        attrs: { "data-tone": "sodium", "data-pulse": "" },
                      }),
                  h(
                    "span",
                    { class: "device-recent-caption" },
                    event.duration_seconds !== null
                      ? `${timeAgo(event.started_at)}, ${formatDuration(event.duration_seconds)}`
                      : timeAgo(event.started_at),
                  ),
                ),
              );
            }),
          ),
        );
      }

      // ── Actions ─────────────────────────────────────────

      /** Counts live status updates, to tell whether one arrived during a request. */
      let statusUpdates = 0;

      /**
       * Applies a device returned by an action. Starting or reloading a camera reports
       * "starting", but "online" can arrive over live events before that response does: a live
       * update that came in meanwhile is newer than the response, so its status wins.
       * @param {number} before statusUpdates when the request was sent
       * @param {Device} result
       */
      function applyResult(before, result) {
        device =
          statusUpdates === before
            ? result
            : { ...result, status: device.status, running: device.running };
        view.update(device);
        renderDevice();
      }

      power.addEventListener("click", async () => {
        const action = device.running ? "stop" : "start";
        power.setAttribute("aria-busy", "true");
        try {
          const before = statusUpdates;
          applyResult(before, await api.post(`${path}/${action}`));
          toast({
            tone: action === "start" ? "signal" : "iris",
            title: action === "start" ? `Starting ${device.name}` : `${device.name} stopped`,
            duration: 3500,
          });
        } catch (error) {
          const { title, detail } = describeError(error);
          toast({ tone: "alarm", title, message: detail });
        } finally {
          power.removeAttribute("aria-busy");
        }
      });

      testButton.addEventListener("click", async () => {
        testButton.setAttribute("aria-busy", "true");
        testResult.textContent = "";
        try {
          /** @type {SourceTestResult} */
          const result = await api.post(`${path}/test`, { timeout: 30_000 });
          testResult.className = `text-sm mt-3 ${result.ok ? "text-signal" : "text-alarm"}`;
          testResult.textContent = result.ok
            ? `Connected in ${Math.round(result.elapsed_ms)} ms${result.width ? `: ${result.width} × ${result.height}` : ""}${result.fps ? ` at ${Math.round(result.fps)} fps` : ""}.`
            : `Couldn't connect: ${result.error ?? "no frame arrived"}.`;
        } catch (error) {
          const { title, detail } = describeError(error);
          testResult.className = "text-sm mt-3 text-alarm";
          testResult.textContent = `${title}. ${detail}`;
        } finally {
          testButton.removeAttribute("aria-busy");
        }
      });

      editAreas.addEventListener("click", () => {
        const on = !roi.editing;
        roi.setEditing(on);
        editAreas.textContent = on ? "Done" : "Edit areas";
        editAreas.setAttribute("aria-pressed", String(on));
      });

      save.addEventListener("click", async () => {
        save.setAttribute("aria-busy", "true");
        try {
          const before = statusUpdates;
          applyResult(before, await api.put(`${path}/detection-config`, { json: draft }));
          draft = structuredClone(device.detection);
          form?.set(draft);
          roi.set(draft.roi);
          renderDraft();
          toast({
            tone: "signal",
            title: "Detection settings saved",
            message: device.running ? "The camera restarts with them now." : undefined,
            duration: 4000,
          });
        } catch (error) {
          const { title, detail } = describeError(error);
          toast({
            tone: "alarm",
            title: `Settings not saved: ${title.toLowerCase()}`,
            message: detail,
          });
        } finally {
          save.removeAttribute("aria-busy");
        }
      });

      reset.addEventListener("click", () => {
        draft = structuredClone(device.detection);
        form?.set(draft);
        roi.set(draft.roi);
        renderDraft();
      });

      // ── Live updates ────────────────────────────────────

      /** @type {ReturnType<typeof setTimeout> | undefined} */
      let refreshRecent;
      cleanups.push(
        realtime.listen((message) => {
          if (!("device_id" in message) || message.device_id !== id) return;
          if (message.type === "device.status") {
            statusUpdates += 1;
            device = {
              ...device,
              status: message.data.status,
              running: message.data.status !== "stopped",
            };
            view.setStatus(message.data.status);
            renderDevice();
          } else if (message.type === "motion.started") {
            view.setMotion(true);
          } else if (message.type === "motion.ended") {
            view.setMotion(false);
            // The snapshot is stored with the end: list it a moment later.
            clearTimeout(refreshRecent);
            refreshRecent = setTimeout(loadRecent, 500);
          }
        }),
        () => clearTimeout(refreshRecent),
        () => roi.destroy(),
        () => view.destroy(),
      );
      // Status changes are not replayed after a reconnect: refetch the camera.
      let realtimeStatus = realtime.state.get().status;
      cleanups.push(
        realtime.state.subscribe(({ status }) => {
          if (status === "live" && realtimeStatus !== "live") {
            api.get(path, { signal: controller.signal }).then(
              (/** @type {Device} */ fresh) => {
                device = fresh;
                view.update(fresh);
                renderDevice();
              },
              () => {},
            );
          }
          realtimeStatus = status;
        }),
      );
      const onVisibility = () => view.sync();
      document.addEventListener("visibilitychange", onVisibility);
      cleanups.push(() => document.removeEventListener("visibilitychange", onVisibility));

      renderDevice();
      renderDraft();
      loadRecent();
    }

    return () => cleanups.forEach((cleanup) => cleanup());
  },
};

/** @type {Record<Device["status"], { tone: string, label: string }>} */
const STATUS = {
  online: { tone: "signal", label: "Live" },
  starting: { tone: "iris", label: "Starting" },
  reconnecting: { tone: "sodium", label: "Reconnecting" },
  failed: { tone: "alarm", label: "Failed" },
  stopped: { tone: "", label: "Stopped" },
};

/** Where the picture comes from, without credentials (the hub never returns them).
 * @param {Device} device */
function describeSource(device) {
  const source = device.source;
  switch (source.kind) {
    case "synthetic":
      return "Simulated camera";
    case "webcam":
      return `USB or built-in camera #${source.index}`;
    case "video_file":
      return `Video file ${source.path.split("/").pop()}`;
    case "rtsp": {
      try {
        const url = new URL(source.url);
        return `Network camera at ${url.hostname}${source.has_password ? ", password saved" : ""}`;
      } catch {
        return "Network camera";
      }
    }
  }
}

function backLink() {
  return h(
    "a",
    { class: "btn btn-ghost btn-sm -ml-2 mb-3", attrs: { href: "/" } },
    icon("back"),
    "All cameras",
  );
}

/**
 * @param {unknown} error
 * @param {string} id
 */
function problem(error, id) {
  const missing = error instanceof Error && "status" in error && error.status === 404;
  const { title, detail } = missing
    ? {
        title: "Camera not found",
        detail: `There's no camera called “${id}”. It may have been removed.`,
      }
    : describeError(error);
  return h(
    "div",
    { class: "camera-empty card" },
    h("span", { class: "camera-empty-icon" }, icon("camera")),
    h("h1", { class: "font-medium outline-none", attrs: { tabindex: "-1" }, text: title }),
    h("p", { class: "text-sm text-haze", text: detail }),
  );
}
