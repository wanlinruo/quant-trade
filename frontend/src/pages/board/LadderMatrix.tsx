import { useState } from 'react'
import { Button } from 'antd'
import type { Board } from '../../api/board'
import { hms, parseLocal } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { BoardCard } from './BoardCard'

export const CELL_LIMIT = 6

/** 梯度按高度降序、未知在最后（JS 对象的数字键会被重排为升序，不能直接用原顺序）。 */
export function gradientEntries(g: Record<string, number>): [string, number][] {
  return Object.entries(g).sort(([a], [b]) => (a === '?' ? 1 : b === '?' ? -1 : Number(b) - Number(a)))
}

/** 题材 × 连板高度矩阵（11-11 §5.3）：列为题材，行为今日高度；超量折叠。面板内横向滚动。 */
export function LadderMatrix({ data, onPick }: { data: Board; onPick(symbol: string): void }) {
  const p = usePalette()
  const [open, setOpen] = useState<Set<string>>(new Set())
  const columns = data.columns ?? []
  const rows = data.rows ?? []
  const cards = data.cards ?? {}
  const mode = data.mode ?? 'live'
  if (columns.length === 0) return <div style={{ color: p.muted }}>尚无进入看板的个股</div>
  const cell = { borderBottom: `1px solid ${p.border}`, padding: 6, verticalAlign: 'top' as const }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', minWidth: '100%', fontSize: 12 }}>
        <thead>
          <tr>
            <th style={{ ...cell, textAlign: 'left', color: p.muted, fontWeight: 400, whiteSpace: 'nowrap' }}>高度</th>
            {columns.map((c) => {
              const first = parseLocal(c.first_seal)
              const grad = gradientEntries(c.gradient).map(([h, n]) => `${h === '?' ? '未知' : h}板×${n}`).join('·')
              return (
                <th key={c.code} scope="col" style={{ ...cell, textAlign: 'left', minWidth: 140 }}>
                  <div style={{ color: p.head }}>{c.name}{c.sealed_count !== null && <span style={{ color: p.up, marginLeft: 6 }}>{c.sealed_count} 家</span>}</div>
                  <div style={{ color: p.muted, fontWeight: 400 }}>{grad && `梯度 ${grad}`}{first && ` · 首家 ${hms(first)}`}</div>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <th scope="row" style={{ ...cell, textAlign: 'left', whiteSpace: 'nowrap', color: p.head }}>
                {r.yesterday !== null && <span style={{ color: p.muted, fontWeight: 400 }}>昨 {r.yesterday} → </span>}{r.label}
              </th>
              {columns.map((c) => {
                const key = `${r.key}|${c.code}`
                const list = r.cells[c.code] ?? []
                const shown = open.has(key) ? list : list.slice(0, CELL_LIMIT)
                return (
                  <td key={c.code} style={cell}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {shown.map((s) => cards[s] ? <BoardCard key={s} card={cards[s]} mode={mode} onPick={onPick} /> : <span key={s}>{s}</span>)}
                      {list.length > shown.length && (
                        <Button size="small" type="dashed" onClick={() => setOpen((o) => new Set(o).add(key))}>{`展开 ${list.length - shown.length} 只`}</Button>
                      )}
                    </div>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
