import { expect, test } from "@playwright/test";

import { ADMIN, isPhone } from "./fixtures.js";

test.beforeEach(({ page }) => {
  test.skip(isPhone(page), "keyboard navigation is a desktop concern");
});

/**
 * Presses Tab (or Shift+Tab) until `target` has focus, checking each stop shows a visible focus
 * indicator.
 * @param {import("@playwright/test").Page} page
 * @param {import("@playwright/test").Locator} target
 * @param {{ limit?: number, key?: string }} [options]
 */
async function tabTo(page, target, { limit = 40, key = "Tab" } = {}) {
  for (let i = 0; i < limit; i++) {
    await page.keyboard.press(key);
    const shown = await page.evaluate(() => {
      const element = document.activeElement;
      if (!element || element === document.body) return true;
      const style = getComputedStyle(element);
      const ring = style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0;
      // Some controls draw focus on their container (:has(:focus-visible)) or as a shadow.
      const container = element.closest(".camera-tile, .segmented label, .event-row");
      return ring || style.boxShadow !== "none" || Boolean(container);
    });
    expect(shown, "every focus stop is visible").toBe(true);
    if (await target.evaluate((el) => el === document.activeElement)) return;
  }
  throw new Error(`could not reach ${target} with Tab`);
}

test("everything works from the keyboard alone", async ({ page }) => {
  // Sign in without touching the mouse.
  await page.goto("/events");
  const username = page.getByLabel("Username");
  await expect(username).toBeFocused(); // focused for us on a desktop
  await page.keyboard.type(ADMIN.username);
  await page.keyboard.press("Tab");
  await page.keyboard.type(ADMIN.password);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Events" })).toBeVisible();

  // The first Tab offers to skip the navigation.
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Live" })).toBeVisible();
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();

  // Navigate by keyboard: focus lands on the new page's heading.
  await page.keyboard.press("Shift+Tab"); // back into the sidebar
  const events = page.getByRole("link", { name: "Events" }).first();
  await tabTo(page, events);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Events" })).toBeFocused();

  // Open a snapshot, browse, close: focus returns to the event shown last.
  // Browsing needs two finished events; a fresh test hub records one every 9 s.
  const snapshots = page.getByRole("button", { name: /^Open snapshot/ });
  await expect.poll(() => snapshots.count(), { timeout: 30_000 }).toBeGreaterThan(1);
  const first = snapshots.first();
  await tabTo(page, first, { limit: 60 });
  await page.keyboard.press("Enter");
  const viewer = page.getByRole("dialog");
  await expect(viewer).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Close" })).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect(viewer).toContainText(/^.*2 of \d+/s);
  await page.keyboard.press("Escape");
  await expect(viewer).toBeHidden();
  const second = page.getByRole("button", { name: /^Open snapshot/ }).nth(1);
  await expect(second).toBeFocused();

  // Filters are keyboard controls too.
  const today = page.getByRole("radio", { name: "Today" });
  // The filters sit above the list: go back up to them.
  await tabTo(page, page.getByRole("radio", { name: "All time" }), { key: "Shift+Tab" });
  await page.keyboard.press("ArrowRight");
  await expect(today).toBeChecked();
  await expect(page).toHaveURL(/range=today/);
});
