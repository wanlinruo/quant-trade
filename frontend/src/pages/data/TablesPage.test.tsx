import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '../../shell/ThemeProvider'
import { TablesPage as Page } from './TablesPage'
import { dfx } from '../data-fixtures'
import { domainLabel, sourceText } from './labels'

const TablesPage = () => <ThemeProvider><Page /></ThemeProvider>
const ID = dfx.table().row.id
let urls: string[] = []
let sample: () => unknown
let list: () => unknown
beforeEach(() => {
  urls = []; sample = () => dfx.sample(); list = () => dfx.tables()
  history.replaceState(null, '', '/app/tables')
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    urls.push(String(u))
    const s = String(u)
    const body = s.endsWith('/sample') ? sample() : s.startsWith(`/api/app/data/tables/`) ? dfx.table() : list()
    return new Response(JSON.stringify(body), { status: 200 })
  }))
})
afterEach(() => { vi.unstubAllGlobals() })

test('tables list filters by domain and shows coverage pending', async () => {
  list = () => {
    const d = dfx.tables()
    d.rows[0] = { ...d.rows[0], coverage: null, coverage_state: 'pending' }
    d.rows[1] = { ...d.rows[1], coverage: { rows: 12345, symbols: 10, first_dt: '2020-01-02', last_dt: '2026-11-17', measured_at: null }, coverage_state: 'ready' }
    return d
  }
  render(<TablesPage />)
  const all = dfx.tables().rows
  const first = (await screen.findByText(all[0].name)).closest('tr')!
  expect(first.textContent).toContain('统计中')
  const second = screen.getByText(all[1].name).closest('tr')!
  expect(second.textContent).toContain('12,345 行')
  expect(second.textContent).toContain('2020-01-02 ~ 2026-11-17')
  const dom = domainLabel(all[0].domain)
  history.replaceState(null, '', `/app/tables?domain=${encodeURIComponent(dom)}`)
  dispatchEvent(new PopStateEvent('popstate'))
  await waitFor(() => expect(screen.queryByText(all.find((r) => domainLabel(r.domain) !== dom)!.name)).toBeNull())
  expect(screen.getByText(all[0].name)).toBeInTheDocument()
})

test('detail tabs render overview fields quality lineage', async () => {
  history.replaceState(null, '', `/app/tables?id=${ID}`)
  render(<TablesPage />)
  const t = dfx.table()
  expect(await screen.findByText(t.row.grain)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: '字段' }))
  expect(screen.getByText(t.fields[0].name)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: '质量' }))
  expect(screen.getAllByText(t.reasons[0].message).length).toBeGreaterThan(0)
  await userEvent.click(screen.getByRole('tab', { name: '血缘' }))
  expect(screen.getByText(sourceText(t.sources[0]))).toBeInTheDocument()
  expect(location.search).toContain('tab=lineage')
})

test('sample loads lazily and shows null literally', async () => {
  history.replaceState(null, '', `/app/tables?id=${ID}`)
  render(<TablesPage />)
  await screen.findByText(dfx.table().row.grain)
  expect(urls.some((u) => u.endsWith('/sample'))).toBe(false)
  await userEvent.click(screen.getByRole('tab', { name: '样本' }))
  const nul = await screen.findByText('null')
  expect(nul.closest('tr')!.textContent).toContain('000001.SZ')
  expect(screen.getByText(/只读抽样，不代表全表/)).toBeInTheDocument()
})

test('sample unavailable shows reason', async () => {
  sample = () => ({ schema_version: 1, enabled: true, available: false, reason: 'no_file', file: null, row_group: null, columns: [], rows: [], limit: 20 })
  history.replaceState(null, '', `/app/tables?id=${ID}&tab=sample`)
  render(<TablesPage />)
  expect(await screen.findByText('仓库里没有这张表的文件')).toBeInTheDocument()
})
