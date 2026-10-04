import { describe, expect, it } from "vitest";

import { areaOf, sensitivityOf } from "./detection-form.js";

describe("sensitivity scale", () => {
  it("maps the slider ends to 5 % and 0.1 % of the frame", () => {
    expect(areaOf(0)).toBe(0.05);
    expect(areaOf(100)).toBe(0.001);
  });

  it("round-trips the hub's default within one step", () => {
    expect(
      Math.abs(sensitivityOf(areaOf(sensitivityOf(0.01))) - sensitivityOf(0.01)),
    ).toBeLessThanOrEqual(1);
    expect(sensitivityOf(0.01)).toBe(41);
  });

  it("clamps values outside the scale", () => {
    expect(sensitivityOf(0.5)).toBe(0);
    expect(sensitivityOf(0.0001)).toBe(100);
  });
});
