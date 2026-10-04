import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import globals from "globals";

export default defineConfig([
  { ignores: ["dist/", ".e2e-data/", "test-results/", "playwright-report/"] },
  js.configs.recommended,
  {
    languageOptions: { globals: globals.browser },
    rules: {
      eqeqeq: ["error", "always"],
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "prefer-const": "error",
    },
  },
  { files: ["*.config.js", "scripts/**"], languageOptions: { globals: globals.node } },
  { files: ["sw/**"], languageOptions: { globals: globals.serviceworker } },
  // Playwright tests run in Node; callbacks passed to page.evaluate() run in the browser.
  {
    files: ["e2e/**", "playwright.config.js"],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
  },
]);
