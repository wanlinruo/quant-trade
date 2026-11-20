# Web 改版 R4：数据组迁到新契约与查询层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 数据组四页（数据总览、数据表、指标字典、数据质量）的接口改由 `qsys.webapp` 提供显式契约。查询层直接使用 `qsys.data.catalog` 领域对象与 `WarehouseQuery`，只输出代码与事实；状态、维度、域等显示文案移到前端。

**Architecture:**
- **`queries/data_assets.py`（`DataAssets`）：** 表清单、表详情、表样本、数据总览。
  - 覆盖统计的后台刷新逻辑自旧 `DataAssetService` 迁入，行为不变：pending / failed，失败 30 秒后重试。
  - 健康评估用 `current_result` + `aggregate_health` + `policy_for`。
- **`queries/data_catalog.py`（`DataCatalog`）：** 指标清单、指标详情。指标状态判定自旧 `CatalogService._status` 原样迁入。
- **`queries/data_quality.py`（`DataQuality`）：** 质检汇总（近 30 次运行、警告来源、规则表）、规则详情、违规样本下载。
  - `quality_runs`、`warning_sources`、`lineage_graph`、`table_sample` 自 `web/data_app_views.py` 迁入。
- **`routes/data.py`：** 薄路由，全部只读。
- **前端 `pages/data/labels.ts`：** 文案映射。
- **删除：** `web/app_data.py`、`web/data_app_views.py`。
- **旧服务不动：** `asset_views`、`catalog_views`、`domain_views` 继续供旧页面使用，合入时随旧页面删除。

**Tech Stack:** FastAPI、pydantic 2、polars、pyarrow、pytest；前端不新增依赖。

**Spec:**
- `docs/superpowers/plans/2026-11-18-web-redesign-roadmap-v2.md`：第 1 节、第 2 节 `catalog_views` / `asset_views` / `domain_views` 一行、第 4 节 R4
- `docs/superpowers/specs/2026-11-17-web-redesign-design.md` §8.13
- `docs/superpowers/plans/2026-11-18-web-redesign-09-data.md`：已定口径
- R1–R3 统一约定

## Global Constraints

- **沿用 R1 统一约定：**
  - 时间带时区；缺失为 `null`；
  - 错误体统一；GET 一律 no-store；
  - 契约 `extra="forbid"`，带 `schema_version`，契约名全局唯一，登记进 `EXPORTED`。
- **查询层不含展示口径：**
  - 状态、维度、指标状态、数据源状态、域只输出代码；
  - 中文名由前端 `labels.ts` 映射；
  - 数据表名、指标含义、数据源的 provider / api 是目录登记的事实，照常输出。
- **09 已定口径不变：**
  - 灰为缺证据，不当正常；
  - 近 30 次质检按 `metric_checks.run_id` 汇总；没有表级记录的 run 记 `tables=null`，不当 0；
  - 警告来源只看最近一次质检，分三类：上游自相矛盾 #653 / 已登记 #594 / 待归类；
  - 表样本只取最新分区最后一个行组的前 20 行，null 原样输出；
  - 列表类接口走 60 秒单飞缓存，缓存键只收合法筛选值。
- **修复遗留：** 规则表中关系类规则（R1/R2）的最新状态读取 `relation_checks`（#662 09 遗留）。
- **违规样本下载：**
  - 改走 `GET /api/app/data/quality/samples/{run_id}/{file}`；
  - 只允许 `.parquet`，用 `controlled_file`，根目录为 `CatalogService` 同一 `sample_root`；
  - 契约给出 `sample_file`（`<run_id>/<check_id>.parquet`）。只有记录的 `sample_path` 最后两级恰为该形状时才给出，否则为 null。
- **不搬迁的内容：** 表详情不再计算结构建议（advise_split / advise_pair），新页面没有使用。
- 旧 `/data` 页的退市整理期股票池与全仓覆盖总数仍待维护者在合入时决定（不在本计划内）。
- 不改任何生产组件。

## Review Focus

1. **覆盖统计尚未就绪或刷新失败：**
   - 表清单返回 `coverage=null` 并带 `coverage_state="pending"|"failed"`；
   - 页面显示“统计中 / 统计失败”，不显示 0 行。

   归 Task 1 `test_tables_coverage_pending_and_failed`。
2. **表的健康证据完全缺失：** 五维全部为 gray，总体状态为 gray，排序在 red、yellow 之后、green 之前。归 Task 1 `test_missing_evidence_is_gray_everywhere`。
3. **样本路径异常：** 记录的 `sample_path` 不在样本根下、形状不对，或请求的 `run_id` / 文件名带 `..`。契约给出 null，下载返回 404。归 Task 3 `test_sample_file_shape_and_download_guard`。
4. **筛选参数非法：**
   - 未知 status 返回 422 统一错误体；
   - 未知 dataset / kind 返回空结果，且不进入缓存。

   归 Task 2 `test_catalog_filters_validate_and_do_not_grow_cache`。
5. **关系规则只有 `relation_checks` 结果：** 规则表的最新状态取自 `relation_checks`，不显示 unknown。归 Task 3 `test_relation_rule_latest_status_from_relation_checks`。

---

### Task 1: 数据资产查询（总览、表清单、表详情、样本）

**Files:**
- Create: `src/qsys/webapp/contracts/data.py`（本任务写资产部分）、`src/qsys/webapp/queries/data_assets.py`
- Move: `src/qsys/web/data_app_views.py` → `src/qsys/webapp/queries/data_derive.py`；`tests/web/test_data_app_views.py` → `tests/webapp/test_webapp_data_derive.py`
- Test: `tests/webapp/test_webapp_data_assets.py`

**契约（资产部分）：**
- 类型别名：
  - `Health = Literal["red","yellow","green","gray"]`
  - `Dimension = Literal["freshness","completeness","structure","validity","lineage"]`
- `DomainRef{code: str, unclassified_reason: str | None}`：`code` 为 `DataDomain` 值，未归类时为 `"unclassified"`。
- `SourceRef{kind: Literal["source","table","note"], id: str, provider: str | None, api: str | None, status: Literal["active","frozen","dead"] | None}`：分类沿用 `classify_entry`。
- `Coverage`：对应 `qsys.data.coverage.DatasetCoverage` 的全部字段，时间为 `Timestamp`，日期为 `date`。
- `DataTableRow{id, name, layer, domain: DomainRef, status: Health, purpose, grain, coverage: Coverage | None, coverage_state: Literal["ready","pending","failed","none"]}`：
  - `ready` 表示有统计；
  - `none` 表示没有统计且不在待刷新中（不是物理表，或尚无文件）。
- `DataTableList{schema_version, enabled: Literal[True], rows}`
- `DatasetContract`：`Dataset` 的显式字段，与旧前端类型 `DatasetContract` 同名同义，`domain` 改为 `DomainRef`。
- `DependencyInfo{upstream, downstream, kind, join_keys: list[tuple[str, str]], cardinality, requirement, allowed_lag, metrics: list[str], note}`
- `HealthReasonInfo{code, message, dimension: Dimension | None, check_id: str | None}`
- `FieldInfo{name, type, meaning: str | None}`：没有登记含义时为 null（旧文案“尚无字段说明”移到前端）。
- `DataTableDetail{schema_version, enabled, row: DataTableRow, contract: DatasetContract, sources: list[SourceRef], root_sources: list[SourceRef], dimensions: dict[Dimension, Health], reasons, evidence: list[dict[str, JsonValue]], upstream, downstream, fields, rules: list[str]}`：
  - `dimensions` 五维齐全，缺证据的维度为 gray。
- `TableSample{schema_version, enabled, available: bool, reason: Literal["ok","logical_view","no_file","empty_file","read_failed"], file: str | None, row_group: int | None, columns: list[str], rows: list[dict[str, JsonValue]], limit: int}`：原 `message` 文案改为 `reason` 代码。
- `OverviewTable{id, name, layer, domain: DomainRef, status: Health, dimensions: dict[Dimension, Health]}`
- `DomainSummary{domain: DomainRef, tables: int, sources: int, status_counts: dict[Health, int]}`
- `LineageNode{id, label, kind, ref: str | None, status: Health | None}`、`LineageEdge{source, target}`
- `DataOverview{schema_version, enabled, tables, domains, lineage: {nodes, edges}}`：不再携带 `dimension_labels` 与 `status_labels`。
- `DataDisabled{schema_version, enabled: Literal[False]}`

**Interfaces（`DataAssets(warehouse: Warehouse, *, background_coverage: bool = False)`）：**
- `root -> Path`、`query -> WarehouseQuery`
- `assessment(dataset_id)`：`aggregate_health` 的结果
- `overview() -> DataOverview`
- `tables(include_raw: bool) -> DataTableList`
- `table(dataset_id) -> DataTableDetail | None`
- `sample(dataset_id) -> TableSample | None`
- 排序沿用旧口径：red → yellow → gray → green，再按 layer、id。

- [ ] **Step 1: 写失败测试**（迁移 `tests/web/test_app_data.py` 中总览、表、样本相关的用例，并改为新契约断言），新增：
  - `test_tables_coverage_pending_and_failed`
  - `test_missing_evidence_is_gray_everywhere`
  - `test_overview_has_no_display_labels`：响应文本不含“红·阻断”、“时效”等文案。
  - `test_table_detail_dimensions_complete_and_sources_structured`
  - `test_sample_reason_codes`
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 运行 `uv run pytest tests/webapp tests/web -q`，全部通过。
- [ ] **Step 5: Commit：** `refactor(web): 数据资产查询层重写，只输出代码与事实（#662）`

---

### Task 2: 指标字典查询

**Files:**
- Modify: `contracts/data.py`（指标部分）
- Create: `src/qsys/webapp/queries/data_catalog.py`
- Test: `tests/webapp/test_webapp_data_catalog.py`

**契约：**
- `MetricStatus = Literal["failed","unknown","uncovered","unverified","known_issue","ok"]`
- `DirectLineage{source_id, provider: str | None, api: str | None, field, multiplier: float}`
- `MetricRow{id, dataset, column, kind, validity, meaning: str | None, direct: DirectLineage | None, roots: list[str], status: MetricStatus, issues: list[str]}`：含义未登记（`UNKNOWN`）时为 null。
- `CatalogSummary{total, by_kind: dict[str, int], covered, pending, latest_run: Timestamp | None}`
- `MetricList{schema_version, enabled, summary, rows, datasets: list[str], kinds: list[str]}`：原 `statuses` 文案对移到前端。
- `MetricEvidence`：`metric_evidence` 的显式字段，时间为 `Timestamp`。
- `MetricCheckRow`：`metric_checks` 的显式字段，时间为 `Timestamp`，并带 `sample_file`（规则同 Global Constraints）。
- `MetricDetail{schema_version, enabled, row, upstream, downstream, ext_consumers, formula: str | None, params: dict[str, JsonValue], checks: list[MetricCheckRow], history: list[MetricCheckRow], evidence: list[MetricEvidence]}`

**Interfaces：** `DataCatalog(query: WarehouseQuery)`：
- `rows(dataset, kind, status) -> list[MetricRow]`
- `summary() -> CatalogSummary`
- `list(dataset, kind, status) -> MetricList`
- `detail(metric_id) -> MetricDetail | None`
- `datasets()`、`kinds()`

- [ ] **Step 1: 写失败测试**（迁移指标相关用例），新增：
  - `test_metric_status_classification`：六种状态各一例。
  - `test_catalog_filters_validate_and_do_not_grow_cache`
  - `test_direct_lineage_structured`：直取指标有 `direct`；派生指标 `direct=null` 且有 `roots`。
  - `test_unknown_meaning_is_null`
- [ ] **Step 2–4:** 先失败；实现后运行 `uv run pytest tests/webapp tests/web -q`，全部通过。
- [ ] **Step 5: Commit：** `refactor(web): 指标字典查询层重写（#662）`

---

### Task 3: 数据质量查询、违规样本下载与路由

**Files:**
- Modify: `contracts/data.py`（质量部分）
- Create: `src/qsys/webapp/queries/data_quality.py`、`src/qsys/webapp/routes/data.py`
- Modify:
  - `src/qsys/webapp/app.py`：`WebappDeps` 增加 `data_assets: DataAssets | None`、`data_catalog: DataCatalog | None`、`sample_root: Path | None`；挂载 `data_router`
  - `src/qsys/web/app.py`：删除 `register_app_data`；deps 未设置时，由 `assets`（旧 `DataAssetService`）的 warehouse 与 `catalog` 的 sample_root 构造新查询。不新增 `create_app` 参数。
  - `registry.py`：登记契约；重新生成前端类型。
- Delete: `src/qsys/web/app_data.py`、`tests/web/test_app_data.py`（用例已迁入）
- Test: `tests/webapp/test_webapp_data_quality.py`、`tests/webapp/test_webapp_data_routes.py`

**契约：**
- `QualityRun{run_id, checked_at: Timestamp | None, fields: {total, passed, blocking, warning}, tables: dict[Health, int] | None}`
- `WarningSample{metric_id, check_id, detail, known_issue: str | None}`
- `WarningSource{key: Literal["upstream","registered","unclassified"], issue: str | None, count, samples}`：`label` 文案移到前端。
- `RuleStatus = Literal["blocking","warning","unknown","passed"]`
- `RuleRow{id, name, category, scope, known_issues, upstream_self_consistency, latest_status: RuleStatus}`
- `QualitySummary{schema_version, enabled, runs, warning_sources, rules}`
- `RuleResult`：`metric_checks` / `dataset_checks` / `relation_checks` 合并行，字段按并集显式列出，均可空；带 `sample_file`。
- `RuleDetail{schema_version, enabled, id, name, category, condition, limitation, implementation, targets, latest: list[RuleResult], history: list[RuleResult]}`

**规则表最新状态（修遗留）：**
- 字段级规则取各 `(metric_id, check_id)` 最新一条；表级规则取各 `(dataset_id, check_id)` 最新一条；
- 关系级规则取各 `(upstream, downstream, check_id)` 最新一条，状态映射与表级相同（red → blocking 等）；
- 三者取最差。

**路由（全部 GET）：**
- `/api/app/data/overview`
- `/api/app/data/tables?include_raw=`
- `/api/app/data/tables/{id}`
- `/api/app/data/tables/{id}/sample`
- `/api/app/data/catalog?dataset=&kind=&status=`
- `/api/app/data/catalog/{metric_id}`
- `/api/app/data/quality`
- `/api/app/data/quality/rules/{check_id}`
- `/api/app/data/quality/samples/{run_id}/{file}`

- [ ] **Step 1: 写失败测试**（迁移质检与路由用例），新增：
  - `test_relation_rule_latest_status_from_relation_checks`
  - `test_sample_file_shape_and_download_guard`
  - `test_data_responses_validate_against_contracts`
  - `test_data_routes_disabled_and_no_write_methods`
- [ ] **Step 2–4:** 先失败；实现后运行 `uv run pytest tests/webapp tests/web -q`，全部通过。
- [ ] **Step 5: Commit：** `refactor(web): 数据质量查询与数据组路由迁到新契约（#662）`

---

### Task 4: 前端改用生成类型与文案映射

**Files:**
- Create: `frontend/src/pages/data/labels.ts`：
  - `HEALTH_LABEL`、`DIMENSION_LABEL`、`METRIC_STATUS_LABEL`（含 pill 色调）、`SOURCE_STATUS_LABEL`、`WARNING_SOURCE_LABEL`；
  - `domainLabel(d: DomainRef)`：未归类时显示“未归类：<理由>”；
  - `sourceText(r: SourceRef)`、`lineageText(row: MetricRow)`；
  - `SAMPLE_REASON_TEXT`。
  - 文案逐字取自旧服务。
- Modify: `frontend/src/api/data.ts`：删除手写类型，从 `contracts.gen` 导出；新增 `qualitySampleUrl(file)`。
- Modify: `frontend/src/pages/data/*.tsx` 改用 labels；样例 JSON（`pages/data-fixtures`、`e2e/fixtures/data-*.json`）由后端夹具经新接口导出。

- [ ] **Step 1: 写失败测试：**
  - `labels cover every contract code`：每个契约枚举值都有文案。
  - `unclassified domain shows reason`
  - `coverage pending shows 统计中, not 0`
  - `rule sample link uses new route`
- [ ] **Step 2:** 实现；运行 `npm test && npm run typecheck && npm run build && npx playwright test`，全部通过。
- [ ] **Step 3: Commit：** `refactor(web): 数据组前端改用契约生成类型与前端文案映射（#662）`

---

### Task 5: 收尾

- [ ] `grep -rn "app_data\|data_app_views\|/data/catalog/sample\|dimension_labels\|status_labels" frontend/src src/qsys/webapp`：无结果。
- [ ] 全量 `uv run pytest -q`；前端全套检查。
- [ ] 本地只读预览冒烟：四个列表 / 详情接口、样本、违规样本下载、非法路径返回 404。
- [ ] 推送需求分支；在 #662 记录。
