// The demo hub's side of the realtime protocol (WS /api/v1/ws/events, docs/api-conventions.md):
// a WebSocket look-alike the dashboard's realtime client talks to without knowing.

/** @typedef {import("../api/types.js").ServerMessage} ServerMessage */
/** @typedef {import("../api/types.js").ClientMessage} ClientMessage */
/** @typedef {import("../api/types.js").MotionEvent} MotionEvent */

const PING_MS = 20_000;

/**
 * @typedef {{
 *   events: () => MotionEvent[],
 *   devices: () => string[],
 *   connect: (socket: DemoSocket) => () => void,
 * }} SocketHub
 * events: every recorded event, newest first. connect: registers the socket for broadcasts and
 * returns how to unregister it.
 */

export class DemoSocket extends EventTarget {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  /**
   * @param {string} url
   * @param {SocketHub} hub
   */
  constructor(url, hub) {
    super();
    this.url = url;
    this.hub = hub;
    this.readyState = DemoSocket.CONNECTING;
    /** @type {Set<string> | null} null: every device */
    this.devices = null;
    /** @type {() => void} */
    this.disconnect = () => {};
    /** @type {ReturnType<typeof setInterval> | undefined} */
    this.pinger = undefined;
    setTimeout(() => {
      if (this.readyState !== DemoSocket.CONNECTING) return;
      this.readyState = DemoSocket.OPEN;
      this.disconnect = hub.connect(this);
      this.pinger = setInterval(() => this.deliver({ v: 1, ts: now(), type: "ping" }), PING_MS);
      this.dispatchEvent(new Event("open"));
    }, 30);
  }

  /** @param {string} data */
  send(data) {
    if (this.readyState !== DemoSocket.OPEN) return;
    /** @type {ClientMessage} */
    const message = JSON.parse(data);
    if (message.type === "subscribe") {
      this.devices = message.devices ? new Set(message.devices) : null;
      const known = this.hub.devices();
      this.deliver({
        v: 1,
        ts: now(),
        type: "subscription",
        data: {
          devices: message.devices ?? known,
          excluded: (message.devices ?? []).filter((id) => !known.includes(id)),
        },
      });
    } else if (message.type === "unsubscribe") {
      for (const id of message.devices) this.devices?.delete(id);
    } else if (message.type === "resume") {
      this.replay(message.after);
    }
  }

  /**
   * Everything after the cursor, oldest first, like the hub's replay.
   * @param {string} after an event id
   */
  replay(after) {
    const missed = this.hub
      .events()
      .filter((event) => event.id > after && this.wants(event.device_id))
      .reverse();
    for (const event of missed) {
      this.deliver({
        v: 1,
        ts: now(),
        type: "motion.ended",
        device_id: event.device_id,
        replay: true,
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
    }
    this.deliver({
      v: 1,
      ts: now(),
      type: "replay.done",
      data: { count: missed.length, truncated: false },
    });
  }

  /** @param {string} deviceId */
  wants(deviceId) {
    return !this.devices || this.devices.has(deviceId);
  }

  /** @param {ServerMessage} message */
  deliver(message) {
    if (this.readyState !== DemoSocket.OPEN) return;
    if ("device_id" in message && !this.wants(message.device_id)) return;
    const data = JSON.stringify(message);
    setTimeout(() => this.dispatchEvent(new MessageEvent("message", { data })), 0);
  }

  close(code = 1000, reason = "") {
    if (this.readyState >= DemoSocket.CLOSING) return;
    this.readyState = DemoSocket.CLOSED;
    clearInterval(this.pinger);
    this.disconnect();
    this.dispatchEvent(new CloseEvent("close", { code, reason, wasClean: true }));
  }
}

function now() {
  return new Date().toISOString();
}
