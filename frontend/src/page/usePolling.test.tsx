import { act, renderHook } from '@testing-library/react'
import { usePolling } from './usePolling'
import { refreshPage } from '../shell/refresh'

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

async function flush() { await act(async () => { await Promise.resolve(); await Promise.resolve() }) }

test('usePolling respects minIntervalMs', async () => {
  const fn = vi.fn(async () => 1)
  renderHook(() => usePolling(fn, { intervalMs: 5000, minIntervalMs: 15000 }))
  await flush()
  expect(fn).toHaveBeenCalledTimes(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(14_900) })
  expect(fn).toHaveBeenCalledTimes(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(200) })
  expect(fn).toHaveBeenCalledTimes(2)
})

test('usePolling keeps last data on failure, sets error and backs off', async () => {
  let fail = false
  const fn = vi.fn(async () => { if (fail) throw new Error('x'); return 'v1' })
  const { result } = renderHook(() => usePolling(fn, { intervalMs: 10_000 }))
  await flush()
  expect(result.current.data).toBe('v1')
  expect(result.current.error).toBeNull()
  fail = true
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
  expect(fn).toHaveBeenCalledTimes(2)
  expect(result.current.data).toBe('v1')
  expect(result.current.error).not.toBeNull()
  // 失败后间隔翻倍：10 秒后不应再取，20 秒后才取
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
  expect(fn).toHaveBeenCalledTimes(2)
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
  expect(fn).toHaveBeenCalledTimes(3)
  fail = false
  await act(async () => { await vi.advanceTimersByTimeAsync(40_000) })
  expect(fn).toHaveBeenCalledTimes(4)
  expect(result.current.error).toBeNull()
  // 成功后恢复原间隔
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
  expect(fn).toHaveBeenCalledTimes(5)
})

test('usePolling pauses and resumes', async () => {
  const fn = vi.fn(async () => 1)
  const { result } = renderHook(() => usePolling(fn, { intervalMs: 10_000 }))
  await flush()
  act(() => result.current.setPaused(true))
  expect(result.current.paused).toBe(true)
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
  expect(fn).toHaveBeenCalledTimes(1)
  act(() => result.current.setPaused(false))
  await flush()
  expect(fn).toHaveBeenCalledTimes(2)
})

test('usePolling skips fetch while tab is hidden', async () => {
  const fn = vi.fn(async () => 1)
  renderHook(() => usePolling(fn, { intervalMs: 10_000 }))
  await flush()
  const spy = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
  expect(fn).toHaveBeenCalledTimes(1)
  spy.mockRestore()
})

test('R key page refresh triggers registered polling refresh', async () => {
  const fn = vi.fn(async () => 1)
  renderHook(() => usePolling(fn, { intervalMs: 60_000 }))
  await flush()
  act(() => refreshPage())
  await flush()
  expect(fn).toHaveBeenCalledTimes(2)
})
