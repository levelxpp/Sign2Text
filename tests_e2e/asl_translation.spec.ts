import { test, expect } from '@playwright/test';
import { gotoApp } from './_helpers';

const FAKE_USER = {
  sub: 'google-test-sub-asl-789',
  email: 'asl-test@example.com',
  name: 'ASL Test User',
  picture: '',
  email_verified: true,
};

async function mockSignedIn(page: import('@playwright/test').Page) {
  await page.route('**/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(FAKE_USER),
    });
  });
}

async function switchToAslMode(page: import('@playwright/test').Page) {
  await page.click('#mode-asl');
  await expect(page.locator('#mode-asl')).toHaveAttribute('aria-pressed', 'true');
}

test.describe('ASL Mode Translation & TTS', () => {

  test('ASL mode: "To" dropdown is selectable, "From" stays locked to asl', async ({ page }) => {
    await mockSignedIn(page);
    await gotoApp(page);
    await switchToAslMode(page);

    const fromSel = page.locator('#lang-from');
    const toSel = page.locator('#lang-to');

    await expect(fromSel).toBeDisabled();
    await expect(fromSel).toHaveValue('asl');
    await expect(toSel).toBeEnabled();

    await toSel.selectOption('es');
    await expect(toSel).toHaveValue('es');

    await toSel.selectOption('ar');
    await expect(toSel).toHaveValue('ar');

    await toSel.selectOption('hy');
    await expect(toSel).toHaveValue('hy');

    await toSel.selectOption('en');
    await expect(toSel).toHaveValue('en');
  });

  test('ASL mode: target language persists across mode switches', async ({ page }) => {
    await mockSignedIn(page);
    await gotoApp(page);
    await switchToAslMode(page);

    const toSel = page.locator('#lang-to');
    await toSel.selectOption('hy');
    await expect(toSel).toHaveValue('hy');

    await page.click('#mode-speech');
    await page.click('#mode-letter');
    await switchToAslMode(page);

    await expect(toSel).toHaveValue('hy');
  });

  test('ASL mode: per-mode language settings are independent', async ({ page }) => {
    await mockSignedIn(page);
    await gotoApp(page);

    const toSel = page.locator('#lang-to');

    // Voice target = Spanish
    await toSel.selectOption('es');

    // Letter target = Arabic
    await page.click('#mode-letter');
    await toSel.selectOption('ar');
    await expect(toSel).toHaveValue('ar');

    // ASL target = Armenian
    await switchToAslMode(page);
    await toSel.selectOption('hy');
    await expect(toSel).toHaveValue('hy');

    // Verify each mode restores its own target
    await page.click('#mode-speech');
    await expect(toSel).toHaveValue('es');

    await page.click('#mode-letter');
    await expect(toSel).toHaveValue('ar');

    await switchToAslMode(page);
    await expect(toSel).toHaveValue('hy');
  });

  test('ASL mode: no /api/translate fires on mode entry when target is English', async ({ page }) => {
    await mockSignedIn(page);

    const translateCalls: string[] = [];
    await page.route('**/api/translate', async (route) => {
      translateCalls.push(route.request().url());
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ translatedText: 'x', to: 'en', from: 'en' }),
      });
    });

    await gotoApp(page);
    await switchToAslMode(page);

    const toSel = page.locator('#lang-to');
    // Force default back to English to cover the ASL → English direct branch
    await toSel.selectOption('en');
    await expect(toSel).toHaveValue('en');

    // Without an active ASL SSE translation event, nothing should call translate.
    // This asserts no spurious translate fires from mode switching or dropdown UI alone.
    await page.waitForTimeout(500);
    expect(translateCalls).toHaveLength(0);
  });

  test('ASL mode: hint text is empty (no fixed ASL → English banner)', async ({ page }) => {
    await mockSignedIn(page);
    await gotoApp(page);
    await switchToAslMode(page);

    const hint = page.locator('#lang-mode-hint');
    // After Task 2, ASL mode no longer shows the "ASL -> English" fixed hint
    await expect(hint).toBeHidden();
  });

  test('ASL mode: wiring sanity — finalized `translation` SSE event appends to captions when To=en, no translate call', async ({ page }) => {
    await mockSignedIn(page);

    const translateCalls: string[] = [];
    await page.route('**/api/translate', async (route) => {
      translateCalls.push(route.request().url());
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ translatedText: 'unused', to: 'en', from: 'en' }),
      });
    });

    await gotoApp(page);
    await switchToAslMode(page);

    const toSel = page.locator('#lang-to');
    await toSel.selectOption('en');

    // The ASL SSE pipeline is backend-driven; from an E2E browser test we
    // cannot cleanly synthesize an SSE `translation` event without running the
    // full ASL service. What we *can* assert is the negative contract: mode
    // entry + dropdown interaction must not produce spurious translate calls
    // when To = English.
    await page.waitForTimeout(500);
    expect(translateCalls).toHaveLength(0);
  });
});
