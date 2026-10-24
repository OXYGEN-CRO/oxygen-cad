import { test, expect, type Page } from '@playwright/test';

test.skip(!process.env.O3D_PLATFORM_BROWSER_FIXTURE, 'Requires tests/platform_browser_server.py with a disposable project catalog and imported reference');

async function platform(page: Page) {
  await page.getByRole('combobox', { name: 'Workspace', exact: true }).selectOption('platform');
  await expect(page.getByRole('heading', { name: 'Projects and recovery', exact: true })).toBeVisible();
}
async function expand(page: Page, summary: string) {
  const detail = page.locator('details').filter({ has: page.locator('summary', { hasText: summary }) }).first();
  if ((await detail.getAttribute('open')) === null) await detail.locator('summary').click();
  return detail;
}

test('platform lifecycle draft recovery revision review and portable restore use real integrated UI', async ({ page, context, request }, testInfo) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await page.goto('/');
  await platform(page);
  const original = await (await request.get('/api/project')).json();
  expect(original.references.length).toBeGreaterThan(0);
  let local = await expand(page, 'Local projects');
  await local.getByLabel('Current project title').fill('Renamed via platform browser');
  await local.getByRole('button', { name: 'Rename current project', exact: true }).click();
  await expect.poll(async () => (await (await request.get('/api/project')).json()).name).toBe('Renamed via platform browser');
  await local.getByLabel('Directory name').fill('created-ui');
  await local.getByRole('button', { name: 'Create', exact: true }).click();
  await expect(local.locator('a[href="/p/created-ui/"]')).toBeVisible();
  await local.getByLabel('Directory name').fill('duplicated-ui');
  await local.getByRole('button', { name: 'Duplicate current', exact: true }).click();
  await expect(local.locator('a[href="/p/duplicated-ui/"]')).toBeVisible();
  const created = local.locator('.platform-project').filter({ has: page.locator('a[href="/p/created-ui/"]') });
  await created.getByRole('button', { name: 'Archive', exact: true }).click();
  await expect(local.locator('a[href="/p/created-ui/"]')).toHaveCount(0);
  await local.getByRole('button', { name: 'Show archived projects', exact: true }).click();
  await expect(created).toBeVisible();
  await created.getByRole('button', { name: 'Reopen', exact: true }).click();
  await expect(local.locator('a[href="/p/created-ui/"]')).toHaveCount(0);
  await local.getByRole('button', { name: 'Show active projects', exact: true }).click();
  await expect(local.locator('a[href="/p/created-ui/"]')).toBeVisible();

  let drafts = await expand(page, 'Recoverable source draft');
  await drafts.getByRole('button', { name: 'Start from saved source', exact: true }).click();
  const source = drafts.getByRole('textbox', { name: 'Recoverable project source', exact: true });
  const draft = JSON.parse(await source.inputValue());
  draft.name = 'Draft restored through browser';
  await source.fill(JSON.stringify(draft, null, 2));
  await drafts.getByRole('button', { name: 'Save recovery draft', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Recovery draft saved');
  expect((await (await request.get('/api/project')).json()).name).toBe('Renamed via platform browser');
  await page.reload();
  await platform(page);
  drafts = await expand(page, 'Recoverable source draft');
  expect(JSON.parse(await drafts.getByRole('textbox', { name: 'Recoverable project source', exact: true }).inputValue()).name).toBe(draft.name);
  await drafts.getByRole('button', { name: 'Reconcile and apply', exact: true }).click();
  await expect.poll(async () => (await (await request.get('/api/project')).json()).name).toBe(draft.name);
  await expect(drafts.getByRole('textbox', { name: 'Recoverable project source', exact: true })).toHaveValue('');

  const review = await expand(page, 'Checkpoints and exact revision review');
  await review.getByLabel('Review note').fill('Measured fixture and preserved reference verified');
  await review.getByRole('button', { name: 'Checkpoint', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Named checkpoint saved');
  await review.getByRole('button', { name: 'Approve revision', exact: true }).click();
  await expect(review).toContainText('approved · current');
  local = await expand(page, 'Local projects');
  await local.getByLabel('Current project title').fill('After exact revision review');
  await local.getByRole('button', { name: 'Rename current project', exact: true }).click();
  await expect(review).toContainText('approved · stale');

  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download complete project', exact: true }).click();
  const download = await downloadEvent;
  const saved = testInfo.outputPath('saved-project.oxygen.zip');
  await download.saveAs(saved);
  await local.getByLabel('Directory name').fill('restored-ui');
  await local.getByLabel('Restore a portable project or release').setInputFiles(saved);
  await local.getByRole('button', { name: 'Restore into new directory', exact: true }).click();
  await expect(local.locator('a[href="/p/restored-ui/"]')).toBeVisible();
  const popupEvent = context.waitForEvent('page');
  await local.locator('a[href="/p/restored-ui/"]').click();
  const restored = await popupEvent;
  restored.on('pageerror', error => errors.push(error.message));
  await restored.waitForLoadState('domcontentloaded');
  await platform(restored);
  const childSource = await restored.evaluate(async () => (await fetch('/p/restored-ui/api/project')).json());
  const parentSource = await (await request.get('/api/project')).json();
  expect(childSource).toEqual(parentSource);
  const childLocal = await expand(restored, 'Local projects');
  await childLocal.getByLabel('Current project title').fill('Independent restored project');
  await childLocal.getByRole('button', { name: 'Rename current project', exact: true }).click();
  await expect.poll(async () => (await (await request.get('/p/restored-ui/api/project')).json()).name).toBe('Independent restored project');
  expect((await (await request.get('/api/project')).json()).name).toBe('After exact revision review');
  await page.screenshot({path: testInfo.outputPath('platform-main.png'), fullPage: true});
  await restored.screenshot({path: testInfo.outputPath('platform-restored.png'), fullPage: true});
  expect(errors).toEqual([]);
  await restored.close();
});
