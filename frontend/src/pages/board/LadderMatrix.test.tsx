import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Card } from '../../api/board'
import { LadderMatrix } from './LadderMatrix'
import { board } from './fixtures'

function card(symbol: string, over: Partial<Card> = {}): Card {
  return { symbol, name: `票${symbol}`, status: 'sealed', column: '885001.TI', height: 1, prev_height: 0, first_seal: '2026-11-13T09:40:00',
    open_times: 0, resealed: false, pct: 0.1, is_st: false, is_bj: false, entered_at: '2026-11-13T09:40:00', reason: 'touch', diffs: {}, ...over }
}

test('matrix renders columns, row labels with yesterday counts and cards', () => {
  render(<LadderMatrix data={board()} onPick={() => {}} />)
  const head = screen.getByRole('columnheader', { name: /机器人/ })
  expect(head.textContent).toContain('2 家')
  expect(head.textContent).toContain('2板×1·1板×1')
  expect(head.textContent).toContain('09:30:58')
  const row2 = screen.getByRole('rowheader', { name: /2 板/ })
  expect(row2.textContent).toContain('昨 1 →')
  expect(screen.getByText('甲科技')).toBeInTheDocument()
  expect(screen.getByText('乙智能')).toBeInTheDocument()
})

test('cell folds beyond 6 cards with 展开 N 只', async () => {
  const d = board()
  const extra = Array.from({ length: 8 }, (_, i) => `Z${i}.SZ`)
  for (const s of extra) d.cards![s] = card(s)
  d.rows![1].cells['885001.TI'] = ['B.SZ', ...extra]
  render(<LadderMatrix data={d} onPick={() => {}} />)
  const more = screen.getByRole('button', { name: '展开 3 只' })
  expect(screen.queryByText('票Z7.SZ')).toBeNull()
  await userEvent.click(more)
  expect(screen.getByText('票Z7.SZ')).toBeInTheDocument()
})

test('card variants: live ≈ time, final no ≈ and shows ≠ diffs, bj greyed with 无交易权限, st unknown', async () => {
  const d = board()
  d.cards!['B.SZ'] = card('B.SZ', { name: '乙智能', is_bj: true, is_st: null, first_seal: '2026-11-13T09:31:13' })
  const { unmount } = render(<LadderMatrix data={d} onPick={() => {}} />)
  const a = screen.getByText('甲科技').closest('[data-card]') as HTMLElement
  expect(a.textContent).toContain('≈09:30:58')
  expect(a.textContent).toContain('开 1')
  expect(a.textContent).toContain('回封')
  const b = screen.getByText('乙智能').closest('[data-card]') as HTMLElement
  expect(within(b).getByText('北交所 · 无交易权限')).toBeInTheDocument()
  expect(within(b).getByText('ST未知')).toBeInTheDocument()
  unmount()
  const f = board({ mode: 'final' })
  f.cards!['A.SH'].diffs = { first_seal: '2026-11-13T09:31:00', height: 3 }
  const onPick = vi.fn()
  render(<LadderMatrix data={f} onPick={onPick} />)
  const fa = screen.getByText('甲科技').closest('[data-card]') as HTMLElement
  expect(fa.textContent).toContain('09:30:58')
  expect(fa.textContent).not.toContain('≈')
  expect(within(fa).getByText('≠ 直取首封 09:31:00')).toBeInTheDocument()
  expect(within(fa).getByText('≠ 直取高度 3')).toBeInTheDocument()
  await userEvent.click(fa)
  expect(onPick).toHaveBeenCalledWith('A.SH')
})

test('non-sealed variants show their state text', () => {
  const d = board()
  d.cards!['B.SZ'] = card('B.SZ', { name: '乙智能', status: 'broken', open_times: 2, pct: 0.052 })
  d.cards!['C.SZ'] = card('C.SZ', { name: '丙精密', status: 'rush', pct: 0.075 })
  d.cards!['D.SZ'] = card('D.SZ', { name: '丁', status: 'unknown' })
  d.rows![2].cells['885001.TI'] = ['B.SZ', 'C.SZ', 'D.SZ']
  d.rows![1].cells['885001.TI'] = []
  render(<LadderMatrix data={d} onPick={() => {}} />)
  expect((screen.getByText('乙智能').closest('[data-card]') as HTMLElement).textContent).toContain('炸 · 现 +5.20%')
  expect((screen.getByText('丙精密').closest('[data-card]') as HTMLElement).textContent).toContain('冲 +7.50%')
  expect(screen.getByText('封板未知')).toBeInTheDocument()
})
