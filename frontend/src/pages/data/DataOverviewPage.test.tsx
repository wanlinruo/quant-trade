import { render, screen, within } from '@testing-library/react'
import type { EChartsOption } from '../../page/EChart'
import { ThemeProvider } from '../../shell/ThemeProvider'
import { DataOverviewPage as Page } from './DataOverviewPage'
import { dfx } from '../data-fixtures'
import { domainLabel } from './labels'

const DataOverviewPage = () => <ThemeProvider><Page /></ThemeProvider>
const options: EChartsOption[] = []
let clickHandler: ((p: { dataType?: string; data?: unknown }) => void) | undefined
const handlers: (((p: unknown) => void) | undefined)[] = []
vi.mock('../../page/EChart', () => ({
  EChart: ({ option, onClick }: { option: EChartsOption; onClick?: (p: unknown) => void }) => {
    options.push(option); handlers.push(onClick); clickHandler = onClick as typeof clickHandler; return <div data-testid="echart" />
  },
}))
type Cell = { value: [number, number, number]; id: string; label: { formatter: string } }
const heatmap = () => {
  const i = options.map((o, k) => ((o.series as { type?: string }[]) ?? []).some((x) => x.type === 'heatmap') ? k : -1).reduce((a, b) => Math.max(a, b), -1)
  const o = options[i]
  return { rows: (o.yAxis as { data: string[] }).data, cells: ((o.series as { data: Cell[] }[])[0]).data, click: handlers[i]! }
}
let body: () => unknown
beforeEach(() => {
  options.length = 0; handlers.length = 0; history.replaceState(null, '', '/app/dataov')
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
  await screen.findByText('每张表在五个维度上都健康吗？')
  const { rows, cells } = heatmap()
  const n = mixed().tables.length
  expect(rows[0]).toBe(mixed().tables[0].name)           // 红表排最前
  expect(rows[n - 1]).toBe(mixed().tables[1].name)       // 绿表排最后
  const rowCells = (y: number) => cells.filter((c) => c.value[1] === y).map((c) => c.label.formatter)
  expect(rowCells(0)[0]).toBe('✕')
  expect(rowCells(n - 1)).toEqual(['●', '●', '●', '●', '●', '●'])
  expect(rowCells(1)).toEqual(['○', '○', '○', '○', '○', '○'])  // 缺证据是灰○，不算健康
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
  await screen.findByText('每张表在五个维度上都健康吗？')
  heatmap().click({ data: { id: first.id } })
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
