// @ts-check
import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for RPG frontend e2e tests.
 *
 * Assumes the backend (port 7860) and frontend dev server (port 5173)
 * are already running. Use `./scripts/dev.sh start` to bring them up.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
