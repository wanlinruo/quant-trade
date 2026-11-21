import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '../shell/ThemeProvider'
import { Hero } from './Hero'

test('actionable KPI works with mouse and keyboard', async () => {
  const onClick = vi.fn()
  render(<ThemeProvider><Hero s="ok" title="正常" kpis={[{ k: '未处理', v: 2, onClick }]} /></ThemeProvider>)
  const kpi = screen.getByRole('button', { name: /未处理 2/ })
  await userEvent.click(kpi)
  kpi.focus()
  await userEvent.keyboard('{Enter}')
  expect(onClick).toHaveBeenCalledTimes(2)
})

test('status is a distinct icon with an accessible name; custom status text stays visible', () => {
  const { rerender } = render(<ThemeProvider><Hero s="warn" title="上一交易日 1 项警告" /></ThemeProvider>)
  expect(screen.getByRole('img', { name: '警告' })).toBeInTheDocument()
  expect(screen.queryByText('▲ 警告')).toBeNull()
  rerender(<ThemeProvider><Hero s="idle" statusText="历史 · 只读" title="历史" /></ThemeProvider>)
  expect(screen.getByRole('img', { name: '历史 · 只读' })).toBeInTheDocument()
  expect(screen.getByText('○ 历史 · 只读')).toBeInTheDocument()
})

test('long text KPI values are not rendered as big numbers', () => {
  render(<ThemeProvider><Hero s="ok" title="t" kpis={[{ k: '模型', v: 'limit-up-lgbm-90fa3c17-3ac9496f' }, { k: '数', v: '12' }]} /></ThemeProvider>)
  const [model, num] = screen.getAllByTestId('hero-kpi')
  expect(model.querySelector('span[style*="font-size: 18px"]')).toBeNull()
  expect(num.querySelector('span[style*="font-size: 18px"]')).not.toBeNull()
})
