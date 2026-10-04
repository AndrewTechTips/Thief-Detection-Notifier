// Human time: "just now", "4 minutes ago", "yesterday".

const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/** @type {[Intl.RelativeTimeFormatUnit, number][]} unit and its length in seconds */
const UNITS = [
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
];

/**
 * @param {string | number | Date} when
 * @param {number} [now]
 */
export function timeAgo(when, now = Date.now()) {
  const seconds = (new Date(when).getTime() - now) / 1000;
  if (Math.abs(seconds) < 45) return "just now";
  for (const [unit, length] of UNITS) {
    if (Math.abs(seconds) >= length || unit === "minute") {
      return relative.format(Math.round(seconds / length), unit);
    }
  }
  return "just now";
}
