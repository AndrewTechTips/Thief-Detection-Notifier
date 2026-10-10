// The demo's cameras: each one a looping video in this tab, its recorded events played back in
// step with the picture. Events become history once they end, like on the hub.

import { RECORDING_DIR, detectionsAt, passed, uuid7 } from "./recording.js";

/** @typedef {import("./recording.js").RecordedCamera} RecordedCamera */
/** @typedef {import("./recording.js").RecordedEvent} RecordedEvent */
/** @typedef {import("../api/types.js").MotionEvent} MotionEvent */
/** @typedef {import("../api/types.js").DeviceStatus} DeviceStatus */

/** How much history the demo starts with. */
const HISTORY_MS = 90 * 60_000;
const TICK_MS = 100;

/**
 * @typedef {{
 *   started: (camera: Camera, id: string, startedAt: string) => void,
 *   ended: (camera: Camera, event: MotionEvent) => void,
 *   status: (camera: Camera) => void,
 * }} CameraListener
 */

export class Camera {
  /**
   * @param {RecordedCamera} recording
   * @param {number} startAt seconds into the loop where playback begins
   * @param {CameraListener} listener
   */
  constructor(recording, startAt, listener) {
    this.recording = recording;
    this.listener = listener;
    /** @type {DeviceStatus} */
    this.status = "starting";
    this.detection = recording.detection;
    this.name = recording.name;
    /** @type {{ id: string, startedAt: string, recorded: RecordedEvent } | null} */
    this.open = null;
    this.video = document.createElement("video");
    this.video.muted = true;
    this.video.loop = true;
    this.video.playsInline = true;
    this.video.preload = "auto";
    this.video.src = RECORDING_DIR + recording.loop;
    this.video.currentTime = startAt;
    this.previous = startAt;
    this.lastFrameAt = new Date().toISOString();
    /** @type {ReturnType<typeof setInterval> | undefined} */
    this.timer = undefined;
  }

  get id() {
    return this.recording.id;
  }

  get time() {
    return this.video.currentTime;
  }

  get running() {
    return this.status !== "stopped";
  }

  /** What the hub detected on the frame now showing (null: nothing, or the camera is off). */
  detections() {
    if (this.status !== "online") return null;
    return detectionsAt(this.recording.frames, this.time);
  }

  async start() {
    this.setStatus("starting");
    try {
      await this.video.play();
    } catch {
      // Autoplay refused (rare for muted video): the first interaction starts it.
      const resume = () => this.video.play().catch(() => {});
      document.addEventListener("pointerdown", resume, { once: true });
    }
    this.previous = this.time;
    clearInterval(this.timer);
    this.timer = setInterval(() => this.tick(), TICK_MS);
    this.setStatus("online");
  }

  stop() {
    clearInterval(this.timer);
    this.video.pause();
    if (this.open) this.finish(new Date()); // the hub closes an open event when a camera stops
    this.setStatus("stopped");
  }

  /** @param {DeviceStatus} status */
  setStatus(status) {
    if (status === this.status) return;
    this.status = status;
    this.listener.status(this);
  }

  tick() {
    const now = this.time;
    const { duration, events } = this.recording;
    this.lastFrameAt = new Date().toISOString();
    for (const recorded of events) {
      if (!this.open && passed(this.previous, now, recorded.start, duration)) {
        const startedAt = new Date().toISOString();
        this.open = { id: uuid7(Date.now()), startedAt, recorded };
        this.listener.started(this, this.open.id, startedAt);
      }
      if (this.open?.recorded === recorded && passed(this.previous, now, recorded.end, duration)) {
        this.finish(new Date());
      }
    }
    this.previous = now;
  }

  /** @param {Date} endedAt */
  finish(endedAt) {
    const open = this.open;
    if (!open) return;
    this.open = null;
    this.listener.ended(
      this,
      eventOut(this.id, open.id, new Date(open.startedAt), open.recorded, endedAt),
    );
  }

  /**
   * The events this camera would have recorded before `now`, newest first: the loop playing
   * backwards from where it starts.
   * @param {number} now
   * @param {number} startAt
   */
  history(now, startAt) {
    const { duration, events } = this.recording;
    /** @type {MotionEvent[]} */
    const past = [];
    for (let pass = 0; pass * duration * 1000 < HISTORY_MS; pass += 1) {
      for (const recorded of events) {
        const endsAgo = startAt - recorded.end + pass * duration;
        if (endsAgo <= 0) continue; // not over yet when the demo starts
        const ended = new Date(now - endsAgo * 1000);
        const started = new Date(ended.getTime() - (recorded.end - recorded.start) * 1000);
        past.push(eventOut(this.id, uuid7(started.getTime()), started, recorded, ended));
      }
    }
    return past;
  }
}

/**
 * An event as the hub's API returns it, its pictures and clip served next to the dashboard.
 * @param {string} deviceId
 * @param {string} id
 * @param {Date} started
 * @param {RecordedEvent} recorded
 * @param {Date} ended
 * @returns {MotionEvent}
 */
export function eventOut(deviceId, id, started, recorded, ended) {
  const { snapshots, clip } = recorded;
  return {
    id,
    device_id: deviceId,
    started_at: started.toISOString(),
    ended_at: ended.toISOString(),
    duration_seconds: Math.round((ended.getTime() - started.getTime()) / 100) / 10,
    complete: true,
    interrupted: false,
    peak_area_ratio: recorded.peak_area_ratio,
    motion_frames: recorded.motion_frames,
    person: recorded.person,
    person_confidence: recorded.person_confidence,
    alert: recorded.alert,
    boxes: recorded.boxes,
    snapshots: /** @type {const} */ (["clean", "annotated", "thumbnail"]).map((kind) => ({
      kind,
      url: RECORDING_DIR + snapshots[kind].path,
      size_bytes: snapshots[kind].size_bytes,
    })),
    clip: clip
      ? { url: RECORDING_DIR + clip.path, content_type: "video/webm", size_bytes: clip.size_bytes }
      : null,
  };
}
