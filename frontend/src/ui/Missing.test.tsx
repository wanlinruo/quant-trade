import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Missing } from './Missing'

test('Missing shows 缺失 and exposes reason on hover', async () => {
  render(<Missing why="上游未返回 2025-09-12 分钟数据" />)
  const el = screen.getByText('缺失')
  await userEvent.hover(el)
  expect(await screen.findByText('上游未返回 2025-09-12 分钟数据')).toBeInTheDocument()
})
