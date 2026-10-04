import { expect, test } from "@playwright/test";

import { signedIn } from "./fixtures.js";

test("the installed app still opens when the hub can't be reached", async ({ page, context }) => {
  await signedIn(page);
  // The service worker takes control after its first load.
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)), {
      timeout: 15_000,
    })
    .toBe(true);

  await context.setOffline(true);
  await page.reload();

  await expect(page.getByText("Can't reach the hub")).toBeVisible();
  await context.setOffline(false);
  await expect(page.locator(".camera-tile").first()).toBeVisible({ timeout: 30_000 });
});

test("unknown pages say so", async ({ page }) => {
  await signedIn(page, "/no/such/page");

  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await page.getByRole("link", { name: "Go to live view" }).click();
  await expect(page.getByRole("heading", { name: "Live" })).toBeVisible();
});
