import { ApiError, getJson } from './client'

afterEach(() => { vi.unstubAllGlobals() })

test('client surfaces error contract message', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { code: 'http_409', message: '该版本正被影子盘使用' } }), { status: 409 })))
  await expect(getJson('/api/app/x')).rejects.toMatchObject({ code: 'http_409', message: '该版本正被影子盘使用', status: 409 })
})

test('client network failure message', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
  const err = (await getJson("/api/app/x").catch((e) => e)) as ApiError
  expect(err).toBeInstanceOf(ApiError)
  expect(err.code).toBe('network')
  expect(err.message).toBe('接口无法访问')
})

test('client non-json error falls back to status', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('oops', { status: 502 })))
  await expect(getJson('/api/app/x')).rejects.toMatchObject({ code: 'http_502', message: '接口返回 502' })
})
