import { render, screen } from '@testing-library/react'
import { Freshness } from './Freshness'

test('Freshness flags refresh failure and shows data time', () => {
  render(<Freshness at={new Date(2026, 10, 17, 10, 42, 5)} paused={false} onTogglePause={() => {}} onRefresh={() => {}} error="接口返回 500" />)
  expect(screen.getByText('▲ 刷新失败，显示的是上一次结果')).toBeInTheDocument()
  expect(screen.getByText(/数据截至 10:42:05/)).toBeInTheDocument()
})
