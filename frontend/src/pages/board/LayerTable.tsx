import type { LayerRow } from '../../api/board'
import { pct, signedPct } from '../../page/format'
import type { Palette } from '../../theme/palette'
import { usePalette } from '../../theme/context'
import { Num } from '../../ui/Num'

export const levelLabel = (n: number) => (n === 1 ? '昨首板' : `昨 ${n} 板`)

function segments(r: LayerRow, p: Palette): [string, number, string][] {
  return [
    ['晋级', r.promoted, p.up], ['炸板', r.broken, p.warn], ['断板红', r.break_up, `${p.up}88`],
    ['断板绿', r.break_down, `${p.down}88`], ['跌停', r.limit_down, p.down], ['未知', r.unknown + r.no_quote, p.idle],
  ]
}

/** 晋级表：每层一行，堆叠条每段都有文字说明，不只靠颜色。 */
export function LayerTable({ rows }: { rows: LayerRow[] }) {
  const p = usePalette()
  const td = { padding: '6px 8px', borderBottom: `1px solid ${p.border}`, whiteSpace: 'nowrap' as const }
  return (
    <div style={{ overflowX: 'auto', marginBottom: 12 }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
        <thead><tr style={{ color: p.muted, textAlign: 'left' }}>
          {['层', '家数', '今日去向', '晋级率', '今日平均'].map((h) => <th key={h} style={{ ...td, fontWeight: 400 }}>{h}</th>)}
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.level} data-testid="layer-row">
              <td style={{ ...td, color: p.head }}>{levelLabel(r.level)}</td>
              <td style={td}><Num>{r.total}</Num></td>
              <td style={{ ...td, minWidth: 220 }}>
                <div style={{ display: 'flex', height: 8, borderRadius: 2, overflow: 'hidden', background: p.grid, marginBottom: 3 }}>
                  {segments(r, p).filter(([, n]) => n > 0).map(([k, n, c]) => <span key={k} title={`${k} ${n}`} style={{ width: `${(n / r.total) * 100}%`, background: c }} />)}
                </div>
                <span style={{ color: p.muted }}>{segments(r, p).filter(([, n]) => n > 0).map(([k, n]) => `${k} ${n}`).join(' · ')}</span>
              </td>
              <td style={td}><Num color={p.up}>{pct(r.rate) ?? '未知'}</Num>{' '}
                <span style={{ color: p.muted }}>{r.prev_rate === null ? '无昨日数据' : `昨日 ${pct(r.prev_rate)}`}</span></td>
              <td style={td}><Num color={r.avg_pct === null ? p.muted : r.avg_pct > 0 ? p.up : r.avg_pct < 0 ? p.down : p.muted}>{signedPct(r.avg_pct) ?? '无报价'}</Num></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
