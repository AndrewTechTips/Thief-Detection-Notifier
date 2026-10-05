import { createHash } from "node:crypto";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** The hub as the API client sees it: every call recorded, any of them made to fail. */
const hub = vi.hoisted(() => ({
  /** @type {[string, string, any][]} */
  calls: [],
  push: () => ({ public_key: "" }),
  /** @type {(method: string, path: string) => Error | null} */
  fail: () => null,
}));

vi.mock("./api/client.js", () => {
  /** @param {string} method */
  const verb = (method) => async (/** @type {string} */ path, /** @type {any} */ options) => {
    hub.calls.push([method, path, options?.json]);
    const error = hub.fail(method, path);
    if (error) throw error;
    if (method === "GET" && path === "/push") return hub.push();
    if (path === "/push/test") return { delivered: 1 };
    return undefined;
  };
  return { api: { get: verb("GET"), post: verb("POST"), delete: verb("DELETE") } };
});

const { ApiError } = await import("./api/errors.js");
const { base64UrlBytes, disablePush, enablePush, push, subscriptionId, syncPush } =
  await import("./push.js");

const KEY =
  "BHubKey0000000000000000000000000000000000000000000000000000000000000000000000000000000";
const OLD_KEY =
  "BOldKey0000000000000000000000000000000000000000000000000000000000000000000000000000000";

/** A browser's push subscription. */
function subscription(key = KEY, endpoint = "https://fcm.googleapis.com/fcm/send/abc") {
  return {
    endpoint,
    options: { applicationServerKey: base64UrlBytes(key).buffer },
    unsubscribe: vi.fn(async () => true),
    toJSON: () => ({ endpoint, expirationTime: null, keys: { p256dh: "p", auth: "a" } }),
  };
}

/** A browser that supports push, with `current` as its subscription. */
function browser({
  permission = "default",
  current = /** @type {any} */ (null),
  ua = "Chrome",
} = {}) {
  const state = { current, permission, requested: 0 };
  const pushManager = {
    getSubscription: async () => state.current,
    subscribe: vi.fn(async (/** @type {any} */ options) => {
      state.current = subscription(
        btoa(String.fromCharCode(...options.applicationServerKey))
          .replaceAll("+", "-")
          .replaceAll("/", "_")
          .replace(/=+$/, ""),
        "https://fcm.googleapis.com/fcm/send/new",
      );
      return state.current;
    }),
  };
  const registration = { pushManager };
  vi.stubGlobal("navigator", {
    userAgent: ua,
    platform: "MacIntel",
    maxTouchPoints: 0,
    serviceWorker: {
      ready: Promise.resolve(registration),
      getRegistration: async () => registration,
    },
  });
  vi.stubGlobal("window", { PushManager: class {}, Notification: class {} });
  vi.stubGlobal("Notification", {
    get permission() {
      return state.permission;
    },
    requestPermission: async () => {
      state.requested += 1;
      return state.permission === "default" ? "granted" : state.permission;
    },
  });
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  /** @type {Map<string, string>} */
  const stored = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (/** @type {string} */ k) => stored.get(k) ?? null,
    setItem: (/** @type {string} */ k, /** @type {string} */ v) => stored.set(k, v),
    removeItem: (/** @type {string} */ k) => stored.delete(k),
  });
  return { state, pushManager, stored };
}

beforeEach(() => {
  vi.stubEnv("PROD", true);
  hub.calls = [];
  hub.push = () => ({ public_key: KEY });
  hub.fail = () => null;
  push.set({ state: "unsupported", busy: false });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("syncPush", () => {
  it("stays hidden where push can't work", async () => {
    browser();
    vi.stubGlobal("window", {});

    await syncPush();

    expect(push.get().state).toBe("unsupported");
  });

  it("asks iPhone users to add the app to the Home Screen first", async () => {
    browser({ ua: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)" });
    vi.stubGlobal("window", {});

    await syncPush();

    expect(push.get().state).toBe("install");
  });

  it("hides the switch when the hub has push turned off", async () => {
    browser();
    hub.push = () => {
      throw new ApiError({ kind: "http", status: 404 });
    };

    await syncPush();

    expect(push.get().state).toBe("hub-off");
  });

  it("is off without a subscription, and says so when blocked", async () => {
    browser();
    await syncPush();
    expect(push.get().state).toBe("off");

    browser({ permission: "denied" });
    await syncPush();
    expect(push.get().state).toBe("denied");
  });

  it("tells the hub again about an existing subscription (maybe another account made it)", async () => {
    browser({ permission: "granted", current: subscription() });

    await syncPush();

    expect(push.get()).toEqual({ state: "on", busy: false });
    expect(hub.calls).toContainEqual([
      "POST",
      "/push/subscriptions",
      { endpoint: "https://fcm.googleapis.com/fcm/send/abc", keys: { p256dh: "p", auth: "a" } },
    ]);
  });

  it("replaces a subscription made with an old hub key", async () => {
    const old = subscription(OLD_KEY);
    const { pushManager, stored } = browser({ permission: "granted", current: old });
    stored.set("vision-hub.push", "on");

    await syncPush();

    expect(old.unsubscribe).toHaveBeenCalled();
    expect(pushManager.subscribe).toHaveBeenCalledOnce();
    expect(push.get().state).toBe("on");
  });

  it("subscribes again when the browser dropped a wanted subscription", async () => {
    const { pushManager, stored } = browser({ permission: "granted" });
    stored.set("vision-hub.push", "on");

    await syncPush();

    expect(pushManager.subscribe).toHaveBeenCalledWith(
      expect.objectContaining({ userVisibleOnly: true }),
    );
    expect(push.get().state).toBe("on");
  });
});

describe("enablePush", () => {
  it("asks, subscribes with the hub's key and registers with the hub", async () => {
    const { pushManager, state, stored } = browser();

    expect(await enablePush()).toBe(true);

    expect(state.requested).toBe(1);
    const [[options]] = pushManager.subscribe.mock.calls;
    expect(options.applicationServerKey).toEqual(base64UrlBytes(KEY));
    expect(hub.calls.at(-1)?.[1]).toBe("/push/subscriptions");
    expect(push.get()).toEqual({ state: "on", busy: false });
    expect(stored.get("vision-hub.push")).toBe("on");
  });

  it("stops at a refusal", async () => {
    const { pushManager } = browser({ permission: "denied" });

    expect(await enablePush()).toBe(false);

    expect(pushManager.subscribe).not.toHaveBeenCalled();
    expect(push.get().state).toBe("denied");
  });

  it("leaves no subscription behind when the hub refuses it", async () => {
    const { state } = browser();
    const refused = new ApiError({ kind: "http", status: 400 });
    hub.fail = (method, path) => (path === "/push/subscriptions" ? refused : null);

    await expect(enablePush()).rejects.toBe(refused);

    expect(state.current.unsubscribe).toHaveBeenCalled();
    expect(push.get()).toEqual({ state: "unsupported", busy: false }); // unchanged
  });
});

describe("disablePush", () => {
  it("unsubscribes here and on the hub, by the endpoint's SHA-256", async () => {
    const current = subscription();
    const { stored } = browser({ permission: "granted", current });
    stored.set("vision-hub.push", "on");
    push.set({ state: "on", busy: false });

    await disablePush();

    const id = createHash("sha256").update(current.endpoint).digest("hex");
    expect(hub.calls).toContainEqual(["DELETE", `/push/subscriptions/${id}`, undefined]);
    expect(current.unsubscribe).toHaveBeenCalled();
    expect(push.get().state).toBe("off");
    expect(stored.has("vision-hub.push")).toBe(false);
  });

  it("still unsubscribes when the hub can't be told", async () => {
    const current = subscription();
    browser({ permission: "granted", current });
    hub.fail = (method) => (method === "DELETE" ? new ApiError({ kind: "network" }) : null);

    await disablePush();

    expect(current.unsubscribe).toHaveBeenCalled();
  });
});

describe("helpers", () => {
  it("names a subscription as the hub does", async () => {
    const endpoint = "https://web.push.apple.com/QGx";

    expect(await subscriptionId(endpoint)).toBe(
      createHash("sha256").update(endpoint).digest("hex"),
    );
  });

  it("decodes unpadded base64url", () => {
    expect([...base64UrlBytes("-_8")]).toEqual([0xfb, 0xff]);
  });
});
