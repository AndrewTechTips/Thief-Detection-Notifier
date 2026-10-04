import { defineConfig, devices } from "@playwright/test";

import { ADMIN, PORT } from "./e2e/fixtures.js";

const CI = Boolean(process.env.CI);

// End-to-end tests run the built dashboard against a real, throwaway hub (e2e/start-hub.sh).
// Build first: `npm run build && npm run e2e`.
export default defineConfig({
  testDir: "e2e",
  testMatch: /.*\.e2e\.js/,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: CI ? 1 : 0,
  reporter: CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
    },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: "sh e2e/start-hub.sh",
    url: `http://127.0.0.1:${PORT}/api/v1/health/ready`,
    reuseExistingServer: !CI,
    timeout: 120_000,
    env: { E2E_ADMIN_PASSWORD: ADMIN.password, E2E_PORT: String(PORT) },
  },
});
