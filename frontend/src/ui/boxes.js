// "Motion boxes": one remembered choice for the live views and the event viewer, so turning the
// boxes off in one place turns them off everywhere, including views already on screen.

const KEY = "vision-hub.snapshot-boxes";
const changes = new EventTarget();

/** @returns {boolean} whether boxes are shown (they are, unless turned off) */
export function boxesShown() {
  try {
    return localStorage.getItem(KEY) !== "false";
  } catch {
    return true;
  }
}

/** @param {boolean} value */
export function showBoxes(value) {
  try {
    localStorage.setItem(KEY, String(value));
  } catch {
    // preference not remembered, but still applied to this page
  }
  changes.dispatchEvent(new CustomEvent("change", { detail: value }));
}

/**
 * @param {(shown: boolean) => void} listener
 * @returns {() => void} stops listening
 */
export function onBoxesChange(listener) {
  const handler = (/** @type {Event} */ event) =>
    listener(/** @type {CustomEvent<boolean>} */ (event).detail);
  changes.addEventListener("change", handler);
  return () => changes.removeEventListener("change", handler);
}
