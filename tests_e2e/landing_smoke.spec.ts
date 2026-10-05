import { test, expect } from '@playwright/test';

test.describe('Landing Pages Smoke Tests', () => {
  test.beforeEach(async ({ page }) => {
    // Fail the test if there's any unhandled JS error on the page
    page.on('pageerror', (err) => {
      throw new Error(`Unhandled exception on page: ${err.message}`);
    });
  });

  test('home page loads successfully', async ({ page }) => {
    const response = await page.goto('/');
    expect(response?.status()).toBe(200);

    // Verify main app element is present on landing
    await expect(page.locator('.page-home')).toBeVisible();

    // Check heading
    const heading = page.locator('h2', { hasText: 'Three Modes of Translation' });
    await expect(heading).toBeVisible();
  });

  test('about page loads successfully', async ({ page }) => {
    const response = await page.goto('/about');
    expect(response?.status()).toBe(200);

    // Verify about page content
    await expect(page.locator('.page-about')).toBeVisible();
    
    const heading = page.locator('h2', { hasText: 'Why This Needs to Exist' });
    await expect(heading).toBeVisible();
  });

  test('contribute page loads successfully', async ({ page }) => {
    const response = await page.goto('/contribute');
    expect(response?.status()).toBe(200);

    // Verify contribute page content
    await expect(page.locator('.page-contribute')).toBeVisible();
    
    const heading = page.locator('h1', { hasText: 'Help improve future Sign2Text recognition' });
    await expect(heading).toBeVisible();
  });
});
