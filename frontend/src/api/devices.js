// Device endpoints used by the dashboard.

import { api } from "./client.js";

/** @typedef {import("./types.js").Device} Device */
/** @typedef {import("./types.js").DevicePage} DevicePage */

const PAGE_SIZE = 100;

/**
 * Every device, following the cursor through all pages.
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<Device[]>}
 */
export async function listDevices({ signal } = {}) {
  /** @type {Device[]} */
  const devices = [];
  /** @type {string | null} */
  let cursor = null;
  do {
    /** @type {DevicePage} */
    const page = await api.get("/devices", { query: { limit: PAGE_SIZE, cursor }, signal });
    devices.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return devices;
}

/**
 * A live MJPEG stream URL with a fresh single-use ticket.
 * @param {string} deviceId
 * @param {{ fps?: number }} [options]
 */
export async function streamUrl(deviceId, { fps } = {}) {
  /** @type {import("./types.js").Ticket} */
  const { ticket } = await api.post("/auth/tickets");
  const url = new URL(`/api/v1/devices/${encodeURIComponent(deviceId)}/stream`, location.href);
  url.searchParams.set("ticket", ticket);
  if (fps) url.searchParams.set("fps", String(fps));
  return url.href;
}
