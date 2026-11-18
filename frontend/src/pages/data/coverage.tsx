import { int } from '../../page/format'
import { Missing } from '../../ui/Missing'

type Row = { coverage: Record<string, unknown> | null; coverage_pending: boolean; coverage_failed: boolean }

/** 覆盖统计：统计中/统计失败如实写出；没有统计结果时显示缺失，不显示 0。 */
export function CoverageText({ row }: { row: Row }) {
  if (row.coverage_failed) return <span>统计失败</span>
  if (row.coverage_pending && !row.coverage) return <span>统计中</span>
  const c = row.coverage
  if (!c) return <Missing why="还没有覆盖统计结果" />
  const pending = row.coverage_pending ? '（统计中，显示上次结果）' : ''
  return <span>{int(c.rows as number)} 行 · {int(c.symbols as number)} 标的 · {String(c.first_dt ?? '未知')} ~ {String(c.last_dt ?? '未知')}{pending}</span>
}
