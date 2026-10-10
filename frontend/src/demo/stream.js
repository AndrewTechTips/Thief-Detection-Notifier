// A demo camera's live MJPEG stream, made in the browser: the frame the camera's video is showing,
// encoded as a JPEG, with the hub's recorded detections for that moment in the part's
// X-Detections header. The dashboard's player can't tell it from the hub's.

/** @typedef {import("./cameras.js").Camera} Camera */

const BOUNDARY = "frame";
const DEFAULT_FPS = 10;
const QUALITY = 0.75;

/**
 * @param {Camera} camera
 * @param {{ fps?: number, signal?: AbortSignal | null }} options
 */
export function streamResponse(camera, { fps = DEFAULT_FPS, signal }) {
  const encoder = new TextEncoder();
  const canvas = new OffscreenCanvas(camera.recording.width, camera.recording.height);
  const context = canvas.getContext("2d");
  /** @type {ReturnType<typeof setInterval> | undefined} */
  let timer;
  let busy = false;

  const body = new ReadableStream({
    start(controller) {
      const end = () => {
        clearInterval(timer);
        try {
          controller.close();
        } catch {
          // already closed or cancelled
        }
      };
      signal?.addEventListener("abort", end);
      timer = setInterval(
        async () => {
          if (!camera.running) return end(); // the hub ends a stream when its camera stops
          if (busy || camera.status !== "online" || camera.video.readyState < 2) return;
          busy = true;
          try {
            context?.drawImage(camera.video, 0, 0, canvas.width, canvas.height);
            const detections = camera.detections();
            const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: QUALITY });
            const jpeg = new Uint8Array(await blob.arrayBuffer());
            const extra = detections ? `X-Detections: ${JSON.stringify(detections)}\r\n` : "";
            const head = `--${BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpeg.length}\r\n${extra}\r\n`;
            controller.enqueue(encoder.encode(head));
            controller.enqueue(jpeg);
            controller.enqueue(encoder.encode("\r\n"));
          } catch {
            end();
          } finally {
            busy = false;
          }
        },
        1000 / Math.min(fps, 15),
      );
    },
    cancel() {
      clearInterval(timer);
    },
  });
  return new Response(body, {
    headers: {
      "Content-Type": `multipart/x-mixed-replace; boundary=${BOUNDARY}`,
      "Cache-Control": "no-store",
    },
  });
}

/**
 * The frame a camera is showing, as a JPEG (the hub's "latest frame" endpoint).
 * @param {Camera} camera
 */
export async function snapshotResponse(camera) {
  const canvas = new OffscreenCanvas(camera.recording.width, camera.recording.height);
  canvas.getContext("2d")?.drawImage(camera.video, 0, 0, canvas.width, canvas.height);
  const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: 0.85 });
  return new Response(blob, {
    headers: { "Content-Type": "image/jpeg", "X-Captured-At": camera.lastFrameAt },
  });
}
