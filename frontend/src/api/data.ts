import { getJson } from './client'

/** /api/app/data/* 的类型（字段见 src/qsys/web/app_data.py 与 asset_views / catalog_views）。只读。 */

export type Health = 'red' | 'yellow' | 'green' | 'gray'
export type Dim = 'freshness' | 'completeness' | 'structure' | 'validity' | 'lineage'
type Disabled = { enabled: false }

export interface OverviewTable { id: string; name: string; layer: string; domain: string; domain_label: string; status: Health; dimensions: Record<Dim, Health> }
export interface LineageNode { id: string; label: string; kind: string; ref: string | null; status: Health | null }
export interface DomainSummary { domain: string; label: string; tables: number; sources: number; status_counts: Record<Health, number> }
export type DataOverview = Disabled | {
  enabled: true; dimension_labels: Record<Dim, string>; status_labels: Record<Health, string>
  tables: OverviewTable[]; domains: DomainSummary[]; lineage: { nodes: LineageNode[]; edges: { source: string; target: string }[] }
}

export interface TableRow {
  id: string; name: string; layer: string; domain_label: string; status: Health; status_label: string; purpose: string; grain: string
  coverage: Record<string, unknown> | null; coverage_pending: boolean; coverage_failed: boolean
}
export type TableList = Disabled | { enabled: true; rows: TableRow[] }

export interface DatasetContract {
  id: string; name: string; purpose: string; layer: string; role: string; status: string; grain: string
  primary_key: string[]; time_columns: string[]; partition_keys: string[]; sort_keys: string[]
  schema_id: string | null; schema_version: number; compatibility: string; builder: string; update_mode: string
  finalized_at: string; lifecycle: string; sources: string[]; consumers: string[]; health_policy: string | null; physical: boolean
}
export interface Dependency { upstream: string; downstream: string; kind: string; join_keys: [string, string][]; cardinality: string; requirement: string; allowed_lag: string; metrics: string[]; note: string }
export type TableDetail = Disabled | {
  enabled: true; status_label: string; dimension_labels: Record<Dim, string>; domain_label: string
  row: { dataset: DatasetContract; status: Health; coverage: Record<string, unknown> | null; coverage_pending: boolean; coverage_failed: boolean }
  source_refs: { kind: string; label: string; href: string; status: string }[]; root_refs: { kind: string; label: string; href: string; status: string }[]
  dimensions: [string, Health][]; reasons: { code: string; message: string; dimension: string; check_id: string | null }[]
  evidence: Record<string, unknown>[]; upstream: Dependency[]; downstream: Dependency[]
  fields: [string, string, string][]; rules: string[]
}
export type TableSample = Disabled | { enabled: true; available: boolean; message: string; file: string | null; row_group: number | null; columns: string[]; rows: Record<string, unknown>[]; limit?: number }

export interface MetricRow { id: string; dataset: string; column: string; kind: string; validity: string; meaning: string; source_label: string; status: string; issues: string[]; label: string }
export type CatalogList = Disabled | {
  enabled: true; summary: { total: number; by_kind: [string, number][]; covered: number; pending: number; latest_run: string | null }
  rows: MetricRow[]; datasets: string[]; kinds: string[]; statuses: [string, string][]
}
export type CatalogDetail = Disabled | {
  enabled: true; row: MetricRow; upstream: string[]; downstream: string[]; ext_consumers: string[]; formula: string
  params: [string, unknown][]; roots: string[]; checks: Record<string, unknown>[]; history: Record<string, unknown>[]
  evidence: { metric_id: string; symbol: string; dt: string; our_value: string; reference_value: string; reference_source: string; verdict: string; checked_by: string; checked_at: string; note: string }[]
}

export interface QualityRun { run_id: string; checked_at: string; fields: { total: number; passed: number; blocking: number; warning: number }; tables: Record<Health, number> | null }
export interface WarningSource { key: 'upstream' | 'registered' | 'unclassified'; label: string; issue: string | null; count: number; samples: { metric_id: string; check_id: string; detail: string; known_issue: string | null }[] }
export interface RuleRow { id: string; name: string; category: string; scope: string; known_issues: string[]; upstream_self_consistency: boolean; latest_status: 'blocking' | 'warning' | 'unknown' | 'passed' }
export type QualitySummary = Disabled | { enabled: true; runs: QualityRun[]; warning_sources: WarningSource[]; rules: RuleRow[] }
export type RuleDetail = Disabled | {
  enabled: true; id: string; name: string; category: string; condition: string; limitation: string; implementation: string
  targets: string[]; latest: (Record<string, unknown> & { sample_url?: string })[]; history: Record<string, unknown>[]
}

const get = getJson
const enc = encodeURIComponent
const qs = (p: Record<string, string | null | undefined>) => {
  const q = new URLSearchParams(); for (const [k, v] of Object.entries(p)) if (v) q.set(k, v)
  const s = q.toString(); return s ? `?${s}` : ''
}

export const fetchOverview = (s?: AbortSignal) => get<DataOverview>('/api/app/data/overview', s)
export const fetchTables = (includeRaw: boolean, s?: AbortSignal) => get<TableList>(`/api/app/data/tables${includeRaw ? '?include_raw=true' : ''}`, s)
export const fetchTable = (id: string, s?: AbortSignal) => get<TableDetail>(`/api/app/data/tables/${enc(id)}`, s)
export const fetchSample = (id: string, s?: AbortSignal) => get<TableSample>(`/api/app/data/tables/${enc(id)}/sample`, s)
export const fetchCatalog = (f: { dataset?: string | null; kind?: string | null; status?: string | null }, s?: AbortSignal) =>
  get<CatalogList>(`/api/app/data/catalog${qs(f)}`, s)
export const fetchMetric = (id: string, s?: AbortSignal) => get<CatalogDetail>(`/api/app/data/catalog/${enc(id)}`, s)
export const fetchQuality = (s?: AbortSignal) => get<QualitySummary>('/api/app/data/quality', s)
export const fetchRule = (id: string, s?: AbortSignal) => get<RuleDetail>(`/api/app/data/quality/rules/${enc(id)}`, s)
