import { render, screen, waitFor, within } from '@testing-library/react'
import { ThemeProvider } from '../../shell/ThemeProvider'
import { CatalogPage as Page } from './CatalogPage'
import { dfx } from '../data-fixtures'

vi.mock('../../page/EChart', () => ({ EChart: () => <div data-testid="echart" /> }))
const CatalogPage = () => <ThemeProvider><Page /></ThemeProvider>
const MID = dfx.metric().row.id
let urls: string[] = []
let metric: () => unknown
beforeEach(() => {
  urls = []; metric = () => dfx.metric()
  history.replaceState(null, '', '/app/catalog')
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    urls.push(String(u))
    const body = String(u).startsWith('/api/app/data/catalog/') ? metric() : dfx.catalog()
    return new Response(JSON.stringify(body), { status: 200 })
  }))
})
afterEach(() => { vi.unstubAllGlobals() })

test('catalog filters write url and detail shows evidence table', async () => {
  history.replaceState(null, '', `/app/catalog?kind=derived&id=${encodeURIComponent(MID)}`)
  render(<CatalogPage />)
  expect((await screen.findByTestId('hero-title')).textContent).toContain(`共 ${dfx.catalog().summary.total} 个指标`)
  await waitFor(() => expect(urls.some((u) => u === '/api/app/data/catalog?kind=derived')).toBe(true))
  const ev = (await screen.findByText('人工抽样验真')).closest('section')!
  const row = within(ev).getByText('600000.SH').closest('tr')!
  expect(row.textContent).toContain('同花顺')
  expect(row.textContent).toContain('一致')
  expect(row.textContent).toContain('维护者')
})

test('catalog detail without evidence says no manual samples', async () => {
  metric = () => ({ ...dfx.metric(), evidence: [] })
  history.replaceState(null, '', `/app/catalog?id=${encodeURIComponent(MID)}`)
  render(<CatalogPage />)
  expect(await screen.findByText('暂无人工抽样记录')).toBeInTheDocument()
})
