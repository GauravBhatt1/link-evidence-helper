import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./src",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  reporter: [["line"]],
  outputDir: "test-results",
  use: {
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});
