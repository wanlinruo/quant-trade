import { render, screen } from '@testing-library/react'
import { StatusTag } from './StatusTag'

test('StatusTag renders symbol plus text, not color alone', () => {
  render(<StatusTag s="bad" />)
  expect(screen.getByText(/✕/)).toHaveTextContent('✕ 失败')
})

test('StatusTag uses custom text but keeps the symbol', () => {
  render(<StatusTag s="warn" text="完成·有警告" />)
  expect(screen.getByText(/▲/)).toHaveTextContent('▲ 完成·有警告')
})
