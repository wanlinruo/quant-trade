import { render, screen, within } from '@testing-library/react'
import type { EChartsOption } from '../../page/EChart'
import { ThemeProvider } from '../../shell/ThemeProvider'
import { QualityPage as Page } from './QualityPage'
import { dfx } from '../data-fixtures'

const options: EChartsOption[] = []
vi.mock('../../page/EChart', () => ({ EChart: ({ option }: { option: EChartsOption }) => { options.push(option); return <div data-testid="echart" /> } }))
const QualityPage = () => <ThemeProvider><Page /></ThemeProvider>
beforeEach(() => {
  options.length = 0; history.replaceState(null, '', '/app/quality')
  vi.stubGlobal('fetch', vi.fn(async (u: string) => new Response(JSON.stringify(String(u).includes('/rules/') ? dfx.rule() : dfx.quality()), { status: 200 })))
})
afterEach(() => { vi.unstubAllGlobals() })

type S = { name?: string; stack?: string; data?: unknown[] }

test('quality runs chart stacks counts and marks missing table-level', async () => {
  render(<QualityPage />)
  await screen.findByText('近 30 次质检结构')
  const series = options.flatMap((o) => (o.series as S[]) ?? [])
  const get = (n: string) => series.find((s) => s.name === n)!
  expect(get('阻断').stack).toBe('fields')
  expect(get('通过').data).toEqual([1, 0])
  expect(get('警告').data).toEqual([0, 2])
  expect(get('阻断').data).toEqual([0, 1])
  expect(screen.getByText(/2 次质检没有表级记录/)).toBeInTheDocument()
  expect(screen.getByTestId('hero-title').textContent).toContain('阻断 1 · 警告 2')
})

test('warning sources link issues', async () => {
  render(<QualityPage />)
  const card = (await screen.findByText('上游自相矛盾')).closest('[data-source]') as HTMLElement
  expect(card.textContent).toContain('1')
  expect(within(card).getByRole('link', { name: '#653' }).getAttribute('href')).toBe('https://github.com/wanlinruo/quant-trade/issues/653')
  const unc = screen.getByText('待归类').closest('[data-source]') as HTMLElement
  expect(unc.textContent).toContain('1')
})

test('rule drawer shows sample download link', async () => {
  history.replaceState(null, '', '/app/quality?rule=c.rule')
  render(<QualityPage />)
  const link = await screen.findByRole('link', { name: '下载违规样本' })
  expect(link.getAttribute('href')).toBe('/api/app/data/quality/samples/r2/c.rule.parquet')
  expect(screen.getByText('以最近持久化结果的状态或 passed 字段为准')).toBeInTheDocument()
})

test('rules table sorted with blocking first', async () => {
  render(<QualityPage />)
  const panel = (await screen.findByText('有哪些规则？')).closest('section')!
  const first = within(panel).getAllByRole('row')[1]
  expect(first.textContent).toContain('c.other')
  expect(within(first).getByText('✕ 阻断')).toBeInTheDocument()
})
