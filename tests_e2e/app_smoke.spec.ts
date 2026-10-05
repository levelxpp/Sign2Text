import { test, expect } from '@playwright/test';

test.describe('Main App Smoke', () => {
  test('should load without crashing and show main shell', async ({ page }) => {
    const errors: Error[] = [];
    page.on('pageerror', err => errors.push(err));

    const response = await page.goto('/index.html');
    expect(response?.status()).toBe(200);

    // Wait for the app shell to stabilize
    await page.waitForLoadState('networkidle');

    // Ensure no unhandled exceptions during startup
    expect(errors).toHaveLength(0);

    // Verify key UI elements are present
    await expect(page).toHaveTitle(/Sign2Text/);
    await expect(page.locator('#video')).toBeVisible();
    await expect(page.locator('#start')).toBeVisible();
  });
});
