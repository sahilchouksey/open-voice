import { test, expect, type Page } from '@playwright/test';

async function openHistoryDrawer(page: Page) {
  const settingsButton = page.getByLabel('Settings');
  await expect(settingsButton).toBeVisible();
  await settingsButton.click();

  const historyButton = page.getByRole('button', { name: 'History' });
  await expect(historyButton).toBeVisible();
  await historyButton.click();

  const historySidebar = page.getByLabel('Conversation history');
  await expect(historySidebar).toBeVisible();
  return historySidebar;
}

test.describe('History Drawer', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('main');

    const minimalTab = page.getByText('Minimal');
    if (await minimalTab.isVisible()) {
      await minimalTab.click();
    }
  });

  test('should open history drawer when clicking history button', async ({ page }) => {
    const historySidebar = await openHistoryDrawer(page);

    const drawerTitle = page.getByText('Recent chats');
    await expect(drawerTitle).toBeVisible();
    await expect(historySidebar).toBeVisible();
  });

  test('should close history drawer when clicking close button', async ({ page }) => {
    const historySidebar = await openHistoryDrawer(page);

    const closeButton = page.locator('.history-close-btn');
    await closeButton.click();

    await expect(historySidebar).not.toBeVisible();
  });

  test('should show "No previous sessions yet" when no sessions exist', async ({ page }) => {
    await openHistoryDrawer(page);

    const noSessionsMessage = page.getByText('No previous sessions yet.');
    const hasSessions = await page.locator('.history-item').count();

    if (hasSessions === 0) {
      await expect(noSessionsMessage).toBeVisible();
    } else {
      await expect(noSessionsMessage).not.toBeVisible();
    }
  });

  test('should close drawer when clicking overlay', async ({ page }) => {
    const historySidebar = await openHistoryDrawer(page);

    const overlay = page.locator('.history-overlay');
    await overlay.click();

    await expect(historySidebar).not.toBeVisible();
  });
});

test.describe('Session Selection and Transcript', () => {
  test.skip(!process.env.TEST_WITH_BACKEND, 'Skipping - requires backend (set TEST_WITH_BACKEND=true)');

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('main');

    const minimalTab = page.getByText('Minimal');
    if (await minimalTab.isVisible()) {
      await minimalTab.click();
    }
  });

  test('should load transcript when selecting a session from history', async ({ page }) => {
    await page.waitForTimeout(1000);
    await openHistoryDrawer(page);
    await page.waitForTimeout(1000);

    const noSessionsMessage = page.getByText('No previous sessions yet.');
    const hasNoSessions = await noSessionsMessage.isVisible().catch(() => false);

    if (hasNoSessions) {
      const closeButton = page.locator('.history-close-btn');
      await closeButton.click();
      return;
    }

    const sessionItem = page.locator('.history-item:not(.current)').first();

    if (await sessionItem.isVisible()) {
      await sessionItem.locator('.history-open-btn').click();
      await page.waitForTimeout(2000);

      const historySidebar = page.getByLabel('Conversation history');
      await expect(historySidebar).not.toBeVisible();

      const url = page.url();
      expect(url).toContain('session=');
    }
  });

  test('should highlight selected session in history list', async ({ page }) => {
    await openHistoryDrawer(page);

    const sessionItem = page.locator('.history-item:not(.current)').first();

    if (await sessionItem.isVisible()) {
      await sessionItem.locator('.history-open-btn').click();
      await page.waitForTimeout(1000);
      await openHistoryDrawer(page);

      const currentSession = page.locator('.history-item.current');
      await expect(currentSession).toBeVisible();
    }
  });
});
