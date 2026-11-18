import { expect, test } from '@playwright/test'
import market from './fixtures/market.json' with { type: 'json' }
import research from './fixtures/research.json' with { type: 'json' }
import shadow from './fixtures/shadow.json' with { type: 'json' }
import board from './fixtures/board.json' with { type: 'json' }
import boardSeries from './fixtures/board-series.json' with { type: 'json' }
import strategies from './fixtures/strategies.json' with { type: 'json' }
import strategy from './fixtures/strategy.json' with { type: 'json' }
import backtests from './fixtures/backtests.json' with { type: 'json' }
import backtest from './fixtures/backtest.json' with { type: 'json' }
import researchJobs from './fixtures/research-jobs.json' with { type: 'json' }
import dOverview from './fixtures/data-overview.json' with { type: 'json' }
import dTables from './fixtures/data-tables.json' with { type: 'json' }
import dTable from './fixtures/data-table.json' with { type: 'json' }
import dSample from './fixtures/data-sample.json' with { type: 'json' }
import dCatalog from './fixtures/data-catalog.json' with { type: 'json' }
import dMetric from './fixtures/data-metric.json' with { type: 'json' }
import dQuality from './fixtures/data-quality.json' with { type: 'json' }
import dRule from './fixtures/data-rule.json' with { type: 'json' }

const CLEAR = {
  schema_version: 1, checked_at: '2026-11-17T10:40:00+08:00',
  kill: { state: 'clear', label: '未触发', message: '', sources: [] },
  live: { state: 'trading', label: '实盘运行' }, agent_heartbeat_at: '2026-11-17T10:39:58+08:00',
  qmt: null, alerts_open: null, missing: { qmt: '后续计划接入', alerts_open: '后续计划接入' },
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/app/shell/status', (r) => r.fulfill({ json: CLEAR }))
  await page.route('**/api/app/market', (r) => r.fulfill({ json: market }))
  await page.route('**/api/app/ailu/shadow*', (r) => r.fulfill({ json: shadow }))
  await page.route('**/api/app/ailu/research*', (r) => r.fulfill({ json: research }))
  await page.route('**/api/app/board/series*', (r) => r.fulfill({ json: boardSeries }))
  await page.route(/\/api\/app\/board(\?.*)?$/, (r) => r.fulfill({ json: board }))
  await page.route('**/api/app/strategies', (r) => r.fulfill({ json: strategies }))
  await page.route('**/api/app/strategies/*', (r) => r.fulfill({ json: strategy }))
  await page.route('**/api/app/backtests', (r) => r.fulfill({ json: backtests }))
  await page.route('**/api/app/backtests/*', (r) => r.fulfill({ json: backtest }))
  await page.route('**/api/app/research-jobs', (r) => r.fulfill({ json: researchJobs }))
  await page.route('**/api/app/data/overview', (r) => r.fulfill({ json: dOverview }))
  await page.route(/\/api\/app\/data\/tables(\?.*)?$/, (r) => r.fulfill({ json: dTables }))
  await page.route('**/api/app/data/tables/*/sample', (r) => r.fulfill({ json: dSample }))
  await page.route(/\/api\/app\/data\/tables\/[^/]+$/, (r) => r.fulfill({ json: dTable }))
  await page.route(/\/api\/app\/data\/catalog(\?.*)?$/, (r) => r.fulfill({ json: dCatalog }))
  await page.route(/\/api\/app\/data\/catalog\/[^/?]+$/, (r) => r.fulfill({ json: dMetric }))
  await page.route('**/api/app/data/quality', (r) => r.fulfill({ json: dQuality }))
  await page.route('**/api/app/data/quality/rules/*', (r) => r.fulfill({ json: dRule }))
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

  test(`board page has no horizontal overflow at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/app/board')
    await expect(page.getByTestId('hero-title')).toContainText('涨停 72')
    await expect(page.getByText('谁在封板、梯队如何？')).toBeVisible()
    await noOverflow(page)
    await page.getByText('昨日涨停今日表现').click()
    await expect(page).toHaveURL(/view=compare/)
    await expect(page.getByTestId('effect-avg')).toBeVisible()
    await noOverflow(page)
    await page.locator('[data-item]').first().click()
    await expect(page.locator('.ant-drawer-title')).toContainText('300500.SZ')
    await expect(page.locator('.ant-drawer canvas').first()).toBeVisible()
  })

  test(`strategies and backtest pages have no horizontal overflow at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/app/strategies?id=b5715c156ffc446a91c7c5fae70ff134')
    await expect(page.getByLabel('策略源码')).toBeVisible()
    await noOverflow(page)
    await page.goto('/app/backtest?id=big')
    await expect(page.getByText('跑赢基准了吗？')).toBeVisible()
    await expect(page.locator('canvas').first()).toBeVisible()
    await noOverflow(page)
    await page.goto('/app/backtest?tab=research')
    await expect(page.getByText('任务跑完了吗？')).toBeVisible()
    await noOverflow(page)
  })

  test(`data pages have no horizontal overflow at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/app/dataov')
    await expect(page.getByText('数据从哪来、被谁用？')).toBeVisible()
    await expect(page.locator('canvas').first()).toBeVisible()
    await noOverflow(page)
    await page.goto('/app/tables?id=instruments&tab=sample')
    await expect(page.getByText(/只读抽样，不代表全表/)).toBeVisible()
    await noOverflow(page)
    await page.goto('/app/catalog?id=tushare_daily.dt')
    await expect(page.getByText('人工抽样验真')).toBeVisible()
    await noOverflow(page)
    await page.goto('/app/quality?rule=c.rule')
    await expect(page.getByRole('link', { name: '下载违规样本' })).toBeVisible()
    await noOverflow(page)
  })
}
