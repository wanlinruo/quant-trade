import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { EChartsOption } from '../../page/EChart'
import { ThemeProvider } from '../../shell/ThemeProvider'
import { BacktestPage as Page } from './BacktestPage'
import { fx } from '../research-fixtures'
import type { BacktestDetail, BacktestList } from '../../api/research'

const BacktestPage = () => <ThemeProvider><Page /></ThemeProvider>
const options: EChartsOption[] = []
vi.mock('../../page/EChart', () => ({ EChart: ({ option }: { option: EChartsOption }) => { options.push(option); return <div data-testid="echart" /> } }))

type Report = Extract<BacktestDetail, { report: true }>
const list = () => fx.backtests() as Extract<BacktestList, { enabled: true }>
const REPORT_ID = list().rows.find((r) => r.has_report)!.id
const RUNNING_ID = list().rows.find((r) => !r.has_report)!.id
let detail: () => unknown
let calls: string[] = []
function mockApi() {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    calls.push(String(u))
    const body = String(u).startsWith('/api/app/backtests/') ? detail() : String(u).startsWith('/api/app/research-jobs') ? fx.research() : list()
    return new Response(JSON.stringify(body), { status: 200 })
  }))
}
type S = { name?: string; data?: unknown[]; connectNulls?: boolean; markArea?: { data: unknown[] } }
const series = () => options.flatMap((o) => (o.series as S[]) ?? [])

beforeEach(() => { options.length = 0; detail = () => fx.backtest(); history.replaceState(null, '', `/app/backtest?id=${REPORT_ID}`) })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

test('report shows kpis, warnings and charts with null benchmark gaps', async () => {
  mockApi()
  render(<BacktestPage />)
  const alert = await screen.findByText(/下单被拒 1 笔/)
  expect(alert.closest('[role="alert"]')!.textContent).toContain('1 个交易日缺基准')
  const hero = screen.getByTestId('hero-title').closest('section')!
  expect(hero.textContent).toContain('-1.00%')
  expect(hero.textContent).toContain('+10.00%')
  expect(hero.textContent).toContain('-20.00%')
  expect(hero.textContent).toContain('1 / 1')
  expect(hero.textContent).toContain('连续竞价判定')
  const bench = series().find((s) => s.name === '基准')!
  expect((bench.data![2] as [string, number | null])[1]).toBeNull()
  expect(bench.connectNulls).toBe(false)
  expect(screen.getByText('2 笔，显示最后 2 笔', { exact: false })).toBeInTheDocument()
  expect(screen.getByText('1,000.00')).toBeInTheDocument()
  expect(screen.getByText('990.00')).toBeInTheDocument()
  // 入场口径与费用模型是说明性字段，在副行，不占 KPI 格
  expect(screen.getByTestId('hero-subtitle').textContent).toContain('入场口径 连续竞价判定')
  expect(screen.getAllByTestId('hero-kpi').map((x) => x.textContent).join()).not.toContain('费用模型')
})

test('trade times are formatted to seconds without timezone reinterpretation', async () => {
  detail = () => ({ ...fx.backtest(), fills: [{ ...(fx.backtest() as Report).fills[0], filled_at: '2025-01-08T09:31:00.000000' }] })
  mockApi()
  render(<BacktestPage />)
  expect(await screen.findByText('2025-01-08 09:31:00')).toBeInTheDocument()
  expect(screen.queryByText(/09:31:00\.000000/)).toBeNull()
})

test('underwater chart marks the 5 deepest episodes and lists them', async () => {
  mockApi()
  render(<BacktestPage />)
  // 原型：收益与回撤同一张图（上下子图），最深回撤段的表在同一面板
  const panel = (await screen.findByText('跑赢基准了吗？')).closest('section')!
  const dd = series().find((s) => s.name === '回撤')!
  expect(dd.markArea!.data).toHaveLength(1)
  const row = within(panel).getByText('2024-01-03').closest('tr')!
  expect(row.textContent).toContain('未收复')
  expect(row.textContent).toContain('-20.00%')
})

test('monthly heatmap lays out years by months', async () => {
  detail = () => { const d = fx.backtest() as Report; d.monthly = [{ month: '2023-12', value: 0.02 }, { month: '2024-01', value: -0.01 }, { month: '2024-03', value: null }]; return d }
  mockApi()
  render(<BacktestPage />)
  const grid = await screen.findByTestId('monthly-heatmap')
  expect(within(grid).getByTestId('m-2023-12').textContent).toBe('+2.00%')
  expect(within(grid).getByTestId('m-2024-01').textContent).toBe('-1.00%')
  expect(within(grid).getByTestId('m-2024-02').textContent).toBe('')
  expect(within(grid).getByTestId('m-2024-03').textContent).toBe('缺失')
})

test('running backtest shows progress and polls until done', async () => {
  vi.useFakeTimers()
  history.replaceState(null, '', `/app/backtest?id=${RUNNING_ID}`)
  let n = 0
  detail = () => {
    n += 1
    if (n === 1) return fx.backtestRunning()
    if (n === 2) { const r = fx.backtestRunning() as Extract<BacktestDetail, { report: false }>; r.job.status = 'running'; r.job.progress = 30; r.job.total = 60; r.job.current_session = '2024-02-15'; return r }
    return fx.backtest()
  }
  mockApi()
  render(<BacktestPage />)
  await act(async () => { await vi.advanceTimersByTimeAsync(100) })
  expect(screen.getByText('◐ 排队中')).toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
  expect(screen.getByText(/30 \/ 60/)).toBeInTheDocument()
  expect(screen.getByText(/2024-02-15/)).toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
  expect(screen.getByText(/下单被拒 1 笔/)).toBeInTheDocument()
  const before = calls.filter((u) => u.startsWith('/api/app/backtests/')).length
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
  expect(calls.filter((u) => u.startsWith('/api/app/backtests/')).length).toBe(before)
})

test('picker defaults to latest report and writes id to url', async () => {
  history.replaceState(null, '', '/app/backtest')
  const depth = history.length
  mockApi()
  render(<BacktestPage />)
  await screen.findByText(/下单被拒 1 笔/)
  expect(location.search).toContain(`id=${REPORT_ID}`)
  expect(history.length).toBe(depth) // 默认选中只替换当前记录，不制造后退陷阱
})

test('report download links use new route', async () => {
  mockApi()
  render(<BacktestPage />)
  const link = await screen.findByRole('link', { name: 'fills.csv' })
  expect(link.getAttribute("href")).toBe(`/api/app/backtests/${(fx.backtest() as Report).name}/files/fills.csv`)
})

test('null summary shows unknown, not zero', async () => {
  detail = () => { const d = fx.backtest() as Report; d.summary = { ...d.summary, cumulative_return: null, buys: null, sells: null, rejects: null }; return d }
  mockApi()
  render(<BacktestPage />)
  expect(await screen.findByText('被拒')).toBeInTheDocument()
  const kpi = (k: string) => screen.getByText(k).parentElement!.textContent
  expect(kpi('被拒')).toContain('未知')
  expect(kpi('买 / 卖')).toContain('未知 / 未知')
  expect(kpi('被拒')).not.toContain('0')
})

test('benchmark toggle hides benchmark and excess, range buttons narrow the zoom window', async () => {
  mockApi()
  render(<BacktestPage />)
  await screen.findByText('跑赢基准了吗？')
  const latest = () => options.filter((o) => ((o.series as S[]) ?? []).some((x) => x.name === '回撤')).at(-1)!
  expect(((latest().series as S[]).map((x) => x.name))).toEqual(['策略', '基准', '超额', '回撤'])
  await userEvent.click(screen.getByLabelText('显示基准'))
  expect(((latest().series as S[]).map((x) => x.name))).toEqual(['策略', '回撤'])
  const zoom = () => (latest().dataZoom as { startValue: string }[])[0].startValue
  const all = zoom()
  await userEvent.click(screen.getByText('1月'))
  expect(zoom() >= all).toBe(true)
})
