import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DrawerNav } from './DrawerNav'

test('drawer navigation moves to adjacent rows', async () => {
  const rows = [{ id: 'a' }, { id: 'b' }, { id: 'c' }]
  const pick = vi.fn()
  render(<DrawerNav rows={rows} current={rows[1]} identify={(x) => x.id} onPick={pick} />)
  await userEvent.click(screen.getByRole('button', { name: '上一条' }))
  await userEvent.click(screen.getByRole('button', { name: '下一条' }))
  expect(pick.mock.calls.map((x) => x[0].id)).toEqual(['a', 'c'])
})

test('drawer shortcuts do not capture editable fields', async () => {
  const rows = [{ id: 'a' }, { id: 'b' }]
  const pick = vi.fn()
  render(<><input aria-label="备注" /><DrawerNav rows={rows} current={rows[0]} identify={(x) => x.id} onPick={pick} /></>)
  const input = screen.getByRole('textbox', { name: '备注' })
  input.focus()
  await userEvent.keyboard('j{ArrowDown}k{ArrowUp}')
  expect(pick).not.toHaveBeenCalled()
})
