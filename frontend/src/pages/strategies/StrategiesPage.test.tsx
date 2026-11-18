import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrategiesPage as Page } from './StrategiesPage'
import { ThemeProvider } from '../../shell/ThemeProvider'
const StrategiesPage = () => <ThemeProvider><Page /></ThemeProvider>
import { fx } from '../research-fixtures'

type Route = (url: string, init?: RequestInit) => unknown | Response
let calls: [string, RequestInit | undefined][] = []
function mockApi(route: Route) {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    calls.push([String(u), init])
    const r = route(String(u), init)
    return r instanceof Response ? r : new Response(JSON.stringify(r), { status: 200 })
  }))
}
const S1 = (fx.strategies() as Extract<ReturnType<typeof fx.strategies>, { enabled: true }>).strategies[0].id

beforeEach(() => { history.replaceState(null, '', '/app/strategies') })
afterEach(() => { vi.unstubAllGlobals() })

test('strategy table shows stage steps and latest backtest from the shared row', async () => {
  mockApi(() => fx.strategies())
  render(<StrategiesPage />)
  const row = (await screen.findByText('首板打板')).closest('tr') as HTMLElement
  expect(within(row).getByText('模拟').getAttribute('aria-current')).toBe('step')
  expect(row.textContent).toContain('-1.00%')
  expect(row.textContent).toContain('-20.00%')
  expect(row.textContent).toContain('c7766d75')
  const draft = screen.getByText('草稿策略').closest('tr') as HTMLElement
  expect(within(draft).getByText('无回测')).toBeInTheDocument()
  expect(within(draft).getByText('草稿').getAttribute('aria-current')).toBe('step')
  expect(screen.getByTestId('hero-title').textContent).toContain('共 2 个策略')
})

test('in-use strategy is read-only and offers save-as', async () => {
  history.replaceState(null, '', `/app/strategies?id=${S1}`)
  mockApi((u) => (u.includes(`/api/app/strategies/${S1}`) ? fx.strategy() : fx.strategies()))
  render(<StrategiesPage />)
  const src = await screen.findByLabelText('策略源码')
  expect((src as HTMLTextAreaElement).readOnly).toBe(true)
  expect(screen.getByText(/该版本正被模拟账户 模拟一号使用，只读/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '保 存' })).toBeNull()
  expect(screen.queryByRole('button', { name: '保存' })).toBeNull()
  expect(screen.getByRole('button', { name: '另存为新策略' })).toBeInTheDocument()
  expect(screen.getAllByText('window').length).toBeGreaterThan(0)
})

test('saving asks for confirmation mentioning 不影响实盘 and surfaces 409 detail', async () => {
  history.replaceState(null, '', `/app/strategies?id=${S1}`)
  const d = fx.strategy(); d.in_use = false; d.used_by = []
  mockApi((u, init) => {
    if (init?.method === 'PUT') return new Response(JSON.stringify({ detail: '该版本正被影子盘使用，只读；请另存为新策略' }), { status: 409 })
    return u.includes(`/api/app/strategies/${S1}`) ? d : fx.strategies()
  })
  render(<StrategiesPage />)
  const src = await screen.findByLabelText('策略源码')
  await userEvent.type(src, '# edit')
  await userEvent.click(screen.getByRole('button', { name: '保存' }))
  const dialog = await screen.findByRole('dialog')
  expect(dialog.textContent).toContain('不影响实盘')
  await userEvent.click(within(dialog).getByRole('button', { name: '确认' }))
  expect(await screen.findByText('该版本正被影子盘使用，只读；请另存为新策略')).toBeInTheDocument()
  const put = calls.find(([, i]) => i?.method === 'PUT')!
  expect(put[0]).toBe(`/api/app/strategies/${S1}`)
  expect(JSON.parse(String(put[1]!.body)).source).toContain('# edit')
})

test('new backtest dialog builds params from spec and posts', async () => {
  history.replaceState(null, '', `/app/strategies?id=${S1}`)
  mockApi((u, init) => {
    if (init?.method === 'POST') return { id: 'job123', location: '/backtests/job123' }
    if (u.includes(`/api/app/strategies/${S1}`)) return fx.strategy()
    if (u.startsWith('/api/app/strategies/')) return new Response(JSON.stringify({ detail: '策略不存在' }), { status: 404 })
    return fx.strategies()
  })
  render(<StrategiesPage />)
  await userEvent.click(await screen.findByRole('button', { name: '新建回测' }))
  const dlg = await screen.findByRole('dialog')
  await userEvent.type(within(dlg).getByLabelText('开始日期'), '2024-01-02')
  await userEvent.type(within(dlg).getByLabelText('结束日期'), '2024-06-28')
  expect((within(dlg).getByLabelText('window') as HTMLInputElement).value).toBe('20')
  await userEvent.click(within(dlg).getByRole('button', { name: '提交回测' }))
  const confirm = (await screen.findAllByRole('dialog')).at(-1)!
  expect(confirm.textContent).toContain('不影响实盘')
  await userEvent.click(within(confirm).getByRole('button', { name: '确认' }))
  await waitFor(() => expect(calls.some(([u, i]) => u === '/api/backtests' && i?.method === 'POST')).toBe(true))
  const body = JSON.parse(String(calls.find(([u]) => u === '/api/backtests')![1]!.body))
  expect(body).toEqual({ strategy_id: S1, start: '2024-01-02', end: '2024-06-28', cash: 100000, params: { window: 20 }, entry_fill: 'continuous', jq_compat: false })
  await waitFor(() => expect(location.pathname + location.search).toBe('/app/backtest?id=job123'))
})

test('workbench disabled shows message', async () => {
  mockApi(() => ({ enabled: false }))
  render(<StrategiesPage />)
  expect(await screen.findByText('策略工作台未启用')).toBeInTheDocument()
})
