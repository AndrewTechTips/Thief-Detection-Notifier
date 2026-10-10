// Where the dashboard lives. Normally at the root of the hub's address; the public demo is
// served from a subdirectory (GitHub Pages: /iot-vision-hub/). Routes and links are written as
// app paths ("/devices/porch"); these turn them into address-bar paths and back.

const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

/**
 * The address-bar path of an app path: "/events" becomes "/iot-vision-hub/events" in the demo.
 * @param {string} path
 */
export function href(path) {
  return path.startsWith("/") ? BASE + path : path;
}

/**
 * The app path of an address-bar path, or null when it lies outside the dashboard (another
 * site on the same origin).
 * @param {string} pathname
 */
export function appPath(pathname) {
  if (!BASE) return pathname;
  if (pathname === BASE) return "/";
  return pathname.startsWith(`${BASE}/`) ? pathname.slice(BASE.length) : null;
}
