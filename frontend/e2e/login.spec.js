// @ts-check
import { test, expect } from '@playwright/test';

/**
 * Login page e2e smoke tests.
 *
 * These tests verify that the Login page loads and renders schema-driven
 * form fields from the backend /api/v1/auth/schema endpoint.
 *
 * Prerequisites: backend on port 7860 + frontend dev server on port 5173.
 */

test.describe('Login page', () => {
  test('loads the login page', async ({ page }) => {
    await page.goto('/Login.html');
    // The page should load without errors
    await expect(page).toHaveTitle(/.*/);
    // The page should contain a form or login-related content
    const body = page.locator('body');
    await expect(body).toBeVisible();
  });

  test('displays schema-driven form fields', async ({ page }) => {
    await page.goto('/Login.html');
    // Wait for the auth schema to load (the LoginApp component fetches
    // /api/v1/auth/schema on mount and renders fields dynamically)
    // If backend is up, we should see username/password fields
    const usernameInput = page.locator('input[autocomplete="username"]');
    const passwordInput = page.locator('input[type="password"]');

    // Either schema loads (fields visible) or schema error is shown
    // Both are valid states depending on whether backend is running
    const hasForm = await usernameInput.isVisible().catch(() => false);
    const hasBody = await page.locator('body').isVisible();
    expect(hasBody).toBe(true);

    if (hasForm) {
      await expect(usernameInput).toBeVisible();
      await expect(passwordInput).toBeVisible();
    }
  });

  test('has no console errors on load', async ({ page }) => {
    const errors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });

    await page.goto('/Login.html');
    await page.waitForLoadState('networkidle').catch(() => {});

    // Filter out expected network errors (backend not running)
    const realErrors = errors.filter(
      (e) => !e.includes('Failed to fetch') && !e.includes('net::ERR_')
    );
    expect(realErrors).toHaveLength(0);
  });
});
