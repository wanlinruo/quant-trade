import type { DataTableRow } from '../../api/data'
import { int } from '../../page/format'
import { Missing } from '../../ui/Missing'

/** 覆盖统计：统计中/统计失败如实写出；没有统计结果时显示缺失，不显示 0。 */
export function CoverageText({ row }: { row: Pick<DataTableRow, 'coverage' | 'coverage_state'> }) {
  const c = row.coverage
  if (row.coverage_state === 'failed') return <span>统计失败{c ? '（显示上次结果）' : ''}</span>
  if (row.coverage_state === 'pending' && !c) return <span>统计中</span>
  if (!c) return <Missing why="还没有覆盖统计结果" />
  const pending = row.coverage_state === 'pending' ? '（统计中，显示上次结果）' : ''
  return <span>{int(c.rows)} 行 · {int(c.symbols)} 标的 · {String(c.first_dt ?? '未知')} ~ {String(c.last_dt ?? '未知')}{pending}</span>
}
