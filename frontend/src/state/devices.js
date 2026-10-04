// Camera directory: names for the ids that live events carry. Filled by whichever view loads
// the device list first, and loaded on demand when an unknown camera shows up.

import { listDevices } from "../api/devices.js";
import { Store } from "./store.js";

/** @typedef {import("../api/types.js").Device} Device */

/** @type {Store<Map<string, Device>>} */
export const devices = new Store(new Map());

/** @type {Promise<void> | null} */
let loading = null;

/** @param {Device[]} list */
export function rememberDevices(list) {
  devices.set(new Map(list.map((device) => [device.id, device])));
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
