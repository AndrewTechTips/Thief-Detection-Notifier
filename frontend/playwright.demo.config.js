import { defineConfig, devices } from "@playwright/test";

const CI = Boolean(process.env.CI);
const PORT = 4174;

// The public demo build (AD-24) on its own: no hub behind it, served from the same subdirectory
// as on GitHub Pages. Build first: `npm run build:demo && npm run e2e:demo`.
export default defineConfig({
  testDir: "e2e",
  testMatch: /.*\.demo\.js/,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  retries: CI ? 1 : 0,
  reporter: CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}/iot-vision-hub/`,
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
    command: `npx vite preview --mode demo --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}/iot-vision-hub/`,
    reuseExistingServer: !CI,
  },
});
