import { getJson, sendJson } from './client'

/** 研究侧接口类型（字段见 src/qsys/web/app_research.py）。写操作沿用现有接口。 */

export interface BacktestRow {
  id: string; strategy: string; strategy_id: string; start: string; end: string; created_at: string; duration: string
  status: string; cost_model: string; return_pct: number | null; benchmark_pct: number | null; max_drawdown_pct: number | null
  has_report: boolean; source: string; progress: number | null; total: number | null; current_session: string | null
}

export interface StrategyRow {
  id: string; name: string; updated_at: string; version_id: string
  stage: 'live' | 'paper' | 'backtest' | 'draft'; stage_label: string; stage_detail: string
  in_use: boolean; used_by: string[]; latest_backtest: BacktestRow | null
}

export interface ParameterSpec {
  name: string; type: 'int' | 'float' | 'bool' | 'str'; default: unknown; label: string; description: string
  minimum: number | null; maximum: number | null; choices: unknown[]
}

export type Disabled = { enabled: false }
export type StrategyList = { enabled: true; core_label: string; templates: string[]; strategies: StrategyRow[] } | Disabled
export type StrategyDetail = StrategyRow & {
  enabled: true; core_label: string; source: string; parameters: ParameterSpec[]
  versions: { id: string; created_at: string }[]; backtests: BacktestRow[]
}
export type BacktestList = { enabled: true; rows: BacktestRow[] } | Disabled

export interface SeriesPoint { date: string; strategy: number | null; benchmark: number | null; excess: number | null; drawdown: number | null }
export interface Episode { peak_date: string; trough_date: string; recovery_date: string | null; depth: number }

export interface BacktestJobInfo {
  id: string; strategy_name?: string; status: string; progress?: number; total?: number; percent?: number
  current_session?: string | null; error?: string; config: Record<string, unknown>
}

export type BacktestDetail =
  | { enabled: true; report: true; name: string; strategy_id: string | null; job: BacktestJobInfo | null
      summary: Record<string, unknown> & { return_pct: number | null; buys: number; sells: number; rejects: number; cost_model: string; entry_fill: string; start: string; end: string; strategy: string }
      metrics: Record<string, number>; series: SeriesPoint[]; episodes: Episode[]
      monthly: { month: string; return: number | null }[]; fills: Record<string, unknown>[]; fills_total: number
      rejects: Record<string, unknown>[]; files: string[]; warnings: string[] }
  | { enabled: true; report: false; strategy_id: string; job: BacktestJobInfo; log_tail: string }
  | Disabled

export interface ResearchJob {
  id: string; state: string; detail: string; task: string; parameters: Record<string, unknown>; timeout_seconds: number | null
  created_at: string; code_sha: string; snapshot_id: string; snapshot_content_sha256: string
  artifacts: { path: string; bytes: number; sha256: string }[]
}
export type ResearchJobList = { enabled: true; submission_enabled: boolean; jobs: ResearchJob[] } | Disabled

const get = getJson
const call = <T,>(method: 'POST' | 'PUT', url: string, body?: unknown) => sendJson<T>(method, url, body)

export const fetchStrategies = (s?: AbortSignal) => get<StrategyList>('/api/app/strategies', s)
export const fetchStrategy = (id: string, s?: AbortSignal) => get<StrategyDetail | Disabled>(`/api/app/strategies/${encodeURIComponent(id)}`, s)
export const saveStrategy = (id: string, name: string, source: string) => call<{ id: string }>('PUT', `/api/app/strategies/${encodeURIComponent(id)}`, { name, source })
export const createStrategy = (name: string, source: string) => call<{ id: string }>('POST', '/api/strategies', { name, source })
export const importTemplate = (filename: string) => call<{ id: string }>('POST', '/api/strategies/import', { filename })

export const fetchBacktests = (s?: AbortSignal) => get<BacktestList>('/api/app/backtests', s)
export const fetchBacktest = (id: string, s?: AbortSignal) => get<BacktestDetail>(`/api/app/backtests/${encodeURIComponent(id)}`, s)
export interface NewBacktest { strategy_id: string; start: string; end: string; cash: number; params: Record<string, unknown>; entry_fill: 'open' | 'continuous'; jq_compat: boolean }
export const createBacktest = (b: NewBacktest) => call<{ id: string }>('POST', '/api/backtests', b)
export const cancelBacktest = (id: string) => call<{ status: string }>('POST', `/api/backtests/${encodeURIComponent(id)}/cancel`)

export const fetchResearchJobs = (s?: AbortSignal) => get<ResearchJobList>('/api/app/research-jobs', s)
export const submitResearchJob = (task: string, parameters: Record<string, unknown>, timeout_seconds?: number) =>
  call<{ id: string }>('POST', '/api/research-jobs', { task, parameters, ...(timeout_seconds ? { timeout_seconds } : {}) })
export const cancelResearchJob = (id: string) => call<{ status: string }>('POST', `/api/research-jobs/${encodeURIComponent(id)}/cancel`)
export const resubmitResearchJob = (id: string) => call<{ id: string }>('POST', `/api/app/research-jobs/${encodeURIComponent(id)}/resubmit`)
