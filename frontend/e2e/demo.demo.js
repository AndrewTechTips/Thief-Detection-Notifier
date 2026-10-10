import { expect, test } from "@playwright/test";

import { audit } from "./axe.js";

// The demo opens signed in, on the live view, with a hub that only exists in the page.

test("opens signed in, with every camera live and no hub behind it", async ({ page }) => {
  /** @type {string[]} */
  const apiCalls = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) apiCalls.push(request.url());
  });
  await page.goto("./");

  await expect(page.getByRole("heading", { name: "Live" })).toBeVisible();
  await expect(page.locator(".camera-tile")).toHaveCount(4);
  for (const tile of await page.locator(".camera-tile").all()) {
    await tile.scrollIntoViewIfNeeded(); // streams only run on screen
    await expect(tile).toHaveAttribute("data-state", "playing");
  }
  const note = page.getByRole("complementary", { name: "About this demo" });
  await expect(note.getByRole("link", { name: "Run it yourself" })).toHaveAttribute(
    "href",
    "https://github.com/AndrewTechTips/iot-vision-hub",
  );
  expect(apiCalls, "every API call is answered in the page").toEqual([]);

  await note.getByRole("button", { name: "Hide this note" }).click();
  await expect(note).toBeHidden();
  await page.reload();
  await expect(page.locator(".camera-tile").first()).toBeVisible();
  await expect(note).toBeHidden(); // for the rest of the visit
});

test("a person at the front door raises an alert that opens the event", async ({ page }) => {
  await page.goto("./");

  // The demo starts the front door a moment before someone walks up; the visit lasts ~20 s.
  const alert = page.locator(".toast", { hasText: "Person at Front door" });
  await expect(alert).toBeVisible({ timeout: 45_000 });
  // Boxes turned red while he was in view, and the patio's shadows never alerted.
  await expect(page.locator(".toast", { hasText: "Patio" })).toHaveCount(0);

  await alert.getByRole("button", { name: "View" }).click();
  await expect(page).toHaveURL(/\/iot-vision-hub\/events\?event=/);
  const viewer = page.getByRole("dialog");
  await expect(viewer.getByRole("heading", { name: "Front door" })).toBeVisible();
  await expect(viewer).toContainText("a person was seen");
  const clip = await viewer.locator("video").getAttribute("src");
  expect(clip).toMatch(/\/iot-vision-hub\/demo\/front-door\/event-\d+\.webm$/);
});

test("the camera page draws the person's box from the recording", async ({ page }) => {
  await page.goto("./devices/front-door");

  const stage = page.locator(".device-stage");
  await expect(stage).toHaveAttribute("data-state", "playing");
  await expect(stage.locator(".detection-layer[data-person]:not([data-idle])")).toBeAttached({
    timeout: 45_000,
  });
  await expect(stage.locator(".camera-motion")).toContainText(/Person \d+ %/);
});

test("history, filters and deep links work without a server", async ({ page }) => {
  await page.goto("./events?people=1");

  const rows = page.locator(".event-row");
  await expect(rows.first()).toBeVisible();
  for (const name of await rows.locator(".font-medium").allTextContents()) {
    expect(["Front door", "Lobby"]).toContain(name.trim());
  }
  await page.reload(); // a deep link, as GitHub Pages serves it
  await expect(page.getByRole("heading", { name: "Events" })).toBeVisible();

  await page.goto("./activity");
  await expect(page.getByText("changed").first()).toBeVisible();
});

test("settings and camera controls work in memory", async ({ page }, { project }) => {
  test.skip(project.name === "phone", "covered on desktop");
  await page.goto("./devices/driveway");

  await page.getByRole("button", { name: "Stop camera" }).click();
  await expect(page.locator(".device-stage")).toHaveAttribute("data-state", "stopped");
  await page.getByRole("button", { name: "Start camera" }).click();
  await expect(page.locator(".device-stage")).toHaveAttribute("data-state", "playing");

  // Through the app: reloading would start a fresh demo hub, without this visit's changes.
  await page
    .getByRole("navigation", { name: "Main" })
    .first()
    .getByRole("link", { name: "Activity" })
    .click();
  await expect(page.getByText("started").first()).toBeVisible();
  await expect(page.getByText("stopped").first()).toBeVisible();
});

test("the demo passes the same accessibility audit", async ({ page }) => {
  await page.goto("./");
  await expect(page.locator(".camera-tile").first()).toHaveAttribute("data-state", "playing");
  await audit(page, "demo live view");
  await page.goto("./events");
  await expect(page.locator(".event-row").first()).toBeVisible();
  await audit(page, "demo events");
});
