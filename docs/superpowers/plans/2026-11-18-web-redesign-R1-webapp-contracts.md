# Web 改版 R1：新后端骨架与契约机制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立新后端 `qsys.webapp`，包括契约、只读查询、薄路由、错误格式，以及“前端类型由契约生成”的机制。外壳状态、市场总览、AI 涨停三组接口迁到新结构，前端改用生成的类型。

**Architecture:**
- 契约是带 `schema_version` 的 pydantic 模型（`extra="forbid"`），作为唯一来源。
- 查询层直接读领域对象与存储，不经过旧视图服务。
- 路由只做参数校验、调用查询、返回契约。
- `scripts/gen_frontend_contracts.py` 从契约的 JSON Schema 生成 `frontend/src/api/contracts.gen.ts`，测试保证生成物与仓库一致。
- 本计划期间，旧 `qsys.web.app.create_app` 通过 `mount_webapp(app, deps)` 挂载新接口。合入时旧应用删除，`qsys.webapp.app` 成为唯一入口（见总路线 v2）。

**Tech Stack:** FastAPI、pydantic 2、pytest；前端不新增依赖。

**Spec:**
- `docs/superpowers/plans/2026-11-18-web-redesign-roadmap-v2.md`（第 1、3 节）
- `docs/superpowers/specs/2026-11-17-web-redesign-design.md`（§5、§8.8、§8.12、§9、§10）

## Global Constraints

- 总路线 v2 的四项决定：结构化、独立契约、新页即删旧页、内部服务重写。生产安全规则与数据铁律照旧。
- **统一约定**：
  - 时间字段一律带时区。存储里的无时区时间按 Asia/Shanghai 解释；带时区的原样转换。
  - 缺失值为 `null`，原因放在同级 `missing: {字段名: 原因}`。
  - 比例取 0–1，金额单位为元。
  - 错误体为 `{"error": {"code", "message"}}`，不回显异常文本与路径。
  - GET 接口一律 `Cache-Control: no-store`。
- 契约模型 `extra="forbid"`、`frozen=True`；每个顶层响应都带 `schema_version: Literal[1]`。
- 读 Redis 的查询沿用 15 秒单飞缓存（§10、#666）；仓库查询沿用 300 秒缓存。
- 不改任何生产组件的行为。R1 只动 `qsys.webapp`（新）、`qsys.web` 中被迁移的部分，以及前端。

## Review Focus

1. **存储里的无时区时间**（快照 `published_at`、心跳时间）：
   - 契约输出 `+08:00`，前端显示时刻不变。
   - 归 Task 1 `test_naive_local_time_serialized_with_shanghai_offset`，以及 Task 5 的外壳与市场测试。
2. **契约生成物过期**（改了模型没重新生成）：测试失败，并给出重新生成的命令。归 Task 2 `test_generated_contracts_are_current`。
3. **路由里出现未处理异常**：返回 500，错误体统一，不回显异常文本。归 Task 1 `test_unhandled_error_is_generic`。
4. **行情控制台未启用、影子榜单未配置**：返回契约内的“未启用”变体，状态码 200，不是 404。归 Task 3、Task 4。
5. **旧页面在本计划期间不受影响**：旧路由的测试全部照常通过。归每个任务的 `uv run pytest tests/web` 全量运行。

---

### Task 1: `qsys.webapp` 骨架：契约基类、错误格式、缓存、SPA 挂载

**Files:**
- Create:
  - `src/qsys/webapp/__init__.py`
  - `src/qsys/webapp/contracts/__init__.py`、`src/qsys/webapp/contracts/base.py`
  - `src/qsys/webapp/cache.py`：自 `web/read_cache.py` 迁入并删除原文件
  - `src/qsys/webapp/spa.py`：自 `web/spa.py` 迁入并删除原文件
  - `src/qsys/webapp/errors.py`
  - `src/qsys/webapp/app.py`
- Modify: `src/qsys/web/app.py`：改用 `qsys.webapp.spa`，并调用 `mount_webapp`
- Test: `tests/webapp/test_base.py`；`tests/web/test_spa_*.py`、`tests/web/test_read_cache.py` 迁到 `tests/webapp/`，同步改导入

**Interfaces:**
- Produces:
  - `LOCAL_TZ = ZoneInfo("Asia/Shanghai")`
  - `Timestamp = Annotated[datetime, AfterValidator(to_aware)]`：无时区值视为 LOCAL_TZ。
  - `class Contract(BaseModel)`：`model_config = ConfigDict(extra="forbid", frozen=True)`。
  - `ErrorDetail(code: str, message: str)`、`ErrorBody(error: ErrorDetail)`。
  - `install_error_handlers(app)`：只作用于 `/api/app/` 路径，`HTTPException` 转为 `{error: {code: "http_<status>", message: detail}}`。
    - 校验错误为 422，code 为 `invalid_request`。
    - 未处理异常为 500，code 为 `internal_error`，message 为“服务内部错误”，并记日志。
    - 其他路径保持 FastAPI 默认行为，旧页面不受影响。
  - `no_store(model) -> JSONResponse`：用 `model.model_dump(mode="json")` 序列化，附 no-store。
  - `@dataclass WebappDeps`：
    - `live_kill: LiveKillReader`
    - `market: MarketQuery | None = None`
    - `shadow: LimitUpShadowReader | None = None`
    - `research: LimitUpResearchReader | None = None`
    - `spa_root: Path | None = None`
  - `mount_webapp(app: FastAPI, deps: WebappDeps) -> None`：安装错误处理与各路由（Task 3、4 填入），最后挂 SPA。

- [ ] **Step 1: 写失败测试**：
  - `test_naive_local_time_serialized_with_shanghai_offset`
  - `test_aware_time_converted_not_relabelled`：UTC 10:00 → `+08:00` 18:00。
  - `test_contract_forbids_extra`
  - `test_http_error_body_shape`
  - `test_validation_error_body`
  - `test_unhandled_error_is_generic`：路由抛 `RuntimeError("/secret")` → 500，正文不含 secret。
  - `test_non_app_paths_keep_default_errors`
- [ ] **Step 2: 运行，确认失败**：`uv run pytest tests/webapp -q`
- [ ] **Step 3: 实现；迁移 cache 与 spa，删除旧文件，修正全部导入**
- [ ] **Step 4: 运行 `uv run pytest tests/webapp tests/web -q`**，全部通过
- [ ] **Step 5: Commit**：`refactor(web): 新后端 qsys.webapp 骨架：契约基类、统一错误格式、缓存与单页挂载（#662）`

---

### Task 2: 契约到前端类型的生成器

**Files:**
- Create: `src/qsys/webapp/contracts/registry.py`：`EXPORTED: tuple[type[Contract], ...]`，列出所有对外契约，后续任务往里追加。
- Create: `scripts/gen_frontend_contracts.py`：`render(models) -> str` 与 `main()`。
  - 用 `pydantic.json_schema.models_json_schema` 生成合并 schema，`ref_template="#/$defs/{model}"`，再把每个 `$defs` 输出为 `export type Name = ...` 或 `export interface Name {...}`。
  - 映射规则：
    - `string` → `string`；`integer`、`number` → `number`；`boolean` → `boolean`；`null` → `null`
    - `const` → 字面量；`enum` → 字面量联合
    - `array` → `T[]`；`prefixItems` → 元组
    - `object` 带 `properties` → interface，必填字段不带 `?`，可选字段带 `?`
    - `additionalProperties` → `Record<string, T>`
    - `anyOf`、`oneOf` → 联合；`$ref` → 类型名
  - 文件头写“由 scripts/gen_frontend_contracts.py 生成，勿手改”。输出确定性排序。
- Create: `frontend/src/api/contracts.gen.ts`（生成物，提交）
- Modify: `tests/scripts/test_scripts_import.py`：新入口加入清单（AGENTS.md §2）
- Test: `tests/webapp/test_contracts_generated.py`

- [ ] **Step 1: 写失败测试**：
  - `test_render_maps_schema_shapes`：用一组测试模型覆盖可空、字面量、联合、元组、Record、嵌套与引用，断言输出片段。
  - `test_generated_contracts_are_current`：读取仓库中的 `contracts.gen.ts`，与 `render(EXPORTED)` 逐字相等；失败信息为“运行 uv run python scripts/gen_frontend_contracts.py 重新生成”。
  - `test_every_route_response_model_is_exported`：遍历 `mount_webapp` 挂载的 `/api/app/*` 路由，`response_model` 都在 `EXPORTED` 中，包括联合中的各成员。
- [ ] **Step 2: 运行，确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行测试；运行脚本生成文件；`cd frontend && npm run typecheck` 通过**
- [ ] **Step 5: Commit**：`build(web): 由后端契约生成前端类型并校验一致（#662）`

---

### Task 3: 外壳状态与市场总览的契约、查询、路由

**Files:**
- Create:
  - `src/qsys/webapp/contracts/shell.py`
  - `src/qsys/webapp/contracts/market.py`
  - `src/qsys/webapp/queries/shell.py`：由 `web/shell_status.py` 重写，旧文件删除
  - `src/qsys/webapp/queries/market.py`：取代 `web/app_market.py`，旧文件删除
  - `src/qsys/webapp/routes/shell.py`、`src/qsys/webapp/routes/market.py`
- Modify:
  - `src/qsys/web/monitor_cli.py`：用同一个 transport 与 warehouse 构造 `MarketQuery`，传入 `WebappDeps`。
  - `src/qsys/web/app.py`：`create_app` 增加 `webapp: WebappDeps | None = None`；为 None 时用 `WebappDeps(live_kill=app.state.live_kill, shadow=limit_up_shadow, research=limit_up_research)`。
  - `src/qsys/web/app_board.py`：交易时段改取 `deps.market.schedule()`（过渡，R2 迁走）。
- Test:
  - `tests/webapp/test_shell.py`：迁移并改写 `tests/web/test_shell_status.py`
  - `tests/webapp/test_market.py`：取代 `tests/web/test_app_market.py`
  - `tests/web/test_app_board.py`：调整交易时段来源

**契约（字段即约定）：**
- `ShellStatus`：
  - `schema_version`、`checked_at: Timestamp`
  - `kill: KillStatus{state: Literal["stopped","clear","unknown"], label, message, sources: [KillSource{id, name, state, reason, recorded_at: Timestamp|None}]}`
  - `live: LivePosture{state: Literal["trading","reduce_only","unknown"], label}`
  - `agent_heartbeat_at: Timestamp|None`、`qmt: None`、`alerts_open: int|None`
  - `missing: dict[str, str]`：`qmt` 与 `alerts_open` 的原因放在这里，取代旧的 `qmt_reason`、`alerts_reason`。
- `MarketOverview`：
  - `schema_version`、`enabled: Literal[True]`
  - `live: LiveMarket | None`、`schedule: MarketSchedule | None`、`limit_check: LimitCheck | None`
  - `regime: list[RegimeDay] | None`
  - `missing: dict[str, str]`、`errors: dict[str, Literal["读取失败"]]`
- `MarketDisabled`：`schema_version`、`enabled: Literal[False]`。路由返回 `MarketOverview | MarketDisabled`。
- `LiveMarket{available, stale, snapshot: MarketSnapshot|None}`：`MarketSnapshot` 的字段显式列出（指数、家数、比例、成交额、缺涨跌停价、封板未知、覆盖），`published_at: Timestamp`。
- `LimitCheck`：显式字段 `session, checked, mismatched, samples[{symbol, tushare, qmt}], unbounded_equivalent, missing_in_qmt, missing_samples[{symbol, tushare}], missing_in_roster: list|None, missing_outside_roster: list|None, missing_split_complete: bool|None`。名录拆分逻辑（#460）从旧 `MarketService._split_missing_by_roster` 迁入查询层，不新增推断：名录读不到或为空时不拆分。
- `RegimeDay`：显式列出 `schema.MARKET_REGIME` 的全部列，均可空，`dt: date`。

**查询 `MarketQuery(transport, warehouse, *, snapshot_key, schedule_key, limit_check_prefix, stale_seconds=45, clock)`：**
- `live()`、`schedule()`、`limit_check()`、`regime(limit=60)`。
- Redis 部分共用一个 15 秒单飞缓存；`regime` 使用 300 秒缓存；单部分失败只置 null 并写入 errors，不回显异常。
- 直接读 transport 与 `qsys.data` 的市场情绪表，不经过 `web/data_views.MarketService`。
- `stale` 按读取时刻计算。

- [ ] **Step 1: 写失败测试**：迁移原有用例（单飞缓存、null 保留、升序、上限 60、部分失败隔离、no-store、未启用 200），并新增：
  - `test_shell_missing_reasons_in_missing_map`
  - `test_market_snapshot_time_has_offset`
  - `test_limit_check_roster_split_only_when_roster_available`
  - `test_market_response_validates_against_contract`：响应能被契约模型解析，没有多余字段。
  - `test_board_schedule_uses_market_query_cache`：打板接口不再自行读 Redis。
- [ ] **Step 2: 运行，确认失败**
- [ ] **Step 3: 实现；把契约加入 `EXPORTED` 并重新生成前端类型**
- [ ] **Step 4: 运行 `uv run pytest tests/webapp tests/web -q`**，全部通过
- [ ] **Step 5: Commit**：`refactor(web): 外壳状态与市场总览迁到新契约与查询层（#662）`

---

### Task 4: AI 涨停的契约、查询、路由

**Files:**
- Create: `src/qsys/webapp/contracts/ailu.py`、`src/qsys/webapp/queries/ailu.py`、`src/qsys/webapp/routes/ailu.py`
- Test: `tests/webapp/test_ailu.py`

**接口：**
- `GET /api/app/ailu/shadow?day=`，返回 `ShadowBoard`：
  - `schema_version`
  - `state: Literal["unavailable","waiting","generating","ready","stale","failed"]`、`message`、`historical`
  - `snapshot: ShadowSnapshot|None`：`prediction_date`、`target_session`、`generated_at: Timestamp`、`model_version`、`eligible_count`、`rolling_precision_at_20`、`calibration_error`、`drift_score`、`entries[ShadowEntry]`
  - `history: list[ShadowDay]`
  - `missing: dict[str, str]`：基线、10 日滚动、校准曲线、漂移序列四项，原因写明（见 #676）。
  - 状态与消息取影子榜单读取器的结果，不另判断。
- `GET /api/app/ailu/research?day=`，返回 `ResearchBoard`：
  - `schema_version`
  - `state: Literal["ready","unavailable","not_found"]`、`message`
  - `summary: ResearchSummary|None`、`selected: ResearchDay|None`、`history`、`monthly`
  - 契约中不出现模型或数据的内部路径。
- 读取器为 None 时返回 `state="unavailable"`，并附对应 message。

- [ ] **Step 1: 写失败测试**：
  - `test_shadow_contract_maps_reader_view`
  - `test_shadow_unconfigured_is_unavailable_200`
  - `test_shadow_day_param_passes_through_and_validates_date`：非法日期返回 422 统一错误体。
  - `test_research_contract_maps_reader_view`
  - `test_ailu_missing_reasons_cite_676`
- [ ] **Step 2–4:** 先运行确认失败；实现后把契约加入 `EXPORTED` 并重新生成前端类型；运行 `uv run pytest tests/webapp tests/web -q` 全部通过。
- [ ] **Step 5: Commit**：`refactor(web): AI 涨停迁到新契约与查询层（#662）`

---

### Task 5: 前端改用生成类型与统一请求客户端

**Files:**
- Create: `frontend/src/api/client.ts`：
  - `getJson<T>(path, signal): Promise<T>`
  - 网络失败时抛 `ApiError(code="network", message="接口无法访问")`；非 2xx 时解析 `{error}`，抛 `ApiError(code, message)`。
- Modify: `frontend/src/api/shell.ts`、`market.ts`、`ailu.ts`：删除手写类型，改为 `import type {...} from './contracts.gen'`；AI 涨停改用 `/api/app/ailu/*`。
- Modify：使用这些类型的组件与测试。外壳的 `qmt_reason`、`alerts_reason` 改读 `missing`；测试数据由后端生成（与 08c 做法相同）。
- Modify: `frontend/e2e`：AI 涨停的路由拦截改为新路径，测试数据换成新契约形状。

- [ ] **Step 1: 改测试**：
  - 外壳、市场、AI 涨停的现有用例改用新契约形状的测试数据；
  - 新增 `client surfaces error contract message`，`client network failure message`。
- [ ] **Step 2: 运行，确认失败**：`npm test`
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行 `npm test && npm run typecheck && npm run build && npx playwright test`**，全部通过
- [ ] **Step 5: Commit**：`refactor(web): 前端改用契约生成类型与统一请求客户端（#662）`

---

### Task 6: 收尾

- [ ] 确认 `grep -rn "app_market\|read_cache\|shell_status\|qsys.web.spa" src tests frontend` 没有残留引用。
- [ ] 运行全量 `uv run pytest -q`；前端全套检查。
- [ ] 本地只读预览联调外壳、市场、AI 涨停：时间带偏移且显示正确；未启用、未配置时的变体正常。
- [ ] 推送需求分支；在 #662 记录。
