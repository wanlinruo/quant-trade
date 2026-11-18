import { getJson } from './client'

/** /api/app/board 的类型（字段见 src/qsys/web/limit_up_board.py BoardView 与 app_board.py）。 */

export type BoardMode = 'live' | 'final' | 'history'
export type CardStatus = 'sealed' | 'unknown' | 'broken' | 'touched' | 'rush'
export type CompareStatus = 'promoted' | 'broken' | 'rush' | 'limit_down' | 'unknown' | 'missed'
type Pair = [number | null, number | null]

export interface Sentiment {
  published_at: string; quotes: number; sealed: number; limit_down: number; touched: number; broken: number
  seal_rate: number | null; max_height: number | null
  promote_1_2: Pair; promote_2_3: Pair; promote_3_up: Pair
  rushing: number; missing_limit: number; seal_unknown: number
}

export interface Card {
  symbol: string; name: string; status: CardStatus; column: string | null
  height: number | null; prev_height: number | null; first_seal: string | null; open_times: number
  resealed: boolean; pct: number | null; is_st: boolean | null; is_bj: boolean
  entered_at: string; reason: string; diffs: Record<string, unknown>
}

export interface Column { code: string; name: string; sealed_count: number | null; first_seal: string | null; gradient: Record<string, number> }
export interface LadderRow { key: string; label: string; yesterday: number | null; cells: Record<string, string[]> }

export interface CompareItem {
  symbol: string; name: string; pct: number | null; column: string | null; status: CompareStatus
  first_seal: string | null; open_times: number | null; prediction: number | null
}
export interface CompareGroup { level: number; items: CompareItem[]; promoted: number }

export interface ShadowInfo {
  state: string; message: string; prediction_date: string | null; model_version: string | null
  precision_at_20: number | null; scored: number; hits: number | null
}

export interface TimelineEvent { at: string; symbol: string; name: string; kind: string; detail: string }
export interface ConceptCurve { code: string; name: string; points: [string, number][] }

export interface MoneyEffect {
  total: number; priced: number; no_quote: number; avg_pct: number | null; red_rate: number | null
  multi_avg_pct: number | null; big_loss: number; top_level: number
  top_items: { symbol: string; name: string; pct: number | null; status: CompareStatus }[]
}

export interface LayerRow {
  level: number; total: number; promoted: number; broken: number; break_up: number; break_down: number
  limit_down: number; unknown: number; no_quote: number; rate: number | null; prev_rate: number | null; avg_pct: number | null
}

export interface BoardMeta {
  prev_day: string | null; concept_fetched_at: string | null; concept_sha: string | null; concept_error: string | null
  st_published: number | boolean | null; heights_error: string | null; compacted_at: string | null; compact_error: string | null
  reconciled_at: string | null; reconcile_error: string | null; has_raw?: boolean
}

export interface BoardData {
  available: boolean; message: string
  day?: string | null; days?: string[]; mode?: BoardMode | null; at?: string | null; times?: string[]
  status?: Record<string, unknown>; meta?: BoardMeta | Record<string, never>
  sentiment?: Sentiment | null; columns?: Column[]; rows?: LadderRow[]; cards?: Record<string, Card>
  compare?: CompareGroup[]; compare_unknown?: number; shadow?: ShadowInfo | null
  timeline?: TimelineEvent[]; curves?: ConceptCurve[]; mismatch_count?: number
  provisional?: boolean; prev_day?: string | null; effect?: MoneyEffect | null; layers?: LayerRow[]
  errors?: Record<string, string>
  schedule?: { checked_at: string; is_trading_day: boolean; is_open: boolean; next_open_at: string | null } | null
}

export interface SeriesData {
  available: boolean; message: string; symbol: string; name?: string
  limit_up?: number | null; prev_close?: number | null; first_seal?: string | null
  points?: { at: string; price: number; volume: number | null }[]
  events?: { at: string; kind: string; detail: string }[]
}

export function fetchBoard(day: string | null, at: string | null, signal?: AbortSignal): Promise<BoardData> {
  const q = new URLSearchParams()
  if (day) q.set('day', day)
  if (day && at) q.set('at', `${day}T${at}`)
  const s = q.toString()
  return getJson<BoardData>(`/api/app/board${s ? `?${s}` : ''}`, signal)
}

export function fetchBoardSeries(day: string, symbol: string, signal?: AbortSignal): Promise<SeriesData> {
  const q = new URLSearchParams({ day, symbol })
  return getJson<SeriesData>(`/api/app/board/series?${q}`, signal)
}
