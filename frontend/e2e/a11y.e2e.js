import { expect, test } from "@playwright/test";

import { audit } from "./axe.js";
import { signedIn } from "./fixtures.js";

test("sign-in page", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await audit(page, "sign-in");
});

test("live view", async ({ page }) => {
  await signedIn(page);
  await expect(page.locator(".camera-tile").first()).toHaveAttribute(
    "data-state",
    /playing|stopped/,
  );
  await audit(page, "live view");
});

test("events and the snapshot viewer", async ({ page }) => {
  await signedIn(page, "/events");
  const first = page.getByRole("button", { name: /^Open snapshot/ }).first();
  await expect(first).toBeVisible({ timeout: 20_000 });
  await audit(page, "events");
  await first.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await audit(page, "snapshot viewer");
});

test("camera page with admin controls", async ({ page }) => {
  await signedIn(page, "/devices/e2e-porch");
  await expect(page.getByRole("button", { name: "Edit areas" })).toBeVisible();
  await audit(page, "camera page");
  await page.getByRole("button", { name: "Edit areas" }).click();
  await audit(page, "camera page, editing areas");
});

test("activity and not-found pages", async ({ page }) => {
  await signedIn(page, "/activity");
  await expect(page.locator(".activity-row").first()).toBeVisible();
  await audit(page, "activity");
  await page.goto("/no/such/page");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await audit(page, "not found");
});

test("the hub-unreachable screen", async ({ page, context }) => {
  await signedIn(page);
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)), {
      timeout: 15_000,
    })
    .toBe(true);
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByText("Can't reach the hub")).toBeVisible();
  await audit(page, "hub unreachable");
});
