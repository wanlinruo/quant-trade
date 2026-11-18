import { fetchShellStatus } from './shell'
import { shellStatus } from '../shell/fixtures'

afterEach(() => vi.restoreAllMocks())

test('fetchShellStatus returns parsed body on 200', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(shellStatus('clear')), { status: 200 }))
  const r = await fetchShellStatus()
  expect(r.data?.kill.state).toBe('clear')
  expect(r.error).toBeNull()
})

test('fetchShellStatus never returns stale data on failure', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('boom', { status: 500 }))
  expect(await fetchShellStatus()).toEqual({ data: null, error: '接口返回 500' })
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
  expect((await fetchShellStatus()).data).toBeNull()
})

test('fetchShellStatus rejects an unexpected schema', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ schema_version: 2 }), { status: 200 }))
  const r = await fetchShellStatus()
  expect(r.data).toBeNull()
  expect(r.error).toContain('格式')
})
