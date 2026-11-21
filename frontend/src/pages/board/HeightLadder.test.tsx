import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { BoardCard } from '../../api/board'
import { HeightLadder } from './HeightLadder'
import { board } from './fixtures'

function card(symbol: string, over: Partial<BoardCard> = {}): BoardCard {
  return { symbol, name: `票${symbol}`, status: 'sealed', column: null, height: 1, prev_height: 0, first_seal: '2026-11-13T09:40:00',
    open_times: 0, resealed: false, pct: 0.1, is_st: false, is_bj: false, entered_at: '2026-11-13T09:40:00', reason: 'touch', diffs: {}, ...over }
}

test('height ladder puts the highest board first, unknown heights apart and broken/touched last', async () => {
  const cards = Object.fromEntries([
    card('a', { height: 1, first_seal: '2026-11-13T10:00:00' }), card('b', { height: 1, first_seal: '2026-11-13T09:31:00' }),
    card('c', { height: 3 }), card('d', { height: null }), card('e', { status: 'broken', height: null }),
  ].map((c) => [c.symbol, c]))
  const onPick = vi.fn()
  render(<HeightLadder data={{ ...board(), cards }} onPick={onPick} />)
  const cols = [...screen.getByTestId('height-ladder').querySelectorAll('section')].map((x) => x.getAttribute('data-col'))
  expect(cols).toEqual(['h3', 'h1', 'hnull', 'other'])
  const first = screen.getByTestId('height-ladder').querySelector('[data-col="h1"]') as HTMLElement
  expect([...first.querySelectorAll('[data-card]')].map((x) => x.getAttribute('data-card'))).toEqual(['b', 'a'])  // 首封早的在前
  expect(within(first).getByText('首板')).toBeInTheDocument()
  await userEvent.click(screen.getByText('票c'))
  expect(onPick).toHaveBeenCalledWith('c')
})
