import { expect, test } from "@playwright/test";

import { isPhone, signedIn } from "./fixtures.js";

test("the People only filter keeps events with a person", async ({ page }) => {
  await signedIn(page, "/events");
  await expect(page.getByRole("button", { name: /^Open snapshot/ }).first()).toBeVisible({
    timeout: 25_000,
  });

  // The test cameras' visitors are rectangles, not people.
  await page.getByText("People only").click();
  await expect(page).toHaveURL(/people=1/);
  await expect(page.getByText("No events match these filters")).toBeVisible();

  await page.getByRole("button", { name: "Show all events" }).click();
  await expect(page).not.toHaveURL(/people=1/);
  await expect(page.getByRole("button", { name: /^Open snapshot/ }).first()).toBeVisible();
});

test("a camera can be set to alert on people only", async ({ page }) => {
  test.skip(isPhone(page), "covered on desktop: it changes a shared camera");
  // The shed camera is stopped: changing it can't disturb the live tests.
  await signedIn(page, "/devices/e2e-shed");
  const alertOn = page.getByRole("radiogroup", { name: "Alert on" });
  await expect(alertOn.getByRole("radio", { name: "Any motion" })).toBeChecked();

  await alertOn.getByText("People").click();
  await expect(page.locator("#detect-alert-hint")).toContainText("only when someone is seen");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText("Detection settings saved")).toBeVisible();

  await page.reload();
  await expect(
    page.getByRole("radiogroup", { name: "Alert on" }).getByRole("radio", { name: "People" }),
  ).toBeChecked();

  // Put it back for the next run.
  await page.getByRole("radiogroup", { name: "Alert on" }).getByText("Any motion").click();
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText("Detection settings saved")).toBeVisible();
});
