import AxeBuilder from "@axe-core/playwright";
import { expect } from "@playwright/test";

/** WCAG 2.2 A and AA, the standard the dashboard aims for, plus axe's best practices. */
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

/**
 * Runs axe on the page as it is now and fails with a readable list of violations.
 * @param {import("@playwright/test").Page} page
 * @param {string} where
 */
export async function audit(page, where) {
  // Colours are only final once fades finish (looping pulses never do: skip those).
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((animation) => animation.effect?.getTiming().iterations !== Infinity)
        .map((animation) => animation.finished.catch(() => {})),
    ),
  );
  const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const report = violations.map(
    (v) =>
      `${v.id} (${v.impact}): ${v.help}\n${v.nodes
        .slice(0, 5)
        .map(
          (n) => `    ${n.target.join(" ")}: ${n.failureSummary?.split("\n").slice(1).join(" ")}`,
        )
        .join("\n")}`,
  );
  expect(report, `accessibility problems on ${where}`).toEqual([]);
}
