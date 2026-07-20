import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const e2eDataDir = path.resolve("test-results", "e2e-data");

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }]
  ],
  use: {
    baseURL: "http://127.0.0.1:15173",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off"
  },
  projects: [
    {
      name: "desktop-chrome",
      use: { ...devices["Desktop Chrome"], channel: "chrome" }
    }
  ],
  webServer: [
    {
      command: "python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 18000",
      url: "http://127.0.0.1:18000/api/health",
      reuseExistingServer: false,
      timeout: 120_000,
      env: { CODERAI_DATA_DIR: e2eDataDir }
    },
    {
      command: "node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 15173",
      url: "http://127.0.0.1:15173",
      reuseExistingServer: false,
      timeout: 120_000,
      env: { VITE_API_URL: "http://127.0.0.1:18000" }
    }
  ]
});
