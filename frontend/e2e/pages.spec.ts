import { expect, test } from '@playwright/test'
import market from './fixtures/market.json' with { type: 'json' }
import research from './fixtures/research.json' with { type: 'json' }
import shadow from './fixtures/shadow.json' with { type: 'json' }

const CLEAR = {
  schema_version: 1, checked_at: '2026-11-17T10:40:00',
  kill: { state: 'clear', label: '未触发', message: '', sources: [] },
  live: { state: 'trading', label: '实盘运行' }, agent_heartbeat_at: '2026-11-17T10:39:58',
  qmt: null, qmt_reason: '后续计划接入', alerts_open: null, alerts_reason: '后续计划接入',
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/shell/status', (r) => r.fulfill({ json: CLEAR }))
  await page.route('**/api/app/market', (r) => r.fulfill({ json: market }))
  await page.route('**/api/market/limit-up-shadow*', (r) => r.fulfill({ json: shadow }))
  await page.route('**/api/market/limit-up-research*', (r) => r.fulfill({ json: research }))
})

async function noOverflow(page: import('@playwright/test').Page) {
  const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth])
  expect(sw).toBe(cw)
}

for (const width of [390, 1280, 1600]) {
  test(`market page has no horizontal overflow at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/app/market')
    await expect(page.getByTestId('hero-title')).toContainText('涨停 54')
    await expect(page.getByText('近 60 日市场情绪怎样？')).toBeVisible()
    await expect(page.locator('canvas').first()).toBeVisible()
    await noOverflow(page)
  })

  test(`ailu page has no horizontal overflow at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/app/ailu')
    await expect(page.getByText('不是交易信号，不进入实盘或模拟决策。')).toBeVisible()
    await expect(page.getByText('今天模型看好谁？')).toBeVisible()
    await noOverflow(page)
    await page.getByRole('tab', { name: '研究回测' }).click()
    await expect(page.getByText('月度稳定吗？')).toBeVisible()
    await expect(page).toHaveURL(/tab=research/)
    await noOverflow(page)
  })
}
