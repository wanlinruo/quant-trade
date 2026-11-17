import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { EChartsOption } from '../../page/EChart'
import { MarketPage } from './MarketPage'
import { payload, snapshot } from './fixtures'

const options: EChartsOption[] = []
vi.mock('../../page/EChart', () => ({
  EChart: ({ option }: { option: EChartsOption }) => { options.push(option); return <div data-testid="echart" /> },
}))

function mockFetch(body: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })))
}

beforeEach(() => { options.length = 0; vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date(2026, 10, 17, 10, 43)) })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

test('market hero shows counts, coverage and snapshot time', async () => {
  mockFetch(payload())
  render(<MarketPage />)
  const title = await screen.findByTestId('hero-title')
  expect(title.textContent).toContain('涨停 54')
  expect(title.textContent).toContain('跌停 3')
  expect(title.textContent).toContain('62.3%')
  const sub = screen.getByTestId('hero-subtitle').textContent!
  expect(sub).toContain('有效 5,438 / 应有 5,441')
  expect(sub).toContain('10:42:00')
  expect(screen.getByText('● 正常')).toBeInTheDocument()
  expect(screen.getByText('上证指数')).toBeInTheDocument()
  expect(screen.getAllByText('+0.81%').length).toBe(2) // 上证涨幅与中位涨幅恰好相同
})

test('market page renders history without live snapshot', async () => {
  mockFetch(payload({ live: { available: false, stale: true, snapshot: null } }))
  render(<MarketPage />)
  expect((await screen.findByTestId('hero-title')).textContent).toBe('无实时快照')
  expect(screen.getByText('○ 待运行')).toBeInTheDocument()
  expect(screen.getByText('近 60 日市场情绪怎样？')).toBeInTheDocument()
  expect(options.length).toBeGreaterThan(0)
})

test('stale snapshot is flagged and closed market says so', async () => {
  mockFetch(payload({ live: { available: true, stale: true, snapshot: snapshot() } }))
  const { unmount } = render(<MarketPage />)
  expect(await screen.findByText('▲ 已过期')).toBeInTheDocument()
  unmount()
  mockFetch(payload({ live: { available: true, stale: true, snapshot: snapshot() }, schedule: { checked_at: '2026-11-17T20:00:00', is_trading_day: true, is_open: false, next_open_at: null } }))
  render(<MarketPage />)
  expect(await screen.findByText('— 休市 · 最近快照')).toBeInTheDocument()
})

test('coverage null shows unbound, never zeros', async () => {
  const s = snapshot({ coverage: null })
  mockFetch(payload({ live: { available: true, stale: false, snapshot: s } }))
  const { unmount } = render(<MarketPage />)
  expect(await screen.findByText(/未绑定覆盖信息/)).toBeInTheDocument()
  expect(screen.getByTestId('hero-subtitle').textContent).toContain('应有 未知')
  unmount()
  const s2 = snapshot()
  s2.coverage!.expected = null
  mockFetch(payload({ live: { available: true, stale: false, snapshot: s2 } }))
  render(<MarketPage />)
  expect((await screen.findByTestId('hero-subtitle')).textContent).toContain('应有 未知')
  expect(screen.getByText('北交所')).toBeInTheDocument()
  expect(screen.getAllByText('未知').length).toBeGreaterThan(0) // 源时间缺失
})

test('intraday and distribution panels show 缺失 with reason', async () => {
  mockFetch(payload())
  render(<MarketPage />)
  await screen.findByText('上证分时')
  expect(screen.getByText('涨跌幅分布')).toBeInTheDocument()
  vi.useRealTimers()
  expect(screen.getAllByText('缺失').length).toBeGreaterThanOrEqual(3) // 两个面板 + 封板未知（涨）
  const panel = screen.getByText('上证分时').closest('section')!
  await userEvent.hover(within(panel).getByText('缺失'))
  expect(await screen.findByText(/见 #675/)).toBeInTheDocument()
})

test('trend series keeps null days as gaps', async () => {
  mockFetch(payload())
  render(<MarketPage />)
  await screen.findByText('近 60 日市场情绪怎样？')
  const slope = options.flatMap((o) => (o.series as { name?: string; data?: unknown[]; connectNulls?: boolean }[]) ?? [])
    .find((s) => s.name === 'HS300 MA20 五日斜率')!
  expect(slope.data).toEqual([['2026-11-13', 0.1], ['2026-11-14', null], ['2026-11-16', 0.2]])
  expect(slope.connectNulls).toBe(false)
})

test('limit check unpublished says so', async () => {
  mockFetch(payload({ limit_check: null }))
  render(<MarketPage />)
  expect(await screen.findByText('今日核对结果未发布')).toBeInTheDocument()
})

test('market disabled shows message', async () => {
  mockFetch({ enabled: false })
  render(<MarketPage />)
  expect(await screen.findByText('行情控制台未启用')).toBeInTheDocument()
})
