import type { Dim, DomainRef, MetricRow, MetricStatus, SourceRef, WarningSource } from '../../api/data'

/** 数据组文案（#662 R4）：后端只给代码，中文名在这里映射；文字取自旧服务（asset_views / catalog_views / domain_views）。 */

export { HEALTH_LABEL } from './healthStatus'
export const DIMENSION_LABEL: Record<Dim, string> = { freshness: '时效', completeness: '完整', structure: '结构', validity: '真实', lineage: '血缘' }

export type Pill = 'bad' | 'todo' | 'warn' | 'ok'
export const METRIC_STATUS: Record<MetricStatus, { label: string; pill: Pill }> = {
  failed: { label: '失败', pill: 'bad' }, unknown: { label: '含义待查', pill: 'todo' }, uncovered: { label: '未覆盖', pill: 'todo' },
  unverified: { label: '未验证', pill: 'todo' }, known_issue: { label: '已知缺陷', pill: 'warn' }, ok: { label: '通过', pill: 'ok' },
}
export const METRIC_STATUS_ORDER: MetricStatus[] = ['failed', 'unknown', 'uncovered', 'unverified', 'known_issue', 'ok']

export const SOURCE_STATUS_LABEL: Record<NonNullable<SourceRef['status']>, string> = {
  active: '在采集', frozen: '已停采，历史仍在读', dead: '上游已失效',
}
export const WARNING_SOURCE_LABEL: Record<WarningSource['key'], string> = {
  upstream: '上游自相矛盾', registered: '已登记缺口', unclassified: '待归类',
}
export const KIND_LABEL: Record<string, string> = { direct: '直取', derived: '纯派生', factor: '因子' }

const DOMAIN_LABEL: Record<string, string> = {
  reference: '基础 Reference', market: '行情 Market', fundamental: '基本面 Fundamental', factor: '衍生 Factor',
  macro: '宏观 Macro', alternative: '另类 Alternative', runtime: '运行与质检 Runtime',
}
/** 未归类的表必须连理由一起说出来，不能只是不出现。 */
export function domainLabel(d: DomainRef): string {
  if (d.code === 'unclassified') return d.unclassified_reason ? `未归类：${d.unclassified_reason}` : '未归类'
  return DOMAIN_LABEL[d.code] ?? d.code
}

export function sourceText(r: SourceRef): string {
  if (r.kind === 'source') return `${r.provider}·${r.api}`
  if (r.kind === 'table') return `表：${r.id}`
  return r.id
}

/** 直取显示接口与字段（倍数不为 1 时附上），派生显示递归追溯到的根源。 */
export function lineageText(row: MetricRow): string {
  const d = row.direct
  if (d) return `${d.provider ?? d.source_id}·${d.api ?? '?'}.${d.field}${d.multiplier !== 1 ? ` ×${d.multiplier}` : ''}`
  return row.roots.length ? row.roots.join(' / ') : '（无法追溯到直取源）'
}

export const SAMPLE_REASON_TEXT: Record<string, string> = {
  ok: '', logical_view: '逻辑视图没有物理文件，无法抽样', no_file: '仓库里没有这张表的文件', empty_file: '文件为空',
  read_failed: '抽样失败：文件无法读取',
}
