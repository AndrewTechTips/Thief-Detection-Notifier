import { expect, test } from "@playwright/test";

import { signedIn } from "./fixtures.js";

/** Opens the newest event that has a clip (the test hub's cameras record one per visit).
 * @param {import("@playwright/test").Page} page */
async function openEventWithClip(page) {
  await signedIn(page, "/events");
  const withClip = page.getByRole("button", { name: /^Open snapshot and clip/ }).first();
  await expect(withClip).toBeVisible({ timeout: 25_000 }); // a visit comes every 9 s
  await expect(page.locator(".event-clip-mark:visible").first()).toBeVisible();
  await withClip.click();
  const viewer = page.getByRole("dialog");
  await expect(viewer).toBeVisible();
  return viewer;
}

test("an event's clip plays in the viewer, from before the motion was detected", async ({
  page,
}) => {
  const viewer = await openEventWithClip(page);

  // The clip is shown first, with the snapshot as its poster, and does not play by itself.
  await expect(viewer.getByRole("radio", { name: "Clip" })).toBeChecked();
  const video = viewer.locator("video");
  await expect(video).toBeVisible();
  await expect(video).toHaveAttribute("poster", /\/snapshot\?kind=annotated/);
  await expect(video).toHaveAttribute("src", /\/clip\?expires=/);
  await expect
    .poll(() => video.evaluate((v) => /** @type {HTMLVideoElement} */ (v).paused))
    .toBe(true);

  // It really decodes: real duration, real frames, and it plays.
  await expect
    .poll(() => video.evaluate((v) => /** @type {HTMLVideoElement} */ (v).duration), {
      timeout: 10_000,
    })
    .toBeGreaterThan(3); // 3 s of pre-roll alone
  const progress = await video.evaluate(async (element) => {
    const v = /** @type {HTMLVideoElement} */ (element);
    v.muted = true;
    await v.play();
    await new Promise((resolve) => setTimeout(resolve, 800));
    v.pause();
    return { time: v.currentTime, width: v.videoWidth, height: v.videoHeight };
  });
  expect(progress.time).toBeGreaterThan(0.3);
  expect(progress).toMatchObject({ width: 640, height: 480 });

  const download = viewer.getByRole("link", { name: "Download clip" });
  await expect(download).toHaveAttribute("href", /\/clip\?/);
  await expect(download).toHaveAttribute("download", /^e2e-porch-.+\.webm$/);
});

test("the viewer switches between clip and snapshot, and remembers the choice", async ({
  page,
}) => {
  const viewer = await openEventWithClip(page);

  // People click the label: the radio itself is visually hidden.
  await viewer.getByRole("radiogroup", { name: "Show" }).getByText("Snapshot").click();
  await expect(viewer.getByRole("radio", { name: "Snapshot" })).toBeChecked();
  await expect(viewer.locator("video")).toBeHidden();
  await expect(viewer.getByRole("img")).toBeVisible();
  await expect(viewer.getByRole("switch", { name: "Motion boxes" })).toBeVisible();
  await expect(viewer.getByRole("link", { name: "Download", exact: true })).toHaveAttribute(
    "download",
    /\.jpg$/,
  );

  await page.keyboard.press("Escape");
  await expect(viewer).toBeHidden();
  await page
    .getByRole("button", { name: /^Open snapshot and clip/ })
    .first()
    .click();
  await expect(page.getByRole("radio", { name: "Snapshot" })).toBeChecked();
});

test("arrow keys on the clip seek instead of changing events", async ({ page, isMobile }) => {
  test.skip(isMobile, "keyboard");
  await signedIn(page, "/events");
  // Two events, so there is a next one the arrow could wrongly jump to.
  const withClip = page.getByRole("button", { name: /^Open snapshot and clip/ });
  await expect.poll(() => withClip.count(), { timeout: 30_000 }).toBeGreaterThan(1);
  await withClip.first().click();
  const viewer = page.getByRole("dialog");
  const position = viewer.getByText(/^\d+ of \d+$/);
  await expect(position).toHaveText(/^1 of/);

  await viewer.locator("video").focus();
  await page.keyboard.press("ArrowRight");
  await expect(position).toHaveText(/^1 of/);

  await viewer.getByRole("button", { name: "Next event" }).focus();
  await page.keyboard.press("ArrowRight"); // elsewhere in the viewer, arrows still browse
  await expect(position).toHaveText(/^2 of/);
});
