// Event endpoints used by the dashboard.

import { api } from "./client.js";

/** @typedef {import("./types.js").MotionEvent} MotionEvent */
/** @typedef {import("./types.js").EventPage} EventPage */
/** @typedef {import("./types.js").Schemas["SnapshotKind"]} SnapshotKind */

/**
 * One page of events, newest first.
 * @param {{
 *   deviceId?: string | null,
 *   since?: Date | null,
 *   until?: Date | null,
 *   cursor?: string | null,
 *   limit?: number,
 *   signal?: AbortSignal,
 * }} [options]
 * @returns {Promise<EventPage>}
 */
export function listEvents({ deviceId, since, until, cursor, limit = 30, signal } = {}) {
  return api.get("/events", {
    query: { device_id: deviceId, since, until, cursor, limit },
    signal,
  });
}

/**
 * @param {string} id
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<MotionEvent>}
 */
export function getEvent(id, { signal } = {}) {
  return api.get(`/events/${encodeURIComponent(id)}`, { signal });
}

/**
 * The event's signed snapshot link of a kind, usable directly in an <img>.
 * @param {MotionEvent} event
 * @param {SnapshotKind} kind
 */
export function snapshotUrl(event, kind) {
  return event.snapshots.find((snapshot) => snapshot.kind === kind)?.url ?? null;
}
