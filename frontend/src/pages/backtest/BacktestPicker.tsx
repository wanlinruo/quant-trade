import { Select } from 'antd'
import type { BacktestRow } from '../../api/research'

const STATUS: Record<string, string> = { queued: '排队中', running: '运行中', succeeded: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' }
const pts = (v: number | null) => (v === null ? '' : ` · ${v > 0 ? '+' : ''}${v.toFixed(2)}%`)

/** 回测选择器：每次回测一份确定结果；可按策略名、ID、区间搜索。 */
export function BacktestPicker({ rows, value, onChange }: { rows: BacktestRow[]; value: string | null; onChange(id: string): void }) {
  return (
    <Select showSearch style={{ width: '100%', maxWidth: 560 }} value={value ?? undefined} placeholder="选择一次回测" onChange={onChange}
      optionFilterProp="label"
      options={rows.map((r) => ({
        value: r.id,
        label: `${r.strategy} · ${r.start} ~ ${r.end} · ${STATUS[r.status] ?? r.status}${r.status === 'running' && r.total ? ` ${r.progress}/${r.total}` : ''}${pts(r.return_pct)} · ${r.id.slice(0, 8)}`,
      }))} />
  )
}
