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

it("formats durations compactly", async () => {
  const { formatDuration } = await import("./time.js");
  expect(formatDuration(9.4)).toBe("9 s");
  expect(formatDuration(125)).toBe("2 min 5 s");
  expect(formatDuration(120)).toBe("2 min");
  expect(formatDuration(3840)).toBe("1 h 4 min");
});

it("names days relative to today", async () => {
  const { formatDay } = await import("./time.js");
  const now = new Date(2026, 9, 4, 12).getTime();
  expect(formatDay(new Date(2026, 9, 4, 0, 5), now)).toBe("Today");
  expect(formatDay(new Date(2026, 9, 3, 23, 59), now)).toBe("Yesterday");
  expect(formatDay(new Date(2026, 9, 1, 9), now)).not.toMatch(/Today|Yesterday/);
});
