// Live video: reads an MJPEG stream (multipart/x-mixed-replace, one JPEG per part) with fetch and
// paints the newest frame on a canvas. Unlike pointing <img src> at the stream, this knows when
// frames arrive, so it can tell a stalled stream from a live one, reconnect, and stop instantly.
//
// The very first frame also goes into a poster <img> under the canvas, like a <video> poster:
// Largest Contentful Paint ignores canvases, and an <img> whose source keeps changing would
// count every frame as a new "largest paint". The poster marks when the picture first appeared.

/**
 * idle: not playing. loading: connecting, no frame yet. playing: frames arriving.
 * retrying: the stream failed or stalled; trying again. ended: the hub ended the stream
 * (the camera stopped).
 * @typedef {"idle" | "loading" | "playing" | "retrying" | "ended"} PlayerState
 */

const CRLF2 = [13, 10, 13, 10];
const MAX_HEADER_BYTES = 8 * 1024;
const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 15_000;

/**
 * Splits an MJPEG byte stream into JPEG parts. Each part header must carry Content-Length
 * (the hub's always does), so no scanning for the boundary inside image data.
 * @param {(jpeg: Uint8Array) => void} onPart
 */
export function createPartParser(onPart) {
  let buffer = /** @type {Uint8Array} */ (new Uint8Array(0));
  /** @type {number | null} */
  let expected = null;
  const decoder = new TextDecoder();

  /** @param {Uint8Array} chunk */
  return function push(chunk) {
    buffer = concat(buffer, chunk);
    for (;;) {
      if (expected === null) {
        const end = indexOf(buffer, CRLF2);
        if (end < 0) {
          if (buffer.length > MAX_HEADER_BYTES) throw new Error("MJPEG part header too long");
          return;
        }
        const header = decoder.decode(buffer.subarray(0, end));
        const length = /content-length:\s*(\d+)/i.exec(header);
        if (!length) throw new Error("MJPEG part without Content-Length");
        expected = Number(length[1]);
        buffer = buffer.subarray(end + CRLF2.length);
      }
      if (buffer.length < expected) return;
      onPart(buffer.slice(0, expected));
      buffer = buffer.subarray(expected);
      expected = null;
    }
  };
}

/**
 * @param {{
 *   canvas: HTMLCanvasElement,
 *   poster?: HTMLImageElement,
 *   source: () => Promise<string>,
 *   onState?: (state: PlayerState) => void,
 *   onFrame?: (width: number, height: number) => void,
 *   stallMs?: number,
 * }} options
 * `source` returns a fresh stream URL (with a new ticket) for every connection.
 */
export function createPlayer({ canvas, poster, source, onState, onFrame, stallMs = 8_000 }) {
  const context = canvas.getContext("2d");
  /** @type {PlayerState} */
  let state = "idle";
  let running = false;
  /** @type {AbortController | null} the current request */
  let controller = null;
  /** @type {AbortController | null} the wait between retries, so pause() can cut it short */
  let resting = null;
  /** @type {Uint8Array | null} */
  let pending = null;
  let decoding = false;
  let framesThisConnection = 0;

  /** @param {PlayerState} next */
  function setState(next) {
    if (next === state) return;
    state = next;
    onState?.(next);
  }

  /** Latest frame wins: while one frame decodes, newer ones replace the waiting one. */
  async function paint() {
    decoding = true;
    while (pending) {
      const jpeg = pending;
      pending = null;
      const blob = new Blob([/** @type {BlobPart} */ (jpeg)], { type: "image/jpeg" });
      if (poster && !poster.src) poster.src = URL.createObjectURL(blob);
      try {
        const bitmap = await createImageBitmap(blob);
        if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
          canvas.width = bitmap.width;
          canvas.height = bitmap.height;
        }
        context?.drawImage(bitmap, 0, 0);
        bitmap.close();
        onFrame?.(canvas.width, canvas.height);
        if (running) setState("playing");
      } catch {
        // a corrupt frame: skip it, the next one replaces it anyway
      }
    }
    decoding = false;
  }

  async function run() {
    let attempt = 0;
    while (running) {
      const abort = new AbortController();
      controller = abort;
      framesThisConnection = 0;
      if (state !== "playing") setState(attempt ? "retrying" : "loading");
      /** @type {ReturnType<typeof setTimeout> | undefined} */
      let stall;
      const watch = () => {
        clearTimeout(stall);
        stall = setTimeout(() => abort.abort(new Error("stalled")), stallMs);
      };
      try {
        watch();
        const url = await source();
        const response = await fetch(url, {
          signal: abort.signal,
          cache: "no-store",
          credentials: "omit",
        });
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
        const push = createPartParser((jpeg) => {
          watch();
          framesThisConnection += 1;
          pending = jpeg;
          if (!decoding) paint();
        });
        const reader = response.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          push(value);
        }
        clearTimeout(stall);
        // The hub ends a stream when the camera stops: not an error, nothing to retry.
        if (running) {
          running = false;
          setState("ended");
        }
        return;
      } catch {
        clearTimeout(stall);
        if (!running) return; // paused or destroyed
        if (framesThisConnection > 0) attempt = 0; // it was working: start backoff over
        attempt += 1;
        setState("retrying");
        resting = new AbortController();
        await sleep(retryDelay(attempt), resting.signal);
      } finally {
        clearTimeout(stall);
      }
    }
  }

  return {
    get state() {
      return state;
    },
    play() {
      if (running) return;
      running = true;
      run();
    },
    /** Stops the stream; the last frame stays on the canvas. */
    pause() {
      running = false;
      controller?.abort(new Error("paused"));
      resting?.abort();
      controller = null;
      resting = null;
      setState("idle");
    },
  };
}

/** @param {number} attempt */
function retryDelay(attempt) {
  const base = Math.min(RETRY_MAX_MS, RETRY_MIN_MS * 2 ** (attempt - 1));
  return Math.round(base * (0.8 + Math.random() * 0.4));
}

/**
 * @param {number} ms
 * @param {AbortSignal} signal
 */
function sleep(ms, signal) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve(undefined);
    });
  });
}

/**
 * @param {Uint8Array} a
 * @param {Uint8Array} b
 */
function concat(a, b) {
  if (a.length === 0) return b;
  const joined = new Uint8Array(a.length + b.length);
  joined.set(a);
  joined.set(b, a.length);
  return joined;
}

/**
 * @param {Uint8Array} haystack
 * @param {number[]} needle
 */
function indexOf(haystack, needle) {
  outer: for (let i = 0; i <= haystack.length - needle.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (haystack[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}
