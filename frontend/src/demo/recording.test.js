import { describe, expect, it } from "vitest";

import { detectionsAt, passed, uuid7 } from "./recording.js";

/** @type {import("./recording.js").RecordedCamera["frames"]} */
const FRAMES = [
  [0, null],
  [0.1, { boxes: [[0.1, 0.1, 0.2, 0.2]], person: null }],
  [0.2, { boxes: [[0.2, 0.1, 0.2, 0.2]], person: 0.9 }],
];

describe("detectionsAt", () => {
  it("takes the last analysed frame up to the moment", () => {
    expect(detectionsAt(FRAMES, 0.15)).toBe(FRAMES[1][1]);
    expect(detectionsAt(FRAMES, 0.2)).toBe(FRAMES[2][1]);
    expect(detectionsAt(FRAMES, 0.05)).toBeNull();
  });

  it("forgets detections that are too old", () => {
    expect(detectionsAt(FRAMES, 0.45)).toBe(FRAMES[2][1]);
    expect(detectionsAt(FRAMES, 0.6)).toBeNull();
    expect(detectionsAt([], 1)).toBeNull();
  });
});

describe("passed", () => {
  it("sees a mark crossed during playback", () => {
    expect(passed(1, 2, 1.5, 10)).toBe(true);
    expect(passed(1, 2, 2, 10)).toBe(true);
    expect(passed(1, 2, 1, 10)).toBe(false);
    expect(passed(1, 2, 3, 10)).toBe(false);
  });

  it("follows the loop around its end", () => {
    expect(passed(9.5, 0.5, 9.8, 10)).toBe(true);
    expect(passed(9.5, 0.5, 0.2, 10)).toBe(true);
    expect(passed(9.5, 0.5, 5, 10)).toBe(false);
    // An event ending past the loop's end ends early in the next pass.
    expect(passed(0.1, 0.4, 10.3, 10)).toBe(true);
  });
});

describe("uuid7", () => {
  it("makes version 7 ids that sort by time, like the hub's", () => {
    const early = uuid7(1_700_000_000_000);
    const late = uuid7(1_700_000_000_001);

    expect(early).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(early < late).toBe(true);
  });
});
