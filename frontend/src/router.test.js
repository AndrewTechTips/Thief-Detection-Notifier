import { describe, expect, it } from "vitest";

import { compile, match } from "./router.js";

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
