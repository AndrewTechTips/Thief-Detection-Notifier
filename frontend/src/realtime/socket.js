// Live events from the hub (WS /api/v1/ws/events, protocol in docs/api-conventions.md).
//
// Each connection uses a fresh single-use ticket. Dropped connections come back with backoff
// and jitter, and ask the hub to replay what was missed ("resume"). Replay is keyed on event ids
// ("everything after X"), so the cursor sits just before the oldest event still in progress:
// an event that started before a drop and ended during it is replayed as ended. Duplicates are
// filtered, so listeners see each start and end once.

import { ApiError } from "../api/errors.js";
import { Store } from "../state/store.js";

/** @typedef {import("../api/types.js").ServerMessage} ServerMessage */
/** @typedef {import("../api/types.js").ClientMessage} ClientMessage */
/** @typedef {import("../api/types.js").Ticket} Ticket */
/**
 * connecting: first connection. live: connected. reconnecting: waiting to retry. offline: the
 * browser has no network. stopped: not running (signed out).
 * @typedef {"connecting" | "live" | "reconnecting" | "offline" | "stopped"} RealtimeStatus
 * @typedef {{ status: RealtimeStatus, attempt: number, retryAt: number | null }} RealtimeState
 */
/**
 * @typedef {{
 *   post: (path: string, options?: object) => Promise<any>,
 * }} TicketClient
 * @typedef {{
 *   client: TicketClient,
 *   url: (ticket: string) => string,
 *   WebSocket?: typeof WebSocket,
 *   isOnline?: () => boolean,
 *   staleAfterMs?: number,
 *   random?: () => number,
 * }} RealtimeOptions
 */

const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 30_000;
/** A connection that lasted this long was healthy: the next drop starts backoff from scratch. */
const HEALTHY_AFTER_MS = 10_000;
/** The hub pings every 20 s; this much silence means the connection is dead (e.g. after sleep). */
const STALE_AFTER_MS = 50_000;
/** Events in progress longer than this are assumed lost (e.g. interrupted by a hub crash). */
const OPEN_EVENT_MAX_AGE_MS = 30 * 60_000;
const SEEN_LIMIT = 512;

/**
 * @param {RealtimeOptions} options
 */
export function createRealtime({
  client,
  url,
  WebSocket: Socket,
  isOnline = () => globalThis.navigator?.onLine ?? true,
  staleAfterMs = STALE_AFTER_MS,
  random = Math.random,
}) {
  const state = new Store(
    /** @type {RealtimeState} */ ({ status: "stopped", attempt: 0, retryAt: null }),
  );
  /** @type {Set<(message: ServerMessage) => void>} */
  const listeners = new Set();

  /** @type {WebSocket | null} */
  let socket = null;
  let running = false;
  let generation = 0; // bumps on every (re)connect, so late callbacks from old sockets are ignored
  let inFlight = -1; // the generation of the attempt still fetching a ticket or opening
  let openedAt = 0;
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let retryTimer;
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let staleTimer;
  /** @type {string[] | null} null: every device */
  let devices = null;

  // Replay bookkeeping
  /** @type {string | null} */
  let lastEventId = null;
  /** @type {Map<string, number>} event id -> when we learned it started */
  const openEvents = new Map();
  /** @type {Set<string>} "type:event_id", insertion-ordered so the oldest can be evicted */
  const seen = new Set();

  /** @param {Partial<RealtimeState>} patch */
  function setState(patch) {
    state.set({ ...state.get(), ...patch });
  }

  function start() {
    if (running) return;
    running = true;
    setState({ status: "connecting", attempt: 0, retryAt: null });
    connect();
  }

  function stop() {
    running = false;
    clearTimeout(retryTimer);
    disconnect(1000, "stopped");
    lastEventId = null;
    openEvents.clear();
    seen.clear();
    setState({ status: "stopped", attempt: 0, retryAt: null });
  }

  /** Skip the backoff and try now (the hub is back, the tab is visible again...). */
  function retryNow() {
    const { status } = state.get();
    if (!running || status === "live" || inFlight === generation) return;
    if (!isOnline()) return;
    clearTimeout(retryTimer);
    connect();
  }

  async function connect() {
    const current = ++generation;
    inFlight = current;
    clearTimeout(retryTimer);
    if (!isOnline()) {
      setState({ status: "offline", retryAt: null });
      return;
    }
    if (state.get().status !== "connecting") setState({ status: "reconnecting", retryAt: null });

    /** @type {Ticket} */
    let ticket;
    try {
      ticket = await client.post("/auth/tickets");
    } catch (error) {
      if (current !== generation || !running) return;
      inFlight = -1;
      // 401 here means the session is over: the API client has already signed out.
      if (error instanceof ApiError && error.status === 401) return;
      scheduleRetry();
      return;
    }
    if (current !== generation || !running) return;

    // Looked up per connection, not once: the public demo swaps in its own (demo/hub.js).
    const ws = new (Socket ?? globalThis.WebSocket)(url(ticket.ticket));
    socket = ws;
    ws.addEventListener("open", () => {
      if (current !== generation) return;
      inFlight = -1;
      openedAt = Date.now();
      setState({ status: "live", retryAt: null });
      if (devices) send({ type: "subscribe", devices });
      const after = resumeCursor();
      if (after) send({ type: "resume", after });
      watchdog();
    });
    ws.addEventListener("message", (event) => {
      if (current !== generation) return;
      watchdog();
      receive(event.data);
    });
    ws.addEventListener("close", () => {
      if (current !== generation) return;
      inFlight = -1;
      socket = null;
      clearTimeout(staleTimer);
      // Rejected tickets (4401) close before the socket opens and look like any other failure:
      // every attempt fetches a new ticket anyway.
      if (running) scheduleRetry();
    });
  }

  function scheduleRetry() {
    const healthy = openedAt && Date.now() - openedAt > HEALTHY_AFTER_MS;
    const attempt = healthy ? 1 : state.get().attempt + 1;
    openedAt = 0;
    if (!isOnline()) {
      setState({ status: "offline", attempt, retryAt: null });
      return;
    }
    const delay = backoff(attempt, random);
    setState({ status: "reconnecting", attempt, retryAt: Date.now() + delay });
    retryTimer = setTimeout(connect, delay);
  }

  /**
   * @param {number} code
   * @param {string} reason
   */
  function disconnect(code, reason) {
    generation += 1;
    clearTimeout(staleTimer);
    const ws = socket;
    socket = null;
    if (ws && ws.readyState <= 1) ws.close(code, reason);
  }

  /** No message for too long: the connection is dead even if the browser hasn't noticed. */
  function watchdog() {
    clearTimeout(staleTimer);
    staleTimer = setTimeout(() => {
      disconnect(4000, "no heartbeat");
      if (running) scheduleRetry();
    }, staleAfterMs);
  }

  /** @param {ClientMessage} message */
  function send(message) {
    if (socket?.readyState === 1) socket.send(JSON.stringify(message));
  }

  /** @param {unknown} data */
  function receive(data) {
    /** @type {ServerMessage} */
    let message;
    try {
      message = JSON.parse(String(data));
    } catch {
      return; // not ours to handle; the hub only sends JSON
    }
    switch (message.type) {
      case "ping":
        send({ type: "pong" });
        return;
      case "motion.started":
      case "motion.ended": {
        const id = message.data.event_id;
        const key = `${message.type}:${id}`;
        if (seen.has(key)) return; // replayed again after a reconnect
        remember(key);
        if (!lastEventId || id > lastEventId) lastEventId = id;
        if (message.type === "motion.started") {
          if (!seen.has(`motion.ended:${id}`)) openEvents.set(id, Date.now());
        } else {
          openEvents.delete(id);
        }
        break;
      }
    }
    for (const listener of [...listeners]) listener(message);
  }

  /** @param {string} key */
  function remember(key) {
    seen.add(key);
    if (seen.size > SEEN_LIMIT) seen.delete(/** @type {string} */ (seen.values().next().value));
  }

  /** Where to resume: just before the oldest event still in progress, else after the last one. */
  function resumeCursor() {
    const now = Date.now();
    for (const [id, since] of openEvents) {
      if (now - since > OPEN_EVENT_MAX_AGE_MS) openEvents.delete(id);
    }
    const oldestOpen = [...openEvents.keys()].sort()[0];
    return oldestOpen ? cursorBefore(oldestOpen) : lastEventId;
  }

  return {
    state,
    start,
    stop,
    retryNow,
    /**
     * Listens to every server message (motion, device status, subscription, replay.done...).
     * @param {(message: ServerMessage) => void} listener
     */
    listen(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    /**
     * Limits events to some devices (null: all). Kept across reconnects.
     * @param {string[] | null} next
     */
    subscribe(next) {
      devices = next;
      send({ type: "subscribe", devices: next });
    },
    /** Drops the connection as if the network failed (tests and diagnostics). */
    drop() {
      disconnect(4000, "dropped");
      if (running) scheduleRetry();
    },
  };
}

/** @typedef {ReturnType<typeof createRealtime>} Realtime */

/**
 * Exponential backoff with ±20 % jitter: 1 s, 2 s, 4 s ... up to 30 s.
 * @param {number} attempt 1 for the first retry
 * @param {() => number} [random]
 */
export function backoff(attempt, random = Math.random) {
  const base = Math.min(RETRY_MAX_MS, RETRY_MIN_MS * 2 ** (attempt - 1));
  return Math.round(base * (0.8 + random() * 0.4));
}

/**
 * An id that sorts just before a UUIDv7 event id: its timestamp minus 1 ms, the rest zero. The
 * hub replays events with ids greater than the cursor, so this includes the event itself.
 * @param {string} eventId
 */
export function cursorBefore(eventId) {
  const hex = eventId.replaceAll("-", "");
  const ms = Number.parseInt(hex.slice(0, 12), 16);
  const before = Math.max(0, ms - 1)
    .toString(16)
    .padStart(12, "0");
  return `${before.slice(0, 8)}-${before.slice(8, 12)}-7000-8000-000000000000`;
}
