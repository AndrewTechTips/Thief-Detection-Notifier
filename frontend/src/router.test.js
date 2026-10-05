import { describe, expect, it } from "vitest";

import { compile, match, safeRedirect } from "./router.js";

const view = { default: { title: "", mount() {} } };
const routes = [
  { path: "/", load: async () => view },
  { path: "/events", load: async () => view },
  { path: "/devices/:id", load: async () => view },
];

describe("compile", () => {
  it("matches static paths with an optional trailing slash", () => {
    expect(compile("/events").test("/events")).toBe(true);
    expect(compile("/events").test("/events/")).toBe(true);
    expect(compile("/events").test("/events/x")).toBe(false);
  });

  it("escapes regex characters in static parts", () => {
    expect(compile("/a.b").test("/axb")).toBe(false);
  });
});

describe("match", () => {
  it("picks the route and extracts decoded params", () => {
    const found = match(routes, "/devices/front%20door");
    expect(found.route?.path).toBe("/devices/:id");
    expect(found.params).toEqual({ id: "front door" });
  });

  it("does not let a param span segments", () => {
    expect(match(routes, "/devices/a/b").route).toBeNull();
  });

  it("returns no route for unknown paths and malformed escapes", () => {
    expect(match(routes, "/nope").route).toBeNull();
    expect(match(routes, "/devices/%E0%A4%A").route).toBeNull();
  });

  it("matches the root only exactly", () => {
    expect(match(routes, "/").route?.path).toBe("/");
    expect(match(routes, "/eventsx").route).toBeNull();
  });
});

describe("safeRedirect", () => {
  const origin = "http://hub.test";

  it("keeps paths on this site, with their query and hash", () => {
    expect(safeRedirect("/events?device=porch#latest", origin)).toBe("/events?device=porch#latest");
  });

  it.each([
    null,
    "",
    "events",
    "https://evil.example/",
    "//evil.example/path",
    "/\\evil.example",
    "/\t/evil.example",
    "javascript:alert(1)",
  ])("sends %j home", (value) => {
    expect(safeRedirect(value, origin)).toBe("/");
  });

  it("never returns to a page it should avoid", () => {
    expect(safeRedirect("/login?next=/x", origin, ["/login"])).toBe("/");
  });
});

describe("isInternalClick", () => {
  it("leaves same-page fragment links (like a skip link) to the browser", async () => {
    const { isInternalClick } = await import("./router.js");
    const location = { pathname: "/events", search: "", origin: "http://hub.test" };
    globalThis.location = /** @type {any} */ (location);
    /** @param {string} href */
    const link = (href) => {
      const url = new URL(href, "http://hub.test/events");
      return /** @type {any} */ ({
        target: "",
        origin: url.origin,
        pathname: url.pathname,
        search: url.search,
        hash: url.hash,
        hasAttribute: () => false,
      });
    };
    const click = /** @type {any} */ ({ defaultPrevented: false, button: 0 });

    expect(isInternalClick(click, link("#main"))).toBe(false);
    expect(isInternalClick(click, link("/devices/porch"))).toBe(true);
    expect(isInternalClick(click, link("/#main"))).toBe(true);
    expect(isInternalClick(click, link("/api/v1/events/1/snapshot"))).toBe(false);
  });
});
