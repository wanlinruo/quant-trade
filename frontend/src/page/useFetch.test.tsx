import { act, renderHook, waitFor } from '@testing-library/react'
import { refreshPage } from '../shell/refresh'
import { useFetch } from './useFetch'

test('R/page refresh refetches one-shot pages', async () => {
  let n = 0
  const { result } = renderHook(() => useFetch(async () => ++n, []))
  await waitFor(() => expect(result.current.data).toBe(1))
  expect(result.current.fetchedAt).toBeInstanceOf(Date)
  act(() => refreshPage())
  await waitFor(() => expect(result.current.data).toBe(2))
})
