import type { Page } from '@playwright/test';

export async function gotoApp(page: Page, path = '/index.html'): Promise<void> {
  await page.goto(path);
  await waitForAppReady(page);
}

export async function waitForAppReady(page: Page): Promise<void> {
  await page.waitForFunction(() => {
    const btn = document.getElementById('mode-letter');
    const langTo = document.getElementById('lang-to') as HTMLSelectElement | null;
    return !!btn && btn.hasAttribute('aria-pressed')
      && !!langTo && langTo.options.length > 0;
  }, undefined, { timeout: 15000 });
}
