// Shared by the end-to-end tests and playwright.config.js.

import { expect } from "@playwright/test";

export const PORT = 8765;
/** The throwaway hub's admin (e2e/start-hub.sh creates it). A test-only value. */
export const ADMIN = { username: "admin", password: "e2e-admin-password-0b5e" };

/**
 * Signs in through the real form.
 * @param {import("@playwright/test").Page} page
 * @param {{ password?: string }} [options]
 */
export async function signIn(page, { password = ADMIN.password } = {}) {
  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

/** @param {import("@playwright/test").Page} page */
export async function signedIn(page, path = "/") {
  await page.goto(path);
  await expect(page).toHaveURL(/\/login/);
  await signIn(page);
  await expect(page.getByRole("button", { name: "Sign in" })).toBeHidden();
}

/** @param {import("@playwright/test").Page} page */
export function isPhone(page) {
  return (page.viewportSize()?.width ?? 1280) < 768;
}
