// Signed-in session: who you are and the tokens that prove it.
//
// - The access token lives in memory only. The refresh token is kept in localStorage when you
//   choose "Keep me signed in", otherwise in sessionStorage (gone when the tab closes).
// - The hub rotates refresh tokens and rejects a used one, so tabs must never refresh with the
//   same token at once: refreshes run under a cross-tab lock, re-read the stored token first,
//   and share the result over a BroadcastChannel so other tabs adopt it instead of refreshing.
// - Signing out in one tab signs out every tab. A session that expires in one tab does not
//   end the others (they find out for themselves on their next refresh).

import { ApiError } from "../api/errors.js";
import { Store } from "./store.js";

/** @typedef {import("../api/types.js").Principal} Principal */
/** @typedef {import("../api/types.js").TokenResponse} TokenResponse */
/** @typedef {import("../api/client.js").ApiClient} ApiClient */
/**
 * @typedef {{
 *   status: "unknown" | "signed-out" | "signed-in",
 *   user: Principal | null,
 *   reason: "expired" | "signed-out" | null,
 * }} SessionState
 * reason: why the last session ended, so the sign-in page can say so.
 */
/**
 * Messages between tabs. Tokens never leave the origin: BroadcastChannel is same-origin only.
 * @typedef {(
 *   | { type: "signed-in", tokens: TokenResponse, user: Principal, keep: boolean }
 *   | { type: "rotated", previous: string, tokens: TokenResponse }
 *   | { type: "signed-out" }
 * )} SessionMessage
 */
/**
 * @typedef {{
 *   client: ApiClient,
 *   local: Storage | null,
 *   ephemeral: Storage | null,
 *   channel: BroadcastChannel | null,
 *   locks: LockManager | null,
 *   visibility: { hidden: boolean, addEventListener: Document["addEventListener"] } | null,
 *   now?: () => number,
 * }} SessionDeps
 */

export const REFRESH_KEY = "vision-hub.refresh-token";
const LOCK_NAME = "vision-hub.refresh";
/** Refresh this long before the access token expires (visible tabs only). */
const REFRESH_EARLY_MS = 60_000;
/** Treat the access token as expired slightly early, to absorb clock skew and latency. */
const EXPIRY_SKEW_MS = 5_000;

export class Session {
  /** @param {SessionDeps} deps */
  constructor(deps) {
    this.deps = deps;
    this.now = deps.now ?? Date.now;
    /** @type {Store<SessionState>} */
    this.state = new Store(
      /** @type {SessionState} */ ({ status: "unknown", user: null, reason: null }),
    );
    /** @type {string | null} */
    this.access = null;
    this.accessExpiresAt = 0;
    /** @type {string | null} */
    this.refreshToken = null;
    this.keep = false;
    /** @type {ReturnType<typeof setTimeout> | undefined} */
    this.timer = undefined;

    deps.channel?.addEventListener("message", (event) =>
      this.receive(/** @type {SessionMessage} */ (event.data)),
    );
    deps.visibility?.addEventListener("visibilitychange", () => {
      if (!deps.visibility?.hidden && this.refreshToken && this.expiresSoon()) {
        this.refreshInBackground();
      }
    });
  }

  /** Hooks for the API client (api.setAuth). */
  get hooks() {
    return {
      token: () => this.token(),
      refresh: () => this.rotate(),
      expired: () => this.end("expired"),
    };
  }

  /** The access token, or null when there is none or it is about to expire. */
  token() {
    return this.access && this.now() < this.accessExpiresAt - EXPIRY_SKEW_MS ? this.access : null;
  }

  /** Whether a session was kept from an earlier visit (before restore() checks it). */
  hasStoredSession() {
    return read(this.deps.local) !== null || read(this.deps.ephemeral) !== null;
  }

  /**
   * On startup: resume a stored session. Throws ApiError if the hub can't be reached, so the
   * caller can try again; a refused token simply means "signed out".
   */
  async restore() {
    const local = read(this.deps.local);
    this.keep = local !== null;
    this.refreshToken = local ?? read(this.deps.ephemeral);
    if (!this.refreshToken) {
      this.state.set({ status: "signed-out", user: null, reason: null });
      return;
    }
    const access = await this.rotate();
    if (!access) {
      this.end("expired");
      return;
    }
    // The access token already names you: no round trip to /auth/me on every page load.
    const user = principalFrom(access) ?? (await this.loadUser());
    this.state.set({ status: "signed-in", user, reason: null });
  }

  /**
   * @param {string} username
   * @param {string} password
   * @param {boolean} keep keep the refresh token after the browser closes
   */
  async signIn(username, password, keep) {
    /** @type {TokenResponse} */
    const tokens = await this.deps.client.post("/auth/token", {
      form: { username, password },
      auth: false,
    });
    this.accept(tokens, keep);
    const user = await this.loadUser();
    this.state.set({ status: "signed-in", user, reason: null });
    this.post({ type: "signed-in", tokens, user, keep });
    return user;
  }

  /** Signs out here and in every other tab, and revokes the refresh token on the hub. */
  async signOut() {
    const token = this.refreshToken;
    this.end("signed-out");
    this.post({ type: "signed-out" });
    if (!token) return;
    // Best effort: if the hub is unreachable the token still expires on its own.
    await this.deps.client
      .post("/auth/logout", { json: { refresh_token: token }, auth: false, timeout: 5000 })
      .catch(() => {});
  }

  /**
   * Gets a new access token, rotating the refresh token. Resolves to null when the session is
   * over; throws ApiError when the hub can't answer (network, rate limit, server error).
   * @returns {Promise<string | null>}
   */
  rotate() {
    const before = this.access;
    return this.exclusive(async () => {
      // While this tab waited for the lock, another tab may have rotated and shared a newer
      // token. The token we started with is never reused: the hub may have just rejected it.
      const fresh = this.token();
      if (fresh && fresh !== before && !this.expiresSoon()) return fresh;

      const stored = this.keep ? read(this.deps.local) : read(this.deps.ephemeral);
      const current = stored ?? this.refreshToken;
      if (!current) return null;
      /** @type {TokenResponse} */
      let tokens;
      try {
        tokens = await this.deps.client.post("/auth/refresh", {
          json: { refresh_token: current },
          auth: false,
        });
      } catch (error) {
        if (isRefusal(error)) return null;
        throw error;
      }
      this.accept(tokens, this.keep);
      this.post({ type: "rotated", previous: current, tokens });
      return this.access;
    });
  }

  /** Ends the session in this tab only. Idempotent.
   * @param {"expired" | "signed-out"} reason */
  end(reason) {
    clearTimeout(this.timer);
    this.access = null;
    this.accessExpiresAt = 0;
    this.refreshToken = null;
    remove(this.deps.local);
    remove(this.deps.ephemeral);
    if (this.state.get().status !== "signed-out") {
      this.state.set({ status: "signed-out", user: null, reason });
    }
  }

  /** @param {SessionMessage} message */
  receive(message) {
    const { status } = this.state.get();
    switch (message.type) {
      case "signed-in":
        // Signing in once signs in every tab that is waiting at the sign-in page.
        if (status === "signed-in") return;
        this.accept(message.tokens, message.keep);
        this.state.set({ status: "signed-in", user: message.user, reason: null });
        return;
      case "rotated":
        // Same session, newer tokens: adopt them instead of refreshing (and reusing) ourselves.
        if (this.refreshToken === message.previous) this.accept(message.tokens, this.keep);
        return;
      case "signed-out":
        if (status !== "signed-out") this.end("signed-out");
        return;
    }
  }

  /**
   * @param {TokenResponse} tokens
   * @param {boolean} keep
   */
  accept(tokens, keep) {
    this.access = tokens.access_token;
    this.accessExpiresAt = this.now() + tokens.expires_in * 1000;
    this.refreshToken = tokens.refresh_token;
    this.keep = keep;
    write(keep ? this.deps.local : this.deps.ephemeral, tokens.refresh_token);
    remove(keep ? this.deps.ephemeral : this.deps.local);
    this.schedule();
  }

  /** Refreshes shortly before expiry, while the tab is visible. Hidden tabs refresh when they
   * are shown again, or on their next request. */
  schedule() {
    clearTimeout(this.timer);
    const delay = Math.max(5_000, this.accessExpiresAt - this.now() - REFRESH_EARLY_MS);
    this.timer = setTimeout(() => {
      if (!this.deps.visibility?.hidden) this.refreshInBackground();
    }, delay);
  }

  refreshInBackground() {
    this.rotate().then(
      (token) => {
        if (!token) this.end("expired");
      },
      () => {}, // offline or rate limited: the next request refreshes on demand
    );
  }

  expiresSoon() {
    return this.accessExpiresAt - this.now() < REFRESH_EARLY_MS;
  }

  async loadUser() {
    /** @type {Principal} */
    const user = await this.deps.client.get("/auth/me");
    return user;
  }

  /**
   * Runs `task` while holding the cross-tab refresh lock (or directly where the Web Locks API
   * is unavailable, e.g. plain-HTTP LAN addresses, which are not secure contexts).
   * @template T
   * @param {() => Promise<T>} task
   * @returns {Promise<T>}
   */
  exclusive(task) {
    const locks = this.deps.locks;
    return locks ? /** @type {Promise<T>} */ (locks.request(LOCK_NAME, task)) : task();
  }

  /** @param {SessionMessage} message */
  post(message) {
    this.deps.channel?.postMessage(message);
  }
}

/** The hub refused the refresh token (revoked, expired, malformed): the session is over.
 * Rate limits and server errors are not refusals.
 * @param {unknown} error */
function isRefusal(error) {
  return (
    error instanceof ApiError &&
    error.kind === "http" &&
    [400, 401, 403, 422].includes(error.status)
  );
}

/**
 * Who an access token belongs to, read from its claims, or null if it can't be read. Only for
 * display and routing: the hub checks the token's signature on every request.
 * @param {string} token
 * @returns {Principal | null}
 */
export function principalFrom(token) {
  try {
    const payload = token.split(".")[1].replaceAll("-", "+").replaceAll("_", "/");
    const bytes = Uint8Array.from(atob(payload), (char) => char.charCodeAt(0));
    const claims = JSON.parse(new TextDecoder().decode(bytes));
    if (typeof claims.sub !== "string" || !["admin", "viewer"].includes(claims.role)) return null;
    return { username: claims.sub, role: claims.role };
  } catch {
    return null;
  }
}

// Storage can throw (blocked cookies, private modes, full quota): fall back to memory only.

/** @param {Storage | null} storage */
function read(storage) {
  try {
    return storage?.getItem(REFRESH_KEY) ?? null;
  } catch {
    return null;
  }
}

/** @param {Storage | null} storage @param {string} value */
function write(storage, value) {
  try {
    storage?.setItem(REFRESH_KEY, value);
  } catch {
    // memory only
  }
}

/** @param {Storage | null} storage */
function remove(storage) {
  try {
    storage?.removeItem(REFRESH_KEY);
  } catch {
    // nothing stored
  }
}
