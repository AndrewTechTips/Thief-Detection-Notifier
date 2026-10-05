// Frame timing under a slow CPU (4x throttling, roughly a mid-range phone). Opt-in, because
// timing budgets are too noisy for shared CI runners: `PERF=1 npm run e2e -- e2e/perf.e2e.js`.

import { expect, test } from "@playwright/test";

import { signedIn } from "./fixtures.js";

test.skip(!process.env.PERF, "set PERF=1 to measure frame timing");
test.skip(({ browserName }) => browserName !== "chromium", "CPU throttling needs Chromium");

/** Starts recording frame intervals in the page. (A 120 ms block shows up as a ~100 ms frame,
 * so jank is visible here; PerformanceObserver "longtask" reports nothing in headless mode.) */
async function record(/** @type {import("@playwright/test").Page} */ page) {
  await page.evaluate(() => {
    const w = /** @type {any} */ (window);
    w.__frames = [];
    let last = performance.now();
    const tick = (/** @type {number} */ now) => {
      w.__frames.push(now - last);
      last = now;
      if (w.__frames) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

/** Stops recording and summarises: frames per second, the 95th percentile and the worst frame. */
async function summary(/** @type {import("@playwright/test").Page} */ page) {
  return page.evaluate(() => {
    const w = /** @type {any} */ (window);
    const frames = /** @type {number[]} */ (w.__frames).slice(1);
    w.__frames = null;
    const sorted = [...frames].sort((a, b) => a - b);
    const total = frames.reduce((sum, f) => sum + f, 0);
    return {
      fps: Math.round((frames.length / total) * 1000),
      p95: Math.round(sorted[Math.floor(sorted.length * 0.95)] * 10) / 10,
      worst: Math.round(sorted.at(-1) ?? 0),
      over50ms: frames.filter((f) => f > 50).length,
    };
  });
}

test("animations stay smooth on a slow CPU", async ({ page }, info) => {
  await signedIn(page);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  const results = {};

  // Two live streams decoding and painting, plus whatever alerts arrive meanwhile.
  await expect(page.locator(".camera-tile[data-state=playing]")).toHaveCount(1);
  await record(page);
  await page.waitForTimeout(6000);
  results.liveGrid = await summary(page);

  // A page change: the outlet fades in, the list renders.
  await record(page);
  await page.getByRole("link", { name: "Events" }).first().click();
  await expect(page.getByRole("heading", { name: "Events" })).toBeVisible();
  await page.waitForTimeout(1500);
  results.pageChange = await summary(page);

  // The snapshot viewer: open, browse twice, close.
  const snapshots = page.getByRole("button", { name: /^Open snapshot/ });
  await expect.poll(() => snapshots.count(), { timeout: 30_000 }).toBeGreaterThan(2);
  await record(page);
  await snapshots.first().click();
  await page.waitForTimeout(600);
  await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(600);
  await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(600);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(600);
  results.viewer = await summary(page);

  info.annotations.push({
    type: "frame timing (4x CPU throttling)",
    description: JSON.stringify(results),
  });
  console.log(JSON.stringify(results, null, 2));
  for (const [scenario, result] of Object.entries(results)) {
    expect.soft(result.p95, `${scenario}: 95 % of frames within 33 ms (30+ fps)`).toBeLessThan(34);
    expect.soft(result.over50ms, `${scenario}: dropped-frame spikes`).toBeLessThanOrEqual(3);
  }
});
