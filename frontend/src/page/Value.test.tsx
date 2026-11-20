import { render, screen } from '@testing-library/react'
import { Value } from './Value'

test('Value renders Missing for null and Num otherwise', () => {
  const { unmount } = render(<Value v={null} why="上游未返回" />)
  expect(screen.getByText('缺失')).toBeInTheDocument()
  unmount()
  render(<Value v={0} why="不应出现" />)
  expect(screen.getByText('0')).toBeInTheDocument()
  expect(screen.queryByText('缺失')).toBeNull()
})
