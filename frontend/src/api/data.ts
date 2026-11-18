import { getJson } from './client'
import type {
  DataDisabled, DataOverview as Overview, DataTableDetail, DataTableList, MetricDetail, MetricList, QualitySummary as Quality,
  RuleDetail as Rule, TableSample as Sample,
} from './contracts.gen'

/** /api/app/data/* 的类型由后端契约生成（src/qsys/webapp/contracts/data.py）。只读；文案见 pages/data/labels.ts。 */
export type {
  Coverage, DataTableRow, DatasetContract, DependencyInfo, DomainRef, DomainSummary, FieldInfo, LineageNode, MetricCheckRow,
  MetricRow, OverviewTable, QualityRun, RuleResult, RuleRow, SourceRef, WarningSource,
} from './contracts.gen'

export type Health = 'red' | 'yellow' | 'green' | 'gray'
export type Dim = 'freshness' | 'completeness' | 'structure' | 'validity' | 'lineage'
export type MetricStatus = MetricList['rows'][number]['status']
export type DataOverview = Overview | DataDisabled
export type TableList = DataTableList | DataDisabled
export type TableDetail = DataTableDetail | DataDisabled
export type TableSample = Sample | DataDisabled
export type CatalogList = MetricList | DataDisabled
export type CatalogDetail = MetricDetail | DataDisabled
export type QualitySummary = Quality | DataDisabled
export type RuleDetail = Rule | DataDisabled

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
/** 违规样本下载地址；file 为契约给出的 "<run_id>/<check_id>.parquet"。 */
export const qualitySampleUrl = (file: string) => `/api/app/data/quality/samples/${file.split('/').map(enc).join('/')}`
