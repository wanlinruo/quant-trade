// 由 scripts/gen_frontend_contracts.py 生成，勿手改。契约来源：src/qsys/webapp/contracts/

export interface ErrorBody {
  error: ErrorDetail
}

export interface ErrorDetail {
  code: string
  message: string
}

export interface ExchangeCoverage {
  market: "SH" | "SZ" | "BJ"
  expected: number
  received: number
  valid: number
  suspended: number
  missing: number
  invalid: number
  other_session: number
  missing_samples: string[]
  source_at_min: string | null
  source_at_max: string | null
}

export interface IndexQuote {
  symbol: string
  name: string
  price: number
  change_pct: number
  amount: number
}

export interface KillSource {
  id: string
  name: string
  state: string
  reason: string
  recorded_at: string | null
}

export interface KillStatus {
  state: "stopped" | "clear" | "unknown"
  label: string
  message: string
  sources: KillSource[]
}

export interface LimitCheck {
  session: string
  checked_at: string | null
  checked: number
  mismatched: number
  unbounded_equivalent: number
  missing_in_qmt: number
  samples: LimitCheckSample[]
  missing_samples: LimitCheckSample[]
  missing_in_roster: string[] | null
  missing_outside_roster: string[] | null
  missing_split_complete: boolean | null
}

export interface LimitCheckSample {
  symbol: string
  tushare: LimitPair | null
  qmt: LimitPair | null
}

export interface LimitPair {
  up: number | null
  down: number | null
}

export interface LiveMarket {
  available: boolean
  stale: boolean
  snapshot: MarketSnapshot | null
}

export interface LivePosture {
  state: "trading" | "reduce_only" | "unknown"
  label: string
}

export interface MarketCoverage {
  status: "complete" | "incomplete" | "unknown"
  universe_source: string | null
  universe_session: string | null
  expected: number | null
  excluded_non_a: number | null
  invalid_identity_count: number | null
  unexpected_count: number | null
  by_market: ExchangeCoverage[]
}

export interface MarketDisabled {
  schema_version?: 1
  enabled?: false
}

export interface MarketOverview {
  schema_version?: 1
  enabled?: true
  live: LiveMarket | null
  schedule: MarketSchedule | null
  limit_check: LimitCheck | null
  regime: RegimeDay[] | null
  missing: Record<string, string>
  errors: Record<string, "读取失败">
}

export interface MarketSchedule {
  checked_at: string
  is_trading_day: boolean
  is_open: boolean
  next_open_at: string | null
}

export interface MarketSnapshot {
  session: string
  published_at: string
  indices: IndexQuote[]
  valid_stock_count: number
  advance_count: number
  decline_count: number
  flat_count: number
  limit_up_count: number
  limit_down_count: number
  touched_limit_up_count: number
  broken_board_count: number
  broken_board_rate: number
  market_advance_ratio: number
  market_median_return_1d: number
  market_total_money: number
  limit_price_missing_count: number
  limit_up_unknown_count: number | null
  limit_down_unknown_count: number | null
  coverage: MarketCoverage | null
}

export interface RegimeDay {
  dt: string
  limit_up_count: number | null
  limit_down_count: number | null
  limit_up_down_ratio: number | null
  touched_limit_up_count: number | null
  broken_board_count: number | null
  broken_board_rate: number | null
  first_board_count: number | null
  market_advance_ratio: number | null
  market_median_return_1d: number | null
  market_total_money: number | null
  prior_limitup_nextday_return_mean: number | null
  prior_limitup_nextday_return_median: number | null
  valid_stock_count: number | null
  index_ma20_slope_5d: number | null
}

export interface ShellStatus {
  schema_version?: 1
  checked_at: string
  kill: KillStatus
  live: LivePosture
  agent_heartbeat_at: string | null
  qmt?: null
  alerts_open?: number | null
  missing: Record<string, string>
}
