import { expect, test } from "@playwright/test";

import { isPhone, signIn, signedIn } from "./fixtures.js";

test("a signed-out visitor signs in and lands where they were going", async ({ page }) => {
  await page.goto("/events?range=today");

  await expect(page).toHaveURL(/\/login\?next=%2Fevents%3Frange%3Dtoday$/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

  await signIn(page, { password: "not-the-password" });
  await expect(page.getByRole("alert")).toHaveText("Wrong username or password.");
  await expect(page.getByLabel("Password", { exact: true })).toBeFocused();

  await signIn(page);
  await expect(page).toHaveURL(/\/events\?range=today$/);
  await expect(page.getByRole("heading", { name: "Events" })).toBeVisible();
});

test("signing out returns to the sign-in page", async ({ page }) => {
  await signedIn(page);

  if (isPhone(page)) {
    await page.getByRole("button", { name: /^Account/ }).click();
    await page.getByRole("button", { name: "Sign out" }).last().click();
  } else {
    await page.getByRole("button", { name: "Sign out" }).first().click();
  }

  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("status").filter({ hasText: "You're signed out." })).toBeVisible();
});
