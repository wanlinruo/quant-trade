import { render, screen, within } from '@testing-library/react'
import { ThemeProvider } from './ThemeProvider'
import { TopBar } from './TopBar'
import { KillBanner } from './KillBanner'
import { shellStatus } from './fixtures'
import type { ShellStatus } from '../api/shell'

const bar = (status: ShellStatus | null, error: string | null = null) =>
  render(<ThemeProvider><TopBar status={status} error={error} onOpenSearch={() => {}} onOpenHelp={() => {}} /></ThemeProvider>)

test('TopBar shows red 停机状态未知 when kill state unknown, never 未触发', () => {
  bar(shellStatus('unknown'))
  const kill = screen.getByTestId('kill-pill')
  expect(kill).toHaveTextContent('停机状态未知')
  expect(kill).not.toHaveTextContent('未触发')
  expect(screen.getByTestId('live-pill')).toHaveTextContent('实盘状态未知')
})

test('TopBar treats a failed status request as unknown', () => {
  bar(null, '网络错误')
  expect(screen.getByTestId('kill-pill')).toHaveTextContent('停机状态未知')
  expect(screen.getByTestId('live-pill')).toHaveTextContent('实盘状态未知')
  expect(document.body.textContent).not.toContain('未触发')
})

test('TopBar shows clear and stopped states', () => {
  const { unmount } = bar(shellStatus('clear'))
  expect(screen.getByTestId('kill-pill')).toHaveTextContent('停机闸 未触发')
  expect(screen.getByTestId('live-pill')).toHaveTextContent('实盘运行')
  unmount()
  bar(shellStatus('stopped'))
  expect(screen.getByTestId('kill-pill')).toHaveTextContent('停机闸 已触发')
  expect(screen.getByTestId('live-pill')).toHaveTextContent('仅允许减仓')
})

test('TopBar shows 缺失 for qmt and alerts when null', () => {
  bar(shellStatus('clear'))
  expect(within(screen.getByTestId('stat-qmt')).getByText('缺失')).toBeInTheDocument()
  expect(within(screen.getByTestId('stat-alerts')).getByText('缺失')).toBeInTheDocument()
})

test('TopBar exposes the global history date picker', () => {
  bar(shellStatus('clear'))
  expect(screen.getByLabelText('全局回看日期')).toBeInTheDocument()
})

test('TopBar ignores an invalid global history date', () => {
  history.pushState({}, '', '/app/today?day=garbage')
  bar(shellStatus('clear'))
  expect(screen.getByLabelText('全局回看日期')).toHaveValue('')
  history.pushState({}, '', '/app/today')
})

test('KillBanner appears only when kill.state is stopped and cannot be dismissed', () => {
  const wrap = (s: ShellStatus | null) => <ThemeProvider><KillBanner status={s} /></ThemeProvider>
  const { rerender } = render(wrap(shellStatus('clear')))
  expect(screen.queryByRole('alert')).toBeNull()
  rerender(wrap(shellStatus('unknown')))
  expect(screen.queryByRole('alert')).toBeNull()
  rerender(wrap(null))
  expect(screen.queryByRole('alert')).toBeNull()
  rerender(wrap(shellStatus('stopped')))
  const banner = screen.getByRole('alert')
  expect(banner).toHaveTextContent('停机闸已触发')
  expect(banner).toHaveTextContent('对账不一致')
  expect(banner).toHaveTextContent('10:32:35')
  expect(banner).toHaveTextContent('停机闸不自动解除')
  expect(within(banner).queryByRole('button')).toBeNull()
  expect(within(banner).getByRole('link')).toHaveAttribute('href', '/app/recon')
})
