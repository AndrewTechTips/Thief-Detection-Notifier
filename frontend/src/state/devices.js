// Camera directory: names for the ids that live events carry. Filled by whichever view loads
// the device list first, and loaded on demand when an unknown camera shows up.

import { listDevices } from "../api/devices.js";
import { Store } from "./store.js";

/** @typedef {import("../api/types.js").Device} Device */

/** @type {Store<Map<string, Device>>} */
export const devices = new Store(new Map());

/** @type {Promise<void> | null} */
let loading = null;
/** The whole list has been loaded at least once (not just single cameras remembered). */
let complete = false;

/** Replaces the directory with a full list of cameras.
 * @param {Device[]} list */
export function rememberDevices(list) {
  complete = true;
  devices.set(new Map(list.map((device) => [device.id, device])));
}

export function directoryLoaded() {
  return complete;
}

/** Loads the directory once (concurrent callers share the request). */
export function loadDevices() {
  loading ??= listDevices()
    .then(rememberDevices)
    .finally(() => {
      loading = null;
    });
  return loading;
}

/**
 * The camera's name, falling back to its id until the directory knows it.
 * @param {string} id
 */
export function deviceName(id) {
  const device = devices.get().get(id);
  if (!device && !loading) loadDevices().catch(() => {});
  return device?.name ?? id;
}

/**
 * What the camera alerts on: any motion, or people only (its detection settings). Unknown
 * cameras alert on motion: better a toast too many than a missed one.
 * @param {string} id
 * @returns {"motion" | "person"}
 */
export function alertsOn(id) {
  return devices.get().get(id)?.detection.alert_on ?? "motion";
}

/** Adds or replaces one camera (e.g. after loading or changing it).
 * @param {Device} device */
export function rememberDevice(device) {
  const next = new Map(devices.get());
  next.set(device.id, device);
  devices.set(next);
}
