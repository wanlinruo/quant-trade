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
