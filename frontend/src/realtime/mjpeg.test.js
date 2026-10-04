import { describe, expect, it } from "vitest";

import { createPartParser } from "./mjpeg.js";

const encoder = new TextEncoder();

/** One part exactly as the hub writes it (realtime/mjpeg.py). */
function part(/** @type {number[]} */ jpeg) {
  const header = encoder.encode(
    `--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpeg.length}\r\n\r\n`,
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
