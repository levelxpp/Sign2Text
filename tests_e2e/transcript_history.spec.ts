import { test, expect } from '@playwright/test';
import { gotoApp, waitForAppReady } from './_helpers';

const FAKE_USER = {
  sub: 'google-test-sub-123',
  email: 'test@example.com',
  name: 'Test User',
  picture: '',
  email_verified: true,
};

const SCOPED_KEY = `aslHistorySessions:${FAKE_USER.sub}`;
const ANON_KEY = 'aslHistorySessions:anon';

function makeFakeBackendRow(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    google_sub: FAKE_USER.sub,
    client_history_id: 'backend_entry_1',
    mode: 'asl',
    started_at: '2026-04-12T10:00:00Z',
    text: 'Hello from backend',
    created_at: '2026-04-12T10:00:00Z',
    updated_at: '2026-04-12T10:00:00Z',
    ...overrides,
  };
}

/**
 * Mock /auth/me to return the fake user so index.html's fetchMe()
 * sets __AUTH_USER__ correctly (instead of getting a real 401).
 */
async function mockSignedIn(page: import('@playwright/test').Page) {
  await page.route('**/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(FAKE_USER),
    });
  });
}

test.describe('Transcript History Backend Integration', () => {

  test('anonymous user: no backend transcript calls made', async ({ page }) => {
    const apiCalls: string[] = [];
    await page.route('**/api/transcripts**', async (route) => {
      apiCalls.push(route.request().method());
      await route.fulfill({ status: 401, body: '{}' });
    });

    await gotoApp(page);

    expect(apiCalls).toHaveLength(0);
  });

  test('signed-in user: loads transcript history from backend on startup', async ({ page }) => {
    const rows = [
      makeFakeBackendRow({ id: 1, client_history_id: 'entry_a', text: 'First transcript' }),
      makeFakeBackendRow({ id: 2, client_history_id: 'entry_b', text: 'Second transcript', started_at: '2026-04-12T09:00:00Z' }),
    ];

    await mockSignedIn(page);

    await page.route('**/api/transcripts**', async (route) => {
      const method = route.request().method();
      if (method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(rows),
        });
      } else {
        // Handle any POSTs (migration) gracefully
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(makeFakeBackendRow()) });
      }
    });

    await gotoApp(page);

    const historyList = page.locator('#history-list');
    await expect(historyList.locator('.history-snippet').first()).toBeVisible({ timeout: 10000 });

    const snippets = historyList.locator('.history-snippet');
    await expect(snippets).toHaveCount(2);
    await expect(snippets.nth(0)).toContainText('First transcript');
    await expect(snippets.nth(1)).toContainText('Second transcript');
  });

  test('signed-in user: save archives transcript to backend via POST', async ({ page }) => {
    let postPayload: Record<string, unknown> | null = null;

    await mockSignedIn(page);

    await page.route('**/api/transcripts**', async (route) => {
      const method = route.request().method();
      if (method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: '[]',
        });
      } else if (method === 'POST') {
        postPayload = JSON.parse(route.request().postData() || '{}');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(makeFakeBackendRow({
            id: 99,
            client_history_id: (postPayload as any)?.client_history_id,
            text: (postPayload as any)?.text,
          })),
        });
      } else {
        await route.continue();
      }
    });

    await gotoApp(page);

    // Seed user-scoped localStorage with a local-only entry (no _backendId)
    // to trigger migration on next async load
    await page.evaluate((key) => {
      const entry = {
        id: 'test_save_entry',
        mode: 'speech',
        startedAt: new Date().toISOString(),
        text: 'Test transcript text for POST',
      };
      localStorage.setItem(key, JSON.stringify([entry]));
    }, SCOPED_KEY);

    // Reload to trigger loadHistoryAsync -> migration of local-only entry
    await page.reload();
    await waitForAppReady(page);

    // Wait for the migration POST
    await expect.poll(() => postPayload, { timeout: 10000 }).not.toBeNull();
    expect(postPayload).toMatchObject({
      client_history_id: 'test_save_entry',
      mode: 'speech',
      text: 'Test transcript text for POST',
    });
  });

  test('signed-in user: delete calls backend DELETE with correct ID', async ({ page }) => {
    let deleteUrl = '';

    await mockSignedIn(page);

    const backendRow = makeFakeBackendRow({ id: 42, client_history_id: 'del_entry', text: 'To be deleted' });

    await page.route('**/api/transcripts**', async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([backendRow]),
        });
      } else if (method === 'DELETE') {
        deleteUrl = url;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ deleted: true }),
        });
      } else if (method === 'POST') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(makeFakeBackendRow()) });
      } else {
        await route.continue();
      }
    });

    await gotoApp(page);

    // Wait for history to render from backend
    const historyList = page.locator('#history-list');
    await expect(historyList.locator('.history-snippet').first()).toBeVisible({ timeout: 10000 });

    // Click the item menu button then delete
    const item = historyList.locator('.history-item').first();
    await item.locator('.history-item-menu-button').click();
    await item.locator('.history-item-delete').click();

    // Verify DELETE was called with the backend ID
    await expect.poll(() => deleteUrl, { timeout: 5000 }).toContain('/api/transcripts/42');
  });

  test('backend failure: gracefully falls back to localStorage', async ({ page }) => {
    await mockSignedIn(page);

    // Backend transcript routes return 500
    await page.route('**/api/transcripts**', async (route) => {
      await route.fulfill({ status: 500, body: 'Internal Server Error' });
    });

    await gotoApp(page);

    // Seed localStorage after page load (so scoped key works with auth)
    await page.evaluate((key) => {
      const entries = [
        { id: 'fallback_1', mode: 'asl', startedAt: '2026-04-12T09:00:00Z', text: 'Fallback entry one' },
        { id: 'fallback_2', mode: 'asl', startedAt: '2026-04-12T08:00:00Z', text: 'Fallback entry two' },
      ];
      localStorage.setItem(key, JSON.stringify(entries));
    }, SCOPED_KEY);

    // Trigger re-render by dispatching auth:changed (simulates scope reload)
    await page.evaluate((user) => {
      window.dispatchEvent(new CustomEvent('auth:changed', { detail: { user } }));
    }, FAKE_USER);

    // History should show local entries despite backend failure
    const historyList = page.locator('#history-list');
    await expect(historyList.locator('.history-snippet').first()).toBeVisible({ timeout: 10000 });

    const snippets = historyList.locator('.history-snippet');
    await expect(snippets).toHaveCount(2);
    await expect(snippets.nth(0)).toContainText('Fallback entry one');
  });

  test('migration: user-scoped local entries are pushed to backend', async ({ page }) => {
    const postPayloads: Record<string, unknown>[] = [];

    await mockSignedIn(page);

    // Backend GET returns empty (no existing backend entries)
    await page.route('**/api/transcripts**', async (route) => {
      const method = route.request().method();
      if (method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: '[]',
        });
      } else if (method === 'POST') {
        postPayloads.push(JSON.parse(route.request().postData() || '{}'));
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(makeFakeBackendRow({ id: postPayloads.length })),
        });
      } else {
        await route.continue();
      }
    });

    await gotoApp(page);

    // Seed user-scoped localStorage with entries that have no _backendId
    await page.evaluate((key) => {
      const entries = [
        { id: 'migrate_1', mode: 'asl', startedAt: '2026-04-12T09:00:00Z', text: 'Entry to migrate 1' },
        { id: 'migrate_2', mode: 'speech', startedAt: '2026-04-12T08:00:00Z', text: 'Entry to migrate 2' },
      ];
      localStorage.setItem(key, JSON.stringify(entries));
    }, SCOPED_KEY);

    // Trigger re-render + async load which will detect local-only entries and migrate
    await page.evaluate((user) => {
      window.dispatchEvent(new CustomEvent('auth:changed', { detail: { user } }));
    }, FAKE_USER);

    // Wait for migration POSTs
    await expect.poll(() => postPayloads.length, { timeout: 10000 }).toBeGreaterThanOrEqual(2);

    const migrated_ids = postPayloads.map(p => p.client_history_id);
    expect(migrated_ids).toContain('migrate_1');
    expect(migrated_ids).toContain('migrate_2');
  });

  test('signed-in user: clear-all calls backend DELETE /api/transcripts', async ({ page }) => {
    let bulkDeleteCalled = false;

    await mockSignedIn(page);

    const backendRows = [
      makeFakeBackendRow({ id: 1, client_history_id: 'entry1', text: 'One' }),
      makeFakeBackendRow({ id: 2, client_history_id: 'entry2', text: 'Two' }),
    ];

    await page.route('**/api/transcripts**', async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(backendRows),
        });
      } else if (method === 'DELETE' && !url.match(/\/api\/transcripts\/\d+/)) {
        bulkDeleteCalled = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ deleted: true, count: 2 }),
        });
      } else if (method === 'POST') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(makeFakeBackendRow()) });
      } else {
        await route.continue();
      }
    });

    await gotoApp(page);

    // Wait for backend data to reach localStorage (from loadHistoryAsync)
    await expect.poll(() =>
      page.evaluate((key) => {
        const d = localStorage.getItem(key);
        return d ? JSON.parse(d).length : 0;
      }, SCOPED_KEY),
      { timeout: 10000 },
    ).toBeGreaterThan(0);

    // Accept the confirmation dialog
    page.on('dialog', dialog => dialog.accept());

    // Click clear-all
    await page.locator('#history-menu-button').click();
    await page.locator('#history-clear-all').click();

    // Verify bulk DELETE was called
    await expect.poll(() => bulkDeleteCalled, { timeout: 5000 }).toBe(true);

    // Verify localStorage is cleared (poll: the async handler needs to finish)
    await expect.poll(() =>
      page.evaluate((key) => {
        const d = localStorage.getItem(key);
        return d ? JSON.parse(d).length : 0;
      }, SCOPED_KEY),
      { timeout: 5000 },
    ).toBe(0);
  });

  test('signed-in user: clear-all backend failure preserves local entries', async ({ page }) => {
    await mockSignedIn(page);

    await page.route('**/api/transcripts**', async (route) => {
      const method = route.request().method();
      if (method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([makeFakeBackendRow({ id: 1, client_history_id: 'keep_me', text: 'Keep me' })]),
        });
      } else if (method === 'DELETE') {
        await route.fulfill({ status: 500, body: 'Internal Server Error' });
      } else if (method === 'POST') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(makeFakeBackendRow()) });
      } else {
        await route.continue();
      }
    });

    await gotoApp(page);

    // Wait for backend data to reach localStorage (from loadHistoryAsync)
    await expect.poll(() =>
      page.evaluate((key) => {
        const d = localStorage.getItem(key);
        return d ? JSON.parse(d).length : 0;
      }, SCOPED_KEY),
      { timeout: 10000 },
    ).toBeGreaterThan(0);

    page.on('dialog', dialog => dialog.accept());

    await page.locator('#history-menu-button').click();
    await page.locator('#history-clear-all').click();

    // Wait for the DELETE attempt to complete, then verify localStorage was NOT cleared
    await page.waitForTimeout(1000);
    const stored = await page.evaluate((key) => localStorage.getItem(key), SCOPED_KEY);
    const entries = JSON.parse(stored || '[]');
    expect(entries.length).toBeGreaterThan(0);
  });

  test('anonymous user: clear-all has no backend transcript calls', async ({ page }) => {
    const apiCalls: string[] = [];
    await page.route('**/api/transcripts**', async (route) => {
      apiCalls.push(route.request().method());
      await route.fulfill({ status: 401, body: '{}' });
    });

    await gotoApp(page);

    // Seed anon localStorage
    await page.evaluate((key) => {
      localStorage.setItem(key, JSON.stringify([
        { id: 'anon_1', mode: 'asl', startedAt: '2026-04-12T09:00:00Z', text: 'Anon entry' },
      ]));
    }, ANON_KEY);

    // Accept the confirmation dialog
    page.on('dialog', dialog => dialog.accept());

    // Click clear-all (buttons are always in the DOM; anonymous path is synchronous)
    await page.locator('#history-menu-button').click();
    await page.locator('#history-clear-all').click();

    await page.waitForTimeout(500);

    // No backend calls should have been made
    expect(apiCalls).toHaveLength(0);

    // localStorage should be cleared
    const stored = await page.evaluate((key) => localStorage.getItem(key), ANON_KEY);
    expect(JSON.parse(stored || '[]')).toEqual([]);
  });

  test('sign-out: no backend calls, shows anon-scoped history', async ({ page }) => {
    await mockSignedIn(page);

    await page.route('**/api/transcripts**', async (route) => {
      const method = route.request().method();
      if (method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: '[]',
        });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      }
    });

    await gotoApp(page);

    // Seed anon localStorage
    await page.evaluate((key) => {
      localStorage.setItem(key, JSON.stringify([
        { id: 'anon_entry', mode: 'asl', startedAt: '2026-04-12T07:00:00Z', text: 'Anon history item' },
      ]));
    }, ANON_KEY);

    // Track API calls after sign-out
    const postSignOutCalls: string[] = [];
    await page.route('**/api/transcripts**', async (route) => {
      postSignOutCalls.push(route.request().method());
      await route.fulfill({ status: 401, body: '{}' });
    });

    // Simulate sign-out
    await page.evaluate(() => {
      (window as any).__AUTH_USER__ = null;
      window.dispatchEvent(new CustomEvent('auth:changed', { detail: { user: null } }));
    });

    // Give a moment for any would-be async calls
    await page.waitForTimeout(1000);

    // No transcript API calls should have been made after sign-out
    expect(postSignOutCalls).toHaveLength(0);

    // Anon history should be visible
    const historyList = page.locator('#history-list');
    await expect(historyList.locator('.history-snippet').first()).toBeVisible({ timeout: 5000 });
    await expect(historyList.locator('.history-snippet').first()).toContainText('Anon history item');
  });
});
