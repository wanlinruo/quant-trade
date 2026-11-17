import { expect, test } from '@playwright/test'

const STOPPED = {
  schema_version: 1,
  checked_at: '2026-11-17T10:40:00',
  kill: {
    state: 'stopped', label: '实盘已停机', message: '禁止自动开仓；减仓与风控沿用现有规则。',
    sources: [
      { id: 'linux', name: 'Linux 本地停机闸', state: 'stopped', reason: 'Linux⇄Windows Redis 隧道心跳中断超过 90 秒，触发 watchdog', recorded_at: '2026-11-17T10:32:35' },
      { id: 'redis', name: 'Redis 实盘停机闸', state: 'stopped', reason: '同上', recorded_at: '2026-11-17T10:32:35' },
      { id: 'agent', name: 'Windows 实盘 agent', state: 'unknown', reason: '心跳不可用', recorded_at: null },
    ],
  },
  live: { state: 'reduce_only', label: '仅允许减仓' },
  agent_heartbeat_at: null,
  qmt: null, qmt_reason: '第 03 份计划提供结构化链路记录后接入',
  alerts_open: null, alerts_reason: '第 04 份计划提供告警汇总后接入',
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/shell/status', (r) => r.fulfill({ json: STOPPED }))
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
