import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildUrl, createClient, parseRetryAfter } from "./client.js";
import { ApiError, describeError } from "./errors.js";

const BASE = "http://hub.test/api/v1";

/** @param {number} status @param {unknown} [body] @param {Record<string, string>} [headers] */
function reply(status, body, headers = {}) {
  const type = status >= 400 ? "application/problem+json" : "application/json";
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": type, ...headers },
  });
}

/** @param {number} status @param {string} code @param {object} [extra] */
const problem = (status, code, extra = {}) => ({
  type: `urn:vision-hub:problem:${code}`,
  title: "Problem",
  status,
  ...extra,
});

/** @type {import("vitest").Mock} */
let fetchMock;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** @param {Partial<import("./client.js").AuthHooks> & { initial?: string | null }} [overrides] */
function session({ initial = "old", ...overrides } = {}) {
  let token = initial;
  const hooks = {
    token: vi.fn(() => token),
    refresh: vi.fn(async () => {
      token = "new";
      return token;
    }),
    expired: vi.fn(),
    ...overrides,
  };
  return hooks;
}

/** Header value of the Nth fetch call. */
const sent = (/** @type {number} */ call, /** @type {string} */ header) =>
  new Headers(fetchMock.mock.calls[call][1].headers).get(header);

describe("requests", () => {
  it("sends JSON with a bearer token and parses the reply", async () => {
    fetchMock.mockResolvedValue(reply(200, { id: "porch" }));
    const api = createClient({ baseUrl: BASE, auth: session() });

    const body = await api.post("/devices", { json: { name: "Porch" } });

    expect(body).toEqual({ id: "porch" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`${BASE}/devices`);
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"name":"Porch"}');
    expect(init.credentials).toBe("omit");
    expect(sent(0, "Authorization")).toBe("Bearer old");
    expect(sent(0, "Content-Type")).toBe("application/json");
  });

  it("form-encodes `form` bodies and skips the token when auth is false", async () => {
    fetchMock.mockResolvedValue(reply(200, {}));
    const api = createClient({ baseUrl: BASE, auth: session() });

    await api.post("/auth/token", { form: { username: "admin", password: "x y" }, auth: false });

    const init = fetchMock.mock.calls[0][1];
    expect(String(init.body)).toBe("username=admin&password=x+y");
    expect(sent(0, "Authorization")).toBeNull();
  });

  it("returns undefined for empty responses", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const api = createClient({ baseUrl: BASE });

    await expect(api.delete("/devices/porch")).resolves.toBeUndefined();
  });

  it("builds query strings, skipping empty values and repeating arrays", () => {
    const url = buildUrl(BASE, "/events", {
      device: ["porch", "garage"],
      after: new Date("2026-10-04T10:00:00Z"),
      cursor: undefined,
      limit: 20,
      empty: null,
    });

    expect(url.search).toBe(
      "?device=porch&device=garage&after=2026-10-04T10%3A00%3A00.000Z&limit=20",
    );
  });
});

describe("errors", () => {
  it("turns problem documents into ApiError with code, retry and request id", async () => {
    fetchMock.mockResolvedValue(
      reply(429, problem(429, "rate-limited", { detail: "Slow down." }), {
        "Retry-After": "42",
        "X-Request-ID": "req-1",
      }),
    );
    const api = createClient({ baseUrl: BASE });

    const error = await api.post("/auth/token", { auth: false }).catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: "http", status: 429, retryAfter: 42, requestId: "req-1" });
    expect(error.code).toBe("rate-limited");
    expect(error.message).toBe("Slow down.");
    expect(describeError(error)).toEqual({
      title: "Too many attempts",
      detail: "Try again in 42 s.",
    });
  });

  it("maps validation errors by field", async () => {
    const body = problem(422, "validation", {
      errors: [{ loc: ["body", "name"], msg: "Field required", type: "missing" }],
    });
    fetchMock.mockResolvedValue(reply(422, body));
    const api = createClient({ baseUrl: BASE });

    const error = await api.post("/devices", { json: {} }).catch((e) => e);

    expect(error.fieldErrors.get("name")).toBe("Field required");
  });

  it("treats a bare 502 from the proxy as the hub being unreachable", async () => {
    fetchMock.mockResolvedValue(new Response("Bad Gateway", { status: 502 }));
    const api = createClient({ baseUrl: BASE });

    const error = await api.get("/devices").catch((e) => e);

    expect(error).toMatchObject({ kind: "network", status: 502, problem: null });
    expect(describeError(error).title).toBe("Can't reach the hub");
  });

  it("reports network failures", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const api = createClient({ baseUrl: BASE });

    await expect(api.get("/devices")).rejects.toMatchObject({ kind: "network", status: 0 });
  });

  it("times out slow requests, including slow bodies", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(
      (_url, /** @type {RequestInit} */ init) =>
        new Promise((_, reject) =>
          init.signal?.addEventListener("abort", () => reject(init.signal?.reason)),
        ),
    );
    const api = createClient({ baseUrl: BASE, timeout: 1000 });

    const pending = api.get("/devices").catch((e) => e);
    await vi.advanceTimersByTimeAsync(1000);

    expect(await pending).toMatchObject({ kind: "timeout" });
  });

  it("lets caller cancellation through as an AbortError", async () => {
    fetchMock.mockImplementation(
      (_url, /** @type {RequestInit} */ init) =>
        new Promise((_, reject) =>
          init.signal?.addEventListener("abort", () => reject(init.signal?.reason)),
        ),
    );
    const api = createClient({ baseUrl: BASE });
    const controller = new AbortController();

    const pending = api.get("/devices", { signal: controller.signal }).catch((e) => e);
    controller.abort();

    const error = await pending;
    expect(error).not.toBeInstanceOf(ApiError);
    expect(error.name).toBe("AbortError");
  });

  it("rejects a non-JSON success response", async () => {
    fetchMock.mockResolvedValue(
      new Response("<html>", { status: 200, headers: { "Content-Type": "text/html" } }),
    );
    const api = createClient({ baseUrl: BASE });

    await expect(api.get("/devices")).rejects.toBeInstanceOf(ApiError);
  });

  it("parses Retry-After as seconds or an HTTP date", () => {
    const now = Date.parse("2026-10-04T10:00:00Z");
    expect(parseRetryAfter("120", now)).toBe(120);
    expect(parseRetryAfter("Sun, 04 Oct 2026 10:00:30 GMT", now)).toBe(30);
    expect(parseRetryAfter("soon", now)).toBeNull();
    expect(parseRetryAfter(null, now)).toBeNull();
  });
});

describe("token refresh", () => {
  it("refreshes once for concurrent 401s and retries each request", async () => {
    fetchMock.mockImplementation(async (_url, /** @type {RequestInit} */ init) =>
      new Headers(init.headers).get("Authorization") === "Bearer new"
        ? reply(200, { ok: true })
        : reply(401, problem(401, "unauthenticated")),
    );
    const hooks = session();
    const api = createClient({ baseUrl: BASE, auth: hooks });

    const results = await Promise.all([api.get("/a"), api.get("/b"), api.get("/c")]);

    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }]);
    expect(hooks.refresh).toHaveBeenCalledTimes(1);
    expect(hooks.expired).not.toHaveBeenCalled();
  });

  it("uses a token refreshed by another request instead of refreshing again", async () => {
    const hooks = session();
    fetchMock
      .mockImplementationOnce(async () => {
        await hooks.refresh(); // someone else refreshed while this request was in flight
        return reply(401, problem(401, "unauthenticated"));
      })
      .mockResolvedValueOnce(reply(200, { ok: true }));
    const api = createClient({ baseUrl: BASE, auth: hooks });

    await expect(api.get("/a")).resolves.toEqual({ ok: true });
    expect(hooks.refresh).toHaveBeenCalledTimes(1);
    expect(sent(1, "Authorization")).toBe("Bearer new");
  });

  it("signs out once when the refresh is refused", async () => {
    fetchMock.mockImplementation(async () => reply(401, problem(401, "unauthenticated")));
    const hooks = session({ refresh: vi.fn(async () => null) });
    const api = createClient({ baseUrl: BASE, auth: hooks });

    const errors = await Promise.all([api.get("/a"), api.get("/b")].map((p) => p.catch((e) => e)));

    expect(errors.map((e) => e.status)).toEqual([401, 401]);
    expect(hooks.refresh).toHaveBeenCalledTimes(1);
    expect(hooks.expired).toHaveBeenCalledTimes(1);
    expect(describeError(errors[0]).title).toBe("Session expired");
  });

  it("keeps the session when the refresh fails on the network", async () => {
    fetchMock.mockImplementation(async () => reply(401, problem(401, "unauthenticated")));
    const offline = new ApiError({ kind: "network" });
    const hooks = session({ refresh: vi.fn(() => Promise.reject(offline)) });
    const api = createClient({ baseUrl: BASE, auth: hooks });

    await expect(api.get("/a")).rejects.toBe(offline);
    expect(hooks.expired).not.toHaveBeenCalled();
  });

  it("retries only once: a refused fresh token ends the session", async () => {
    fetchMock.mockImplementation(async () => reply(401, problem(401, "unauthenticated")));
    const hooks = session();
    const api = createClient({ baseUrl: BASE, auth: hooks });

    await expect(api.get("/a")).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(hooks.expired).toHaveBeenCalledTimes(1);
  });

  it("gets an access token first when only the refresh token is left", async () => {
    fetchMock.mockResolvedValue(reply(200, { ok: true }));
    const hooks = session({ initial: null });
    const api = createClient({ baseUrl: BASE, auth: hooks });

    await api.get("/a");

    expect(hooks.refresh).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sent(0, "Authorization")).toBe("Bearer new");
  });

  it("fails without a request when there is no session at all", async () => {
    const hooks = session({ initial: null, refresh: vi.fn(async () => null) });
    const api = createClient({ baseUrl: BASE, auth: hooks });

    await expect(api.get("/a")).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(hooks.expired).toHaveBeenCalledTimes(1);
  });

  it("does not refresh for requests sent without auth", async () => {
    fetchMock.mockImplementation(async () => reply(401, problem(401, "unauthenticated")));
    const hooks = session();
    const api = createClient({ baseUrl: BASE, auth: hooks });

    await expect(api.post("/auth/token", { auth: false })).rejects.toMatchObject({ status: 401 });
    expect(hooks.refresh).not.toHaveBeenCalled();
  });
});
