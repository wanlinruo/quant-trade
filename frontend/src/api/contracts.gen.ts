// 由 scripts/gen_frontend_contracts.py 生成，勿手改。契约来源：src/qsys/webapp/contracts/

export interface AckInfo {
  by: string
  acked_at: string
  through: string
}

export interface AckResult {
  schema_version: 1
  ack: AckInfo
}

export interface AlertCenter {
  schema_version: 1
  enabled: true
  state: "ok" | "unknown"
  day: string
  window_start: string
  window_end: string
  unhandled: number | null
  unhandled_critical: number | null
  acknowledged: number | null
  notices: number | null
  items: AlertItem[]
  trend: AlertTrendDay[]
  missing: Record<string, string>
}

export interface AlertDetail {
  schema_version: 1
  enabled: true
  item: AlertItem
  events: AlertEventInfo[]
}

export interface AlertEventInfo {
  event_id: string
  occurred_at: string
  severity: "INFO" | "WARNING" | "CRITICAL"
  state: "firing" | "resolved" | "notice"
  title: string
  detail: string
  evidence: Record<string, JsonValue>
  delivered: number
  failed: string[]
}

export interface AlertItem {
  key: string
  subject: string
  source: string
  kind: "incident" | "notice" | "unregistered"
  severity: "INFO" | "WARNING" | "CRITICAL"
  status: "unhandled" | "acknowledged" | "resolved" | "notice"
  first_at: string
  last_at: string
  resolved_at: string | null
  count: number
  title: string
  detail: string
  description: string | null
  ack: AckInfo | null
}

export interface AlertTrendDay {
  day: string
  critical: number
  warning: number
  info: number
  resolved: number
}

export interface AlertsDisabled {
  schema_version: 1
  enabled: false
}

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

export interface ComponentRelease {
  id: string
  label: string
  current_sha: string | null
  rollback_sha: string | null
  released_at: string | null
  state: "ok" | "warning" | "failed" | "running" | "fixed" | "paused" | "unknown" | "not_due"
  three_way_consistent: boolean | null
  workflow_id: string | null
  deployment_id: string | null
  record_id: string | null
  steps: ReleaseStep[]
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

export interface DiskInfo {
  id: string
  label: string
  mount: string
  free_bytes: number | null
  total_bytes: number | null
  fail_below_bytes: number | null
  state: "ok" | "warning" | "failed" | "running" | "fixed" | "paused" | "unknown" | "not_due"
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

export interface EquityPoint {
  day: string
  total_value: number | null
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

export interface FieldCounts {
  total: number
  passed: number
  blocking: number
  warning: number
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

export interface KillReviewInfo {
  reviewed_at: string
  overall: "ready" | "blocked" | "needs_confirmation"
  engaged: boolean
  stale: boolean
  items: KillReviewItemInfo[]
}

export interface KillReviewItemInfo {
  id: string
  label: string
  state: "pass" | "fail" | "unknown" | "confirm" | "info"
  detail: string
  observed: Record<string, JsonValue>
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

export interface LinkInfo {
  id: string
  label: string
  state: "ok" | "warning" | "failed" | "running" | "fixed" | "paused" | "unknown" | "not_due"
  last_ok_at: string | null
  latency_ms: MetricPoint[]
  last_error: string | null
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

export interface LiveTradingOverview {
  schema_version: 1
  enabled: true
  day: TradingDayInfo
  posture: "trading" | "reduce_only" | "closed" | "unknown"
  posture_label: string
  observation: MoneyObservation
  positions: TradingPosition[]
  heartbeats: TradingHeartbeat[]
  kill: KillStatus
  risks: RiskLimit[]
  equity: EquityPoint[]
  order_count: number | null
  missing: Record<string, string>
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

export interface MetricPoint {
  at: string
  value: number | null
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

export interface MoneyObservation {
  at: string | null
  cash: number | null
  market_value: number | null
  total_value: number | null
  return_ratio: null
}

export interface MonthReturn {
  month: string
  value: number | null
}

export interface NodeInfo {
  id: string
  label: string
  platform: string
  state: "ok" | "warning" | "failed" | "running" | "fixed" | "paused" | "unknown" | "not_due"
  cpu: MetricPoint[]
  memory: MetricPoint[]
  io: MetricPoint[]
  disks: DiskInfo[]
}

export interface NodesView {
  schema_version: 1
  enabled: true
  captured_at: string
  ttl_seconds: number
  stale: boolean
  nodes: NodeInfo[]
  links: LinkInfo[]
  processes: ProcessInfo[]
  security: SecurityCheck[]
  missing: Record<string, string>
}

export interface OperationsDisabled {
  schema_version: 1
  enabled: false
}

export interface OrderStage {
  state: string
  at: string | null
  detail: string | null
}

export interface OverviewTable {
  id: string
  name: string
  layer: string
  domain: DomainRef
  status: "red" | "yellow" | "green" | "gray"
  dimensions: Record<string, "red" | "yellow" | "green" | "gray">
}

export interface PaperAccountDetail {
  id: string
  name: string
  strategy_name: string
  version_id: string
  status: string
  activation_date: string
  error: string | null
  total_value: number | null
  return_ratio: number | null
  max_drawdown_ratio: number | null
  position_count: number | null
  schema_version: 1
  enabled: true
  backtest_id: string
  strategy_id: string
  initial_cash: number
  cash: number
  notify_mask: string | null
  positions: Record<string, JsonValue>[]
  orders: Record<string, JsonValue>[]
  fills: Record<string, JsonValue>[]
  rejects: Record<string, JsonValue>[]
  events: Record<string, JsonValue>[]
  logs: Record<string, JsonValue>[]
  runs: StrategyRunInfo[]
  equity: PaperEquityPoint[]
  source_backtest: PaperBacktestPoint[]
  day: TradingDayInfo
  missing: Record<string, string>
}

export interface PaperAccountRow {
  id: string
  name: string
  strategy_name: string
  version_id: string
  status: string
  activation_date: string
  error: string | null
  total_value: number | null
  return_ratio: number | null
  max_drawdown_ratio: number | null
  position_count: number | null
}

export interface PaperAccounts {
  schema_version: 1
  enabled: true
  accounts: PaperAccountRow[]
}

export interface PaperActionResult {
  schema_version: 1
  id: string
  status: string
  notify_mask: string | null
}

export interface PaperBacktestPoint {
  day: string
  total_value: number | null
  return_ratio: number | null
}

export interface PaperEquityPoint {
  at: string | null
  day: string
  total_value: number
  strategy_ratio: number | null
  benchmark_ratio: number | null
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

export interface ProcessInfo {
  id: string
  label: string
  node_id: string
  state: "ok" | "warning" | "failed" | "running" | "fixed" | "paused" | "unknown" | "not_due"
  pid: number | null
  restart_count: number | null
  identity: string | null
  resource_limit: string | null
  last_log: string | null
}

export interface QualityRun {
  run_id: string
  checked_at: string | null
  fields: FieldCounts
  tables: Record<string, number> | null
}

export interface QualitySummary {
  schema_version: 1
  enabled: true
  runs: QualityRun[]
  warning_sources: WarningSource[]
  rules: RuleRow[]
}

export interface ReconCell {
  day: string
  layer: "cash" | "positions" | "orders"
  state: "consistent" | "inconsistent" | "unknown" | "not_started"
  at: string | null
  detail: string | null
}

export interface ReconciliationRecord {
  at: string
  layer: string
  mode: string
  consistent: boolean
  cash_diff: number | null
  mismatches: number
  detail: string
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

export interface ReleaseEvent {
  id: string
  component_id: string
  at: string
  sha: string | null
  state: "ok" | "warning" | "failed" | "running" | "fixed" | "paused" | "unknown" | "not_due"
  current: boolean
  detail: string | null
}

export interface ReleaseStep {
  id: string
  label: string
  state: "ok" | "warning" | "failed" | "running" | "fixed" | "paused" | "unknown" | "not_due"
  at: string | null
  detail: string | null
}

export interface ReleasesView {
  schema_version: 1
  enabled: true
  captured_at: string
  ttl_seconds: number
  stale: boolean
  main_sha: string | null
  consistent_components: number | null
  component_count: number | null
  components: ComponentRelease[]
  events: ReleaseEvent[]
  missing: Record<string, string>
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

export interface RiskLimit {
  id: string
  label: string
  value: number | null
  limit: number
  ratio: number | null
  unit: "ratio" | "yuan"
  state: "ok" | "warning" | "blocked" | "unknown"
}

export interface RuleDetail {
  schema_version: 1
  enabled: true
  id: string
  name: string
  category: string
  condition: string
  limitation: string
  implementation: string
  targets: string[]
  latest: RuleResult[]
  history: RuleResult[]
}

export interface RuleResult {
  run_id: string | null
  checked_at: string | null
  metric_id: string | null
  dataset_id: string | null
  upstream: string | null
  downstream: string | null
  check_id: string
  category: string | null
  dimension: string | null
  scope_start: string | null
  scope_end: string | null
  passed: boolean | null
  severity: string | null
  known_issue: string | null
  status: string | null
  evidence_at: string | null
  expires_at: string | null
  required: boolean | null
  violations: number | null
  checked_rows: number | null
  upstream_rows: number | null
  downstream_rows: number | null
  matched_rows: number | null
  orphan_rows: number | null
  detail: string | null
  input_version: string | null
  sample_file: string | null
}

export interface RuleRow {
  id: string
  name: string
  category: string
  scope: string
  known_issues: string[]
  upstream_self_consistency: boolean
  latest_status: "blocking" | "warning" | "unknown" | "passed"
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

export interface SearchIndex {
  schema_version: 1
  items: SearchItem[]
  missing: Record<string, string>
}

export interface SearchItem {
  kind: "委托" | "服务" | "策略" | "数据表" | "指标" | "告警" | "节点"
  key: string
  label: string
  hint: string
  page: string
  params: Record<string, string>
}

export interface SecurityCheck {
  id: string
  label: string
  state: "ok" | "warning" | "failed" | "running" | "fixed" | "paused" | "unknown" | "not_due"
  mismatches: string[]
  failed_logins_24h: number | null
  rejected_unauthenticated_24h: number | null
}

export interface ServiceCell {
  day: string
  state: "ok" | "warning" | "failed" | "running" | "fixed" | "paused" | "unknown" | "not_due"
  run_id: string | null
  detail: string | null
}

export interface ServiceDurationPoint {
  run_id: string
  started_at: string
  duration_seconds: number
}

export interface ServiceRow {
  id: string
  name: string
  kind: string
  cadence: string
  node: string
  schedule: string
  latest_state: "ok" | "warning" | "failed" | "running" | "fixed" | "paused" | "unknown" | "not_due"
  latest_at: string | null
  completion_ratio_30d: number | null
  duration_seconds: number | null
  average_duration_seconds: number | null
  duration_history: ServiceDurationPoint[]
  recent_runs: ServiceRunItem[]
  version: string | null
  cells: ServiceCell[]
}

export interface ServiceRunItem {
  run_id: string
  service_id: string
  session: string | null
  started_at: string
  finished_at: string | null
  state: string
  note: string
  error: string
  steps: ServiceStep[]
}

export interface ServiceStep {
  name: string
  state: string
  started_at: string | null
  finished_at: string | null
  detail: string
  affects_run: boolean
}

export interface ServicesView {
  schema_version: 2
  enabled: true
  selected_day: string
  days: string[]
  services: ServiceRow[]
  runs: ServiceRunItem[]
  missing: Record<string, string>
  trading_days: Record<string, boolean>
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

export interface StrategyRunInfo {
  event_key: string
  phase: string
  scheduled_at: string | null
  started_at: string | null
  finished_at: string | null
  duration_ms: number
  status: "completed" | "failed"
  candidates: number | null
  routed: number | null
  passed: number | null
  orders: number
  rejects: number
  summary: string
  reasons: Record<string, number>
  note: string
  error: string
  event_status: string | null
  event_detail: string | null
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

export interface TodayConcern {
  id: string
  severity: "critical" | "warning" | "info" | "unknown"
  label: string
  detail: string | null
  page: string
}

export interface TodayDisabled {
  schema_version: 1
  enabled: false
}

export interface TodayEvent {
  event_key: string
  phase: string
  scheduled_at: string | null
  started_at: string | null
  finished_at: string | null
  state: "completed" | "failed"
  summary: string
  detail: string | null
}

export interface TodayKpis {
  account_total: number | null
  account_at: string | null
  index_name: string | null
  index_price: number | null
  index_change_ratio: number | null
  limit_up: number | null
  limit_down: number | null
  seal_ratio: number | null
  link_max_age_seconds: number | null
  link_fresh: number | null
  link_total: number | null
}

export interface TodayLink {
  source: string
  points: TodayLinkPoint[]
}

export interface TodayLinkPoint {
  sampled_at: string
  age_seconds: number | null
  threshold_seconds: number | null
  fresh: boolean | null
}

export interface TodayPhase {
  id: string
  label: string
  time_label: string
  state: "ok" | "warning" | "failed" | "running" | "not_started" | "unknown"
  completed: number
  recorded: number
}

export interface TodaySentimentPoint {
  day: string
  limit_up: number | null
  limit_down: number | null
  broken_board_ratio: number | null
  prior_limitup_return_ratio: number | null
}

export interface TodayView {
  schema_version: 1
  enabled: true
  day: string
  days: string[]
  state: "running" | "closed" | "reduce_only" | "waiting" | "unknown"
  title: string
  kill: KillStatus | null
  concerns: TodayConcern[]
  kpis: TodayKpis
  phases: TodayPhase[]
  events: TodayEvent[]
  links: TodayLink[]
  sentiment: TodaySentimentPoint[]
  missing: Record<string, string>
}

export interface TradingDayInfo {
  selected: string | null
  available: string[]
}

export interface TradingDisabled {
  schema_version: 1
  enabled: false
}

export interface TradingFill {
  order_id: string
  symbol: string
  side: string
  qty: number
  price: number
  fee: number
  filled_at: string
  broker_order_id: string | null
  broker_trade_id: string | null
}

export interface TradingHeartbeat {
  source: string
  at: string | null
  age_seconds: number | null
  threshold_seconds: number | null
  fresh: boolean | null
  detail: string | null
}

export interface TradingOrder {
  id: string
  event_key: string
  symbol: string
  side: string
  qty: number
  remaining_qty: number
  order_type: string
  limit_price: number | null
  status: string
  created_at: string
  updated_at: string | null
  broker_order_id: string | null
  broker_accepted_at: string | null
  unknown_no_retry: boolean
  stages: OrderStage[]
}

export interface TradingOrders {
  schema_version: 1
  enabled: true
  day: TradingDayInfo
  orders: TradingOrder[]
  fills: TradingFill[]
  rejects: TradingReject[]
  missing: Record<string, string>
}

export interface TradingPosition {
  symbol: string
  total_qty: number
  available_qty: number
  avg_cost: number | null
  last_price: number | null
  market_value: number | null
  reserved_qty: number | null
}

export interface TradingReconciliation {
  schema_version: 1
  enabled: true
  day: TradingDayInfo
  heatmap: ReconCell[]
  records: ReconciliationRecord[]
  risks: RiskLimit[]
  kill: KillStatus
  review: KillReviewInfo | null
  missing: Record<string, string>
}

export interface TradingReject {
  order_id: string
  symbol: string
  side: string
  qty: number
  reason: string
  at: string
}

export interface TradingRuns {
  schema_version: 1
  enabled: true
  day: TradingDayInfo
  runs: StrategyRunInfo[]
  report_text: string | null
  missing: Record<string, string>
}

export interface WarningSample {
  metric_id: string
  check_id: string
  detail: string | null
  known_issue: string | null
}

export interface WarningSource {
  key: "upstream" | "registered" | "unclassified"
  issue: string | null
  count: number
  samples: WarningSample[]
}
