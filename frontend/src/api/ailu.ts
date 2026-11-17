/** AI 涨停：复用现有只读接口（字段见 src/qsys/web/limit_up_shadow.py、limit_up_research.py）。 */

export type ViewState = 'unavailable' | 'waiting' | 'generating' | 'ready' | 'stale' | 'failed'

export interface ShadowEntry {
  rank: number; symbol: string; name: string; board: string; market_state: string
  p_touch: number; p_seal_given_touch: number; p_close_limit: number
  actual_touched: boolean | null; actual_closed_limit: boolean | null; opened_at_limit: boolean | null; one_price_limit: boolean | null
}

export interface ShadowSnapshot {
  prediction_date: string; target_session: string; generated_at: string; status: 'waiting' | 'generating' | 'ready' | 'failed'
  model_version: string; model_sha: string; data_version: string; data_sha: string; eligible_count: number
  rolling_precision_at_20: number | null; calibration_error: number | null; drift_score: number | null; error: string
  entries: ShadowEntry[]; limit_up_scores: unknown[]
}

export interface ShadowHistory {
  prediction_date: string; target_session: string; generated_at: string; status: string
  model_version: string; entry_count: number; precision_at_20: number | null
}

export interface ShadowView {
  state: ViewState; available: boolean; historical: boolean; message: string
  snapshot: ShadowSnapshot | null; history: ShadowHistory[]
}

export interface ResearchEntry extends Omit<ShadowEntry, 'actual_touched' | 'actual_closed_limit' | 'opened_at_limit' | 'one_price_limit'> {
  actual_touched: boolean; actual_closed_limit: boolean; opened_at_limit: boolean; one_price_limit: boolean
}

export interface ResearchDay {
  prediction_date: string; target_session: string; split: 'TEST' | 'OBS'
  precision_at_20: number; touch_precision_at_20: number; entries: ResearchEntry[]
}

export interface ResearchView {
  state: 'ready' | 'unavailable' | 'not_found'; available: boolean; historical_research: boolean; message: string
  selected: ResearchDay | null
  summary: {
    generated_at: string; source_sha: string; data_sha: string; first_prediction_date: string; last_prediction_date: string
    sessions: number; test_sessions: number; obs_sessions: number; test_precision_at_20: number; obs_precision_at_20: number
    obs_baseline_precision_at_20: number; obs_lift_at_20: number; obs_month_win_rate: number; promoted_to_shadow: boolean
  } | null
  history: { prediction_date: string; target_session: string; split: 'TEST' | 'OBS'; precision_at_20: number; touch_precision_at_20: number }[]
  monthly: { month: string; split: 'TEST' | 'OBS'; precision_at_20: number; predictions: number; hits: number }[]
}

async function getJson<T>(url: string, label: string, signal?: AbortSignal): Promise<T> {
  let r: Response
  try {
    r = await fetch(url, { cache: 'no-store', signal })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new Error(`${label}接口无法访问`)
  }
  if (!r.ok) throw new Error(`${label}接口返回 ${r.status}`)
  return r.json() as Promise<T>
}

const q = (day?: string | null) => (day ? `?day=${encodeURIComponent(day)}` : '')

export const fetchShadow = (day?: string | null, signal?: AbortSignal) =>
  getJson<ShadowView>(`/api/market/limit-up-shadow${q(day)}`, '影子榜单', signal)

export const fetchResearch = (day?: string | null, signal?: AbortSignal) =>
  getJson<ResearchView>(`/api/market/limit-up-research${q(day)}`, '研究回测', signal)
