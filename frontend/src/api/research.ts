import { getJson, sendJson } from './client'
import type {
  BacktestList, BacktestPending, BacktestReport, Cancelled, Created, ResearchDisabled, ResearchJobList, Resubmitted,
  StrategyDetail, StrategyList,
} from './contracts.gen'

/** 研究侧接口类型由后端契约生成（src/qsys/webapp/contracts/research.py）。比例一律 0–1。 */
export type {
  ArtifactInfo, BacktestJobInfo, BacktestList, BacktestPending, BacktestReport, BacktestRow, Episode, MonthReturn,
  ParameterSpec, ResearchDisabled, ResearchJob, ResearchJobList, ReturnPoint, RunFileInfo, RunSummary, StrategyDetail,
  StrategyList, StrategyRow, StrategyVersion,
} from './contracts.gen'

export type Disabled = ResearchDisabled
export type StrategiesData = StrategyList | ResearchDisabled
export type StrategyData = StrategyDetail | ResearchDisabled
export type BacktestsData = BacktestList | ResearchDisabled
export type BacktestDetail = BacktestReport | BacktestPending | ResearchDisabled
export type ResearchJobsData = ResearchJobList | ResearchDisabled

const enc = encodeURIComponent
const get = getJson
const call = <T,>(method: 'POST' | 'PUT', url: string, body?: unknown) => sendJson<T>(method, url, body)

export const fetchStrategies = (s?: AbortSignal) => get<StrategiesData>('/api/app/strategies', s)
export const fetchStrategy = (id: string, s?: AbortSignal) => get<StrategyData>(`/api/app/strategies/${enc(id)}`, s)
export const saveStrategy = (id: string, name: string, source: string) => call<Created>('PUT', `/api/app/strategies/${enc(id)}`, { name, source })
export const createStrategy = (name: string, source: string) => call<Created>('POST', '/api/app/strategies', { name, source })
export const importTemplate = (filename: string) => call<Created>('POST', '/api/app/strategies/import', { filename })

export const fetchBacktests = (s?: AbortSignal) => get<BacktestsData>('/api/app/backtests', s)
export const fetchBacktest = (id: string, s?: AbortSignal) => get<BacktestDetail>(`/api/app/backtests/${enc(id)}`, s)
export interface NewBacktest { strategy_id: string; start: string; end: string; cash: number; params: Record<string, unknown>; entry_fill: 'open' | 'continuous'; jq_compat: boolean }
export const createBacktest = (b: NewBacktest) => call<Created>('POST', '/api/app/backtests', b)
export const cancelBacktest = (id: string) => call<Cancelled>('POST', `/api/app/backtests/${enc(id)}/cancel`)
export const backtestFileUrl = (name: string, file: string) => `/api/app/backtests/${enc(name)}/files/${enc(file)}`

export const fetchResearchJobs = (s?: AbortSignal) => get<ResearchJobsData>('/api/app/research-jobs', s)
export const submitResearchJob = (task: string, parameters: Record<string, unknown>, timeout_seconds?: number) =>
  call<Created>('POST', '/api/app/research-jobs', { task, parameters, ...(timeout_seconds ? { timeout_seconds } : {}) })
export const cancelResearchJob = (id: string) => call<Cancelled>('POST', `/api/app/research-jobs/${enc(id)}/cancel`)
export const resubmitResearchJob = (id: string) => call<Resubmitted>('POST', `/api/app/research-jobs/${enc(id)}/resubmit`)
export const researchArtifactUrl = (id: string, path: string) =>
  `/api/app/research-jobs/${enc(id)}/files/${path.split('/').map(enc).join('/')}`
