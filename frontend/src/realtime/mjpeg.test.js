import { describe, expect, it } from "vitest";

import { createPartParser, parseDetections } from "./mjpeg.js";

const encoder = new TextEncoder();

/**
 * One part exactly as the hub writes it (realtime/mjpeg.py).
 * @param {number[]} jpeg
 * @param {string} [detections] the X-Detections header value
 */
function part(jpeg, detections) {
  const extra = detections ? `X-Detections: ${detections}\r\n` : "";
  const header = encoder.encode(
    `--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpeg.length}\r\n${extra}\r\n`,
  );
  return new Uint8Array([...header, ...jpeg, 13, 10]);
}

describe("createPartParser", () => {
  it("splits a stream into JPEGs however the bytes are chunked", () => {
    // JPEG payloads that contain the boundary text and CRLFs: lengths, not scanning, decide.
    const frames = [
      [0xff, 0xd8, 1, 2, 3, 0xff, 0xd9],
      [...encoder.encode("\r\n\r\n--frame\r\n"), 0xff, 0xd9],
      Array.from({ length: 5000 }, (_, i) => i % 256),
    ];
    const stream = new Uint8Array(frames.flatMap((frame) => [...part(frame)]));

    for (const size of [1, 3, 7, 64, 4096, stream.length]) {
      /** @type {number[][]} */
      const parsed = [];
      const push = createPartParser((jpeg) => parsed.push([...jpeg]));
      for (let i = 0; i < stream.length; i += size) push(stream.subarray(i, i + size));
      expect(parsed, `chunks of ${size} bytes`).toEqual(frames);
    }
  });

  it("hands each frame its own detections", () => {
    const moving = '{"boxes":[[0.5,0.25,0.1,0.5]],"person":0.87}';
    const stream = new Uint8Array([...part([1], moving), ...part([2]), ...part([3], moving)]);
    /** @type {unknown[]} */
    const parsed = [];
    const push = createPartParser((jpeg, detections) => parsed.push([jpeg[0], detections]));

    for (let i = 0; i < stream.length; i += 5) push(stream.subarray(i, i + 5));

    const found = { boxes: [[0.5, 0.25, 0.1, 0.5]], person: 0.87 };
    expect(parsed).toEqual([
      [1, found],
      [2, null],
      [3, found],
    ]);
  });

  it("rejects parts without a length", () => {
    const push = createPartParser(() => {});
    expect(() => push(encoder.encode("--frame\r\nContent-Type: image/jpeg\r\n\r\nxx"))).toThrow(
      /Content-Length/,
    );
  });

  it("rejects runaway headers instead of buffering forever", () => {
    const push = createPartParser(() => {});
    expect(() => push(new Uint8Array(9000).fill(65))).toThrow(/too long/);
  });
});

describe("parseDetections", () => {
  it("reads the header whatever its case", () => {
    expect(
      parseDetections('Content-Length: 3\r\nx-detections: {"boxes":[],"person":null}'),
    ).toEqual({ boxes: [], person: null });
  });

  it("ignores malformed detections instead of failing the frame", () => {
    for (const value of [
      "not json",
      '{"boxes":[[1,2,3]],"person":null}',
      '{"boxes":[["a",0,1,1]],"person":null}',
      '{"boxes":{},"person":null}',
      '{"boxes":[],"person":"high"}',
    ]) {
      expect(parseDetections(`X-Detections: ${value}`), value).toBeNull();
    }
    expect(parseDetections("Content-Length: 3")).toBeNull();
  });
});
