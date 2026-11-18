import { render, screen } from '@testing-library/react'
import type { EChartsOption } from '../../page/EChart'
import { StockDrawer } from './StockDrawer'
import { board, series } from './fixtures'

const options: EChartsOption[] = []
vi.mock('../../page/EChart', () => ({ EChart: ({ option }: { option: EChartsOption }) => { options.push(option); return <div data-testid="echart" /> } }))
afterEach(() => { vi.unstubAllGlobals(); options.length = 0 })

type S = { name?: string; data?: unknown[]; connectNulls?: boolean; markLine?: { data: unknown[] } }

test('drawer plots pct series with limit line and event marks, null volume stays null', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(series()), { status: 200 })))
  render(<StockDrawer day="2026-11-13" symbol="A.SH" card={board().cards!['A.SH']} onClose={() => {}} />)
  expect(await screen.findByText(/甲科技 A\.SH · 2 板/)).toBeInTheDocument()
  const all = options.flatMap((o) => (o.series as S[]) ?? [])
  const line = all.find((s) => s.name === '涨跌幅')!
  expect(line.data!.map((p) => (p as [string, number])[1].toFixed(3))).toEqual(['0.100', '0.090', '0.100'])
  expect(line.connectNulls).toBe(false)
  const marks = JSON.stringify(line.markLine!.data)
  expect(marks).toContain('涨停价')
  expect(marks).toContain('首封 09:30:58')
  expect(marks).toContain('开板 09:31:13')
  expect(marks).toContain('回封 09:31:28')
  const vol = all.find((s) => s.name === '成交量')!
  expect(vol.data![0]).toEqual(['2026-11-13T09:30:58+08:00', null])
  const axes = options.at(-1)!.xAxis as { axisLabel: { formatter?: (v: number) => string } }[]
  expect(axes[1].axisLabel.formatter!(Date.parse('2026-11-13T05:00:00Z'))).toBe('13:00')
})

test('drawer shows message when series unavailable', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ available: false, symbol: 'X.SZ', message: '该票当日无留存分时' }), { status: 200 })))
  render(<StockDrawer day="2026-11-13" symbol="X.SZ" card={undefined} onClose={() => {}} />)
  expect(await screen.findByText('该票当日无留存分时')).toBeInTheDocument()
})

test('drawer marks missing prev close as 缺失', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(series({ prev_close: null })), { status: 200 })))
  render(<StockDrawer day="2026-11-13" symbol="A.SH" card={board().cards!['A.SH']} onClose={() => {}} />)
  expect(await screen.findByText('缺失')).toBeInTheDocument()
})
