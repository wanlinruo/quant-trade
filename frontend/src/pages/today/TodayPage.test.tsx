import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '../../shell/ThemeProvider'
import { TodayPage } from './TodayPage'

vi.mock('../../page/EChart', () => ({ EChart: () => <div data-testid="echart" /> }))
beforeEach(() => {
  history.replaceState(null, '', '/app/today')
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    schema_version: 1, enabled: true, day: '2026-11-19', days: ['2026-11-19'], state: 'running', title: '运行中', kill: null,
    concerns: [], kpis: { account_total: 100000, account_at: '2026-11-19T10:00:00+08:00', index_name: '上证指数', index_price: 3400, index_change_ratio: .01, limit_up: 10, limit_down: 2, seal_ratio: .8, link_max_age_seconds: 1, link_fresh: 1, link_total: 1 },
    phases: [{ id: 'auction', label: '竞价', time_label: '09:25–09:30', state: 'ok', completed: 1, recorded: 1 }],
    events: [{ event_key: 'auction', phase: 'auction', scheduled_at: '2026-11-19T09:25:00+08:00', started_at: '2026-11-19T09:25:01+08:00', finished_at: '2026-11-19T09:25:02+08:00', state: 'completed', summary: '未下单', detail: null }],
    links: [{ source: 'agent', points: [{ sampled_at: '2026-11-19T10:00:00+08:00', age_seconds: 1, threshold_seconds: 10, fresh: true }] }],
    sentiment: [{ day: '2026-11-19', limit_up: 10, limit_down: 2, broken_board_ratio: .2, prior_limitup_return_ratio: null }],
    missing: { index_intraday: '上证 1 分钟分时尚未采集，由 #675 承接；不从当前价生成曲线' },
  }), { status: 200 })))
})
afterEach(() => vi.unstubAllGlobals())

test('today shows direct account value and does not fake intraday', async () => {
  render(<ThemeProvider><TodayPage /></ThemeProvider>)
  expect(await screen.findByText('¥100,000.00')).toBeInTheDocument()
  expect(screen.getByTestId('hero-title')).toHaveTextContent('运行中')
  expect(screen.getByText(/不从当前价生成曲线/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /下单|解除|重启|补跑/ })).toBeNull()
})

test('concerns live in the hero, phases show real completion, sentiment uses direct fields only', async () => {
  const days = ['2026-11-18', '2026-11-19']
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    schema_version: 1, enabled: true, day: '2026-11-19', days, state: 'closed', title: '已收盘', kill: null,
    concerns: [{ id: 'm', severity: 'warning', label: '市场快照已过期', detail: null, page: 'market' }],
    kpis: { account_total: null, account_at: null, index_name: '上证指数', index_price: null, index_change_ratio: null, limit_up: 30, limit_down: 61, seal_ratio: .68, link_max_age_seconds: null, link_fresh: null, link_total: null },
    phases: [{ id: 'pm', label: '晚间任务', time_label: '15:40–23:59', state: 'running', completed: 3, recorded: 4 }],
    events: [], links: [],
    sentiment: [{ day: '2026-11-18', limit_up: 25, limit_down: 70, broken_board_ratio: .3, prior_limitup_return_ratio: -.01 },
      { day: '2026-11-19', limit_up: 30, limit_down: 61, broken_board_ratio: null, prior_limitup_return_ratio: -.02 }],
    missing: { links: '当日没有链路采样', index_intraday: '未采集' },
  }), { status: 200 })))
  render(<ThemeProvider><TodayPage /></ThemeProvider>)
  expect((await screen.findByTestId('hero-title')).textContent).toBe('已收盘 · 1 项需要留意')
  expect(screen.getByTestId('hero-concerns').textContent).toContain('▲ 市场快照已过期')
  expect(screen.queryByText('现在需要留意什么？')).toBeNull()
  expect(screen.getByRole('progressbar', { name: '晚间任务 完成 3/4' }).getAttribute('aria-valuenow')).toBe('75')
  const rows = screen.getAllByTestId('sentiment-row').map((x) => x.textContent)
  expect(rows).toHaveLength(4)
  expect(rows[0]).toContain('30')
  expect(rows[0]).toContain('昨 25')
  expect(rows[2]).toContain('炸板率—')  // 当日缺值显示“—”，不沿用昨日
  expect(screen.queryByText(/情绪温度/)).not.toBeInTheDocument()
  expect(screen.queryByTestId('link-timeline')).toBeNull()
})
