import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { CompareItem } from '../../api/board'
import { CompareView } from './CompareView'
import { board } from './fixtures'

const item = (symbol: string, status: CompareItem['status'], pct: number | null, over: Partial<CompareItem> = {}): CompareItem =>
  ({ symbol, name: `票${symbol}`, pct, column: null, status, first_seal: null, open_times: null, prediction: null, ...over })

test('effect cards show averages with coverage and provisional tag', () => {
  const d = board()
  d.effect = { ...d.effect!, total: 3, priced: 2, no_quote: 1, avg_pct: 0.0123, red_rate: 0.5, multi_avg_pct: null, big_loss: 1 }
  render(<CompareView data={d} onPick={() => {}} />)
  const avg = screen.getByTestId('effect-avg')
  expect(avg.textContent).toContain('+1.23%')
  expect(avg.textContent).toContain('有报价 2/3')
  expect(avg.textContent).toContain('无报价 1 只')
  expect(avg.textContent).toContain('暂计')
  expect(screen.getByTestId('effect-red').textContent).toContain('50.0%')
  expect(within(screen.getByTestId('effect-multi')).getByText('缺失')).toBeInTheDocument()
  expect(screen.getByTestId('effect-loss').textContent).toContain('1')
  const top = screen.getByTestId('effect-top')
  expect(top.textContent).toContain('2 板')
  expect(top.textContent).toContain('丙精密')
  expect(top.textContent).toContain('跌停')
})

test('layer table shows breakdown, rate and previous-day rate or 无昨日数据', () => {
  render(<CompareView data={board({ provisional: false })} onPick={() => {}} />)
  const rows = screen.getAllByTestId('layer-row')
  expect(rows[0].textContent).toContain('昨 2 板')
  expect(rows[0].textContent).toContain('跌停 1')
  expect(rows[0].textContent).toContain('0.0%')
  expect(rows[0].textContent).toContain('无昨日数据')
  expect(rows[1].textContent).toContain('昨首板')
  expect(rows[1].textContent).toContain('100.0%')
  expect(rows[1].textContent).toContain('昨日 50.0%')
  expect(screen.queryByText('暂计')).toBeNull()
})

test('first-board group folds non-promoted items', async () => {
  const d = board()
  d.compare![1].items = [item('A.SH', 'promoted', 0.1), item('R.SZ', 'rush', 0.08), item('M1.SZ', 'missed', 0.02), item('M2.SZ', 'missed', -0.03), item('L.SZ', 'limit_down', -0.1)]
  render(<CompareView data={d} onPick={() => {}} />)
  const group = screen.getByTestId('group-1')
  expect(within(group).getByText('票A.SH')).toBeInTheDocument()
  expect(within(group).getByText('票R.SZ')).toBeInTheDocument()
  expect(within(group).queryByText('票M1.SZ')).toBeNull()
  await userEvent.click(within(group).getByRole('button', { name: '其余 3 只 展开' }))
  expect(within(group).getByText('票M2.SZ')).toBeInTheDocument()
  expect((within(group).getByText('票M1.SZ').closest('[data-item]') as HTMLElement).textContent).toContain('断板')
})

test('ai column shows prediction or 无预测, and status text when shadow not ready', () => {
  const d = board()
  d.compare![0].items[0].prediction = null
  const { unmount } = render(<CompareView data={d} onPick={() => {}} />)
  expect(screen.getByText('AI 影子 · 非交易信号')).toBeInTheDocument()
  expect(screen.getByText(/v-test/)).toBeInTheDocument()
  expect((screen.getByText('甲科技').closest('[data-item]') as HTMLElement).textContent).toContain('42.0%')
  expect((within(screen.getByTestId('group-2')).getByText('丙精密').closest('[data-item]') as HTMLElement).textContent).toContain('无预测')
  unmount()
  render(<CompareView data={board({ shadow: { state: 'failed', message: '离线任务失败', prediction_date: '2026-11-12', model_version: null, precision_at_20: null, scored: 0, hits: null } })} onPick={() => {}} />)
  expect(screen.getAllByText('离线任务失败').length).toBeGreaterThan(0)
  expect(screen.queryByText('42.0%')).toBeNull()
})

test('compare view explains missing heights', () => {
  const d = board({ effect: null, layers: [], compare: [] })
  d.meta = { ...(d.meta as object), heights_error: '2026-11-12 limit_list_d 未入库' } as typeof d.meta
  render(<CompareView data={d} onPick={() => {}} />)
  expect(screen.getByText('昨日连板高度不可用：2026-11-12 limit_list_d 未入库')).toBeInTheDocument()
  expect(screen.queryByTestId('effect-avg')).toBeNull()
})
