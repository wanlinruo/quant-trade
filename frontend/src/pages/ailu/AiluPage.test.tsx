import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { EChartsOption } from '../../page/EChart'
import { AiluPage } from './AiluPage'
import { research, shadow } from './fixtures'
import type { ShadowBoard } from '../../api/ailu'

const options: EChartsOption[] = []
vi.mock('../../page/EChart', () => ({
  EChart: ({ option }: { option: EChartsOption }) => { options.push(option); return <div data-testid="echart" /> },
}))

function mockFetch(sh: unknown, rs: unknown = research()) {
  const f = vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes('research') ? rs : sh), { status: 200 }))
  vi.stubGlobal('fetch', f)
  return f
}

beforeEach(() => { options.length = 0; history.replaceState(null, '', '/app/ailu') })
afterEach(() => { vi.unstubAllGlobals() })

test('ailu always shows the not-a-trading-signal notice', async () => {
  mockFetch(shadow({ state: 'unavailable', message: '影子榜单数据源未配置', snapshot: null, history: [] }))
  render(<AiluPage />)
  expect(await screen.findByText('影子榜单数据源未配置')).toBeInTheDocument()
  expect(screen.getByText('不是交易信号，不进入实盘或模拟决策。')).toBeInTheDocument()
})

test('ailu hero maps every view state', async () => {
  const cases: [ShadowBoard['state'], string][] = [
    ['ready', '● 正常'], ['stale', '▲ 已过期'], ['generating', '◐ 运行中'],
    ['waiting', '○ 待运行'], ['failed', '✕ 失败'], ['unavailable', '○ 待运行'],
  ]
  for (const [state, tag] of cases) {
    const base = shadow()
    const snap = state === 'ready' || state === 'stale' ? base.snapshot : null
    mockFetch(shadow({ state, message: `msg-${state}`, snapshot: snap }))
    const { unmount } = render(<AiluPage />)
    // 状态由 Hero 图标表达，可访问名称即状态文字
    expect(await screen.findByRole('img', { name: tag.slice(2) })).toBeInTheDocument()
    expect(screen.getByText(`msg-${state}`)).toBeInTheDocument()
    unmount()
  }
})

test('shadow table multiplies probabilities and shows 未出结果 for null outcomes', async () => {
  mockFetch(shadow())
  render(<AiluPage />)
  const table = (await screen.findByText('今天模型看好谁？')).closest('section')!
  const row1 = within(table).getByText('样本1').closest('tr')!
  expect(within(row1).getByText('50.0%')).toBeInTheDocument()
  expect(within(row1).getByText('60.0%')).toBeInTheDocument()
  expect(within(row1).getByText('29.0%')).toBeInTheDocument()
  expect(within(row1).getByText('触板 · 未封')).toBeInTheDocument()
  const row2 = within(table).getByText('样本2').closest('tr')!
  expect(within(row2).getByText('未出结果')).toBeInTheDocument()
})

test('precision chart keeps nulls and baseline shows 缺失', async () => {
  mockFetch(shadow())
  render(<AiluPage />)
  const panel = (await screen.findByText('模型最近准不准？')).closest('section')!
  const s = options.flatMap((o) => (o.series as { name?: string; data?: unknown[]; connectNulls?: boolean }[]) ?? []).find((x) => x.name === 'P@20')!
  expect(s.data).toEqual([['2026-11-12', 0.35], ['2026-11-13', null], ['2026-11-16', null]])
  expect(s.connectNulls).toBe(false)
  expect(within(panel).getAllByText('缺失').length).toBeGreaterThanOrEqual(1)
})

test('failed shadow shows error message and no table', async () => {
  mockFetch(shadow({ state: 'failed', message: '特征缺失：bars_daily 2026-11-16', snapshot: null }))
  render(<AiluPage />)
  expect(await screen.findByText('特征缺失：bars_daily 2026-11-16')).toBeInTheDocument()
  expect(screen.queryByText('今天模型看好谁？')).toBeNull()
})

test('clicking a history day loads that day and offers back to latest', async () => {
  const f = mockFetch(shadow())
  render(<AiluPage />)
  await screen.findByText('今天模型看好谁？')
  await userEvent.click(screen.getByRole('button', { name: '2026-11-13' }))
  expect(f.mock.calls.some(([u]) => String(u).includes('day=2026-11-13'))).toBe(true)
  expect(location.search).toContain('day=2026-11-13')
  expect(await screen.findByText(/正在查看 2026-11-13/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '回到最新' }))
  expect(location.search).not.toContain('day=')
})

test('research tab shows monthly hits and receipts', async () => {
  mockFetch(shadow())
  render(<AiluPage />)
  await screen.findByText('今天模型看好谁？')
  await userEvent.click(screen.getByRole('tab', { name: '研究回测' }))
  expect(await screen.findByText('月度稳定吗？')).toBeInTheDocument()
  expect(location.search).toContain('tab=research')
  const bars = options.flatMap((o) => (o.series as { name?: string; data?: unknown[] }[]) ?? []).find((x) => x.name === 'OBS')!
  expect(bars.data).toEqual([['2026-09', 118 / 420], ['2026-10', 132 / 440]])
  expect(screen.getByText(/srcsha1234/)).toBeInTheDocument()
  expect(screen.getByText(/datasha567/)).toBeInTheDocument()
})
