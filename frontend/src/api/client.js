// HTTP client for the hub's API: bearer tokens, timeouts, problem+json errors (ApiError), and
// one shared token refresh when access tokens expire.

import { ApiError } from "./errors.js";

/** @typedef {import("./types.js").Problem} Problem */
/** @typedef {string | number | boolean | Date | null | undefined} QueryValue */
/**
 * @typedef {{
 *   query?: Record<string, QueryValue | QueryValue[]>,
 *   json?: unknown,
 *   form?: Record<string, string>,
 *   headers?: Record<string, string>,
 *   signal?: AbortSignal,
 *   timeout?: number,
 *   auth?: boolean,
 * }} RequestOptions
 * `auth: false` sends no token and never triggers a refresh (login, refresh itself).
 */
/**
 * How the client gets tokens; provided by the session (state/session.js).
 * @typedef {{
 *   token: () => string | null,
 *   refresh: () => Promise<string | null>,
 *   expired: () => void,
 * }} AuthHooks
 * `refresh` resolves to a new access token, or null when the session is over (no refresh token,
 * or the hub rejected it). It throws ApiError for network problems, which do not end the session.
 * `expired` is called when the session can't continue (a refresh failed, or a fresh token was
 * refused); it must be idempotent. The session signs out.
 */

const DEFAULT_TIMEOUT_MS = 15_000;
const JSON_TYPES = /\/(?:problem\+)?json\b/;

/**
 * @param {{ baseUrl?: string, timeout?: number, auth?: AuthHooks }} [options]
 */
export function createClient({ baseUrl = "/api/v1", timeout = DEFAULT_TIMEOUT_MS, auth } = {}) {
  /** @type {AuthHooks | undefined} */
  let hooks = auth;
  /** @type {Promise<string | null> | null} */
  let refreshing = null;

  /** Every request that hits 401 at the same time waits for one refresh. */
  function refreshOnce() {
    const current = /** @type {AuthHooks} */ (hooks);
    refreshing ??= current
      .refresh()
      .then((token) => {
        if (!token) current.expired();
        return token;
      })
      .finally(() => {
        refreshing = null;
      });
    return refreshing;
  }

  /**
   * @param {string} method
   * @param {string} path relative to the base URL, e.g. "/devices"
   * @param {RequestOptions} [options]
   * @returns {Promise<any>} the parsed JSON body; undefined for empty responses
   */
  async function request(method, path, options = {}) {
    const authenticated = options.auth !== false && hooks !== undefined;
    let token = authenticated ? (hooks?.token() ?? null) : null;
    let refreshed = false;
    if (authenticated && !token) {
      // After a reload only the refresh token survives: get an access token first.
      token = await refreshOnce();
      refreshed = true;
      if (!token) throw new ApiError({ kind: "http", status: 401 });
    }
    const first = await attempt(method, path, options, token);
    if (first.response.status !== 401 || !authenticated) return finish(first);

    // Another request may already have refreshed while this one was in flight.
    const current = hooks?.token() ?? null;
    const fresh = current && current !== token ? current : refreshed ? null : await refreshOnce();
    if (!fresh) {
      if (refreshed) hooks?.expired(); // a brand-new token was refused: the session is over
      return finish(first);
    }
    const second = await attempt(method, path, options, fresh);
    if (second.response.status === 401) hooks?.expired();
    return finish(second);
  }

  /**
   * One HTTP exchange, body included, under a single timeout.
   * @param {string} method
   * @param {string} path
   * @param {RequestOptions} options
   * @param {string | null} token
   */
  async function attempt(method, path, options, token) {
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json, application/problem+json");
    /** @type {BodyInit | undefined} */
    let body;
    if (options.json !== undefined) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(options.json);
    } else if (options.form) {
      body = new URLSearchParams(options.form);
    }
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const deadline = withTimeout(options.signal, options.timeout ?? timeout);
    try {
      const response = await fetch(buildUrl(baseUrl, path, options.query), {
        method,
        headers,
        body,
        signal: deadline.signal,
        cache: "no-store",
        credentials: "omit", // bearer tokens only, never cookies
      });
      return { response, body: await readBody(response) };
    } catch (error) {
      if (deadline.timedOut()) throw new ApiError({ kind: "timeout", cause: error });
      if (options.signal?.aborted) throw error; // cancelled by the caller: not an API error
      if (error instanceof ApiError) throw error;
      throw new ApiError({ kind: "network", cause: error });
    } finally {
      deadline.clear();
    }
  }

  return {
    request,
    /** @param {AuthHooks | undefined} next */
    setAuth(next) {
      hooks = next;
    },
    /** @param {string} path @param {RequestOptions} [options] */
    get: (path, options) => request("GET", path, options),
    /** @param {string} path @param {RequestOptions} [options] */
    post: (path, options) => request("POST", path, options),
    /** @param {string} path @param {RequestOptions} [options] */
    put: (path, options) => request("PUT", path, options),
    /** @param {string} path @param {RequestOptions} [options] */
    patch: (path, options) => request("PATCH", path, options),
    /** @param {string} path @param {RequestOptions} [options] */
    delete: (path, options) => request("DELETE", path, options),
  };
}

/** The dashboard's client. The session connects its token hooks with `api.setAuth`. */
export const api = createClient();

/** @typedef {ReturnType<typeof createClient>} ApiClient */

/**
 * @param {{ response: Response, body: unknown }} exchange
 */
function finish({ response, body }) {
  if (response.ok) return body;
  const problem = isProblem(body) ? body : null;
  // A bare 502/504 comes from a proxy in front of the hub: the hub itself is unreachable.
  const unreachable = !problem && (response.status === 502 || response.status === 504);
  throw new ApiError({
    kind: unreachable ? "network" : "http",
    status: response.status,
    problem,
    retryAfter: parseRetryAfter(response.headers.get("Retry-After")),
    requestId: response.headers.get("X-Request-ID"),
  });
}

/** @param {Response} response */
async function readBody(response) {
  const text = await response.text();
  if (!text) return undefined;
  if (!JSON_TYPES.test(response.headers.get("Content-Type") ?? "")) {
    // An HTML error page or a plain-text 502 from a proxy: the hub itself never sends these.
    if (response.ok) throw new ApiError({ kind: "http", status: response.status });
    return undefined;
  }
  try {
    return JSON.parse(text);
  } catch (cause) {
    throw new ApiError({ kind: "http", status: response.status, cause });
  }
}

/**
 * @param {unknown} body
 * @returns {body is Problem}
 */
function isProblem(body) {
  return (
    typeof body === "object" &&
    body !== null &&
    typeof (/** @type {Problem} */ (body).title) === "string" &&
    typeof (/** @type {Problem} */ (body).status) === "number"
  );
}

/**
 * `Retry-After` is either seconds or an HTTP date.
 * @param {string | null} value
 * @returns {number | null} seconds
 */
export function parseRetryAfter(value, now = Date.now()) {
  if (!value) return null;
  if (/^\d+$/.test(value.trim())) return Number(value);
  const date = Date.parse(value);
  return Number.isNaN(date) ? null : Math.max(0, Math.ceil((date - now) / 1000));
}

/**
 * @param {string} baseUrl
 * @param {string} path
 * @param {RequestOptions["query"]} query
 */
export function buildUrl(baseUrl, path, query) {
  const url = new URL(baseUrl.replace(/\/$/, "") + path, globalThis.location?.href);
  for (const [key, raw] of Object.entries(query ?? {})) {
    for (const value of Array.isArray(raw) ? raw : [raw]) {
      if (value === null || value === undefined) continue;
      url.searchParams.append(key, value instanceof Date ? value.toISOString() : String(value));
    }
  }
  return url;
}

/**
 * Combines the caller's signal (if any) with a timeout, and remembers which one fired.
 * @param {AbortSignal | undefined} signal
 * @param {number} ms
 */
function withTimeout(signal, ms) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException("The request timed out.", "TimeoutError"));
  }, ms);
  const forward = () => controller.abort(signal?.reason);
  if (signal?.aborted) forward();
  else signal?.addEventListener("abort", forward, { once: true });
  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    clear() {
      clearTimeout(timer);
      signal?.removeEventListener("abort", forward);
    },
  };
}
