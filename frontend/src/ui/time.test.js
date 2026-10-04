import { expect, it } from "vitest";

import { timeAgo } from "./time.js";

it("says how long ago, in words", () => {
  const now = Date.parse("2026-10-04T12:00:00Z");
  const ago = (/** @type {number} */ seconds) => timeAgo(now - seconds * 1000, now);

  expect(ago(10)).toBe("just now");
  expect(ago(50)).toBe("1 minute ago");
  expect(ago(4 * 60)).toBe("4 minutes ago");
  expect(ago(2 * 3600)).toBe("2 hours ago");
  expect(ago(26 * 3600)).toBe("yesterday");
  expect(ago(3 * 86_400)).toBe("3 days ago");
});
