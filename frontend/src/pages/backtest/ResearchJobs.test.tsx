import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '../../shell/ThemeProvider'
import { ResearchJobs as Inner } from './ResearchJobs'
import { fx } from '../research-fixtures'
import type { ResearchJobList } from '../../api/research'

const ResearchJobs = () => <ThemeProvider><Inner /></ThemeProvider>
type L = Extract<ResearchJobList, { enabled: true }>
let calls: [string, RequestInit | undefined][] = []
let list: () => unknown
function mockApi() {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    calls.push([String(u), init])
    if (init?.method === 'POST') return new Response(JSON.stringify({ id: 'newjob' }), { status: 201 })
    return new Response(JSON.stringify(list()), { status: 200 })
  }))
}
const failed = () => (fx.research() as L).jobs.find((j) => j.state === 'failed')!

beforeEach(() => { list = () => fx.research() })
afterEach(() => { vi.unstubAllGlobals() })

test('research job progress shows 缺失', async () => {
  mockApi()
  render(<ResearchJobs />)
  const row = (await screen.findAllByText('verify-snapshot'))[0].closest('tr')!
  expect(within(row).getByText('缺失')).toBeInTheDocument()
  expect(screen.queryByText('0%')).toBeNull()
  expect(row.textContent).toContain('aaaaaaaaaaaa')
})

test('failed job can be resubmitted after confirmation and list refreshes', async () => {
  mockApi()
  render(<ResearchJobs />)
  const id = failed().id
  const row = (await screen.findByText(id.slice(0, 8))).closest('tr')!
  expect(within(row).getByText('✕ 失败')).toBeInTheDocument()
  const before = calls.filter(([u, i]) => u === '/api/app/research-jobs' && i?.method === 'GET').length
  await userEvent.click(within(row).getByRole('button', { name: '重新提交' }))
  const dlg = await screen.findByRole('dialog')
  expect(dlg.textContent).toContain('原记录保留')
  expect(dlg.textContent).toContain('不影响实盘')
  await userEvent.click(within(dlg).getByRole('button', { name: '确认' }))
  await waitFor(() => expect(calls.some(([u, i]) => u === `/api/app/research-jobs/${id}/resubmit` && i?.method === 'POST')).toBe(true))
  await waitFor(() => expect(calls.filter(([u, i]) => u === '/api/app/research-jobs' && i?.method === 'GET').length).toBeGreaterThan(before))
})

test('submission disabled hides write actions', async () => {
  list = () => ({ ...fx.research(), submission_enabled: false })
  mockApi()
  render(<ResearchJobs />)
  expect(await screen.findByText(/本节点未开启网页提交/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '重新提交' })).toBeNull()
  expect(screen.queryByRole('button', { name: '取消' })).toBeNull()
  expect(screen.queryByRole('button', { name: '提交研究任务' })).toBeNull()
})

test('invalid parameter json is rejected before submit', async () => {
  mockApi()
  render(<ResearchJobs />)
  await userEvent.click(await screen.findByRole('button', { name: '提交研究任务' }))
  const dlg = await screen.findByRole('dialog')
  await userEvent.type(within(dlg).getByLabelText('任务名'), 'verify-snapshot')
  const params = within(dlg).getByLabelText('参数 JSON')
  await userEvent.clear(params)
  await userEvent.type(params, 'not json')
  await userEvent.click(within(dlg).getByRole('button', { name: '提交' }))
  expect(await within(dlg).findByText('参数必须是 JSON 对象')).toBeInTheDocument()
  expect(calls.some(([, i]) => i?.method === 'POST')).toBe(false)
})

test('research disabled shows message', async () => {
  list = () => ({ enabled: false })
  mockApi()
  render(<ResearchJobs />)
  expect(await screen.findByText('研究任务未启用')).toBeInTheDocument()
})
