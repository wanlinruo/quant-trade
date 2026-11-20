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
