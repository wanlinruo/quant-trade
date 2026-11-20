import { act, render, screen } from '@testing-library/react'
import { ThemeProvider, useTheme } from './ThemeProvider'

function Probe() {
  const t = useTheme()
  return <div><span data-testid="mode">{t.mode}</span><span data-testid="pref">{t.pref}</span>
    <button onClick={() => t.setPref('light')}>light</button><button onClick={() => t.toggle()}>toggle</button></div>
}

function mockSystem(light: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: q.includes('light') ? light : !light, media: q, onchange: null,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

beforeEach(() => localStorage.clear())

test('theme follows system by default and persists manual choice in qs-theme', () => {
  mockSystem(true)
  const { unmount } = render(<ThemeProvider><Probe /></ThemeProvider>)
  expect(screen.getByTestId('pref')).toHaveTextContent('system')
  expect(screen.getByTestId('mode')).toHaveTextContent('light')
  act(() => screen.getByText('toggle').click())
  expect(screen.getByTestId('mode')).toHaveTextContent('dark')
  expect(localStorage.getItem('qs-theme')).toBe('dark')
  unmount()
  mockSystem(true)
  render(<ThemeProvider><Probe /></ThemeProvider>)
  expect(screen.getByTestId('mode')).toHaveTextContent('dark')
})

test('invalid stored theme falls back to system', () => {
  localStorage.setItem('qs-theme', 'purple')
  mockSystem(false)
  render(<ThemeProvider><Probe /></ThemeProvider>)
  expect(screen.getByTestId('pref')).toHaveTextContent('system')
  expect(screen.getByTestId('mode')).toHaveTextContent('dark')
})
