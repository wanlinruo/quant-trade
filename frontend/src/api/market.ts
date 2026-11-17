/** /api/app/market 的类型（字段与后端 MarketOverviewSnapshot 一致，见 src/qsys/live/messages.py）。 */

export interface IndexQuote { symbol: string; name: string; price: number; change_pct: number; amount: number }

export interface ExchangeCoverage {
  market: 'SH' | 'SZ' | 'BJ'; expected: number | null; received: number; valid: number; suspended: number
  missing: number; invalid: number; other_session: number; missing_samples: string[]
  source_at_min: string | null; source_at_max: string | null
}

export interface MarketCoverage {
  status: 'complete' | 'incomplete' | 'unknown'; universe_source: string | null; universe_session: string | null
  universe_sha256: string | null; expected: number | null; excluded_non_a: number | null
  invalid_identity_count: number | null; unexpected_count: number | null; by_market: ExchangeCoverage[]
}

export interface MarketSnapshot {
  session: string; published_at: string; indices: IndexQuote[]
  valid_stock_count: number; advance_count: number; decline_count: number; flat_count: number
  limit_up_count: number; limit_down_count: number; touched_limit_up_count: number; broken_board_count: number
  broken_board_rate: number; market_advance_ratio: number; market_median_return_1d: number; market_total_money: number
  limit_price_missing_count: number; limit_up_unknown_count: number | null; limit_down_unknown_count: number | null
  coverage: MarketCoverage | null
}

export interface LimitCheck {
  session?: string; checked?: number; mismatched?: number
  samples?: { symbol: string; tushare: unknown; qmt: unknown }[]
  unbounded_equivalent?: number; missing_in_qmt?: number
  missing_samples?: { symbol: string; tushare: unknown }[]
  missing_in_roster?: string[]; missing_outside_roster?: string[]; missing_split_complete?: boolean
}

export type RegimeRow = { dt: string } & Record<string, number | string | null>

export interface MarketPayload {
  enabled: true
  live: { available: boolean; stale: boolean; snapshot: MarketSnapshot | null } | null
  schedule: { checked_at: string; is_trading_day: boolean; is_open: boolean; next_open_at: string | null } | null
  limit_check: LimitCheck | null
  regime: RegimeRow[] | null
  regime_columns: string[]
  missing: { index_intraday: string; return_distribution: string }
  errors: Record<string, string>
}

export type MarketResponse = MarketPayload | { enabled: false }

export async function fetchMarket(signal: AbortSignal): Promise<MarketResponse> {
  let r: Response
  try {
    r = await fetch('/api/app/market', { cache: 'no-store', signal })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new Error('市场接口无法访问')
  }
  if (!r.ok) throw new Error(`市场接口返回 ${r.status}`)
  const body = await r.json()
  if (!body || typeof body.enabled !== 'boolean') throw new Error('市场接口格式不符')
  return body as MarketResponse
}
