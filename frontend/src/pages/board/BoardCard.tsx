import type { BoardMode, BoardCard as Card } from '../../api/board'
import { signedPct } from '../../page/format'
import { hms, parseLocal } from '../../shell/time'
import { usePalette } from '../../theme/context'

const DIFF_NAMES: Record<string, string> = { height: '高度', first_seal: '首封', open_times: '开板' }
const t = (iso: string | null) => { const d = parseLocal(iso); return d ? hms(d) : '未知' }

function Tag({ children, color }: { children: string; color: string }) {
  return <span style={{ fontSize: 10, lineHeight: '14px', padding: '0 4px', border: `1px solid ${color}88`, color, borderRadius: 2, whiteSpace: 'nowrap' }}>{children}</span>
}

/** 梯队卡片（对齐旧页 chip）：盘中首封带“≈”，定版标出与直取不一致的值；封单额待单位核实前不展示。 */
export function BoardCard({ card, mode, onPick }: { card: Card; mode: BoardMode; onPick(symbol: string): void }) {
  const p = usePalette()
  const approx = mode === 'live' ? '≈' : ''
  const meta: { text: string; color?: string }[] = []
  if (card.status === 'sealed') {
    meta.push({ text: `${approx}${t(card.first_seal)}` }, { text: `开 ${card.open_times}` })
    if (card.resealed) meta.push({ text: '回封', color: p.warn })
  } else if (card.status === 'broken') {
    meta.push({ text: `炸 · 现 ${signedPct(card.pct) ?? '未知'}`, color: p.warn }, { text: `开 ${card.open_times}` })
  } else if (card.status === 'touched') {
    meta.push({ text: `触板未封 · ${signedPct(card.pct) ?? '未知'}`, color: p.warn })
  } else if (card.status === 'rush') {
    meta.push({ text: `冲 ${signedPct(card.pct) ?? '未知'}`, color: p.warn })
  } else {
    meta.push({ text: '到价 · 盘口缺失' })
  }
  const dashed = card.status === 'broken' || card.status === 'touched' || card.status === 'rush'
  const diffs = mode !== 'live' ? Object.entries(card.diffs ?? {}) : []
  return (
    <div data-card={card.symbol} role="button" tabIndex={0} onClick={() => onPick(card.symbol)}
      onKeyDown={(e) => { if (e.key === 'Enter') onPick(card.symbol) }}
      style={{ cursor: 'pointer', padding: '4px 6px', borderRadius: 3, minWidth: 0, opacity: card.is_bj ? 0.55 : 1,
        border: `1px ${dashed ? 'dashed' : 'solid'} ${card.status === 'sealed' ? p.up + '66' : p.border}`,
        background: card.status === 'sealed' && card.resealed ? `${p.warn}1f` : p.panel2 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 4, fontSize: 12, color: p.head }}>
        <span>{card.name || card.symbol}</span>
        {card.is_st === true && <Tag color={p.warn}>ST</Tag>}
        {card.is_st === null && <Tag color={p.muted}>ST未知</Tag>}
        {card.is_bj && <Tag color={p.muted}>北交所 · 无交易权限</Tag>}
        {card.status === 'unknown' && <Tag color={p.muted}>封板未知</Tag>}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, fontSize: 11, color: p.muted, fontVariantNumeric: 'tabular-nums' }}>
        {meta.map((m) => <span key={m.text} style={{ color: m.color }}>{m.text}</span>)}
      </div>
      {diffs.map(([f, v]) => (
        <div key={f} style={{ fontSize: 11, color: p.bad }}>{`≠ 直取${DIFF_NAMES[f] ?? f} ${f === 'first_seal' && typeof v === 'string' ? t(v) : String(v)}`}</div>
      ))}
    </div>
  )
}
