# Web 改版 R3：策略、回测、研究任务迁到新契约与查询层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 研究侧（策略、回测、研究任务）的读、写、下载全部改由 `qsys.webapp` 提供显式契约；回测报告读取层重写，读不到的值一律为 `null`，不再用 0 或文件修改时间顶替。

**Architecture:**
- `queries/backtest_runs.py`：新的回测运行目录读取层 `BacktestRuns`，取代新接口对旧 `web/backtest_views.BacktestService` 的依赖（旧页面继续用旧服务，合入时一起删除）。
- `queries/research_derive.py`：自 `web/research_views.py` 迁入的纯派生。
- `queries/research.py`：`ResearchQuery` 组装策略、回测、研究任务三组视图，并承担写操作的校验与调用。
- `files.py`：受控下载，校验白名单根目录、单一路径组件、拒绝符号链接、扩展名白名单，有清单时还校验字节数与 SHA-256。
- `routes/research.py`：薄路由。写接口只在对应服务启用时注册。

**Tech Stack:** FastAPI、pydantic 2、polars、pytest；前端不新增依赖。

**Spec:**
- `docs/superpowers/plans/2026-11-18-web-redesign-roadmap-v2.md`：第 1 节（四项决定，“文件下载走新接口”）、第 2 节 `backtest_views` / `workbench` / `research_jobs` 三行、第 3 节、第 4 节 R3
- `docs/superpowers/plans/2026-11-18-web-redesign-R1-webapp-contracts.md`：统一约定
- `docs/superpowers/specs/2026-11-17-web-redesign-design.md`：§6.3、§8.10、§8.11
- `docs/superpowers/plans/2026-11-18-web-redesign-08c-strategies-backtests.md`：已定口径

## Global Constraints

- **沿用 R1 统一约定：**
  - 时间带时区（无时区的存储时间按 Asia/Shanghai 解释）；
  - 缺失为 `null`；比例取 0–1，金额单位为元；
  - 错误体为 `{"error": {"code", "message"}}`；GET 一律 no-store；
  - 契约 `extra="forbid"`，带 `schema_version: Literal[1]`，登记进 `EXPORTED` 并重新生成前端类型。
- **比例统一为 0–1：** 原 `return_pct`、`benchmark_pct`、`max_drawdown_pct`（×100 的百分点）改为 `total_return`、`benchmark_return`、`max_drawdown`；前端负责格式化。
- **回测读取不补值：**
  - `run_status.csv` 的数值列为空时为 `null`，不写 0；
  - 没有 `config.json` 的 `generated_at` 时为 `null`，不用文件修改时间；
  - 净值 `total_value` 为空的日子，策略收益与回撤为 `null`，回撤段计算跳过该日。
- **08c 已定口径不变：** 推进位置、使用中只读、回测警告文案、回撤段、基准缺日为 `null`、重新提交只限 failed/cancelled/interrupted 且生成新任务 ID。
- **写接口：** 只在对应服务启用时注册（未启用研究侧的部署保持完全只读）。请求体用契约模型（`extra="forbid"`）。领域校验失败返回 422/409/403，`message` 用领域给出的中文原因；未处理异常仍为通用 500。
- **旧接口不动：** `/api/strategies`、`/api/backtests`、`/api/research-jobs` 与旧下载路由仍供旧页面使用，合入时删除。
- **业务模块位置不动：** `web/workbench.py`、`web/research_jobs.py` 合入时才搬家。
- 不改任何生产组件。

## Review Focus

1. **下载路径逃逸：** `../`、绝对路径、编码后的斜杠、以 `.` 开头、符号链接（文件本身或其所在目录），一律 404，不读取文件。归 Task 1 `test_download_rejects_escape_and_symlinks`。
2. **研究产物被篡改：** 文件存在但字节数或 SHA-256 与清单不符，返回 404，不提供下载。归 Task 1 `test_download_rejects_hash_mismatch`。
3. **半截回测目录：** `run_status.csv` 缺必备列，或必备列的值为空。前者不出现在列表里；后者值为 `null`，页面不显示 0。归 Task 2 `test_summary_blank_numbers_are_null`。
4. **非法写请求：**
   - 多余字段、日期颠倒、资金 ≤ 0、未知入场口径：返回 422 统一错误体，不入队；
   - 取消已结束的回测：返回 409。

   归 Task 4 `test_backtest_enqueue_validation`。
5. **研究侧未启用：**
   - 读接口返回 200 且 `enabled=false`；
   - 写接口不存在：POST/PUT 返回 405，不能落到 SPA 路由或返回 200。

   归 Task 3 `test_endpoints_disabled_without_services`、Task 4 `test_no_write_routes_without_services`。

---

### Task 1: 受控下载 `files.py`

**Files:**
- Create: `src/qsys/webapp/files.py`
- Test: `tests/webapp/test_webapp_files.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Expected: bytes: int; sha256: str`
  - `controlled_file(root: Path, relative: str, *, suffixes: frozenset[str] | None, expected: Expected | None = None) -> Response`，返回或抛出 `HTTPException(404, "文件不存在")`。
    - `relative` 可含 `/` 分隔的多级路径（研究产物），每级不得为空、不得是 `.` / `..`、不得以 `.` 开头、不得含 `\`；
    - 从 `root` 到文件逐级拒绝符号链接；解析后必须位于 `root.resolve()` 之下；必须是普通文件；
    - `suffixes` 不为 None 时，扩展名（小写）须在其中；
    - `expected` 给出时，字节数与 SHA-256 必须一致；
    - 响应头：`Content-Disposition: attachment; filename="<ASCII 替代>"; filename*=UTF-8''<百分号编码>`，`Cache-Control: no-store`；
    - media type 按扩展名映射，未知为 `application/octet-stream`。
  - `RUN_FILE_SUFFIXES = frozenset({".csv", ".json", ".txt", ".zip", ".py", ".log"})`

- [ ] **Step 1: 写失败测试：**
  - `test_download_serves_file_with_attachment_headers`
  - `test_download_rejects_escape_and_symlinks`：`../x.csv`、`/etc/passwd`、`a/../../x.csv`、`.hidden.csv`、`a\\b.csv`、指向根外的文件符号链接、目录符号链接下的文件。
  - `test_download_rejects_suffix_not_allowed`
  - `test_download_rejects_hash_mismatch`：字节数与 SHA-256 分别篡改。
  - `test_download_non_ascii_filename_header`
- [ ] **Step 2:** 运行 `uv run pytest tests/webapp/test_webapp_files.py -q`，确认失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 测试通过。
- [ ] **Step 5: Commit：** `feat(web): 新后端受控下载：白名单根目录、拒绝符号链接与哈希校验（#662）`

---

### Task 2: 回测运行读取层与纯派生迁入

**Files:**
- Create: `src/qsys/webapp/queries/backtest_runs.py`
- Move: `src/qsys/web/research_views.py` → `src/qsys/webapp/queries/research_derive.py`；`tests/web/test_research_views.py` → `tests/webapp/test_webapp_research_derive.py`
- Modify: `research_derive.aligned_series`、`drawdown_episodes` 接受 `total_value=None`（见 Global Constraints）
- Test: `tests/webapp/test_webapp_backtest_runs.py`

**Interfaces:**
- Produces（`backtest_runs.py`）：
  - `RUN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")`
  - `@dataclass(frozen=True) class RunInfo`：
    - `name: str`、`run_id: str`、`strategy: str`
    - `start: str | None`、`end: str | None`
    - `initial_value`、`total_value`、`cumulative_return`：`float | None`
    - `buys`、`sells`、`rejects`、`closed_lots`：`int | None`
    - `cost_model`、`entry_fill`：`str | None`
    - `params: dict[str, object]`
    - `generated_at: str | None`
    - `source: Literal["web", "cli"]`：config 缺 `source` 时为 `"cli"`，沿用旧口径：网页提交的运行都有排队任务，可以由任务侧认出
  - `@dataclass(frozen=True) class RunFile: name: str; size: int`
  - `class BacktestRuns(root: Path)`：
    - `root -> Path`（属性）
    - `runs() -> list[RunInfo]`：坏目录跳过并记日志
    - `run(name) -> RunInfo | None`：`name` 不符合 `RUN_NAME` 时直接返回 None
    - `files(name) -> list[RunFile]`：只列 `RUN_FILE_SUFFIXES` 中的普通文件，不含符号链接
    - `metrics(name) -> dict[str, float]`：值不能转为 float 的指标跳过
    - `table(name, filename, *, limit: int | None = None) -> list[dict[str, str | int | float | bool | None]]`：用 polars 读取，`try_parse_dates=False`，原样保留文件里的日期串；`limit` 取末尾若干行
    - `nav(name) -> list[tuple[str, float | None]]`、`benchmark(name) -> dict[str, float]`：基准值为空的日子不放进字典，即视为缺日
- 必备列 `{"run_id", "strategy", "initial_value", "total_value"}` 缺列，或 `run_status.csv` 为空：该目录不是运行。

- [ ] **Step 1: 写失败测试：**
  - `test_runs_lists_valid_and_skips_broken`
  - `test_summary_blank_numbers_are_null`：`initial_value`、`buys` 为空时为 None，不是 0。
  - `test_generated_at_null_without_config`
  - `test_run_name_rejects_traversal`
  - `test_files_excludes_symlink_and_unlisted_suffix`
  - `test_table_keeps_raw_date_strings`
  - `test_nav_blank_value_is_none`
  - `research_derive` 用例：`test_aligned_series_null_value_day`、`test_episodes_skip_null_value_day`
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现；迁移 `research_views` 并改 `app_research.py` 的导入（Task 3 会删除该文件）。
- [ ] **Step 4:** 运行 `uv run pytest tests/webapp tests/web -q`，全部通过。
- [ ] **Step 5: Commit：** `refactor(web): 重写回测运行读取层，读不到的值为 null（#662）`

---

### Task 3: 研究侧契约、查询与只读路由（含下载）

**Files:**
- Create: `src/qsys/webapp/contracts/research.py`、`src/qsys/webapp/queries/research.py`、`src/qsys/webapp/routes/research.py`
- Modify:
  - `src/qsys/webapp/app.py`：`WebappDeps` 增加 `workbench`、`runs: BacktestRuns | None`、`paper`、`research_jobs`、`strategy_templates: Path | None`、`shadow_lineage`、`core_label: str = "影子盘"`
  - `src/qsys/web/app.py`：`create_app` 在 deps 未设置这些字段时，用自身参数补齐。`runs` 取 `BacktestRuns(backtests.root)`。删除 `register_app_research` 调用。
  - `registry.py`：登记契约；重新生成前端类型。
- Delete: `src/qsys/web/app_research.py`、`tests/web/test_app_research.py`（用例迁入新测试）
- Test: `tests/webapp/test_webapp_research.py`：夹具 `publish_report` 与 `env` 迁入，`env.client()` 改为经 `webapp=WebappDeps(...)` 构造

**契约（字段即约定）：**
- `ResearchDisabled{schema_version, enabled: Literal[False]}`
- `BacktestRow`：
  - `id`、`strategy`、`strategy_id: str | None`
  - `start`、`end`：`date | None`
  - `created_at`、`started_at`、`finished_at`：`Timestamp | None`
  - `duration_seconds: int | None`：仅当开始与结束时间都在时计算
  - `status: str`
  - `cost_model: str | None`：有报告时取报告；没有报告时按任务配置 `jq_compat` 取“聚宽兼容” / “qsys 实盘档位”，与旧口径相同
  - `total_return`、`benchmark_return`、`max_drawdown`：`float | None`
  - `has_report: bool`
  - `source: Literal["web", "cli"]`
  - `progress`、`total`：`int | None`
  - `current_session: date | None`
- `StrategyRow`：
  - `id`、`name`、`updated_at: Timestamp | None`、`version_id`
  - `stage: Literal["live","paper","backtest","draft"]`、`stage_label`、`stage_detail`
  - `in_use: bool`、`used_by: list[str]`
  - `latest_backtest: BacktestRow | None`
- `ParameterSpec`：
  - `name`、`type: Literal["int","float","bool","str"]`
  - `default: int | float | bool | str | None`
  - `label`、`description`
  - `minimum`、`maximum`：`float | None`
  - `choices: list[int | float | bool | str]`
- `StrategyList{schema_version, enabled: Literal[True], core_label, templates: list[str], strategies: list[StrategyRow]}`
- `StrategyDetail`：`StrategyRow` 的全部字段，加 `schema_version`、`enabled`、`core_label`、`source`、`parameters`、`versions: list[StrategyVersion{id, created_at: Timestamp | None}]`、`backtests: list[BacktestRow]`
- `BacktestList{schema_version, enabled: Literal[True], rows: list[BacktestRow]}`
- `BacktestJobInfo`：
  - `id`、`strategy_name`、`status`
  - `progress: int`、`total: int`、`percent: int`
  - `current_session: date | None`
  - `error: str | None`：空串为 None
  - `config: dict[str, JsonValue]`
- `RunSummary`：`RunInfo` 的全部字段，其中 `start`、`end` 为 `date | None`，`generated_at` 为 `Timestamp | None`，`params` 为 `dict[str, JsonValue]`。
- `BacktestReport`：
  - `schema_version`、`enabled: Literal[True]`、`report: Literal[True]`
  - `name`、`strategy_id: str | None`、`job: BacktestJobInfo | None`、`summary: RunSummary`
  - `metrics: dict[str, float]`
  - `series: list[SeriesPoint{date, strategy, benchmark, excess, drawdown}]`
  - `episodes: list[Episode{peak_date, trough_date, recovery_date: date | None, depth}]`
  - `monthly: list[MonthReturn{month: str, value: float | None}]`
  - `fills: list[dict[str, str | int | float | bool | None]]`（最后 500 笔）、`fills_total: int`
  - `rejects: list[...]`（最后 200 笔）
  - `files: list[RunFileInfo{name, size}]`、`warnings: list[str]`
- `BacktestPending{schema_version, enabled: Literal[True], report: Literal[False], strategy_id, job: BacktestJobInfo, log_tail: str}`
- `ResearchJob`：
  - `id`、`state`、`detail`、`task: str | None`
  - `parameters: dict[str, JsonValue]`、`timeout_seconds: int | None`
  - `created_at: Timestamp | None`
  - `code_sha`、`snapshot_id`、`snapshot_content_sha256`：`str | None`
  - `artifacts: list[ArtifactInfo{path, bytes, sha256}]`
- `ResearchJobList{schema_version, enabled: Literal[True], submission_enabled: bool, jobs: list[ResearchJob]}`

**只读路由（新增与改动）：**
- `GET /api/app/strategies` → `StrategyList | ResearchDisabled`
- `GET /api/app/strategies/{ident}` → `StrategyDetail | ResearchDisabled`；不存在返回 404
- `GET /api/app/backtests` → `BacktestList | ResearchDisabled`
- `GET /api/app/backtests/{name}` → `BacktestReport | BacktestPending | ResearchDisabled`；不存在返回 404
- `GET /api/app/backtests/{name}/files/{filename}`：
  - `name` 若是任务 ID，先映射为 `run_name`，再校验 `RUN_NAME`；
  - 只能下载 `files(name)` 列出的文件，经 `controlled_file(runs.root / name, filename, suffixes=RUN_FILE_SUFFIXES)`。
- `GET /api/app/research-jobs` → `ResearchJobList | ResearchDisabled`
- `GET /api/app/research-jobs/{ident}/files/{path:path}`：
  - 只允许任务产物清单里的路径；
  - 经 `controlled_file(research_jobs.root / "jobs" / ident / "artifacts", path, suffixes=None, expected=Expected(bytes, sha256))`；
  - `ident` 不符合研究任务 ID 格式时返回 404。

- [ ] **Step 1: 写失败测试：** 迁移原 10 个用例并改为新契约断言（比例 0–1），并新增：
  - `test_research_responses_validate_against_contracts`
  - `test_backtest_list_blank_status_values_are_null`：`run_status.csv` 的 `cumulative_return` 为空时，`total_return` 为 null。
  - `test_backtest_file_download_via_new_route`：成功；未列出的文件 404；`../` 返回 404。
  - `test_research_artifact_download_checks_manifest`：成功；篡改后 404。
  - `test_endpoints_disabled_without_services`：四个读接口都返回 200 且 `enabled=false`；两个下载接口返回 404。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现；登记契约，重新生成前端类型。
- [ ] **Step 4:** 运行 `uv run pytest tests/webapp tests/web -q`，全部通过。
- [ ] **Step 5: Commit：** `refactor(web): 策略、回测、研究任务读取与下载迁到新契约与查询层（#662）`

---

### Task 4: 研究侧写接口迁到 `/api/app`

**Files:**
- Modify: `contracts/research.py`（请求与结果模型）、`queries/research.py`（写操作）、`routes/research.py`
- Test: `tests/webapp/test_webapp_research_writes.py`

**契约：**
- 请求：
  - `StrategyInput{name: str, source: str}`
  - `TemplateImport{filename: str}`
  - `BacktestRequest`：
    - `strategy_id: str`、`start: date`、`end: date`
    - `cash: float = 100_000`
    - `params: dict[str, JsonValue] = {}`
    - `entry_fill: str = "continuous"`
    - `jq_compat: bool = False`
  - `ResearchJobRequest{task: str, parameters: dict[str, JsonValue] = {}, timeout_seconds: int | None = None}`
  - 请求模型 `extra="forbid"`，不带 `schema_version`。
- 结果：`Created{schema_version, id}`、`Cancelled{schema_version, status: Literal["cancelled", "cancellation-requested"]}`、`Resubmitted{schema_version, id, resubmitted_from}`

**路由（只在服务启用时注册）：**
- `workbench` 启用：
  - `POST /api/app/strategies`（201）
  - `PUT /api/app/strategies/{ident}`：已有接口，改用 `StrategyInput`，保留“使用中只读”409
  - `POST /api/app/strategies/import`（201）：同时需要 `strategy_templates`
  - `POST /api/app/backtests`（201）：
    - 校验沿用旧口径：`start ≤ end`、`cash > 0`、`validate_parameters`、`entry_fill ∈ ENTRY_FILL_MODES`；
    - 入队配置键与旧接口完全相同：`start`、`end`、`cash`、`jq_compat`、`params`、`entry_fill`，因为回测 worker 读取它们。
  - `POST /api/app/backtests/{ident}/cancel`：不能取消时返回 409
- `research_jobs` 启用：
  - `POST /api/app/research-jobs`（201）
  - `POST /api/app/research-jobs/{ident}/cancel`
  - `POST /api/app/research-jobs/{ident}/resubmit`（201）：已有接口，改用新结果模型

- [ ] **Step 1: 写失败测试：**
  - `test_create_and_save_strategy`
  - `test_import_template_rejects_path`：`../x.py`、`x.txt` 返回 422；不存在的模板返回 404。
  - `test_backtest_enqueue_validation`：多余字段、日期颠倒、资金 ≤ 0、未知入场口径返回 422 统一错误体，且队列为空；取消已结束的任务返回 409。
  - `test_backtest_enqueue_config_matches_worker_contract`：入队配置的键集合与上面所列相同。
  - `test_research_job_submit_cancel_resubmit`
  - `test_no_write_routes_without_services`：未启用时，各写路径 POST/PUT 返回 405。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现；登记结果契约，重新生成前端类型。
- [ ] **Step 4:** 运行 `uv run pytest tests/webapp tests/web -q`，全部通过。
- [ ] **Step 5: Commit：** `refactor(web): 研究侧写接口迁到新契约（#662）`

---

### Task 5: 前端改用生成类型与新接口

**Files:**
- Modify: `frontend/src/api/research.ts`：
  - 删除手写类型，从 `contracts.gen` 导出；
  - 写操作改用 `/api/app/*`；
  - 新增 `backtestFileUrl(name, file)`、`researchArtifactUrl(id, path)`。
- Modify: `frontend/src/pages/strategies/*`、`frontend/src/pages/backtest/*`：
  - 百分点字段改为比例字段，用现有的比例格式化函数；
  - 时长由 `duration_seconds` 格式化；
  - 下载链接改用新 URL 函数；
  - 月度收益读 `value`；
  - `summary` 中的 null 显示“未知”。
- Modify: `frontend/src/pages/research-fixtures/*.sample.json`：由 Task 3 测试夹具经新接口导出；`index.ts` 注释写明来源。
- Modify: `frontend/e2e/fixtures` 中研究侧夹具（如有），以及受影响的组件测试。

- [ ] **Step 1:** 改类型与样例；运行 `npm run typecheck`，逐个修正。
- [ ] **Step 2:** 新增测试：
  - `report download links use new route`
  - `null summary shows unknown`：区间收益与被拒数为 null 时显示“未知”，不显示 0。
- [ ] **Step 3:** 运行 `npm test && npm run typecheck && npm run build && npx playwright test`，全部通过。
- [ ] **Step 4: Commit：** `refactor(web): 研究侧前端改用契约生成类型与新接口（#662）`

---

### Task 6: 收尾

- [ ] `grep -rn "app_research\|research_views\|/api/strategies\|/api/backtests\|/api/research-jobs\|/backtests/.*/file/\|/research-jobs/.*/file/" frontend/src src/qsys/webapp`：无结果。旧 `web/app.py` 中的旧路由保留到合入。
- [ ] 全量 `uv run pytest -q`；前端全套检查。
- [ ] 本地只读预览（夹具数据）冒烟：列表、详情、报告、两类下载、非法路径返回 404。
- [ ] 推送需求分支；在 #662 记录。
