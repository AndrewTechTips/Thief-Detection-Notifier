// Errors from the hub's API, and the words the dashboard shows for them.

/** @typedef {import("./types.js").Problem} Problem */
/** @typedef {import("./types.js").FieldError} FieldError */
/**
 * http: the hub answered with an error status. network: no answer (hub down, offline, proxy
 * error page). timeout: no answer in time.
 * @typedef {"http" | "network" | "timeout"} ErrorKind
 */

const PROBLEM_PREFIX = "urn:vision-hub:problem:";

export class ApiError extends Error {
  /**
   * @param {{
   *   kind: ErrorKind,
   *   status?: number,
   *   problem?: Problem | null,
   *   retryAfter?: number | null,
   *   requestId?: string | null,
   *   cause?: unknown,
   * }} init
   */
  constructor({ kind, status = 0, problem = null, retryAfter = null, requestId = null, cause }) {
    super(problem?.detail ?? problem?.title ?? fallbackMessage(kind, status), { cause });
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.problem = problem;
    /** Seconds to wait before retrying (from `Retry-After`), if the hub said. */
    this.retryAfter = retryAfter;
    this.requestId = requestId ?? problem?.request_id ?? null;
  }

  /** The hub's error code, e.g. "rate-limited"; null for plain HTTP errors. Branch on this,
   * never on the message (docs/api-conventions.md). */
  get code() {
    const type = this.problem?.type;
    return type?.startsWith(PROBLEM_PREFIX) ? type.slice(PROBLEM_PREFIX.length) : null;
  }

  /** Validation messages by field name (the last part of each error's location). */
  get fieldErrors() {
    /** @type {Map<string, string>} */
    const fields = new Map();
    for (const error of this.problem?.errors ?? []) {
      const name = String(error.loc.at(-1));
      if (!fields.has(name)) fields.set(name, error.msg);
    }
    return fields;
  }
}

/**
 * @param {ErrorKind} kind
 * @param {number} status
 */
function fallbackMessage(kind, status) {
  if (kind === "network") return "Network error";
  if (kind === "timeout") return "Request timed out";
  return `HTTP ${status}`;
}

/**
 * Short, human copy for an error: what happened and what to do next.
 * @param {unknown} error
 * @returns {{ title: string, detail: string }}
 */
export function describeError(error) {
  if (!(error instanceof ApiError)) {
    return { title: "Something went wrong", detail: "Reload the page and try again." };
  }
  const detail = error.problem?.detail;
  if (error.kind === "network") {
    return { title: "Can't reach the hub", detail: "Check that it's running and you're online." };
  }
  if (error.kind === "timeout") {
    return { title: "The hub is taking too long", detail: "Try again in a moment." };
  }
  switch (error.status) {
    case 401:
      return { title: "Session expired", detail: "Sign in again to continue." };
    case 403:
      return { title: "Not allowed", detail: "Your account can't do this. Ask an admin." };
    case 404:
      return { title: "Not found", detail: detail ?? "It may have been removed." };
    case 409:
      return { title: "Already changed", detail: detail ?? "Reload to see the latest version." };
    case 422:
      return { title: "Check the form", detail: "Some fields need a different value." };
    case 429:
      return {
        title: "Too many attempts",
        detail: error.retryAfter
          ? `Try again in ${formatWait(error.retryAfter)}.`
          : "Wait a minute and try again.",
      };
    case 503:
      return { title: "The hub is busy", detail: detail ?? "Try again in a moment." };
  }
  if (error.status >= 500) {
    const reference = error.requestId ? ` If it keeps happening, quote ${error.requestId}.` : "";
    return { title: "The hub hit an error", detail: `Try again.${reference}` };
  }
  return { title: "Request failed", detail: detail ?? "Check the details and try again." };
}

/** @param {number} seconds */
export function formatWait(seconds) {
  if (seconds < 60) return `${Math.max(1, Math.ceil(seconds))} s`;
  const minutes = Math.ceil(seconds / 60);
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}
