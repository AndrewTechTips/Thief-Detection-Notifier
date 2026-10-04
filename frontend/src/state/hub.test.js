import { describe, expect, it } from "vitest";

import { backoff } from "./hub.js";

describe("backoff", () => {
  it("doubles from 1 s, caps at 15 s and stays within ±20 % jitter", () => {
    for (const [failures, base] of [
      [1, 1000],
      [2, 2000],
      [4, 8000],
      [5, 15000],
      [20, 15000],
    ]) {
      for (let i = 0; i < 50; i++) {
        const delay = backoff(failures);
        expect(delay).toBeGreaterThanOrEqual(base * 0.8);
        expect(delay).toBeLessThanOrEqual(base * 1.2);
      }
    }
  });
});
