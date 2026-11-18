// 由 scripts/gen_frontend_contracts.py 生成，勿手改。契约来源：src/qsys/webapp/contracts/

export interface ArtifactInfo {
  path: string
  bytes: number | null
  sha256: string | null
}

export interface BacktestJobInfo {
  id: string
  strategy_name: string
  status: string
  progress: number
  total: number
  percent: number
  current_session: string | null
  error: string | null
  config: Record<string, JsonValue>
}

export interface BacktestList {
  schema_version: 1
  enabled: true
  rows: BacktestRow[]
}

export interface BacktestPending {
  schema_version: 1
  enabled: true
  report: false
  strategy_id: string
  job: BacktestJobInfo
  log_tail: string
}

export interface BacktestReport {
  schema_version: 1
  enabled: true
  report: true
  name: string
  strategy_id: string | null
  job: BacktestJobInfo | null
  summary: RunSummary
  metrics: Record<string, number>
  series: ReturnPoint[]
  episodes: Episode[]
  monthly: MonthReturn[]
  fills: (Record<string, string | number | boolean | null>)[]
  fills_total: number
  rejects: (Record<string, string | number | boolean | null>)[]
  files: RunFileInfo[]
  warnings: string[]
}

export interface BacktestRow {
  id: string
  strategy: string
  strategy_id: string | null
  start: string | null
  end: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  duration_seconds: number | null
  status: string
  cost_model: string | null
  total_return: number | null
  benchmark_return: number | null
  max_drawdown: number | null
  has_report: boolean
  source: "web" | "cli"
  progress: number | null
  total: number | null
  current_session: string | null
}

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
  diffs: Record<string, string | number | null>
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
  points: BoardSeriesPoint[]
  events: BoardSeriesEvent[]
}

export interface BoardSeriesEvent {
  at: string
  kind: string
  detail: string
}

export interface BoardSeriesPoint {
  at: string
  price: number
  volume: number | null
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

export interface Cancelled {
  schema_version: 1
  status: "cancelled" | "cancellation-requested"
}

export interface CatalogSummary {
  total: number
  by_kind: Record<string, number>
  covered: number
  pending: number
  latest_run: string | null
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

export interface Coverage {
  rows: number
  symbols: number
  first_dt: string | null
  last_dt: string | null
  measured_at: string | null
}

export interface Created {
  schema_version: 1
  id: string
}

export interface DataDisabled {
  schema_version: 1
  enabled: false
}

export interface DataOverview {
  schema_version: 1
  enabled: true
  tables: OverviewTable[]
  domains: DomainSummary[]
  lineage: Lineage
}

export interface DataTableDetail {
  schema_version: 1
  enabled: true
  row: DataTableRow
  contract: DatasetContract
  sources: SourceRef[]
  root_sources: SourceRef[]
  dimensions: Record<string, "red" | "yellow" | "green" | "gray">
  reasons: HealthReasonInfo[]
  evidence: Record<string, JsonValue>[]
  upstream: DependencyInfo[]
  downstream: DependencyInfo[]
  fields: FieldInfo[]
  rules: string[]
}

export interface DataTableList {
  schema_version: 1
  enabled: true
  rows: DataTableRow[]
}

export interface DataTableRow {
  id: string
  name: string
  layer: string
  domain: DomainRef
  status: "red" | "yellow" | "green" | "gray"
  purpose: string
  grain: string
  coverage: Coverage | null
  coverage_state: "ready" | "pending" | "failed" | "none"
}

export interface DatasetContract {
  id: string
  name: string
  purpose: string
  layer: string
  role: string
  status: string
  grain: string
  primary_key: string[]
  time_columns: string[]
  partition_keys: string[]
  sort_keys: string[]
  schema_id: string | null
  schema_version: number
  compatibility: string
  builder: string
  update_mode: string
  finalized_at: string
  lifecycle: string
  sources: string[]
  consumers: string[]
  health_policy: string | null
  domain: DomainRef
  physical: boolean
}

export interface DependencyInfo {
  upstream: string
  downstream: string
  kind: string
  join_keys: [string, string][]
  cardinality: string
  requirement: string
  allowed_lag: string
  metrics: string[]
  health_policy: string | null
  note: string
}

export interface DirectLineage {
  source_id: string
  provider: string | null
  api: string | null
  field: string
  multiplier: number
}

export interface DomainRef {
  code: string
  unclassified_reason: string | null
}

export interface DomainSummary {
  domain: DomainRef
  tables: number
  sources: number
  status_counts: Record<string, number>
}

export interface EffectItem {
  symbol: string
  name: string
  pct: number | null
  status: "promoted" | "broken" | "rush" | "limit_down" | "unknown" | "missed"
}

export interface Episode {
  peak_date: string
  trough_date: string
  recovery_date: string | null
  depth: number
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

export interface FieldInfo {
  name: string
  type: string
  meaning: string | null
}

export interface HealthReasonInfo {
  code: string
  message: string
  dimension: "freshness" | "completeness" | "structure" | "validity" | "lineage" | null
  check_id: string | null
}

export interface IndexQuote {
  symbol: string
  name: string
  price: number
  change_pct: number
  amount: number
}

export type JsonValue = unknown

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

export interface Lineage {
  nodes: LineageNode[]
  edges: LineageEdge[]
}

export interface LineageEdge {
  source: string
  target: string
}

export interface LineageNode {
  id: string
  label: string
  kind: string
  ref: string | null
  status: "red" | "yellow" | "green" | "gray" | null
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

export interface MetricCheckRow {
  run_id: string
  checked_at: string | null
  metric_id: string
  check_id: string
  category: string | null
  scope_start: string | null
  scope_end: string | null
  passed: boolean | null
  severity: string | null
  known_issue: string | null
  violations: number | null
  checked_rows: number | null
  detail: string | null
  sample_file: string | null
}

export interface MetricDetail {
  schema_version: 1
  enabled: true
  row: MetricRow
  upstream: string[]
  downstream: string[]
  ext_consumers: string[]
  formula: string | null
  params: Record<string, JsonValue>
  checks: MetricCheckRow[]
  history: MetricCheckRow[]
  evidence: MetricEvidence[]
}

export interface MetricEvidence {
  metric_id: string
  symbol: string | null
  dt: string | null
  our_value: string | null
  reference_value: string | null
  reference_source: string | null
  verdict: string | null
  checked_by: string | null
  checked_at: string | null
  note: string | null
}

export interface MetricList {
  schema_version: 1
  enabled: true
  summary: CatalogSummary
  rows: MetricRow[]
  datasets: string[]
  kinds: string[]
}

export interface MetricRow {
  id: string
  dataset: string
  column: string
  kind: string
  validity: string
  meaning: string | null
  direct: DirectLineage | null
  roots: string[]
  status: "failed" | "unknown" | "uncovered" | "unverified" | "known_issue" | "ok"
  issues: string[]
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

export interface MonthReturn {
  month: string
  value: number | null
}

export interface OverviewTable {
  id: string
  name: string
  layer: string
  domain: DomainRef
  status: "red" | "yellow" | "green" | "gray"
  dimensions: Record<string, "red" | "yellow" | "green" | "gray">
}

export interface ParameterSpec {
  name: string
  type: "int" | "float" | "bool" | "str"
  default: number | boolean | string | null
  label: string
  description: string
  minimum: number | null
  maximum: number | null
  choices: (number | boolean | string)[]
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

export interface ResearchDisabled {
  schema_version: 1
  enabled: false
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

export interface ResearchJob {
  id: string
  state: string
  detail: string
  task: string | null
  parameters: Record<string, JsonValue>
  timeout_seconds: number | null
  created_at: string | null
  code_sha: string | null
  snapshot_id: string | null
  snapshot_content_sha256: string | null
  artifacts: ArtifactInfo[]
}

export interface ResearchJobList {
  schema_version: 1
  enabled: true
  submission_enabled: boolean
  jobs: ResearchJob[]
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

export interface Resubmitted {
  schema_version: 1
  id: string
  resubmitted_from: string
}

export interface ReturnPoint {
  date: string
  strategy: number | null
  benchmark: number | null
  excess: number | null
  drawdown: number | null
}

export interface RunFileInfo {
  name: string
  size: number
}

export interface RunSummary {
  name: string
  run_id: string
  strategy: string
  start: string | null
  end: string | null
  initial_value: number | null
  total_value: number | null
  cumulative_return: number | null
  buys: number | null
  sells: number | null
  rejects: number | null
  closed_lots: number | null
  cost_model: string | null
  entry_fill: string | null
  params: Record<string, JsonValue>
  generated_at: string | null
  source: "web" | "cli"
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

export interface SourceRef {
  kind: "source" | "table" | "note"
  id: string
  provider: string | null
  api: string | null
  status: "active" | "frozen" | "dead" | null
}

export interface StrategyDetail {
  id: string
  name: string
  updated_at: string | null
  version_id: string
  stage: "live" | "paper" | "backtest" | "draft"
  stage_label: string
  stage_detail: string
  in_use: boolean
  used_by: string[]
  latest_backtest: BacktestRow | null
  schema_version: 1
  enabled: true
  core_label: string
  source: string
  parameters: ParameterSpec[]
  versions: StrategyVersion[]
  backtests: BacktestRow[]
}

export interface StrategyList {
  schema_version: 1
  enabled: true
  core_label: string
  templates: string[]
  strategies: StrategyRow[]
}

export interface StrategyRow {
  id: string
  name: string
  updated_at: string | null
  version_id: string
  stage: "live" | "paper" | "backtest" | "draft"
  stage_label: string
  stage_detail: string
  in_use: boolean
  used_by: string[]
  latest_backtest: BacktestRow | null
}

export interface StrategyVersion {
  id: string
  created_at: string | null
}

export interface TableSample {
  schema_version: 1
  enabled: true
  available: boolean
  reason: "ok" | "logical_view" | "no_file" | "empty_file" | "read_failed"
  file: string | null
  row_group: number | null
  columns: string[]
  rows: Record<string, JsonValue>[]
  limit: number
}
