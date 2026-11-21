import { expect, test } from '@playwright/test'

const STOPPED = {
  schema_version: 1,
  checked_at: '2026-11-17T10:40:00+08:00',
  kill: {
    state: 'stopped', label: '实盘已停机', message: '禁止自动开仓；减仓与风控沿用现有规则。',
    sources: [
      { id: 'linux', name: 'Linux 本地停机闸', state: 'stopped', reason: 'Linux⇄Windows Redis 隧道心跳中断超过 90 秒，触发 watchdog', recorded_at: '2026-11-17T10:32:35+08:00' },
      { id: 'redis', name: 'Redis 实盘停机闸', state: 'stopped', reason: '同上', recorded_at: '2026-11-17T10:32:35+08:00' },
      { id: 'agent', name: 'Windows 实盘 agent', state: 'unknown', reason: '心跳不可用', recorded_at: null },
    ],
  },
  live: { state: 'reduce_only', label: '仅允许减仓' },
  agent_heartbeat_at: null,
  qmt: null, alerts_open: null,
  missing: { qmt: '待计划 03/07 接入', alerts_open: '待计划 04 接入' },
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/app/shell/status', (r) => r.fulfill({ json: STOPPED }))
  await page.route('**/api/app/search', (r) => r.fulfill({ json: { schema_version: 1, missing: {}, items: [
    { kind: '指标', key: 'daily.close', label: 'daily.close', hint: '收盘价', page: 'catalog', params: { id: 'daily.close' } },
  ] } }))
})

for (const width of [390, 1280, 1600]) {
  test(`no horizontal overflow at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/app/today')
    await expect(page.getByRole('alert')).toContainText('停机闸已触发')
    await expect(page.getByTestId('kill-pill')).toBeVisible()
    await expect(page.getByTestId('live-pill')).toBeVisible()
    const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth])
    expect(sw).toBe(cw)
  })
}

test('secondary stats collapse below 1560px and stay visible above', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 800 })
  await page.goto('/app/today')
  await expect(page.getByTestId('stat-qmt')).toBeVisible()
  await page.setViewportSize({ width: 1500, height: 800 })
  await expect(page.getByTestId('stat-qmt')).toBeHidden()
})

test('deep link renders the page and nav switches routes', async ({ page }) => {
  await page.goto('/app/orders')
  await expect(page.getByRole('heading', { name: '委托与成交' })).toBeVisible()
  await page.getByRole('menuitem', { name: '对账与风控' }).click()
  await expect(page).toHaveURL(/\/app\/recon$/)
  await expect(page.getByRole('heading', { name: '对账与风控' })).toBeVisible()
})

test('history day shows a global read-only banner and can return to today', async ({ page }) => {
  await page.goto('/app/today?day=2026-11-17')
  await expect(page.getByRole('status')).toContainText('正在回看 2026-11-17 · 只读')
  await page.getByRole('button', { name: '回到今天' }).click()
  await expect(page).not.toHaveURL(/day=/)
  await expect(page.getByRole('status')).toHaveCount(0)
})

test('global search opens a business object with its query params', async ({ page }) => {
  await page.goto('/app/today')
  await page.getByRole('button', { name: '搜索' }).click()
  await page.getByPlaceholder('搜索页面…').fill('daily.close')
  await page.getByRole('option').filter({ hasText: 'daily.close' }).click()
  await expect(page).toHaveURL('/app/catalog?id=daily.close')
})

test('kill banner never covers the side navigation after scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 420 })
  await page.goto('/app/recon')
  await page.mouse.wheel(0, 600)
  await page.waitForTimeout(200)
  const banner = await page.getByRole('alert').boundingBox()
  const first = await page.getByRole('menuitem', { name: '今日运行' }).boundingBox()
  expect(banner && first).toBeTruthy()
  expect(first!.y).toBeGreaterThanOrEqual(banner!.y + banner!.height - 1)
})

test('sider collapses automatically below 992px and restores preference above', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await page.goto('/app/today')
  const width = async () => (await page.locator('.ant-layout-sider').boundingBox())!.width
  await expect.poll(width).toBeLessThanOrEqual(56)
  await page.setViewportSize({ width: 1280, height: 800 })
  await expect.poll(width).toBe(196)
})
