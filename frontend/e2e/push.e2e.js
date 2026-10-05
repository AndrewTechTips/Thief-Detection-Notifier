import { createHash } from "node:crypto";

import { expect, test } from "@playwright/test";

import { isPhone, signedIn } from "./fixtures.js";

// The full Chromium: the default headless shell has no notifications (permission is always
// "denied", which the dashboard then reports as blocked).
test.use({ channel: "chromium" });

// Test browsers have no push service, so pages get a stand-in PushManager: real P-256 keys, on
// an endpoint the test hub accepts but can never reach (see e2e/start-hub.sh).
/** @param {import("@playwright/test").BrowserContext} context */
async function fakePushManager(context) {
  await context.addInitScript(() => {
    /** @type {any} */
    let current = null;
    /** @param {Uint8Array} bytes */
    const base64url = (bytes) =>
      btoa(String.fromCharCode(...bytes))
        .replaceAll("+", "-")
        .replaceAll("/", "_")
        .replace(/=+$/, "");
    PushManager.prototype.getSubscription = async () => current;
    PushManager.prototype.subscribe = async (/** @type {any} */ options) => {
      const pair = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, [
        "deriveBits",
      ]);
      const publicKey = new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey));
      const auth = crypto.getRandomValues(new Uint8Array(16));
      const endpoint = `https://push.e2e.invalid/${crypto.randomUUID()}`;
      current = {
        endpoint,
        options: { applicationServerKey: new Uint8Array(options.applicationServerKey).buffer },
        toJSON: () => ({
          endpoint,
          expirationTime: null,
          keys: { p256dh: base64url(publicKey), auth: base64url(auth) },
        }),
        unsubscribe: async () => {
          current = null;
          return true;
        },
      };
      return current;
    };
  });
}

/** The switch, wherever this layout keeps it.
 * @param {import("@playwright/test").Page} page */
async function notificationSwitch(page) {
  if (isPhone(page)) await page.getByRole("button", { name: /^Account:/ }).click();
  return page.getByRole("switch", { name: "Notifications" });
}

/** @param {string} endpoint */
const subscriptionId = (endpoint) => createHash("sha256").update(endpoint).digest("hex");

test("notifications can be turned on, tested, kept across reloads and turned off", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["notifications"]);
  await fakePushManager(context);
  await signedIn(page);

  const toggle = await notificationSwitch(page);
  await expect(toggle).not.toBeChecked();
  const subscribed = page.waitForResponse(
    (r) => r.url().endsWith("/push/subscriptions") && r.request().method() === "POST",
  );
  await toggle.click();
  expect((await subscribed).status()).toBe(201);
  await expect(toggle).toBeChecked();
  await expect(toggle).toHaveAccessibleDescription("On for this device.");

  // The test hub can't reach the fake push service: the test says so.
  await page.getByRole("button", { name: "Send a test" }).click();
  await expect(page.getByText("The push service didn't take it")).toBeVisible();

  // After a reload the subscription is made again (this stand-in forgets it, as Safari may).
  await page.reload();
  const again = await notificationSwitch(page);
  await expect(again).toBeChecked();

  const endpoint = await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    return (await registration.pushManager.getSubscription())?.endpoint ?? "";
  });
  const removed = page.waitForResponse((r) => r.request().method() === "DELETE");
  await again.click();
  const response = await removed;
  expect(response.url()).toContain(`/push/subscriptions/${subscriptionId(endpoint)}`);
  expect(response.status()).toBe(204);
  await expect(again).not.toBeChecked();
});

test("signing out stops this device's notifications", async ({ page, context }) => {
  await context.grantPermissions(["notifications"]);
  await fakePushManager(context);
  await signedIn(page);
  const toggle = await notificationSwitch(page);
  await toggle.click();
  await expect(toggle).toBeChecked();

  const order = /** @type {string[]} */ ([]);
  page.on("request", (request) => {
    if (/\/push\/subscriptions\/|\/auth\/logout/.test(request.url())) {
      order.push(request.method() === "DELETE" ? "unsubscribe" : "sign out");
    }
  });
  await page.getByRole("button", { name: "Sign out" }).click(); // in the menu still open on phones

  await expect(page).toHaveURL(/\/login/);
  expect(order).toEqual(["unsubscribe", "sign out"]); // while the session still works
});

test("blocked notifications are explained, not offered", async ({ page, context }) => {
  await fakePushManager(context);
  await context.addInitScript(() => {
    Object.defineProperty(Notification, "permission", { get: () => "denied" });
  });
  await signedIn(page);

  const toggle = await notificationSwitch(page);
  await expect(toggle).toBeDisabled();
  await expect(toggle).toHaveAccessibleDescription(
    "Blocked in this browser's settings for the site.",
  );
});

test("the service worker shows pushed alerts, unless the dashboard is in front", async ({
  page,
  context,
  baseURL,
}) => {
  test.skip(isPhone(page), "the worker is the same on every screen size");
  await context.grantPermissions(["notifications"]);
  await signedIn(page);
  await page.evaluate(() => navigator.serviceWorker.ready);

  const cdp = await context.newCDPSession(page);
  const registrationId = new Promise((resolve) => {
    cdp.on("ServiceWorker.workerRegistrationUpdated", ({ registrations }) => {
      const ours = registrations.find((r) => !r.isDeleted);
      if (ours) resolve(ours.registrationId);
    });
  });
  await cdp.send("ServiceWorker.enable");
  const origin = new URL(baseURL ?? "").origin;
  /** @param {object} data */
  const deliver = async (data) =>
    cdp.send("ServiceWorker.deliverPushMessage", {
      origin,
      registrationId: /** @type {string} */ (await registrationId),
      data: JSON.stringify(data),
    });
  const shown = () =>
    page.evaluate(async () => {
      const registration = await navigator.serviceWorker.ready;
      const notifications = await registration.getNotifications();
      return notifications.map((n) => ({ title: n.title, body: n.body, tag: n.tag, data: n.data }));
    });
  const motion = {
    kind: "motion",
    event_id: "0199e000-0000-7000-8000-000000000001",
    device_id: "e2e-porch",
    device_name: "E2E porch",
    started_at: new Date().toISOString(),
    duration_seconds: 12,
    image: null,
  };

  await deliver({ kind: "test" });
  await expect
    .poll(shown)
    .toContainEqual(expect.objectContaining({ title: "Notifications are on" }));

  // The dashboard is in front and shows its own alert: no system notification on top.
  await deliver(motion);
  await page.waitForTimeout(500);
  expect((await shown()).map((n) => n.title)).not.toContain("Motion on E2E porch");

  // With the dashboard closed, it does.
  const blank = await context.newPage();
  await page.goto("about:blank");
  await deliver(motion);
  await page.goto("/events");
  await expect.poll(shown).toContainEqual({
    title: "Motion on E2E porch",
    body: expect.stringMatching(/^At .+, for 12 s\.$/),
    tag: motion.event_id,
    data: { url: `/events?event=${motion.event_id}` },
  });
  await blank.close();
});
