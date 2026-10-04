import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/errors.js";
import { REFRESH_KEY, Session } from "./session.js";

/** A hub that rotates refresh tokens and rejects any reuse, like the real one. */
function fakeHub() {
  let issued = 0;
  const live = new Set();
  const stats = { refresh: 0, reused: 0, logout: 0 };
  /** @type {null | (() => never)} */
  let failRefresh = null;
  const unauthorized = () => {
    throw new ApiError({ kind: "http", status: 401 });
  };
  const issue = () => {
    issued += 1;
    live.add(`r${issued}`);
    return {
      access_token: `a${issued}`,
      token_type: "bearer",
      expires_in: 900,
      refresh_token: `r${issued}`,
      refresh_expires_in: 604800,
    };
  };
  const client = {
    /** @param {string} path @param {any} options */
    async post(path, options) {
      await new Promise((resolve) => setTimeout(resolve, 5)); // network latency
      if (path === "/auth/token") return options.form.password === "pw" ? issue() : unauthorized();
      if (path === "/auth/refresh") {
        stats.refresh += 1;
        if (failRefresh) failRefresh();
        if (!live.delete(options.json.refresh_token)) {
          stats.reused += 1;
          unauthorized();
        }
        return issue();
      }
      if (path === "/auth/logout") {
        stats.logout += 1;
        live.delete(options.json.refresh_token);
        return undefined;
      }
      throw new Error(`unexpected POST ${path}`);
    },
    /** @param {string} path */
    async get(path) {
      if (path === "/auth/me") return { username: "admin", role: "admin" };
      throw new Error(`unexpected GET ${path}`);
    },
  };
  return {
    client,
    stats,
    live,
    /** @param {null | (() => never)} fail */
    failRefreshWith(fail) {
      failRefresh = fail;
    },
  };
}

/** Minimal Storage over a Map. */
function memoryStorage() {
  const map = new Map();
  return /** @type {Storage} */ (
    /** @type {unknown} */ ({
      getItem: (/** @type {string} */ key) => map.get(key) ?? null,
      setItem: (/** @type {string} */ key, /** @type {string} */ value) => map.set(key, value),
      removeItem: (/** @type {string} */ key) => map.delete(key),
    })
  );
}

/** One lock manager shared by all "tabs": tasks with the same name run one at a time. */
function sharedLocks() {
  /** @type {Map<string, Promise<unknown>>} */
  const tails = new Map();
  return /** @type {LockManager} */ (
    /** @type {unknown} */ ({
      /** @param {string} name @param {() => Promise<unknown>} task */
      request(name, task) {
        const run = (tails.get(name) ?? Promise.resolve()).catch(() => {}).then(task);
        tails.set(name, run);
        return run;
      },
    })
  );
}

let channelId = 0;
/** @type {BroadcastChannel[]} */
const channels = [];

afterEach(() => {
  channels.splice(0).forEach((channel) => channel.close());
  vi.useRealTimers();
});

/** A browser with shared localStorage and locks; each tab has its own sessionStorage. */
function browser(hub = fakeHub()) {
  const name = `session-test-${++channelId}`;
  const local = memoryStorage();
  const locks = sharedLocks();
  const visibility = Object.assign(new EventTarget(), { hidden: false });
  const tab = () => {
    const channel = new BroadcastChannel(name);
    channels.push(channel);
    return new Session({
      client: /** @type {any} */ (hub.client),
      local,
      ephemeral: memoryStorage(),
      channel,
      locks,
      visibility: /** @type {any} */ (visibility),
    });
  };
  return { hub, local, visibility, tab };
}

/** Lets BroadcastChannel messages arrive. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

describe("signing in", () => {
  it("keeps the access token in memory and the refresh token where asked", async () => {
    const { local, tab } = browser();
    const kept = tab();
    const temporary = tab();

    await kept.signIn("admin", "pw", true);
    expect(local.getItem(REFRESH_KEY)).toBe(kept.refreshToken);
    expect(kept.deps.ephemeral?.getItem(REFRESH_KEY)).toBeNull();
    expect(kept.token()).toMatch(/^a/);

    await temporary.signOut();
    await temporary.signIn("admin", "pw", false);
    expect(temporary.deps.ephemeral?.getItem(REFRESH_KEY)).toBe(temporary.refreshToken);
    expect(local.getItem(REFRESH_KEY)).toBeNull();
  });

  it("reports wrong passwords without changing state", async () => {
    const { tab } = browser();
    const session = tab();
    session.state.set({ status: "signed-out", user: null, reason: null });

    await expect(session.signIn("admin", "nope", false)).rejects.toMatchObject({ status: 401 });
    expect(session.state.get().status).toBe("signed-out");
  });

  it("signs in other tabs waiting at the sign-in page", async () => {
    const { tab } = browser();
    const first = tab();
    const second = tab();
    second.state.set({ status: "signed-out", user: null, reason: null });

    await first.signIn("admin", "pw", true);
    await settle();

    expect(second.state.get()).toMatchObject({ status: "signed-in", user: { username: "admin" } });
    expect(second.token()).toBe(first.token());
  });
});

describe("restoring", () => {
  it("resumes a stored session after a reload", async () => {
    const { tab } = browser();
    await tab().signIn("admin", "pw", true);

    const reloaded = tab();
    await reloaded.restore();

    expect(reloaded.state.get()).toMatchObject({ status: "signed-in", user: { role: "admin" } });
    expect(reloaded.token()).not.toBeNull();
  });

  it("is signed out, without a reason, when nothing is stored", async () => {
    const session = browser().tab();

    await session.restore();

    expect(session.state.get()).toEqual({ status: "signed-out", user: null, reason: null });
  });

  it("clears a refresh token the hub refuses", async () => {
    const { hub, local, tab } = browser();
    await tab().signIn("admin", "pw", true);
    hub.live.clear(); // revoked on the hub

    const reloaded = tab();
    await reloaded.restore();

    expect(reloaded.state.get()).toMatchObject({ status: "signed-out", reason: "expired" });
    expect(local.getItem(REFRESH_KEY)).toBeNull();
  });

  it("keeps the stored token when the hub can't be reached", async () => {
    const { hub, local, tab } = browser();
    await tab().signIn("admin", "pw", true);
    hub.failRefreshWith(() => {
      throw new ApiError({ kind: "network" });
    });

    const reloaded = tab();
    await expect(reloaded.restore()).rejects.toMatchObject({ kind: "network" });
    expect(local.getItem(REFRESH_KEY)).not.toBeNull();
  });
});

describe("refreshing", () => {
  it("never reuses a refresh token when tabs refresh at the same time", async () => {
    const { hub, tab } = browser();
    const tabs = [tab(), tab(), tab()];
    await tabs[0].signIn("admin", "pw", true);
    await settle();
    await Promise.all(tabs.slice(1).map((session) => session.restore()));
    hub.stats.reused = 0;

    // Every tab finds its access token rejected at once.
    await Promise.all(tabs.map((session) => session.rotate()));
    await settle();

    expect(hub.stats.reused).toBe(0);
    expect(tabs.every((session) => session.token() !== null)).toBe(true);
    expect(tabs.every((session) => session.state.get().status === "signed-in")).toBe(true);
  });

  it("does not hand back the token the hub just rejected", async () => {
    const { tab } = browser();
    const session = tab();
    await session.signIn("admin", "pw", false);
    const rejected = session.token();

    const fresh = await session.rotate();

    expect(fresh).not.toBe(rejected);
  });

  it("treats a refused refresh as the end of the session, but not a rate limit", async () => {
    const { hub, tab } = browser();
    const session = tab();
    await session.signIn("admin", "pw", false);

    hub.failRefreshWith(() => {
      throw new ApiError({ kind: "http", status: 429, retryAfter: 30 });
    });
    await expect(session.rotate()).rejects.toMatchObject({ status: 429 });

    hub.failRefreshWith(null);
    hub.live.clear();
    await expect(session.rotate()).resolves.toBeNull();
  });

  it("refreshes a minute before expiry, only while the tab is visible", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const { hub, visibility, tab } = browser();
    const session = tab();
    const signingIn = session.signIn("admin", "pw", false);
    await vi.advanceTimersByTimeAsync(20);
    await signingIn;

    visibility.hidden = true;
    await vi.advanceTimersByTimeAsync(840_000); // 15 min token, refresh due at 14 min
    expect(hub.stats.refresh).toBe(0);

    visibility.hidden = false;
    visibility.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(20);
    expect(hub.stats.refresh).toBe(1);

    await vi.advanceTimersByTimeAsync(840_000);
    expect(hub.stats.refresh).toBe(2);
  });
});

describe("signing out", () => {
  it("signs out every tab and revokes the token on the hub", async () => {
    const { hub, local, tab } = browser();
    const first = tab();
    const second = tab();
    await first.signIn("admin", "pw", true);
    await settle();
    await second.restore();

    await first.signOut();
    await settle();

    expect(second.state.get()).toMatchObject({ status: "signed-out", reason: "signed-out" });
    expect(second.token()).toBeNull();
    expect(local.getItem(REFRESH_KEY)).toBeNull();
    expect(hub.stats.logout).toBe(1);
  });

  it("an expired session in one tab leaves the others signed in", async () => {
    const { tab } = browser();
    const first = tab();
    const second = tab();
    await first.signIn("admin", "pw", true);
    await settle();
    await second.restore();

    first.end("expired");
    await settle();

    expect(first.state.get()).toMatchObject({ status: "signed-out", reason: "expired" });
    expect(second.state.get().status).toBe("signed-in");
  });

  it("is idempotent and keeps the first reason", async () => {
    const session = browser().tab();
    await session.signIn("admin", "pw", false);

    session.end("expired");
    session.end("signed-out");

    expect(session.state.get().reason).toBe("expired");
  });
});

it("works in memory when storage is blocked", async () => {
  const hub = fakeHub();
  const blocked = /** @type {Storage} */ (
    /** @type {unknown} */ ({
      getItem() {
        throw new Error("SecurityError");
      },
      setItem() {
        throw new Error("SecurityError");
      },
      removeItem() {
        throw new Error("SecurityError");
      },
    })
  );
  const session = new Session({
    client: /** @type {any} */ (hub.client),
    local: blocked,
    ephemeral: blocked,
    channel: null,
    locks: null,
    visibility: null,
  });

  await session.signIn("admin", "pw", true);

  expect(session.token()).not.toBeNull();
  await expect(session.rotate()).resolves.toMatch(/^a/);
});
