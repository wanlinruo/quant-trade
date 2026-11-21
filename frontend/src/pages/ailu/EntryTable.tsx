import { Table } from 'antd'
import type { ShadowEntry } from '../../api/ailu'
import { pct } from '../../page/format'
import { usePalette } from '../../theme/context'
import { Num } from '../../ui/Num'

function Outcome({ e }: { e: ShadowEntry }) {
  const p = usePalette()
  if (e.actual_touched === null) return <span style={{ color: p.muted }}>未出结果</span>
  const main = !e.actual_touched ? '未触板' : e.actual_closed_limit ? '封板' : '触板 · 未封'
  const color = e.actual_closed_limit ? p.up : e.actual_touched ? p.warn : p.muted
  const extra = [e.opened_at_limit && '开盘即涨停', e.one_price_limit && '一字板'].filter(Boolean).join(' · ')
  return <span><span style={{ color }}>{main}</span>{extra && <span style={{ color: p.muted }}> · {extra}</span>}</span>
}

/** 板块取值见 qsys.research.limit_up_ranking（main / gem / star）；未知值原样显示。 */
const BOARD: Record<string, string> = { main: '主板', gem: '创业板', star: '科创板', bse: '北交所' }

/** 概率条（原型）：条长 = 概率本身，数值在右；不做任何换算。 */
function Prob({ v, color, strong }: { v: number; color: string; strong?: boolean }) {
  const p = usePalette()
  return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, justifyContent: 'flex-end', width: '100%' }}>
    <span aria-hidden style={{ display: 'inline-block', width: 64, height: 6, background: p.idle, borderRadius: 3, overflow: 'hidden' }}>
      <span style={{ display: 'block', height: 6, width: `${Math.max(0, Math.min(1, v)) * 100}%`, background: color }} />
    </span>
    <Num color={strong ? p.head : undefined} weight={strong ? 600 : 500}>{pct(v)}</Num>
  </span>
}

/** 榜单表：三列概率都取后端值（§9.5 不在前端重算），实际结果未出时如实写“未出结果”。 */
export function EntryTable({ entries, resultTitle }: { entries: ShadowEntry[]; resultTitle: string }) {
  const p = usePalette()
  return (
    <Table size="small" pagination={false} rowKey="symbol" dataSource={entries} scroll={{ x: 'max-content' }}
      columns={[
        { title: '排名', dataIndex: 'rank', width: 52, align: 'right', render: (v: number) => <Num>{v}</Num> },
        { title: '名称 / 代码', key: 'n', render: (_, e) => <span>{e.name || e.symbol}{e.name && <span style={{ color: p.muted, fontSize: 11 }}> {e.symbol}</span>}</span> },
        { title: '板块', dataIndex: 'board', render: (v: string) => BOARD[v] ?? v },
        { title: '触板概率', dataIndex: 'p_touch', align: 'right', render: (v: number) => <Prob v={v} color={p.accent} /> },
        { title: '× 触板后封板', dataIndex: 'p_seal_given_touch', align: 'right', render: (v: number) => <Prob v={v} color={p.info} /> },
        { title: '＝ 收盘封板', dataIndex: 'p_close_limit', align: 'right', render: (v: number) => <Prob v={v} color={p.up} strong /> },
        { title: resultTitle, key: 'r', render: (_, e) => <Outcome e={e} /> },
      ]} />
  )
}
