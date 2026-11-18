import { signedPct } from '../../page/format'
import { usePalette } from '../../theme/context'

/** 月度收益热力图：行为年、列为 1–12 月；空月份留空，null 显示“缺失”。涨红跌绿。 */
export function MonthlyHeatmap({ months }: { months: { month: string; return: number | null }[] }) {
  const p = usePalette()
  const byMonth = new Map(months.map((m) => [m.month, m.return]))
  const years = [...new Set(months.map((m) => m.month.slice(0, 4)))].sort()
  const max = Math.max(0.0001, ...months.map((m) => Math.abs(m.return ?? 0)))
  const cell = (key: string) => {
    if (!byMonth.has(key)) return { text: '', bg: 'transparent' }
    const v = byMonth.get(key)!
    if (v === null) return { text: '缺失', bg: p.panel2 }
    const a = Math.round(Math.min(1, Math.abs(v) / max) * 160 + 40).toString(16).padStart(2, '0')
    return { text: signedPct(v) ?? '', bg: `${v >= 0 ? p.up : p.down}${a}` }
  }
  return (
    <div style={{ overflowX: 'auto' }}>
      <div data-testid="monthly-heatmap" style={{ display: 'grid', gridTemplateColumns: '48px repeat(12, minmax(52px, 1fr))', gap: 2, fontSize: 11, minWidth: 680 }}>
        <span />
        {Array.from({ length: 12 }, (_, i) => <span key={i} style={{ color: p.muted, textAlign: 'center' }}>{i + 1} 月</span>)}
        {years.map((y) => [
          <span key={y} style={{ color: p.muted }}>{y}</span>,
          ...Array.from({ length: 12 }, (_, i) => {
            const key = `${y}-${String(i + 1).padStart(2, '0')}`
            const c = cell(key)
            return <span key={key} data-testid={`m-${key}`} style={{ background: c.bg, textAlign: 'center', padding: '4px 0', borderRadius: 2, color: c.text === '缺失' ? p.muted : p.head, fontVariantNumeric: 'tabular-nums' }}>{c.text}</span>
          }),
        ])}
      </div>
    </div>
  )
}
