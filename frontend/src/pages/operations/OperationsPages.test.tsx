import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '../../shell/ThemeProvider'
import { ServicesPage } from './ServicesPage'
import { NodesPage } from './NodesPage'
import { ReleasesPage } from './ReleasesPage'

vi.mock('../../page/EChart', () => ({ EChart: () => <div data-testid="echart" /> }))

const wrap = (node: React.ReactNode) => render(<ThemeProvider>{node}</ThemeProvider>)
const days = Array.from({ length: 30 }, (_, i) => `2026-10-${String(21 + i).padStart(2, '0')}`)
let payload: unknown
beforeEach(() => { history.replaceState(null, '', '/app/services'); vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }))) })
afterEach(() => vi.unstubAllGlobals())

test('services keeps historical failure visible and has no rerun control', async () => {
  payload = { schema_version: 2, enabled: true, selected_day: '2026-11-19', days,
    services: [{ id: 'sync', name: '同步', kind: 'timer', node: 'Linux', schedule: '17:00', latest_state: 'fixed', latest_at: '2026-11-19T17:01:00+08:00', completion_ratio_30d: 1, duration_seconds: 60,
      average_duration_seconds: 60, duration_history: [{ run_id: 'r2', started_at: '2026-11-19T17:00:00+08:00', duration_seconds: 60 }], recent_runs: [], version: null,
      cells: days.map((day, i) => ({ day, state: i === 28 ? 'failed' : i === 29 ? 'fixed' : 'unknown', run_id: null, detail: null })) }],
    runs: [], missing: { versions: '没有受管快照' } }
  wrap(<ServicesPage />)
  expect(await screen.findAllByText(/已修复/)).not.toHaveLength(0)
  expect(screen.getAllByText(/失败/).length).toBeGreaterThan(0)
  expect(screen.queryByRole('button', { name: /补跑|重启|停用/ })).toBeNull()
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
