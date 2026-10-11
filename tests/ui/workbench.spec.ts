import { test, expect } from '@playwright/test';

test('launch a swarm, inspect agents, follow up, and export completed work', async ({ page }) => {
  const browserErrors: string[] = [];
  page.on('pageerror', e => browserErrors.push(e.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /Give your swarm/ })).toBeVisible();
  await page.screenshot({ path: 'test-results/workbench-home.png', fullPage: true });
  await page.getByLabel('THE MISSION').fill('Compare three approaches to building a community research library');
  await page.getByLabel('Execution mode').selectOption('demo');
  await page.getByRole('button', { name: 'Run offline demo' }).click();
  await expect(page.locator('.run-title .badge')).toHaveText('completed', { timeout: 25000 });
  await expect(page.locator('.dock-agent')).toHaveCount(4);
  await page.locator('.dock-agent').filter({ hasText: 'Researcher' }).click();
  await expect(page.locator('.inspector-identity h2')).toHaveText('Researcher');
  await expect(page.locator('.inspector .message-content')).toContainText('Received work from: Planner');
  await page.getByLabel('Message Researcher').fill('Focus on accessibility for a small community');
  await page.getByRole('button', { name: 'Send agent message' }).click();
  await expect(page.locator('.inspector .message-content').last()).toContainText('Your direction: Focus on accessibility', { timeout: 12000 });
  await expect(page.locator('.inspector-header .status-text')).toHaveText('done', { timeout: 12000 });
  await page.screenshot({ path: 'test-results/workbench-run.png', fullPage: true });
  await page.getByRole('button', { name: 'Configure', exact: true }).click();
  await expect(page.getByLabel('Model assignment')).toBeVisible();
  await page.getByRole('button', { name: 'Work products' }).click();
  await expect(page.locator('.output-card')).toHaveCount(4);
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export run' }).click();
  expect((await downloaded).suggestedFilename()).toMatch(/swarm-r_.*\.json/);
  expect(browserErrors).toEqual([]);
});

test('link two account profiles sharing a Google project without exposing keys', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Providers', exact: true }).click();
  for (const [label, key, account] of [['Personal Gemini', 'ui-placeholder-key-one', 'personal'], ['School Gemini', 'ui-placeholder-key-two', 'school']]) {
    await page.getByRole('button', { name: 'Link provider', exact: true }).click();
    const modal = page.getByRole('dialog');
    await modal.getByLabel('Profile name').fill(label);
    await modal.getByLabel('Account label').fill(account);
    await modal.getByLabel('Google Cloud project ID').fill('same-project');
    await modal.getByLabel('API key', { exact: true }).fill(key);
    await modal.getByLabel('Session-only credential storage').check();
    await modal.getByRole('button', { name: 'Link profile', exact: true }).click();
    await expect(page.getByRole('heading', { name: label, exact: true })).toBeVisible();
  }
  await expect(page.locator('.stats-row > div').nth(0).locator('strong')).toHaveText('2');
  await expect(page.locator('.stats-row > div').nth(1).locator('strong')).toHaveText('1');
  await expect(page.locator('.quota-details strong')).toHaveText(['same-project', 'same-project']);
  await expect(page.locator('body')).not.toContainText('ui-placeholder-key');
  await page.getByLabel('Model for Personal Gemini').fill('user-selected-model');
  await page.getByRole('heading', { name: 'Bring your own capacity.' }).click();
  await expect(page.getByLabel('Model for Personal Gemini')).toHaveValue('user-selected-model');
  await page.screenshot({ path: 'test-results/workbench-providers.png', fullPage: true });
});

test('save a custom architecture and show detected resource budgets', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Architectures', exact: true }).click();
  await page.getByLabel('Architecture name').fill('Community team');
  await page.getByRole('button', { name: 'Add agent' }).click();
  await page.getByLabel('Agent name', { exact: true }).fill('Verifier');
  await page.getByLabel('Agent role', { exact: true }).fill('Check all assumptions');
  await page.getByLabel('Agent instructions', { exact: true }).fill('Give concrete validation steps.');
  await page.getByRole('button', { name: 'Save custom copy' }).click();
  await expect(page.getByRole('button', { name: 'Save architecture', exact: true })).toBeVisible();
  await expect(page.getByLabel('Architecture template')).toContainText('Community team');
  await page.getByRole('button', { name: 'Devices', exact: true }).click();
  await expect(page.getByLabel('RAM allocation', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Context budget')).toBeVisible();
  await expect(page.getByText('Opt-in hub adapter')).toBeVisible();
  await page.getByRole('button', { name: 'Pair a device' }).click();
  await expect(page.getByLabel('Pairing invitation')).toHaveValue(/.{30,}/);
  if (process.platform === 'win32') await expect(page.getByRole('dialog').locator('pre')).toContainText('./swarm-engine-windows-x64.exe');
  await page.getByRole('button', { name: 'Close pairing' }).click();
  await page.screenshot({ path: 'test-results/workbench-devices.png', fullPage: true });
});

test('small-screen mission and conversation stay usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /Give your swarm/ })).toBeVisible();
  await page.getByLabel('THE MISSION').fill('Make a simple checklist');
  await page.getByLabel('Execution mode').selectOption('demo');
  await page.getByRole('button', { name: 'Run offline demo' }).click();
  await expect(page.getByRole('button', { name: 'Send agent message' })).toBeVisible();
  await expect(page.locator('.dock-agent')).toHaveCount(4);
  await expect(page.locator('.dock-agent').first()).toBeInViewport({ ratio: .95 });
  await page.screenshot({ path: 'test-results/workbench-mobile.png', fullPage: true });
});
