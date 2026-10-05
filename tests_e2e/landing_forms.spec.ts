import { test, expect } from '@playwright/test';

test.describe('Landing Public Forms Behavior Tests', () => {
  test.beforeEach(async ({ page }) => {
    page.on('pageerror', (err) => {
      throw new Error(`Unhandled exception on page: ${err.message}`);
    });
  });

  test('public feedback form client flow behaves correctly', async ({ page }) => {
    // Mock the feedback endpoint
    await page.route('**/api/public/feedback', async (route) => {
      expect(route.request().method()).toBe('POST');
      const postData = JSON.parse(route.request().postData() || '{}');
      expect(postData.message).toBe('This is an automated test feedback.');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Feedback received' }),
      });
    });

    await page.goto('/');
    await expect(page.locator('.page-home')).toBeVisible();

    // Fill the feedback form
    await page.getByLabel('Name').fill('Test User');
    await page.getByLabel('Feedback message').fill('This is an automated test feedback.');
    
    // Submit
    await page.getByRole('button', { name: 'Send Feedback' }).click();

    // Verify success message
    await expect(page.locator('.contrib-success-message')).toBeVisible();
    await expect(page.locator('.contrib-success-message')).toContainText('Thanks for your feedback.');
  });

  test('contribute form client flow behaves correctly', async ({ page }) => {
    // Mock the contributor endpoint
    await page.route('**/api/public/contributor-leads', async (route) => {
      expect(route.request().method()).toBe('POST');
      const postData = JSON.parse(route.request().postData() || '{}');
      expect(postData.email).toBe('test@example.com');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Lead created' }),
      });
    });

    await page.goto('/contribute');
    await expect(page.locator('.page-contribute')).toBeVisible();

    // Fill the contribute form
    await page.getByPlaceholder('Enter your email address').fill('test@example.com');
    
    // Submit
    await page.getByRole('button', { name: 'Notify Me' }).click();

    // Verify success message
    await expect(page.locator('.contrib-success-message')).toBeVisible();
    await expect(page.locator('.contrib-success-message')).toContainText("You're on the list.");
  });

  test('feedback form shows error for empty message submission', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.page-home')).toBeVisible();
    
    // Attempt submit with empty message
    await page.getByRole('button', { name: 'Send Feedback' }).click();
    
    await expect(page.locator('.contrib-form-notice-error')).toBeVisible();
    await expect(page.locator('.contrib-form-notice-error')).toContainText('Write a message before submitting.');
  });
});
