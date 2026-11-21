import { fireEvent, render, screen } from '@testing-library/react'
import { ThemeProvider } from '../../shell/ThemeProvider'
import { ServicesPage } from './ServicesPage'
import { NodesPage } from './NodesPage'
import { ReleasesPage } from './ReleasesPage'

// 输出矩阵/时间线的 y 轴类目，便于断言哪些服务进入了图表
vi.mock('../../page/EChart', () => ({ EChart: ({ option }: { option: { yAxis?: { data?: string[] } } }) =>
  <div data-testid="echart">{JSON.stringify(option.yAxis?.data ?? [])}</div> }))

const wrap = (node: React.ReactNode) => render(<ThemeProvider>{node}</ThemeProvider>)
const days = Array.from({ length: 30 }, (_, i) => `2026-10-${String(21 + i).padStart(2, '0')}`)
let payload: unknown
beforeEach(() => { history.replaceState(null, '', '/app/services'); vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }))) })
afterEach(() => vi.unstubAllGlobals())

test('services keeps historical failure visible and has no rerun control', async () => {
  payload = { schema_version: 2, enabled: true, selected_day: '2026-11-19', days,
    services: [{ id: 'sync', name: '同步', kind: 'timer', cadence: 'daily', node: 'Linux', schedule: '17:00', latest_state: 'fixed', latest_at: '2026-11-19T17:01:00+08:00', completion_ratio_30d: 1, duration_seconds: 60,
      average_duration_seconds: 60, duration_history: [{ run_id: 'r2', started_at: '2026-11-19T17:00:00+08:00', duration_seconds: 60 }], recent_runs: [], version: null,
      cells: days.map((day, i) => ({ day, state: i === 28 ? 'failed' : i === 29 ? 'fixed' : 'unknown', run_id: null, detail: null })) }],
    runs: [], missing: { versions: '没有受管快照' } }
  wrap(<ServicesPage />)
  expect(await screen.findAllByText(/已修复/)).not.toHaveLength(0)
  expect(screen.getAllByText(/失败/).length).toBeGreaterThan(0)
  expect(screen.queryByRole('button', { name: /补跑|重启|停用/ })).toBeNull()
})

// 2026-10-23 … 2026-11-21（周六），夹具显式给出 trade_cal 开闭市事实
const novDays = Array.from({ length: 30 }, (_, i) => new Date(Date.UTC(2026, 9, 23 + i)).toISOString().slice(0, 10))
const novCalendar = Object.fromEntries(novDays.map((d) => [d, ![0, 6].includes(new Date(`${d}T00:00:00Z`).getUTCDay())]))
const svc = (over: Record<string, unknown>) => ({ id: 'sync', name: '同步', kind: 'timer', cadence: 'daily', node: 'Linux', schedule: '17:00',
  latest_state: 'ok', latest_at: '2026-11-20T17:01:00+08:00', completion_ratio_30d: 1, duration_seconds: 60, average_duration_seconds: 60,
  duration_history: [], recent_runs: [], version: null,
  cells: novDays.map((day) => ({ day, state: novCalendar[day] ? 'ok' : 'not_due', run_id: null, detail: null })), ...over })
const run = (id: string, service: string, day: string, hour: number) => ({ run_id: id, service_id: service, session: day,
  started_at: `${day}T${hour}:00:00+08:00`, finished_at: `${day}T${hour}:01:00+08:00`, state: 'succeeded', note: '', error: '', steps: [] })

test('closed day concludes on the previous trading day and keeps monthly tasks out of the matrix', async () => {
  payload = { schema_version: 2, enabled: true, selected_day: '2026-11-21', days: novDays, trading_days: novCalendar,
    services: [svc({}),
      svc({ id: 'digest', name: '日报', cadence: 'everyday', cells: novDays.map((day) => ({ day, state: 'ok', run_id: null, detail: null })) }),
      svc({ id: 'study', name: '月度研究', cadence: 'monthly', cells: novDays.map((day) => ({ day, state: 'not_due', run_id: null, detail: null })) }),
      svc({ id: 'paper', name: '模拟交易', kind: 'resident', cadence: 'daily', schedule: '常驻' })],
    runs: [run('r1', 'sync', '2026-11-20', 17), run('r2', 'digest', '2026-11-21', 20)],
    missing: {} }
  wrap(<ServicesPage />)
  expect((await screen.findByTestId('hero-title')).textContent).toBe('上一交易日 11-20 定时任务全部正常')
  expect(screen.getByTestId('hero-subtitle').textContent).toContain('11-21 休市')
  const kpis = screen.getAllByTestId('hero-kpi').map((x) => x.textContent)
  expect(kpis[0]).toContain('休市')
  expect(kpis[0]).toContain('上一交易日 11-20')
  expect(kpis[3]).toContain('sync · 11-20')
  // 月度任务和常驻进程不进矩阵，只在服务清单
  expect(screen.getByTestId('echart').textContent).toBe(JSON.stringify(['同步', '日报']))
  expect(screen.getByText('月度研究')).toBeInTheDocument()
  expect(screen.getByText('模拟交易')).toBeInTheDocument()
  // 休市日的 everyday 运行不能抢走“上一交易日”时间线
  fireEvent.click(screen.getByText('上一交易日时间线'))
  expect(await screen.findByText('2026-11-20 的时间花在哪？')).toBeInTheDocument()
  expect(screen.getByTestId('echart').textContent).toBe(JSON.stringify(['sync 17:00']))
})

test('without a trading calendar the timeline falls back to the latest run day and says so', async () => {
  payload = { schema_version: 2, enabled: true, selected_day: '2026-11-21', days: novDays, trading_days: {},
    services: [svc({ cells: novDays.map((day) => ({ day, state: 'unknown', run_id: null, detail: null })) })],
    runs: [run('r1', 'sync', '2026-11-20', 17)],
    missing: { calendar: '交易日历读取失败，休市日保持未知' } }
  wrap(<ServicesPage />)
  expect((await screen.findByTestId('hero-title')).textContent).toBe('上一交易日 11-20 还没有可判定的任务结论')
  fireEvent.click(screen.getByText('最近运行日时间线'))
  expect(await screen.findByText('2026-11-20 的时间花在哪？')).toBeInTheDocument()
})

test('trading day failure is the headline with the failing task and its reason', async () => {
  payload = { schema_version: 2, enabled: true, selected_day: '2026-11-20', days: novDays, trading_days: novCalendar,
    services: [svc({ cells: novDays.map((day) => ({ day, state: day === '2026-11-20' ? 'failed' : 'ok', run_id: null, detail: day === '2026-11-20' ? '源接口超时' : null })) }),
      svc({ id: 'verify', name: '质检', cells: novDays.map((day) => ({ day, state: day === '2026-11-20' ? 'warning' : 'ok', run_id: null, detail: null })) })],
    runs: [run('r1', 'sync', '2026-11-20', 17)], missing: {} }
  wrap(<ServicesPage />)
  expect((await screen.findByTestId('hero-title')).textContent).toBe('2026-11-20 1 项失败')
  expect(screen.getByRole('img', { name: '失败' })).toBeInTheDocument()
  const sub = screen.getByTestId('hero-subtitle').textContent!
  // 多项问题只列任务名保持一行，原因进悬停提示
  expect(sub).toBe('失败：同步 · 警告：质检')
  expect(screen.getByTitle(/同步（源接口超时）/)).toBeInTheDocument()
  expect(screen.getByText('当日时间线')).toBeInTheDocument()
})

test('hero reason comes from the failing step of the run, not invented', async () => {
  const warned = { ...run('w1', 'verify', '2026-11-20', 19), state: 'warning',
    steps: [{ name: '字段级检查', state: 'warning', started_at: null, finished_at: null, detail: '28 个历史字段缺口', affects_run: true }] }
  payload = { schema_version: 2, enabled: true, selected_day: '2026-11-20', days: novDays, trading_days: novCalendar,
    services: [svc({ id: 'verify', name: '质检', cells: novDays.map((day) => ({ day, state: day === '2026-11-20' ? 'warning' : 'ok', run_id: day === '2026-11-20' ? 'w1' : null, detail: null })) }),
      svc({ id: 'paper', name: '模拟交易', kind: 'resident', schedule: '常驻', average_duration_seconds: null })],
    runs: [warned], missing: {} }
  wrap(<ServicesPage />)
  expect((await screen.findByTestId('hero-subtitle')).textContent).toBe('警告：质检（字段级检查：28 个历史字段缺口）')
  // 常驻进程的耗时列不适用，显示“—”，不是“缺失”
  expect(screen.getAllByTitle('常驻进程不按次运行，不适用')).toHaveLength(2)
})

test('nodes keeps system and data disks separate and exposes only security counts', async () => {
  payload = { schema_version: 1, enabled: true, captured_at: '2026-11-19T14:00:00+08:00', ttl_seconds: 300, stale: false,
    nodes: [{ id: 'linux', label: 'Linux', platform: 'linux', state: 'ok', cpu: [], memory: [], io: [], disks: [
      { id: 'system', label: '系统盘', mount: '/opt', free_bytes: 20e9, total_bytes: 50e9, fail_below_bytes: 10e9, state: 'ok' },
      { id: 'data', label: '数据盘', mount: '/data', free_bytes: 15e9, total_bytes: 100e9, fail_below_bytes: 10e9, state: 'ok' }] }],
    links: [], processes: [], security: [{ id: 'ssh', label: 'SSH', state: 'ok', mismatches: [], failed_logins_24h: 2, rejected_unauthenticated_24h: 3 }], missing: {} }
  wrap(<NodesPage />)
  expect(await screen.findByText('系统盘')).toBeInTheDocument(); expect(screen.getByText('数据盘')).toBeInTheDocument()
  expect(screen.getByText('2')).toBeInTheDocument(); expect(screen.queryByText(/fingerprint|ssh-rsa/)).toBeNull()
})

test('releases does not claim consistency when evidence is incomplete', async () => {
  payload = { schema_version: 1, enabled: true, captured_at: '2026-11-19T14:00:00+08:00', ttl_seconds: 300, stale: false,
    main_sha: 'abc', consistent_components: null, component_count: 1, components: [{ id: 'web', label: 'Web', current_sha: 'abc', rollback_sha: null, released_at: null, state: 'unknown', three_way_consistent: null, workflow_id: null, deployment_id: null, record_id: null, steps: [] }], events: [], missing: {} }
  wrap(<ReleasesPage />)
  expect(await screen.findByText('三方一致性未知')).toBeInTheDocument()
  expect(screen.getAllByText('缺失').length).toBeGreaterThan(0)
})
