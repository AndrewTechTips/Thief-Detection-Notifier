import { describe, expect, it, vi } from "vitest";

vi.mock("../state/auth.js", () => ({
  session: { state: { get: () => ({ user: { username: "andrew", role: "admin" } }) } },
}));
vi.mock("../state/devices.js", () => {
  const known = new Map([["porch", { id: "porch", name: "Front porch" }]]);
  return {
    devices: { get: () => known, subscribe: () => () => {} },
    deviceName: (/** @type {string} */ id) => known.get(id)?.name ?? id,
  };
});
vi.mock("../api/client.js", () => ({ api: {} }));

const { describeActor, describeEntry } = await import("./activity.js");

/** @param {Partial<import("../api/types.js").AuditEntry>} overrides */
const entry = (overrides) =>
  /** @type {import("../api/types.js").AuditEntry} */ ({
    id: "a1",
    at: "2026-10-04T12:00:00Z",
    actor: "admin",
    action: "device.started",
    target_type: "device",
    target_id: "porch",
    details: {},
    request_id: null,
    ...overrides,
  });

describe("describeActor", () => {
  it("names people the way they would say it", () => {
    expect(describeActor("andrew")).toBe("You");
    expect(describeActor("system")).toBe("The hub");
    expect(describeActor("cli:root")).toBe("root (command line)");
    expect(describeActor("maria")).toBe("maria");
  });
});

describe("describeEntry", () => {
  it("turns camera changes into sentences with a link to the camera", () => {
    const words = describeEntry(
      entry({ action: "device.updated", details: { fields: ["detection", "name"] } }),
    );
    expect(words).toMatchObject({
      who: "admin",
      did: "changed",
      target: "Front porch",
      link: "/devices/porch",
    });
    expect(words.detail).toBe("Changed detection settings and name.");
  });

  it("does not link removed or unknown cameras", () => {
    expect(describeEntry(entry({ action: "device.deleted" })).link).toBeNull();
    expect(describeEntry(entry({ target_id: "garage" })).link).toBeNull();
  });

  it("explains account changes without exposing anything secret", () => {
    const words = describeEntry(
      entry({
        actor: "system",
        action: "user.created",
        target_type: "user",
        target_id: "admin",
        details: { role: "admin", source: "VISION_HUB_SECURITY__ADMIN_PASSWORD_HASH" },
      }),
    );
    expect(words).toMatchObject({ who: "The hub", did: "created the account", target: "admin" });
    expect(words.detail).toBe("Role: Admin. From the hub's startup settings.");
  });

  it("keeps unknown actions readable", () => {
    expect(describeEntry(entry({ action: "device.renamed_twice" })).did).toBe(
      "device renamed twice",
    );
  });
});
