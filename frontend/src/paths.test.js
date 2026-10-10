import { afterEach, describe, expect, it, vi } from "vitest";

/** @param {string} base */
async function pathsAt(base) {
  vi.resetModules();
  vi.stubEnv("BASE_URL", base);
  return import("./paths.js");
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("at the site root (the hub)", () => {
  it("leaves paths as they are", async () => {
    const { appPath, href } = await pathsAt("/");

    expect(href("/devices/porch")).toBe("/devices/porch");
    expect(appPath("/events")).toBe("/events");
  });
});

describe("under a subdirectory (the public demo)", () => {
  it("adds and removes the base", async () => {
    const { appPath, href } = await pathsAt("/iot-vision-hub/");

    expect(href("/")).toBe("/iot-vision-hub/");
    expect(href("/events?people=1")).toBe("/iot-vision-hub/events?people=1");
    expect(href("https://example.com/")).toBe("https://example.com/");
    expect(appPath("/iot-vision-hub/devices/porch")).toBe("/devices/porch");
    expect(appPath("/iot-vision-hub")).toBe("/");
  });

  it("knows what lies outside the dashboard", async () => {
    const { appPath } = await pathsAt("/iot-vision-hub/");

    expect(appPath("/other-project/")).toBeNull();
    expect(appPath("/iot-vision-hubx/")).toBeNull();
  });
});
