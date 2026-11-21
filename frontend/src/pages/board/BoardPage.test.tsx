import { act, render, screen } from '@testing-library/react'
import type { EChartsOption } from '../../page/EChart'
import { BoardPage } from './BoardPage'
import { board } from './fixtures'

vi.mock('../../page/EChart', () => ({ EChart: (_: { option: EChartsOption }) => <div data-testid="echart" /> }))

let fetchMock: ReturnType<typeof vi.fn>
function mockFetch(body: unknown) {
  fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
}
const boardCalls = () => fetchMock.mock.calls.filter(([u]) => String(u).startsWith('/api/app/board?') || String(u) === '/api/app/board')

beforeEach(() => { history.replaceState(null, '', '/app/board') })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

test('board hero shows counts and provisional tag in live mode', async () => {
  vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date(2026, 10, 13, 9, 31, 40))
  mockFetch(board())
  render(<BoardPage />)
  const title = await screen.findByTestId('hero-title')
  expect(title.textContent).toContain('涨停 2 · 跌停 1')
  expect(title.textContent).toContain('最高 2 板')
  expect(title.textContent).toContain('封板率 100.0%')
  expect(screen.getByText('● 盘中 · 暂计')).toBeInTheDocument()
  expect(screen.getByTestId('hero-subtitle').textContent).toContain('≈09:31:30')
})

test('live snapshot older than 45s while open is flagged stale', async () => {
  vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date(2026, 10, 13, 9, 33, 0))
  mockFetch(board({ schedule: { checked_at: '2026-11-13T09:33:00', is_trading_day: true, is_open: true, next_open_at: null } }))
  render(<BoardPage />)
  expect(await screen.findByText('▲ 快照已过期')).toBeInTheDocument()
})

test('board hero warns on concept error', async () => {
  const d = board()
  d.meta = { ...(d.meta as object), concept_error: 'RuntimeError: 积分不足' } as typeof d.meta
  mockFetch(d)
  render(<BoardPage />)
  expect(await screen.findByText(/题材维度不可用：RuntimeError: 积分不足/)).toBeInTheDocument()
  expect(screen.getByText(/^▲ /)).toBeInTheDocument()
})

test('final mode shows mismatch count', async () => {
  mockFetch(board({ mode: 'final', provisional: false, mismatch_count: 3 }))
  render(<BoardPage />)
  expect(await screen.findByText('▲ 盘后定版 · ≠ 直取 3 处')).toBeInTheDocument()
})

test('history mode is read-only with back-to-today', async () => {
  history.replaceState(null, '', '/app/board?day=2026-11-12')
  mockFetch(board({ mode: 'history', provisional: false }))
  render(<BoardPage />)
  expect(await screen.findByText('— 历史 · 只读')).toBeInTheDocument()
  expect(screen.getByText('▲ 正在回看 · 只读')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '回到今天' })).toBeInTheDocument()
})

test('unavailable board shows message', async () => {
  mockFetch({ available: false, message: '打板看板采集器尚未产生数据' })
  render(<BoardPage />)
  expect(await screen.findByRole('img', { name: '待运行' })).toBeInTheDocument()
  expect(screen.getByTestId('hero-title').textContent).toBe('打板看板采集器尚未产生数据')
})

test('sentiment null promotions show 未知', async () => {
  const d = board()
  d.sentiment!.promote_3_up = [null, null]
  mockFetch(d)
  render(<BoardPage />)
  await screen.findByTestId('hero-title')
  expect(screen.getByTestId('promote-3_up').textContent).toBe('未知')
  expect(screen.getByTestId('promote-1_2').textContent).toBe('1 / 1')
})

test('board polls only live mode and backs off when closed', async () => {
  vi.useFakeTimers()
  history.replaceState(null, '', '/app/board?day=2026-11-12')
  mockFetch(board({ mode: 'history' }))
  const { unmount } = render(<BoardPage />)
  await act(async () => { await vi.advanceTimersByTimeAsync(100) })
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
  expect(boardCalls().length).toBe(1)
  unmount()
  history.replaceState(null, '', '/app/board')
  mockFetch(board({ schedule: { checked_at: '2026-11-13T20:00:00', is_trading_day: true, is_open: false, next_open_at: null } }))
  render(<BoardPage />)
  await act(async () => { await vi.advanceTimersByTimeAsync(100) })
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
  expect(boardCalls().length).toBe(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(31_000) })
  expect(boardCalls().length).toBe(2)
})

test('replay writes at into the url and refetches', async () => {
  mockFetch(board({ mode: 'final', provisional: false }))
  render(<BoardPage />)
  await screen.findByTestId('hero-title')
  const { replayTo } = await import('./DayControls')
  act(() => replayTo('2026-11-13', '2026-11-13T09:31:15'))
  expect(location.search).toContain('at=09%3A31%3A15')
  await act(async () => { await Promise.resolve() })
  expect(boardCalls().some(([u]) => String(u).includes('at=2026-11-13T09%3A31%3A15'))).toBe(true)
})
