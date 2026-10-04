import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/errors.js";
import { backoff, createRealtime, cursorBefore } from "./socket.js";

/** A WebSocket the test drives: open it, send server messages, close it. */
class FakeSocket extends EventTarget {
  /** @type {FakeSocket[]} */
  static all = [];

  /** @param {string} url */
  constructor(url) {
    super();
    this.url = url;
    this.readyState = 0;
    /** @type {any[]} */
    this.sent = [];
    /** @type {{ code: number, reason: string } | null} */
    this.closedWith = null;
    FakeSocket.all.push(this);
  }

  /** @param {string} data */
  send(data) {
    this.sent.push(JSON.parse(data));
  }

  /** @param {number} code @param {string} reason */
  close(code, reason) {
    this.closedWith = { code, reason };
    this.readyState = 3;
    this.dispatchEvent(new Event("close"));
  }

  // Test controls
  open() {
    this.readyState = 1;
    this.dispatchEvent(new Event("open"));
  }

  /** @param {object} message */
  receive(message) {
    this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(message) }));
  }

  serverClose() {
    this.readyState = 3;
    this.dispatchEvent(new Event("close"));
  }
}

const latest = () => /** @type {FakeSocket} */ (FakeSocket.all.at(-1));

/** UUIDv7-like ids that sort by time, like the hub's. */
const eventId = (/** @type {number} */ ms) => {
  const hex = ms.toString(16).padStart(12, "0");
  return `${hex.slice(0, 8)}-${hex.slice(8)}-7abc-8def-0123456789ab`;
};
const started = (/** @type {string} */ id, replay = false) => ({
  type: "motion.started",
  v: 1,
  ts: "2026-10-04T10:00:00Z",
  device_id: "porch",
  replay,
  data: { event_id: id, started_at: "2026-10-04T10:00:00Z" },
});
const ended = (/** @type {string} */ id, replay = false) => ({
  ...started(id, replay),
  type: "motion.ended",
  data: {
    event_id: id,
    started_at: "2026-10-04T10:00:00Z",
    ended_at: "2026-10-04T10:00:09Z",
    peak_area_ratio: 0.2,
    motion_frames: 40,
    boxes: [],
  },
});

/** @type {import("vitest").Mock} */
let post;
let online = true;

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.all = [];
  online = true;
  let n = 0;
  post = vi.fn(async () => ({ ticket: `t${++n}`, expires_in: 30 }));
});

afterEach(() => {
  vi.useRealTimers();
});

function connect(options = {}) {
  const realtime = createRealtime({
    client: { post },
    url: (ticket) => `ws://hub.test/api/v1/ws/events?ticket=${ticket}`,
    WebSocket: /** @type {any} */ (FakeSocket),
    isOnline: () => online,
    random: () => 0.5, // no jitter: delays are exactly 1 s, 2 s, 4 s...
    ...options,
  });
  /** @type {any[]} */
  const received = [];
  realtime.listen((message) => received.push(message));
  return { realtime, received };
}

/** Lets the ticket request resolve and the socket be created. */
const flush = () => vi.advanceTimersByTimeAsync(0);

describe("connecting", () => {
  it("opens with a fresh ticket and goes live", async () => {
    const { realtime } = connect();

    realtime.start();
    expect(realtime.state.get().status).toBe("connecting");
    await flush();
    expect(post).toHaveBeenCalledWith("/auth/tickets");
    expect(latest().url).toBe("ws://hub.test/api/v1/ws/events?ticket=t1");

    latest().open();
    expect(realtime.state.get()).toMatchObject({ status: "live", retryAt: null });
  });

  it("answers pings with pongs and passes other messages on", async () => {
    const { realtime, received } = connect();
    realtime.start();
    await flush();
    latest().open();

    latest().receive({ type: "ping", v: 1, ts: "2026-10-04T10:00:00Z" });
    latest().receive({
      type: "device.status",
      v: 1,
      ts: "x",
      device_id: "porch",
      data: { status: "online" },
    });

    expect(latest().sent).toEqual([{ type: "pong" }]);
    expect(received.map((m) => m.type)).toEqual(["device.status"]);
  });

  it("keeps a device subscription across reconnects", async () => {
    const { realtime } = connect();
    realtime.start();
    await flush();
    latest().open();
    realtime.subscribe(["porch"]);

    latest().serverClose();
    await vi.advanceTimersByTimeAsync(1000);
    latest().open();

    expect(latest().sent).toContainEqual({ type: "subscribe", devices: ["porch"] });
  });
});

describe("reconnecting", () => {
  it("backs off 1 s, 2 s, 4 s ... with a new ticket each time", async () => {
    const { realtime } = connect();
    realtime.start();
    await flush();

    for (const delay of [1000, 2000, 4000]) {
      latest().serverClose(); // fails before opening, like a refused ticket (4401)
      expect(realtime.state.get().status).toBe("reconnecting");
      const sockets = FakeSocket.all.length;
      await vi.advanceTimersByTimeAsync(delay - 1);
      expect(FakeSocket.all.length).toBe(sockets);
      await vi.advanceTimersByTimeAsync(1);
      expect(FakeSocket.all.length).toBe(sockets + 1);
    }
    expect(post).toHaveBeenCalledTimes(4);
  });

  it("starts backoff over after a connection that stayed up", async () => {
    const { realtime } = connect();
    realtime.start();
    await flush();
    latest().serverClose();
    await vi.advanceTimersByTimeAsync(1000);
    latest().serverClose();
    await vi.advanceTimersByTimeAsync(2000);
    latest().open();
    await vi.advanceTimersByTimeAsync(15_000);
    latest().receive({ type: "ping", v: 1, ts: "x" }); // keep the watchdog quiet

    latest().serverClose();

    expect(realtime.state.get().attempt).toBe(1);
  });

  it("retries ticket failures, but stops when the session is over", async () => {
    const { realtime } = connect();
    post.mockRejectedValueOnce(new ApiError({ kind: "network" }));
    realtime.start();
    await flush();
    expect(realtime.state.get().status).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeSocket.all).toHaveLength(1);

    latest().serverClose();
    post.mockRejectedValueOnce(new ApiError({ kind: "http", status: 401 }));
    await vi.advanceTimersByTimeAsync(2000);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(post).toHaveBeenCalledTimes(3);
    expect(FakeSocket.all).toHaveLength(1);
  });

  it("treats a silent connection as dead", async () => {
    const { realtime } = connect({ staleAfterMs: 50_000 });
    realtime.start();
    await flush();
    latest().open();

    await vi.advanceTimersByTimeAsync(50_000);

    expect(FakeSocket.all[0].closedWith?.code).toBe(4000);
    expect(realtime.state.get().status).toBe("reconnecting");
  });

  it("waits while offline and reconnects at once when back", async () => {
    const { realtime } = connect();
    realtime.start();
    await flush();
    latest().open();

    online = false;
    realtime.drop();
    expect(realtime.state.get().status).toBe("offline");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(post).toHaveBeenCalledTimes(1);

    online = true;
    realtime.retryNow();
    await flush();
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("ignores retries while an attempt is already under way", async () => {
    const { realtime } = connect();
    realtime.start();
    await flush();
    latest().serverClose();

    realtime.retryNow(); // e.g. the browser's "online" event...
    realtime.retryNow(); // ...and the hub monitor seeing the hub again
    realtime.retryNow();
    await flush();

    expect(post).toHaveBeenCalledTimes(2);
    expect(FakeSocket.all).toHaveLength(2);
  });

  it("stop() closes the socket and ignores anything still in flight", async () => {
    const { realtime, received } = connect();
    realtime.start();
    await flush();
    const first = latest();
    first.open();

    realtime.stop();
    first.receive(started(eventId(1)));

    expect(first.closedWith?.code).toBe(1000);
    expect(received).toEqual([]);
    expect(realtime.state.get().status).toBe("stopped");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeSocket.all).toHaveLength(1);
  });
});

describe("replay after a reconnect", () => {
  it("resumes after the last event when nothing is in progress", async () => {
    const { realtime } = connect();
    realtime.start();
    await flush();
    latest().open();
    latest().receive(started(eventId(100)));
    latest().receive(ended(eventId(100)));
    latest().receive(started(eventId(200)));
    latest().receive(ended(eventId(200)));

    latest().serverClose();
    await vi.advanceTimersByTimeAsync(1000);
    latest().open();

    expect(latest().sent).toEqual([{ type: "resume", after: eventId(200) }]);
  });

  it("resumes from before an event that was still in progress", async () => {
    const { realtime } = connect();
    realtime.start();
    await flush();
    latest().open();
    latest().receive(started(eventId(100))); // still going when the connection drops
    latest().receive(started(eventId(200)));
    latest().receive(ended(eventId(200)));

    latest().serverClose();
    await vi.advanceTimersByTimeAsync(1000);
    latest().open();

    const [resume] = latest().sent;
    expect(resume.after < eventId(100)).toBe(true);
    expect(resume.after > eventId(98)).toBe(true); // at most 1 ms of extra replay
  });

  it("passes each start and end on once, however often they are replayed", async () => {
    const { realtime, received } = connect();
    realtime.start();
    await flush();
    latest().open();
    latest().receive(started(eventId(100)));

    latest().serverClose();
    await vi.advanceTimersByTimeAsync(1000);
    latest().open();
    latest().receive(started(eventId(100), true)); // already seen
    latest().receive(ended(eventId(100), true)); // missed while away
    latest().receive({ type: "replay.done", v: 1, ts: "x", data: { count: 2, truncated: false } });
    latest().receive(ended(eventId(100))); // and once more, live

    expect(received.map((m) => `${m.type}${m.replay ? " (replay)" : ""}`)).toEqual([
      "motion.started",
      "motion.ended (replay)",
      "replay.done",
    ]);
  });
});

describe("helpers", () => {
  it("cursorBefore sorts just before the event it was made from", () => {
    const id = "01a10714-b0ab-76bf-a4a8-f5616070e083";
    const cursor = cursorBefore(id);

    expect(cursor).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7000-8000-0{12}$/);
    expect(cursor < id).toBe(true);
    expect(cursor > "01a10714-b0aa-0000-0000-000000000000").toBe(true);
  });

  it("backoff caps at 30 s with ±20 % jitter", () => {
    expect(backoff(1, () => 0.5)).toBe(1000);
    expect(backoff(6, () => 0.5)).toBe(30_000);
    expect(backoff(20, () => 0)).toBe(24_000);
    expect(backoff(20, () => 1)).toBe(36_000);
  });
});
