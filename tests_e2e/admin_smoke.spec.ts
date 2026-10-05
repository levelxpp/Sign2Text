import { test, expect } from '@playwright/test';

test.describe('Admin App Smoke', () => {
  test('should load admin lock screen without crashing', async ({ page }) => {
    const errors: Error[] = [];
    page.on('pageerror', err => errors.push(err));

    const response = await page.goto('/admin.html');
    expect(response?.status()).toBe(200);

    await page.waitForLoadState('networkidle');

    // Ensure no unhandled exceptions during startup
    expect(errors).toHaveLength(0);

    // Verify admin requires auth out of the box
    await expect(page).toHaveTitle(/Sign2Text Admin/);
    await expect(page.locator('.lock-screen')).toBeVisible();
    await expect(page.locator('.lock-form')).toBeVisible();
    
    // Ensure dashboard is not visible while locked
    await expect(page.locator('.dashboard')).not.toBeVisible();
  });
});
