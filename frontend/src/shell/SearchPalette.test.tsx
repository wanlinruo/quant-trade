import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from './ThemeProvider'
import { SearchPalette } from './SearchPalette'

afterEach(() => vi.unstubAllGlobals())

test('global search includes business objects and preserves destination params', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ schema_version: 1, missing: {}, items: [
    { kind: '指标', key: 'daily.close', label: 'daily.close', hint: '收盘价', page: 'catalog', params: { id: 'daily.close' } },
  ] }), { status: 200 })))
  const pick = vi.fn()
  render(<ThemeProvider><SearchPalette open recent={[]} onClose={() => {}} onPick={pick} /></ThemeProvider>)
  await userEvent.type(screen.getByPlaceholderText('搜索页面…'), 'daily.close')
  await userEvent.click(await screen.findByText('daily.close'))
  expect(pick).toHaveBeenCalledWith('catalog', { id: 'daily.close' })
})
