import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const shown = vi.hoisted(() => /** @type {any[]} */ ([]));
const dismissed = vi.hoisted(() => /** @type {string[]} */ ([]));

vi.mock("../ui/toast.js", () => ({
  toast: (/** @type {any} */ options) => shown.push(options),
  dismissToast: (/** @type {string} */ key) => dismissed.push(key),
}));
vi.mock("../state/devices.js", () => ({
  deviceName: (/** @type {string} */ id) =>
    ({ porch: "Front porch", garage: "Garage", gate: "Gate" })[id] ?? id,
  // The gate camera alerts on people only.
  alertsOn: (/** @type {string} */ id) => (id === "gate" ? "person" : "motion"),
}));
vi.mock("../api/events.js", () => ({
  getEvent: async (/** @type {string} */ id) => ({ id, snapshots: [] }),
  snapshotUrl: (/** @type {any} */ event) => `/thumb/${event.id}`,
}));

const { startAlerts } = await import("./alerts.js");

/** A realtime stand-in: the test pushes server messages through it. */
function fakeRealtime() {
  /** @type {Set<(message: any) => void>} */
  const listeners = new Set();
  return {
    listen(/** @type {(message: any) => void} */ listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    send(/** @type {any} */ message) {
      listeners.forEach((listener) => listener(message));
    },
  };
}

const started = (/** @type {string} */ device, /** @type {string} */ id, replay = false) => ({
  type: "motion.started",
  device_id: device,
  replay,
  data: { event_id: id, started_at: "2026-10-04T10:00:00Z" },
});
const ended = (
  /** @type {string} */ device,
  /** @type {string} */ id,
  replay = false,
  { person = /** @type {boolean | null} */ (null), alert = true } = {},
) => ({
  type: "motion.ended",
  device_id: device,
  replay,
  data: {
    event_id: id,
    started_at: "2026-10-04T10:00:00Z",
    ended_at: "2026-10-04T10:00:12Z",
    peak_area_ratio: 0.2,
    motion_frames: 30,
    boxes: [],
    person,
    person_confidence: person === null ? null : person ? 0.9 : 0.1,
    alert,
  },
});
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/** @type {EventTarget & { hidden: boolean, title: string }} */
let doc;

beforeEach(() => {
  shown.length = 0;
  dismissed.length = 0;
  doc = Object.assign(new EventTarget(), { hidden: false, title: "Live – Vision Hub" });
  vi.stubGlobal("document", doc);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("motion alerts", () => {
  it("shows motion while it lasts, then the result with a snapshot, in one toast", async () => {
    const realtime = fakeRealtime();
    const navigate = vi.fn();
    startAlerts(/** @type {any} */ (realtime), { navigate });

    realtime.send(started("porch", "e1"));
    realtime.send(ended("porch", "e1"));
    await settle();

    expect(shown).toHaveLength(2);
    expect(shown[0]).toMatchObject({
      key: "motion:porch",
      title: "Motion on Front porch",
      duration: 0,
    });
    expect(shown[1]).toMatchObject({ key: "motion:porch", media: "/thumb/e1" });
    expect(shown[1].message).toMatch(/for 12 s\.$/);
    shown[1].action.run();
    expect(navigate).toHaveBeenCalledWith("/events?event=e1");
  });

  it("does not let an older event's end overwrite newer motion on the same camera", async () => {
    const realtime = fakeRealtime();
    startAlerts(/** @type {any} */ (realtime), { navigate: vi.fn() });

    realtime.send(started("porch", "e1"));
    realtime.send(ended("porch", "e1"));
    realtime.send(started("porch", "e2")); // before e1's snapshot arrived
    await settle();

    expect(shown.at(-1)).toMatchObject({ duration: 0, message: expect.stringMatching(/^Started/) });
  });

  it("people-only cameras toast once a person was seen, and only then", async () => {
    const realtime = fakeRealtime();
    startAlerts(/** @type {any} */ (realtime), { navigate: vi.fn() });

    realtime.send(started("gate", "e1"));
    realtime.send(ended("gate", "e1", false, { person: false, alert: false }));
    realtime.send(started("gate", "e2"));
    realtime.send(ended("gate", "e2", false, { person: true }));
    await settle();

    expect(shown).toHaveLength(1);
    expect(shown[0]).toMatchObject({ key: "motion:gate", title: "Person at Gate" });
  });

  it("takes back 'motion now' when the event turns out not to alert", async () => {
    // The camera was switched to people only while the motion went on.
    const realtime = fakeRealtime();
    startAlerts(/** @type {any} */ (realtime), { navigate: vi.fn() });

    realtime.send(started("porch", "e1"));
    realtime.send(ended("porch", "e1", false, { person: false, alert: false }));
    await settle();

    expect(shown).toHaveLength(1); // only "motion now"
    expect(dismissed).toEqual(["motion:porch"]);
  });

  it("does not count replayed events that did not alert", () => {
    const realtime = fakeRealtime();
    startAlerts(/** @type {any} */ (realtime), { navigate: vi.fn() });

    realtime.send(ended("gate", "e1", true, { person: false, alert: false }));
    realtime.send({ type: "replay.done", data: { replayed: 1, truncated: false } });

    expect(shown).toEqual([]);
  });

  it("sums up replayed events in one toast", () => {
    const realtime = fakeRealtime();
    startAlerts(/** @type {any} */ (realtime), { navigate: vi.fn() });

    realtime.send(started("porch", "e1", true));
    realtime.send(ended("porch", "e1", true));
    realtime.send(ended("garage", "e2", true));
    realtime.send({ type: "replay.done", data: { count: 3, truncated: false } });

    expect(shown).toHaveLength(1);
    expect(shown[0]).toMatchObject({ title: "While you were away" });
    expect(shown[0].message).toBe("3 motion events on your cameras.");
  });

  it("finishes an alert left open by a dropped connection when its end is replayed", async () => {
    const realtime = fakeRealtime();
    startAlerts(/** @type {any} */ (realtime), { navigate: vi.fn() });

    realtime.send(started("porch", "e1"));
    realtime.send(ended("porch", "e1", true));
    realtime.send({ type: "replay.done", data: { count: 1, truncated: false } });
    await settle();

    expect(shown.map((toast) => toast.title)).toEqual([
      "Motion on Front porch",
      "Motion on Front porch",
    ]);
    expect(shown[1].duration).toBeGreaterThan(0);
  });

  it("counts alerts in the title while the tab is hidden", async () => {
    const realtime = fakeRealtime();
    startAlerts(/** @type {any} */ (realtime), { navigate: vi.fn() });

    doc.hidden = true;
    realtime.send(started("porch", "e1"));
    realtime.send(started("garage", "e2"));
    expect(doc.title).toBe("(2) Live – Vision Hub");

    doc.hidden = false;
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(doc.title).toBe("Live – Vision Hub");
  });
});
