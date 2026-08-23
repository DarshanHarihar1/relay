import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: process.env.RELAY_E2E_BASE_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  webServer: process.env.RELAY_E2E_BASE_URL
    ? undefined
    : { command: "pnpm dev", cwd: ".", port: 3000, reuseExistingServer: true },
});
