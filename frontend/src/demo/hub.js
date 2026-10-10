// The public demo (AD-24): the real dashboard, talking to a hub that lives in this browser tab.
// It answers the API, the WebSocket and the live MJPEG streams from what the real hub recorded on
// the demo cameras (`vision-hub export-demo`). Responses are typed against the hub's OpenAPI
// schema, so the demo can't drift from the API without the type check noticing.
//
// Settings changes and camera starts and stops work, in memory, until the page reloads.

import { Camera } from "./cameras.js";
import { loadRecording, uuid7 } from "./recording.js";
import { DemoSocket } from "./socket.js";
import { snapshotResponse, streamResponse } from "./stream.js";

/** @typedef {import("../api/types.js").Device} Device */
/** @typedef {import("../api/types.js").MotionEvent} MotionEvent */
/** @typedef {import("../api/types.js").AuditEntry} AuditEntry */
/** @typedef {import("../api/types.js").ServerMessage} ServerMessage */
/** @typedef {import("../api/types.js").TokenResponse} TokenResponse */
/** @typedef {import("../api/types.js").DetectionConfig} DetectionConfig */
/** @typedef {import("../api/types.js").Readiness} Readiness */
/** @typedef {import("../api/types.js").Principal} Principal */
/** @typedef {import("../api/types.js").Ticket} Ticket */
/** @typedef {import("../api/types.js").SourceTestResult} SourceTestResult */
/** @typedef {import("../api/types.js").DevicePage} DevicePage */

const API = "/api/v1";
const USER = "visitor";
const TOKEN_SECONDS = 15 * 60;
/** Where each camera's loop begins, as a lead (seconds) before its first event: something
 * happens on every camera within a few seconds of opening the demo. */
const LEADS = [2, 6, 3.5, 1.5];

export async function startDemoHub() {
  const recording = await loadRecording();
  /** @type {Set<DemoSocket>} */
  const sockets = new Set();
  /** @type {MotionEvent[]} newest first */
  const events = [];
  /** @type {AuditEntry[]} newest first */
  const audit = [];
  const started = Date.now();

  /** @param {ServerMessage} message */
  const broadcast = (message) => {
    for (const socket of sockets) socket.deliver(message);
  };

  const cameras = recording.cameras.map((recorded, index) => {
    const first = recorded.events[0]?.start ?? 0;
    const startAt = Math.max(0, first - LEADS[index % LEADS.length]);
    const camera = new Camera(recorded, startAt, {
      started(camera, id, startedAt) {
        broadcast({
          v: 1,
          ts: startedAt,
          type: "motion.started",
          device_id: camera.id,
          replay: false,
          data: { event_id: id, started_at: startedAt },
        });
      },
      ended(camera, event) {
        events.unshift(event);
        broadcast({
          v: 1,
          ts: event.ended_at ?? event.started_at,
          type: "motion.ended",
          device_id: camera.id,
          replay: false,
          data: {
            event_id: event.id,
            started_at: event.started_at,
            ended_at: event.ended_at,
            peak_area_ratio: event.peak_area_ratio,
            motion_frames: event.motion_frames,
            boxes: event.boxes,
            person: event.person,
            person_confidence: event.person_confidence,
            alert: event.alert,
          },
        });
      },
      status(camera) {
        broadcast({
          v: 1,
          ts: new Date().toISOString(),
          type: "device.status",
          device_id: camera.id,
          data: { status: camera.status },
        });
      },
    });
    events.push(...camera.history(started, startAt));
    return camera;
  });
  events.sort((a, b) => (a.started_at < b.started_at ? 1 : -1));
  const byId = new Map(cameras.map((camera) => [camera.id, camera]));
  // What happened before the visit: the cameras were set up to alert on people only.
  for (const [index, camera] of cameras.entries()) {
    const fields = ["detection.alert_on"];
    audit.push(
      entry(
        "device.updated",
        "device",
        camera.id,
        { fields },
        started - (55 + index) * 60_000,
        "admin",
      ),
    );
  }
  audit.sort((a, b) => (a.at < b.at ? 1 : -1));

  /** @param {string} action @param {"device" | "user"} type @param {string} target */
  function record(action, type, target, details = {}) {
    audit.unshift(entry(action, type, target, details, Date.now()));
  }

  /** @param {Camera} camera @returns {Device} */
  function deviceOut(camera) {
    const last = events.find((event) => event.device_id === camera.id);
    const { recording: recorded } = camera;
    return {
      id: camera.id,
      name: camera.name,
      enabled: true,
      target_fps: recorded.target_fps,
      retention_days: recorded.retention_days,
      source: recorded.source,
      detection: camera.detection,
      status: camera.status,
      running: camera.running,
      stream:
        camera.status === "stopped"
          ? null
          : { width: recorded.width, height: recorded.height, last_frame_at: camera.lastFrameAt },
      last_event: last
        ? {
            id: last.id,
            started_at: last.started_at,
            ended_at: last.ended_at,
            peak_area_ratio: last.peak_area_ratio,
          }
        : null,
    };
  }

  /**
   * @param {Request} request
   * @returns {Promise<Response>}
   */
  async function handle(request) {
    const url = new URL(request.url);
    const path = url.pathname.slice(API.length);
    const method = request.method;
    const query = url.searchParams;
    /** @type {RegExpExecArray | null} */
    let found;

    if (path === "/health/live") return json({ status: "ok" });
    if (path === "/health/ready") {
      return json(
        /** @satisfies {Readiness} */ ({
          status: "ok",
          checks: [
            { name: "database", healthy: true, duration_ms: 1.2 },
            { name: "cameras", healthy: true, duration_ms: 0.1 },
          ],
        }),
      );
    }
    if (path === "/auth/token" && method === "POST") {
      const form = new URLSearchParams(await request.text());
      return json(tokens(form.get("username") || USER));
    }
    if (path === "/auth/refresh" && method === "POST") return json(tokens(USER));
    if (path === "/auth/logout" && method === "POST") return new Response(null, { status: 204 });
    if (path === "/auth/me")
      return json(/** @satisfies {Principal} */ ({ username: USER, role: "admin" }));
    if (path === "/auth/tickets" && method === "POST") {
      return json(/** @satisfies {Ticket} */ ({ ticket: uuid7(Date.now()), expires_in: 30 }));
    }
    if (path === "/push") return problem(404, "Not Found", "Notifications are off in the demo.");
    if (path === "/devices" && method === "GET") {
      return json(
        /** @satisfies {DevicePage} */ ({ items: cameras.map(deviceOut), next_cursor: null }),
      );
    }
    if ((found = /^\/devices\/([^/]+)(\/[a-z-]+)?$/.exec(path))) {
      const camera = byId.get(decodeURIComponent(found[1]));
      if (!camera) return problem(404, "Not Found", "No such camera.");
      const action = found[2] ?? "";
      if (action === "" && method === "GET") return json(deviceOut(camera));
      if (action === "/stream") {
        return streamResponse(camera, {
          fps: Number(query.get("fps")) || undefined,
          signal: request.signal,
        });
      }
      if (action === "/snapshot") return snapshotResponse(camera);
      if (action === "/stop" && method === "POST") {
        camera.stop();
        record("device.stopped", "device", camera.id);
        return json(deviceOut(camera));
      }
      if (action === "/start" && method === "POST") {
        record("device.started", "device", camera.id);
        void camera.start();
        return json(deviceOut(camera));
      }
      if (action === "/detection-config" && method === "PUT") {
        /** @type {DetectionConfig} */
        const next = await request.json();
        const fields = Object.keys(next).filter(
          (key) =>
            JSON.stringify(next[/** @type {keyof DetectionConfig} */ (key)]) !==
            JSON.stringify(camera.detection[/** @type {keyof DetectionConfig} */ (key)]),
        );
        camera.detection = { ...camera.detection, ...next };
        record("device.updated", "device", camera.id, {
          fields: fields.map((f) => `detection.${f}`),
        });
        return json(deviceOut(camera));
      }
      if (action === "/test" && method === "POST") {
        const { width, height } = camera.recording;
        return json(
          /** @satisfies {SourceTestResult} */ ({
            ok: true,
            elapsed_ms: 38,
            width,
            height,
            fps: 15,
            error: null,
          }),
        );
      }
      return problem(405, "Method Not Allowed", "The demo can't do that.");
    }
    if (path === "/events" && method === "GET") {
      return json(page(events.filter(eventFilter(query)), query));
    }
    if ((found = /^\/events\/([^/]+)$/.exec(path))) {
      const event = events.find((item) => item.id === decodeURIComponent(found?.[1] ?? ""));
      return event ? json(event) : problem(404, "Not Found", "No such event.");
    }
    if (path === "/audit" && method === "GET") {
      const target = query.get("target_type");
      return json(
        page(
          audit.filter((item) => !target || item.target_type === target),
          query,
        ),
      );
    }
    return problem(404, "Not Found", "The demo hub doesn't have this.");
  }

  // ── Install: fetch and WebSocket for /api/v1, everything else untouched ──
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (url.origin !== location.origin || !url.pathname.startsWith(`${API}/`)) {
      return nativeFetch(input, init);
    }
    await new Promise((resolve) => setTimeout(resolve, 40)); // a little network latency
    return handle(request);
  };

  const NativeSocket = window.WebSocket;
  window.WebSocket = /** @type {typeof WebSocket} */ (
    /** @type {unknown} */ (
      new Proxy(NativeSocket, {
        construct(target, args) {
          const url = new URL(String(args[0]), location.href);
          if (!url.pathname.startsWith(`${API}/ws/`)) return Reflect.construct(target, args);
          return new DemoSocket(url.href, {
            events: () => events,
            devices: () => cameras.map((camera) => camera.id),
            connect(socket) {
              sockets.add(socket);
              return () => sockets.delete(socket);
            },
          });
        },
      })
    )
  );

  for (const camera of cameras) void camera.start();
}

/** @param {string} username @returns {TokenResponse} */
function tokens(username) {
  const exp = Math.floor(Date.now() / 1000) + TOKEN_SECONDS;
  const access = [
    { alg: "none", typ: "JWT" },
    { sub: username, role: "admin", exp },
  ]
    .map((part) =>
      btoa(JSON.stringify(part)).replaceAll("=", "").replaceAll("+", "-").replaceAll("/", "_"),
    )
    .join(".");
  return {
    access_token: `${access}.demo`,
    token_type: "bearer",
    expires_in: TOKEN_SECONDS,
    refresh_token: `demo-${uuid7(Date.now())}`,
    refresh_expires_in: 7 * 24 * 3600,
  };
}

/**
 * @param {string} action
 * @param {"device" | "user"} type
 * @param {string} target
 * @param {Record<string, unknown>} details
 * @param {number} at
 * @param {string} [actor]
 * @returns {AuditEntry}
 */
function entry(action, type, target, details, at, actor = USER) {
  return {
    id: uuid7(at),
    at: new Date(at).toISOString(),
    actor,
    action,
    target_type: type,
    target_id: target,
    details,
    request_id: null,
  };
}

/** @param {URLSearchParams} query @returns {(event: MotionEvent) => boolean} */
function eventFilter(query) {
  const devices = query.getAll("device_id");
  const since = query.get("since");
  const until = query.get("until");
  const person = query.get("person");
  return (event) =>
    (devices.length === 0 || devices.includes(event.device_id)) &&
    (!since || event.started_at >= new Date(since).toISOString()) &&
    (!until || event.started_at < new Date(until).toISOString()) &&
    (person === null || String(event.person === true) === person);
}

/**
 * One page of a newest-first list. The cursor is the last id of the previous page, so new items
 * arriving at the front never shift the pages after it.
 * @template {{ id: string }} T
 * @param {T[]} items
 * @param {URLSearchParams} query
 * @returns {{ items: T[], next_cursor: string | null }}
 */
function page(items, query) {
  const limit = Math.min(Number(query.get("limit")) || 50, 200);
  const cursor = query.get("cursor");
  const offset = cursor ? items.findIndex((item) => item.id === cursor) + 1 : 0;
  const slice = items.slice(offset, offset + limit);
  const more = offset + limit < items.length;
  return { items: slice, next_cursor: more ? (slice.at(-1)?.id ?? null) : null };
}

/** @param {unknown} body */
function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** @param {number} status @param {string} title @param {string} detail */
function problem(status, title, detail) {
  return new Response(
    JSON.stringify({
      type: "about:blank",
      title,
      status,
      detail,
      instance: null,
      request_id: null,
    }),
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}
