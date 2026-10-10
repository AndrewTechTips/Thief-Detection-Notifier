// The demo's recording (`vision-hub export-demo`, AD-24): each camera's loop, what the hub
// detected on every analysed frame, and the events it recorded in one pass of the loop.

/** @typedef {import("../api/types.js").DetectionConfig} DetectionConfig */
/** @typedef {import("../realtime/mjpeg.js").Detections} Detections */

/**
 * @typedef {{ path: string, size_bytes: number }} RecordedFile
 * @typedef {{
 *   start: number,
 *   end: number,
 *   peak_area_ratio: number,
 *   motion_frames: number,
 *   person: boolean | null,
 *   person_confidence: number | null,
 *   alert: boolean,
 *   boxes: { x: number, y: number, width: number, height: number }[],
 *   snapshot_at: number,
 *   snapshots: { clean: RecordedFile, annotated: RecordedFile, thumbnail: RecordedFile },
 *   clip: RecordedFile | null,
 * }} RecordedEvent
 * start, end, snapshot_at: seconds into the loop. `end` may pass the loop's end: the event
 * then ends early in the next pass.
 * @typedef {{
 *   id: string,
 *   name: string,
 *   target_fps: number | null,
 *   retention_days: number | null,
 *   source: { kind: "video_file", path: string, loop: boolean },
 *   detection: DetectionConfig,
 *   loop: string,
 *   duration: number,
 *   width: number,
 *   height: number,
 *   frames: [time: number, detections: Detections | null][],
 *   events: RecordedEvent[],
 * }} RecordedCamera
 * @typedef {{ version: 1, cameras: RecordedCamera[] }} Recording
 */

/** Where the recording is published, next to the dashboard. */
export const RECORDING_DIR = `${import.meta.env.BASE_URL}demo/`;

/** @returns {Promise<Recording>} */
export async function loadRecording() {
  const response = await fetch(`${RECORDING_DIR}manifest.json`);
  if (!response.ok) throw new Error(`demo recording: HTTP ${response.status}`);
  /** @type {Recording} */
  const recording = await response.json();
  if (recording.version !== 1) throw new Error("demo recording: unknown format");
  return recording;
}

/**
 * What the hub detected at `time` seconds into the loop: the last analysed frame up to then,
 * or null when it is older than `stale` seconds (or had nothing).
 * @param {RecordedCamera["frames"]} frames sorted by time
 * @param {number} time
 * @param {number} [stale]
 */
export function detectionsAt(frames, time, stale = 0.3) {
  let low = 0;
  let high = frames.length - 1;
  let found = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (frames[middle][0] <= time) {
      found = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  if (found < 0) return null;
  const [at, detections] = frames[found];
  return time - at <= stale ? detections : null;
}

/**
 * Whether playback moving from `previous` to `now` (seconds into a loop of `duration`) passed
 * `mark`, wrapping around the loop's end.
 * @param {number} previous
 * @param {number} now
 * @param {number} mark may be past the loop's end (it then falls in the next pass)
 * @param {number} duration
 */
export function passed(previous, now, mark, duration) {
  const at = mark >= duration ? mark - duration : mark;
  if (now >= previous) return previous < at && at <= now;
  return at > previous || at <= now; // the loop started over
}

/**
 * A UUIDv7 for `ms`: event ids sort by time, as the hub's do (the dashboard's replay relies on it).
 * @param {number} ms
 * @param {() => number} [random]
 */
export function uuid7(ms, random = Math.random) {
  const hex = Math.floor(ms).toString(16).padStart(12, "0");
  const bytes = Array.from({ length: 10 }, () => Math.floor(random() * 256));
  bytes[0] = (bytes[0] & 0x0f) | 0x70; // version 7
  bytes[2] = (bytes[2] & 0x3f) | 0x80; // RFC 4122 variant
  const rest = bytes.map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${rest.slice(0, 4)}-${rest.slice(4, 8)}-${rest.slice(8, 20)}`;
}
