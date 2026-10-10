import { describe, expect, it } from "vitest";

import { layout } from "./detection-overlay.js";

describe("layout", () => {
  it("places boxes as percentages of the picture", () => {
    expect(layout({ boxes: [[0.5, 0.25, 0.1, 0.5]], person: null })).toEqual([
      { left: "50.00%", top: "25.00%", width: "10.00%", height: "50.00%" },
    ]);
  });

  it("clips boxes that reach past the picture", () => {
    expect(layout({ boxes: [[-0.1, 0.9, 0.3, 0.3]], person: null })).toEqual([
      { left: "0.00%", top: "90.00%", width: "20.00%", height: "10.00%" },
    ]);
  });

  it("keeps the largest boxes when there are too many", () => {
    const small = /** @type {[number, number, number, number]} */ ([0, 0, 0.01, 0.01]);
    const large = /** @type {[number, number, number, number]} */ ([0.2, 0.2, 0.5, 0.5]);
    const placed = layout({ boxes: [...Array(30).fill(small), large], person: null });

    expect(placed).toHaveLength(24);
    expect(placed[0]).toEqual({ left: "20.00%", top: "20.00%", width: "50.00%", height: "50.00%" });
  });

  it("has nothing to place without detections", () => {
    expect(layout(null)).toEqual([]);
  });
});
