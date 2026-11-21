import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { EChartsOption } from '../../page/EChart'
import { ThemeProvider } from '../../shell/ThemeProvider'
import { DataOverviewPage as Page } from './DataOverviewPage'
import { dfx } from '../data-fixtures'
import { domainLabel } from './labels'

const DataOverviewPage = () => <ThemeProvider><Page /></ThemeProvider>
const options: EChartsOption[] = []
let clickHandler: ((p: { dataType?: string; data?: unknown }) => void) | undefined
vi.mock('../../page/EChart', () => ({
  EChart: ({ option, onClick }: { option: EChartsOption; onClick?: (p: unknown) => void }) => {
    options.push(option); clickHandler = onClick as typeof clickHandler; return <div data-testid="echart" />
  },
}))
let body: () => unknown
beforeEach(() => {
  options.length = 0; history.replaceState(null, '', '/app/dataov')
  body = () => dfx.overview()
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body()), { status: 200 })))
})
afterEach(() => { vi.unstubAllGlobals() })

function mixed() {
  const d = dfx.overview()
  d.tables[0] = { ...d.tables[0], status: 'red', dimensions: { ...d.tables[0].dimensions, validity: 'red' } }
  d.tables[1] = { ...d.tables[1], status: 'green', dimensions: { freshness: 'green', completeness: 'green', structure: 'green', validity: 'green', lineage: 'green' } }
  return d
}

test('overview hero counts statuses and never shows gray as ok', async () => {
  render(<DataOverviewPage />)
  const title = await screen.findByTestId('hero-title')
  const n = dfx.overview().tables.length
  expect(title.textContent).toContain(`${n} 张核心表 · 0 健康`)
  // 灰（缺证据）单独计数，不算健康
  const gray = screen.getAllByTestId('hero-kpi').find((x) => x.textContent!.startsWith('缺证据（灰）'))!
  expect(gray.textContent).toContain(String(n))
  expect(screen.getByRole('img', { name: '待运行' })).toBeInTheDocument()
  expect(screen.queryByRole('img', { name: '正常' })).toBeNull()
})

test('matrix rows sorted worst first and cells use symbol+text', async () => {
  body = () => mixed()
  render(<DataOverviewPage />)
  const panel = (await screen.findByText('核心表健康吗？')).closest('section')!
  const rows = within(panel).getAllByRole('row').slice(1)
  expect(rows[0].textContent).toContain(mixed().tables[0].name)
  expect(within(rows[0]).getAllByText('✕ 红').length).toBeGreaterThan(0)
  expect(within(rows[rows.length - 1]).getAllByText('● 绿').length).toBe(6)
  expect(within(rows[1]).getAllByText('○ 灰').length).toBe(6)
  expect(screen.getByRole('img', { name: '失败' })).toBeInTheDocument()
})

test('lineage graph option has adjacency focus and fixed columns', async () => {
  render(<DataOverviewPage />)
  await screen.findByText('数据从哪来、被谁用？')
  const g = options.flatMap((o) => (o.series as { type?: string; data?: { id: string; x: number; y: number; category: number }[]; links?: unknown[]; emphasis?: { focus?: string } }[]) ?? []).find((s) => s.type === 'graph')!
  expect(g.emphasis?.focus).toBe('adjacency')
  expect(g.links!.length).toBe(dfx.overview().lineage.edges.length)
  const xOf = (kind: string) => new Set(g.data!.filter((n) => dfx.overview().lineage.nodes.find((x) => x.id === n.id)?.kind === kind).map((n) => n.x))
  expect(xOf('source').size).toBe(1)
  expect([...xOf('source')][0]).toBeLessThan([...xOf('consumer')][0])
})

test('clicking a matrix row opens table detail', async () => {
  render(<DataOverviewPage />)
  const first = dfx.overview().tables[0]
  await userEvent.click(await screen.findByText(first.name))
  expect(location.pathname + location.search).toBe(`/app/tables?id=${first.id}`)
})

test('clicking a dataset node in the graph opens table detail', async () => {
  render(<DataOverviewPage />)
  await screen.findByText('数据从哪来、被谁用？')
  clickHandler!({ dataType: 'node', data: { id: 'dataset:instruments', ref: 'instruments', kind: 'canonical' } })
  expect(location.pathname + location.search).toBe('/app/tables?id=instruments')
})

test('domain summary lists counts', async () => {
  render(<DataOverviewPage />)
  const panel = (await screen.findByText('各数据域怎样？')).closest('section')!
  const d = dfx.overview().domains[0]
  expect(within(panel).getByText(domainLabel(d.domain))).toBeInTheDocument()
})

test('assets disabled shows message', async () => {
  body = () => ({ enabled: false })
  render(<DataOverviewPage />)
  expect(await screen.findByText('数据资产未启用')).toBeInTheDocument()
})
