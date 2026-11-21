import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '../../shell/ThemeProvider'
import type { AlertCenter } from '../../api/alerts'
import { AlertsPage as Inner } from './AlertsPage'
import sample from './alerts.sample.json'
import detailSample from './detail.sample.json'

vi.mock('../../page/EChart', () => ({ EChart: () => <div data-testid="echart" /> }))
const AlertsPage = () => <ThemeProvider><Inner /></ThemeProvider>
const center = (over: Partial<AlertCenter> = {}) => ({ ...(structuredClone(sample) as unknown as AlertCenter), ...over })
let calls: [string, RequestInit | undefined][] = []
let body: () => unknown
function mockApi() {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    calls.push([String(u), init])
    if (init?.method === 'POST') return new Response(JSON.stringify({ schema_version: 1, ack: { by: '维护者', acked_at: '2026-11-18T10:00:00+08:00', through: '2026-11-17T09:33:00+08:00' } }), { status: 201 })
    if (String(u).startsWith('/api/app/alerts/detail')) return new Response(JSON.stringify(detailSample), { status: 200 })
    return new Response(JSON.stringify(body()), { status: 200 })
  }))
}
beforeEach(() => { body = () => center(); history.replaceState(null, '', '/app/alerts'); mockApi() })
afterEach(() => { vi.unstubAllGlobals() })

test('alerts page shows unhandled conclusion and unknown state', async () => {
  render(<AlertsPage />)
  expect((await screen.findByTestId('hero-title')).textContent).toBe('2 条未处理 · 2 条严重')
  const kpis = screen.getAllByTestId('hero-kpi').map((x) => x.textContent)
  expect(kpis[0]).toContain('未处理')
  expect(kpis[0]).toContain('2')
}, 10_000)

test('unknown state never shows zero', async () => {
  body = () => center({ state: 'unknown', unhandled: null, unhandled_critical: null, acknowledged: null, notices: null, items: [], trend: [], missing: { alerts: '告警存储不可读或未配置' } })
  render(<AlertsPage />)
  expect(await screen.findByText('告警存储不可读')).toBeInTheDocument()
  expect(screen.queryByText(/未处理 0 条/)).toBeNull()
})

test('tabs filter by status', async () => {
  render(<AlertsPage />)
  const table = await screen.findByTestId('alerts-table')
  expect(within(table).getByText('模拟交易已停止')).toBeInTheDocument()
  expect(within(table).queryByText('Web 已恢复')).toBeNull()
  await userEvent.click(screen.getByRole('tab', { name: /已恢复/ }))
  expect(await within(screen.getByTestId('alerts-table')).findByText('Web 已恢复')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: /全部/ }))
  expect(within(screen.getByTestId('alerts-table')).getByText('模拟｜A 竞价决策')).toBeInTheDocument()
})

test('unregistered badge', async () => {
  render(<AlertsPage />)
  expect(await screen.findByText('未登记键')).toBeInTheDocument()
})

test('drawer acks with note and refreshes', async () => {
  render(<AlertsPage />)
  await userEvent.click(await screen.findByText('模拟交易已停止'))
  expect(await screen.findByText('只记录已知悉，不改变任何交易状态')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '确认已知悉' }))
  await waitFor(() => expect(calls.some(([u, i]) => u === '/api/app/alerts/ack' && i?.method === 'POST')).toBe(true))
  const post = calls.find(([u]) => u === '/api/app/alerts/ack')!
  expect(JSON.parse(String(post[1]!.body))).toMatchObject({ key: 'observer.process.down', subject: 'paper' })
  await waitFor(() => expect(calls.filter(([u]) => u.startsWith('/api/app/alerts?') || u === '/api/app/alerts').length).toBeGreaterThan(1))
})

test('day view hides ack button', async () => {
  history.replaceState(null, '', '/app/alerts?day=2026-11-17')
  render(<AlertsPage />)
  await userEvent.click(await screen.findByText('模拟交易已停止'))
  expect(await screen.findByText('回看 2026-11-17：只读，不能确认')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '确认已知悉' })).toBeNull()
  expect(calls.some(([u]) => u.includes('day=2026-11-17'))).toBe(true)
})
