import { expect, test } from "@playwright/test";

import { isPhone, signedIn } from "./fixtures.js";

test("live view shows each camera with its state and real frames", async ({ page }) => {
  await signedIn(page);

  const porch = page.locator(".camera-tile", { hasText: "E2E porch" });
  const shed = page.locator(".camera-tile", { hasText: "E2E shed" });
  await expect(porch.locator(".camera-chip").first()).toHaveText("Live");
  await expect(porch).toHaveAttribute("data-state", "playing");
  await expect(shed.locator(".camera-chip").first()).toHaveText("Stopped");
  await expect(shed).toContainText("This camera is stopped.");
  // The gate camera may be mid stop-and-start in another test: count around it.
  await expect(page.getByText(/[12] of 3 cameras live/)).toBeVisible();

  // The picture is real video, not a blank: its pixels vary.
  const picture = porch.getByRole("img", { name: "Live view of E2E porch" });
  await expect
    .poll(() => picture.evaluate((c) => /** @type {HTMLCanvasElement} */ (c).width))
    .toBeGreaterThan(0);
  const distinct = await picture.evaluate((canvas) => {
    const c = /** @type {HTMLCanvasElement} */ (canvas);
    const data = c.getContext("2d")?.getImageData(0, 0, c.width, c.height).data ?? [];
    const values = new Set();
    for (let i = 0; i < data.length; i += 4001) values.add(data[i]);
    return values.size;
  });
  expect(distinct).toBeGreaterThan(5);

  // Phones get the bottom tab bar, wider screens the sidebar.
  const navigation = page.getByRole("navigation", { name: "Main" });
  await expect(isPhone(page) ? navigation.last() : navigation.first()).toBeVisible();
});

test("motion raises an alert within a second of the hub closing the event", async ({ page }) => {
  /** @type {{ ts: number, id: string } | null} */
  let ended = null;
  page.on("websocket", (socket) => {
    socket.on("framereceived", ({ payload }) => {
      const message = JSON.parse(String(payload));
      if (message.type === "motion.ended" && !message.replay && message.device_id === "e2e-porch") {
        ended = { ts: Date.parse(message.ts), id: message.data.event_id };
      }
    });
  });
  await signedIn(page);

  const alert = page.locator(".toast", { hasText: "Motion on E2E porch" });
  await expect(alert).toBeVisible({ timeout: 20_000 }); // a visit comes every 9 s
  // The same toast turns into the result, with the snapshot.
  await expect(alert.locator(".toast-media")).toBeVisible({ timeout: 20_000 });
  const shownAt = Date.now();
  await expect(alert).toContainText(/At .+, for \d+ s\./);

  expect(ended, "the hub's motion.ended message").not.toBeNull();
  const latency = shownAt - /** @type {{ ts: number }} */ (/** @type {unknown} */ (ended)).ts;
  test.info().annotations.push({ type: "alert latency", description: `${latency} ms` });
  expect(latency).toBeLessThan(1000);

  // "View" opens the event.
  await alert.getByRole("button", { name: "View" }).click();
  await expect(page).toHaveURL(/\/events\?event=/);
  const viewer = page.getByRole("dialog");
  await expect(viewer).toBeVisible();
  await expect(viewer.getByRole("heading", { name: "E2E porch" })).toBeVisible();
  // The event opens on its clip, with the snapshot as the poster until it plays.
  const video = viewer.locator("video");
  await expect(video).toBeVisible();
  const poster = await video.getAttribute("poster");
  expect(poster).toMatch(/\/snapshot\?kind=annotated/);
  const image = await page.request.get(/** @type {string} */ (poster));
  expect(image.headers()["content-type"]).toBe("image/jpeg");
  await page.keyboard.press("Escape");
  await expect(viewer).toBeHidden();
});

test("a camera page streams at full rate and an admin can stop and start it", async ({ page }) => {
  // One browser at a time: the phone run would stop the same camera under this one.
  test.skip(isPhone(page), "covered on desktop");
  await signedIn(page, "/devices/e2e-gate");

  await expect(page.getByRole("heading", { name: "E2E gate" })).toBeVisible();
  const stage = page.locator(".device-stage");
  await expect(stage).toHaveAttribute("data-state", "playing");

  await page.getByRole("button", { name: "Stop camera" }).click();
  await expect(stage).toHaveAttribute("data-state", "stopped");
  await page.getByRole("button", { name: "Start camera" }).click();
  await expect(stage).toHaveAttribute("data-state", "playing", { timeout: 15_000 });
});
