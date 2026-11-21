import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '../../shell/ThemeProvider'
import { LivePage } from './LivePage'
import { OrdersPage } from './OrdersPage'
import { ReconciliationPage } from './ReconciliationPage'
import { RunsPage } from './RunsPage'
import { PaperPage } from './PaperPage'

vi.mock('../../page/EChart', () => ({ EChart: () => <div data-testid="echart" /> }))
const wrap = (page: React.ReactNode) => render(<ThemeProvider>{page}</ThemeProvider>)
const day = { selected: '2026-11-19', available: ['2026-11-19'] }
const kill = { state: 'clear', label: '停机闸已解除', message: '只表示三道闸未生效', sources: [] }
let payload: unknown
beforeEach(() => {
  history.replaceState(null, '', '/app/live')
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 })))
})
afterEach(() => vi.unstubAllGlobals())

test('live page never turns total asset into return', async () => {
  payload = { schema_version: 1, enabled: true, day, posture: 'trading', posture_label: '可下单',
    observation: { at: '2026-11-19T14:00:00+08:00', cash: 90000, market_value: 10000, total_value: 100000, return_ratio: null },
    positions: [], heartbeats: [], kill, risks: [], equity: [], order_count: 0,
    missing: { return_ratio: '缺少完整资金变动流水，不从总资产跳变计算收益' } }
  wrap(<LivePage />)
  expect(await screen.findByText('可下单')).toBeInTheDocument()
  expect(screen.getByText('¥100,000.00')).toBeInTheDocument()
  expect(screen.getByText('收益率').parentElement?.textContent).toContain('缺失')
})

test('unknown order says no retry and missing broker acceptance stays missing', async () => {
  payload = { schema_version: 1, enabled: true, day, fills: [], rejects: [], missing: { broker_accepted_at: '未记录，不用成交时刻反推' }, orders: [{
    id: 'o1', event_key: 'auction', symbol: '600000.SH', side: 'buy', qty: 100, remaining_qty: 100,
    order_type: 'limit', limit_price: 10, status: 'unknown', created_at: '2026-11-19T09:25:00+08:00', updated_at: null,
    broker_order_id: null, broker_accepted_at: null, unknown_no_retry: true,
    stages: [{ state: 'dispatch:unknown', at: '2026-11-19T09:25:01+08:00', detail: 'timeout' }],
  }] }
  wrap(<OrdersPage />)
  expect(await screen.findByText(/未知·不重发/)).toBeInTheDocument()
  await userEvent.click(screen.getByText('o1'))
  expect(await screen.findByText(/状态未知：按交易契约不自动重发/)).toBeInTheDocument()
  const missing = screen.getAllByText('缺失')
  await userEvent.hover(missing[missing.length - 1])
  expect(await screen.findByText('未记录，不用成交时刻反推')).toBeInTheDocument()
})

test('reconciliation page has managed checklist but no release control', async () => {
  payload = { schema_version: 1, enabled: true, day, heatmap: [
    { day: '2026-11-19', layer: 'cash', state: 'consistent', at: '2026-11-19T15:30:00+08:00', detail: '' },
    { day: '2026-11-19', layer: 'positions', state: 'consistent', at: '2026-11-19T15:30:00+08:00', detail: '' },
    { day: '2026-11-19', layer: 'orders', state: 'unknown', at: null, detail: null }], records: [], risks: [], kill,
    review: { reviewed_at: '2026-11-19T16:00:00+08:00', overall: 'needs_confirmation', engaged: true, stale: false,
      items: [{ id: 'maintainer.confirmation', label: '维护者确认', state: 'confirm', detail: '尚未确认', observed: {} }] }, missing: {} }
  wrap(<ReconciliationPage />)
  expect(await screen.findByText('解除停机闸前还差什么？')).toBeInTheDocument()
  expect(screen.getByText(/本页没有解除按钮/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /解除/ })).toBeNull()
})

test('runs page shows missing planned time instead of guessing', async () => {
  payload = { schema_version: 1, enabled: true, day, report_text: null, missing: { report: '没有日报' }, runs: [{
    event_key: 'auction', phase: 'auction', scheduled_at: null, started_at: '2026-11-19T09:25:01+08:00', finished_at: '2026-11-19T09:25:02+08:00', duration_ms: 1000,
    status: 'completed', candidates: 3, routed: 2, passed: 1, orders: 0, rejects: 0, summary: '未下单', reasons: {}, note: '', error: '', event_status: null, event_detail: null }] }
  wrap(<RunsPage />)
  const missing = await screen.findAllByText('缺失')
  await userEvent.hover(missing[0])
  expect(await screen.findByText('调度记录未关联')).toBeInTheDocument()
  await userEvent.hover(missing[missing.length - 1])
  expect(await screen.findByText('没有日报')).toBeInTheDocument()
})

test('paper page only displays masked webhook', async () => {
  const list = { schema_version: 1, enabled: true, accounts: [{ id: 'p1', name: '模拟一号', strategy_name: 'S', version_id: 'v1', status: 'running', activation_date: '2026-11-19', error: null, total_value: 100001, return_ratio: .00001, max_drawdown_ratio: 0, position_count: 0 }] }
  const detail = { ...list.accounts[0], schema_version: 1, enabled: true, backtest_id: 'b1', strategy_id: 's1', initial_cash: 100000, cash: 100001,
    notify_mask: '••••••abcdef', positions: [], orders: [], fills: [], rejects: [], events: [], logs: [], runs: [], equity: [], source_backtest: [], day, missing: { fill_reference: '模拟成交尚未记录独立参考价' } }
  vi.stubGlobal('fetch', vi.fn(async (u: string) => new Response(JSON.stringify(String(u).endsWith('/paper') ? list : detail), { status: 200 })))
  wrap(<PaperPage />)
  expect(await screen.findByText(/已配置 ••••••abcdef/)).toBeInTheDocument()
  expect(screen.queryByText(/qyapi\.weixin/)).toBeNull()
})

test('orders map live_orders terminal/pending to labels and summarise in hero KPIs', async () => {
  const order = (id: string, status: string, remaining: number) => ({ id, event_key: 'auction', symbol: '600000.SH', side: 'buy', qty: 100, remaining_qty: remaining,
    order_type: 'limit', limit_price: 10, status, created_at: '2026-11-19T09:25:00+08:00', updated_at: null,
    broker_order_id: 'b1', broker_accepted_at: null, unknown_no_retry: false, stages: [] })
  payload = { schema_version: 1, enabled: true, day, missing: {}, orders: [order('o1', 'terminal', 0), order('o2', 'pending', 100)],
    fills: [{ order_id: 'o1', symbol: '600000.SH', side: 'buy', qty: 100, price: 10, fee: 5, filled_at: '2026-11-19T09:30:00+08:00', broker_trade_id: 't1' }],
    rejects: [{ order_id: 'r1', symbol: '920001.BJ', side: 'buy', qty: 100, reason: '北交所禁开仓', at: '2026-11-19T09:25:00+08:00' }] }
  wrap(<OrdersPage />)
  expect((await screen.findByTestId('hero-title')).textContent).toBe('2 笔委托，1 笔未终结')
  expect(screen.getByTestId('hero-subtitle').textContent).toContain('交易日 2026-11-19')
  expect(screen.getByText('● 已终结')).toBeInTheDocument()
  expect(screen.getByText('◐ 未终结')).toBeInTheDocument()
  expect(screen.queryByText('terminal')).toBeNull()
  const kpis = screen.getAllByTestId('hero-kpi').map((x) => x.textContent)
  expect(kpis[0]).toContain('1 / 2')
  expect(kpis[3]).toContain('风控拦截1')
  // 页面内不再有重复的交易日下拉（全局日期在顶栏）
  expect(screen.queryByLabelText('交易日')).toBeNull()
})

test('reconciliation hero shows each layer of the selected day and never counts unknown as consistent', async () => {
  payload = { schema_version: 1, enabled: true, day: { selected: '2026-11-19', available: ['2026-11-18', '2026-11-19'] }, heatmap: [
    { day: '2026-11-18', layer: 'cash', state: 'inconsistent', at: null, detail: '现金差' },
    { day: '2026-11-19', layer: 'cash', state: 'consistent', at: null, detail: '' },
    { day: '2026-11-19', layer: 'positions', state: 'consistent', at: null, detail: '' },
    { day: '2026-11-19', layer: 'orders', state: 'unknown', at: null, detail: null }], records: [], risks: [], kill, review: null, missing: {} }
  wrap(<ReconciliationPage />)
  expect((await screen.findByTestId('hero-title')).textContent).toBe('11-19 资金、持仓对账一致，委托 / 成交未对账')
  const kpis = screen.getAllByTestId('hero-kpi').map((x) => x.textContent)
  expect(kpis[0]).toContain('✓ 一致')
  expect(kpis[2]).toContain('○ 未对账')
  expect(kpis[3]).toContain('1 天')
  expect(kpis[4]).toContain('停机闸已解除')
})

test('runs hero sums orders and reports the largest start lag from recorded times only', async () => {
  const run = (key: string, phase: string, sched: string | null, start: string, ms: number, orders: number) => ({ event_key: key, phase, scheduled_at: sched, started_at: start,
    finished_at: start, duration_ms: ms, status: 'completed', candidates: phase === 'auction' ? 12 : null, routed: phase === 'auction' ? 3 : null, passed: phase === 'auction' ? 1 : null,
    orders, rejects: 0, summary: '', reasons: {}, note: '', error: '', event_status: null, event_detail: null })
  payload = { schema_version: 1, enabled: true, day, report_text: null, missing: {}, runs: [
    run('a', 'auction', '2026-11-19T09:25:00+08:00', '2026-11-19T09:25:10+08:00', 2100, 2),
    run('b', 'checkpoint', '2026-11-19T10:05:00+08:00', '2026-11-19T10:05:44+08:00', 2400, 1),
    run('c', 'bar', null, '2026-11-19T15:00:01+08:00', 700, 0)] }
  wrap(<RunsPage />)
  expect((await screen.findByTestId('hero-title')).textContent).toBe('3 个策略事件全部完成')
  const kpis = screen.getAllByTestId('hero-kpi').map((x) => x.textContent)
  expect(kpis[0]).toContain('12')
  expect(kpis[1]).toContain('3 / 0')
  expect(kpis[2]).toContain('44.0s')
  expect(kpis[3]).toContain('2.4s')
})
