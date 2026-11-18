// 由 scripts/gen_frontend_contracts.py 生成，勿手改。契约来源：src/qsys/webapp/contracts/

export interface Board {
  schema_version: 1
  available: true
  message: string
  day: string
  days: string[]
  mode: "live" | "final" | "history"
  provisional: boolean
  at: string | null
  times: string[]
  meta: BoardMeta
  sentiment: BoardSentiment | null
  columns: BoardColumn[]
  rows: LadderRow[]
  cards: Record<string, BoardCard>
  compare: CompareGroup[]
  compare_unknown: number
  shadow: BoardShadow | null
  timeline: BoardEvent[]
  curves: ConceptCurve[]
  mismatch_count: number
  effect: MoneyEffect | null
  layers: LayerRow[]
  schedule: MarketSchedule | null
  errors: Record<string, "读取失败">
}

export interface BoardCard {
  symbol: string
  name: string
  status: "sealed" | "unknown" | "broken" | "touched" | "rush"
  column: string | null
  height: number | null
  prev_height: number | null
  first_seal: string | null
  open_times: number
  resealed: boolean
  pct: number | null
  is_st: boolean | null
  is_bj: boolean
  entered_at: string
  reason: string
  diffs: Record<string, string>
}

export interface BoardColumn {
  code: string
  name: string
  sealed_count: number | null
  first_seal: string | null
  gradient: Record<string, number>
}

export interface BoardEvent {
  at: string
  symbol: string
  name: string
  kind: string
  detail: string
}

export interface BoardMeta {
  prev_day: string | null
  concept_fetched_at: string | null
  concept_error: string | null
  st_published: boolean
  heights_error: string | null
  compacted_at: string | null
  compact_error: string | null
  reconciled_at: string | null
  reconcile_error: string | null
  has_raw: boolean
}

export interface BoardSentiment {
  published_at: string
  quotes: number
  sealed: number
  limit_down: number
  touched: number
  broken: number
  seal_rate: number | null
  max_height: number | null
  promote_1_2: [number | null, number | null]
  promote_2_3: [number | null, number | null]
  promote_3_up: [number | null, number | null]
  rushing: number
  missing_limit: number
  seal_unknown: number
}

export interface BoardSeries {
  schema_version: 1
  available: boolean
  message: string
  symbol: string
  name: string
  limit_up: number | null
  prev_close: number | null
  first_seal: string | null
  points: SeriesPoint[]
  events: SeriesEvent[]
}

export interface BoardShadow {
  state: string
  message: string
  prediction_date: string | null
  model_version: string | null
  precision_at_20: number | null
  scored: number
  hits: number | null
}

export interface BoardUnavailable {
  schema_version: 1
  available: false
  message: string
  day: string | null
  days: string[]
}

export interface CompareGroup {
  level: number
  items: CompareItem[]
  promoted: number
}

export interface CompareItem {
  symbol: string
  name: string
  pct: number | null
  column: string | null
  status: "promoted" | "broken" | "rush" | "limit_down" | "unknown" | "missed"
  first_seal: string | null
  open_times: number | null
  prediction: number | null
}

export interface ConceptCurve {
  code: string
  name: string
  points: [string, number][]
}

export interface EffectItem {
  symbol: string
  name: string
  pct: number | null
  status: "promoted" | "broken" | "rush" | "limit_down" | "unknown" | "missed"
}

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

export interface LadderRow {
  key: string
  label: string
  yesterday: number | null
  cells: Record<string, string[]>
}

export interface LayerRow {
  level: number
  total: number
  promoted: number
  broken: number
  break_up: number
  break_down: number
  limit_down: number
  unknown: number
  no_quote: number
  rate: number | null
  prev_rate: number | null
  avg_pct: number | null
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
  schema_version: 1
  enabled: false
}

export interface MarketOverview {
  schema_version: 1
  enabled: true
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

export interface MoneyEffect {
  total: number
  priced: number
  no_quote: number
  avg_pct: number | null
  red_rate: number | null
  multi_avg_pct: number | null
  big_loss: number
  top_level: number
  top_items: EffectItem[]
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

export interface ResearchBoard {
  schema_version: 1
  state: "ready" | "unavailable" | "not_found"
  message: string
  summary: ResearchSummary | null
  selected: ResearchDay | null
  history: ResearchHistoryDay[]
  monthly: ResearchMonth[]
}

export interface ResearchDay {
  prediction_date: string
  target_session: string
  split: "TEST" | "OBS"
  precision_at_20: number
  touch_precision_at_20: number
  entries: ResearchEntry[]
}

export interface ResearchEntry {
  rank: number
  symbol: string
  name: string
  board: string
  p_touch: number
  p_seal_given_touch: number
  p_close_limit: number
  actual_touched: boolean
  actual_closed_limit: boolean
  opened_at_limit: boolean
  one_price_limit: boolean
}

export interface ResearchHistoryDay {
  prediction_date: string
  target_session: string
  split: "TEST" | "OBS"
  precision_at_20: number
  touch_precision_at_20: number
}

export interface ResearchMonth {
  month: string
  split: "TEST" | "OBS"
  precision_at_20: number
  predictions: number
  hits: number
}

export interface ResearchSummary {
  generated_at: string | null
  source_sha: string
  data_sha: string
  first_prediction_date: string
  last_prediction_date: string
  sessions: number
  test_sessions: number
  obs_sessions: number
  test_precision_at_20: number
  obs_precision_at_20: number
  obs_baseline_precision_at_20: number
  obs_lift_at_20: number
  obs_month_win_rate: number
  promoted_to_shadow: boolean
}

export interface SeriesEvent {
  at: string
  kind: string
  detail: string
}

export interface SeriesPoint {
  at: string
  price: number
  volume: number | null
}

export interface ShadowBoard {
  schema_version: 1
  state: "unavailable" | "waiting" | "generating" | "ready" | "stale" | "failed"
  message: string
  historical: boolean
  snapshot: ShadowSnapshot | null
  history: ShadowDay[]
  missing: Record<string, string>
}

export interface ShadowDay {
  prediction_date: string
  target_session: string
  generated_at: string
  status: "waiting" | "generating" | "ready" | "failed"
  model_version: string
  entry_count: number
  precision_at_20: number | null
}

export interface ShadowEntry {
  rank: number
  symbol: string
  name: string
  board: string
  p_touch: number
  p_seal_given_touch: number
  p_close_limit: number
  actual_touched: boolean | null
  actual_closed_limit: boolean | null
  opened_at_limit: boolean | null
  one_price_limit: boolean | null
}

export interface ShadowSnapshot {
  prediction_date: string
  target_session: string
  generated_at: string
  status: "waiting" | "generating" | "ready" | "failed"
  model_version: string
  eligible_count: number
  rolling_precision_at_20: number | null
  calibration_error: number | null
  drift_score: number | null
  entries: ShadowEntry[]
}

export interface ShellStatus {
  schema_version: 1
  checked_at: string
  kill: KillStatus
  live: LivePosture
  agent_heartbeat_at: string | null
  qmt: null
  alerts_open: number | null
  missing: Record<string, string>
}
