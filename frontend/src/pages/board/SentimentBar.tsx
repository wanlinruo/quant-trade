import type { BoardSentiment as Sentiment } from '../../api/board'
import type { BoardColumn } from '../../api/contracts.gen'
import type { Kpi } from '../../page/Hero'
import { pct } from '../../page/format'
import { usePalette } from '../../theme/context'
import { Num } from '../../ui/Num'

const pair = (v: [number | null, number | null]) => (v[0] === null || v[1] === null ? '未知' : `${v[1]} / ${v[0]}`)

/** 情绪栏 9 格（11-11 §5.3）。晋级显示“今 / 昨”，任一侧未知就整格写未知。 */
export function SentimentBar({ s, provisional }: { s: Sentiment; provisional: boolean }) {
  const p = usePalette()
  const cells: [string, React.ReactNode, string?][] = [
    ['涨停 / 跌停', <><Num color={p.up}>{s.sealed}</Num> / <Num color={p.down}>{s.limit_down}</Num></>],
    ['触板 / 炸板', <Num>{s.touched} / {s.broken}</Num>],
    ['封板率', <Num>{pct(s.seal_rate) ?? '未知'}</Num>],
    ['最高板', <Num color={p.up}>{s.max_height === null ? '未知' : `${s.max_height} 板`}</Num>],
    ['晋级 1→2', <Num><span data-testid="promote-1_2">{pair(s.promote_1_2)}</span></Num>, '今日晋级 / 昨日首板'],
    ['晋级 2→3', <Num><span data-testid="promote-2_3">{pair(s.promote_2_3)}</span></Num>],
    ['晋级 3→4+', <Num><span data-testid="promote-3_up">{pair(s.promote_3_up)}</span></Num>],
    ['冲板中 ≥7%', <Num color={p.warn}>{s.rushing}</Num>],
    ['缺涨停价 / 封板未知', <Num>{s.missing_limit} / {s.seal_unknown}</Num>, '封板未知：到价但买卖一档量都为 0，不计入封板与晋级'],
  ]
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(118px, 1fr))', gap: 8, marginBottom: 12 }}>
      {cells.map(([k, v, title]) => (
        <div key={k} title={title} style={{ background: p.panel, border: `1px solid ${p.border}`, borderRadius: 4, padding: '6px 10px' }}>
          <div style={{ fontSize: 11, color: p.muted }}>{k}{provisional && <span style={{ marginLeft: 4 }}>· 暂计</span>}</div>
          <div style={{ fontSize: 15 }}>{v}</div>
        </div>
      ))}
    </div>
  )
}

/** 原型：晋级率、封板率、最强题材放进 Hero KPI；其余计数并入副行。“今 / 昨”任一侧未知整格写未知。 */
export function sentimentKpis(s: Sentiment, columns: BoardColumn[], provisional: boolean): Kpi[] {
  const tag = provisional ? ' · 暂计' : ''
  const top = [...columns].filter((c) => c.sealed_count != null).sort((a, b) => (b.sealed_count ?? 0) - (a.sealed_count ?? 0))[0]
  return [
    { k: `晋级 1→2${tag}`, v: <span data-testid="promote-1_2">{pair(s.promote_1_2)}</span>, sub: '今日晋级 / 昨日首板' },
    { k: `晋级 2→3${tag}`, v: <span data-testid="promote-2_3">{pair(s.promote_2_3)}</span> },
    { k: `晋级 3→4+${tag}`, v: <span data-testid="promote-3_up">{pair(s.promote_3_up)}</span> },
    { k: `封板率${tag}`, v: pct(s.seal_rate) ?? '未知', sub: `触板 ${s.touched} · 炸板 ${s.broken}` },
    { k: '最强题材', v: top ? top.name : '未知', sub: top ? `涨停 ${top.sealed_count} 家` : '题材维度不可用' },
  ]
}

export function sentimentLine(s: Sentiment, provisional: boolean): string {
  return [`冲板中 ≥7% ${s.rushing}`, `缺涨停价 ${s.missing_limit}`, `封板未知 ${s.seal_unknown}（到价但买卖一档量为 0，不计入封板与晋级）`, provisional ? '盘中暂计' : null].filter(Boolean).join(' · ')
}
