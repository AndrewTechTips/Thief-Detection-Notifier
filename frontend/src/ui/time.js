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

const clock = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const clockSeconds = new Intl.DateTimeFormat(undefined, {
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
});
const weekday = new Intl.DateTimeFormat(undefined, {
  weekday: "long",
  day: "numeric",
  month: "long",
});

/** "14:32" in the viewer's locale.
 * @param {string | number | Date} when
 * @param {{ seconds?: boolean }} [options] */
export function formatClock(when, { seconds = false } = {}) {
  return (seconds ? clockSeconds : clock).format(new Date(when));
}

/** "9 s", "2 min 5 s", "1 h 4 min".
 * @param {number} seconds */
export function formatDuration(seconds) {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return total % 60 ? `${minutes} min ${total % 60} s` : `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
}

/** "Today", "Yesterday", or a date like "Thursday, 1 October" in the viewer's locale.
 * @param {string | number | Date} when
 * @param {number} [now] */
export function formatDay(when, now = Date.now()) {
  const day = startOfDay(new Date(when));
  const today = startOfDay(new Date(now));
  const days = Math.round((today - day) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return weekday.format(new Date(when));
}

/** @param {Date} date */
function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}
