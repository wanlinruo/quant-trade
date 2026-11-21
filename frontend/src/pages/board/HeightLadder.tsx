import type { Board } from '../../api/contracts.gen'
import { usePalette } from '../../theme/context'
import { Num } from '../../ui/Num'
import { BoardCard } from './BoardCard'

const OTHER = '炸板 · 触板 · 冲板'

/** 高度梯队（原型“今天的连板梯队”）：按今日连板高度分列，最高板在左、首板在右；
 *  炸板 / 触板 / 冲板单列在最后；高度未知的封板卡单独成列，不推算高度。列内按首封时间排序。 */
export function HeightLadder({ data, onPick }: { data: Board; onPick(symbol: string): void }) {
  const p = usePalette()
  const cards = Object.values(data.cards ?? {})
  const sealed = cards.filter((c) => c.status === 'sealed' || c.status === 'unknown')
  const heights = [...new Set(sealed.map((c) => c.height).filter((h): h is number => h != null))].sort((a, b) => b - a)
  const byFirstSeal = (a: typeof cards[number], b: typeof cards[number]) => (a.first_seal ?? '~').localeCompare(b.first_seal ?? '~') || a.symbol.localeCompare(b.symbol)
  const cols: { key: string; title: string; items: typeof cards }[] = [
    ...heights.map((h) => ({ key: `h${h}`, title: h === 1 ? '首板' : `${h} 板`, items: sealed.filter((c) => c.height === h).sort(byFirstSeal) })),
    ...(sealed.some((c) => c.height == null) ? [{ key: 'hnull', title: '高度未知', items: sealed.filter((c) => c.height == null).sort(byFirstSeal) }] : []),
    { key: 'other', title: OTHER, items: cards.filter((c) => c.status !== 'sealed' && c.status !== 'unknown').sort(byFirstSeal) },
  ].filter((c) => c.items.length)
  if (!cols.length) return <div style={{ color: p.muted }}>当日没有涨停或触板记录</div>
  return (
    <div data-testid="height-ladder" style={{ display: 'grid', gridAutoFlow: 'column', gridAutoColumns: 'minmax(150px, 1fr)', gap: 8, overflowX: 'auto', paddingBottom: 4 }}>
      {cols.map((c) => (
        <section key={c.key} data-col={c.key} style={{ minWidth: 0 }}>
          <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '2px 2px 6px', borderBottom: `2px solid ${c.key === 'other' ? p.border : p.up}`, marginBottom: 6 }}>
            <b style={{ color: c.key === 'other' ? p.muted : p.up }}>{c.title}</b>
            <span style={{ fontSize: 11, color: p.muted }}>{c.key === 'other' ? '' : '封 '}<Num size={11} color={p.muted}>{c.items.length}</Num></span>
          </header>
          <div style={{ display: 'grid', gap: 4, maxHeight: 520, overflowY: 'auto' }}>
            {c.items.map((card) => <BoardCard key={card.symbol} card={card} mode={data.mode} onPick={onPick} />)}
          </div>
        </section>
      ))}
    </div>
  )
}
