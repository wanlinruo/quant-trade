# Web 改版 09：数据组 4 页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在新前端做出数据组 4 页：数据总览 `/app/dataov`、数据表 `/app/tables`、指标字典 `/app/catalog`、数据质量 `/app/quality`。同时修掉外壳在窄屏不自动收起侧栏的问题。

**Architecture:**
- 新增只读接口 `/api/app/data/*`（全部 GET、no-store）。复用现有 `DataAssetService`、`CatalogService`、`DomainService`、`WarehouseQuery`，派生在后端完成：
  - 五维健康矩阵、血缘图、近 30 次质检结构、警告来源归类、表样本。
- 列表类接口（总览、表清单、指标清单、质检汇总）经 60 秒服务端缓存。#442 已把覆盖统计挪到后台，常态下应在 1 秒内返回，执行时实测并记录。
- 旧页面与旧接口不改。

**Tech Stack:** 同前几份计划。血缘图使用 ECharts `graph`，需在 `EChart.tsx` 注册 `GraphChart`。

**Spec:** `docs/superpowers/specs/2026-11-17-web-redesign-design.md`（§8.13、§9、§10、§11 #449）

**路线说明：** 计划 01 路线表中 09 的“#442 性能治理”已在 2026-10-20 发布（#442、#448 均已关闭），本计划只做实测确认。“旧模板下线”属于最终合入阶段，不在本计划内。

## Global Constraints

- 前几份计划的全部 Global Constraints 继续有效。
- 数据组页面全部只读，没有任何写入口，不会在 `test_no_write_endpoints_exist` 中出现新的方法。
- 表样本只读：
  - 每次最多 20 行，只读一个 Parquet 行组中的一批，不整表加载。
  - 不使用 DuckDB 全表扫描；拿不到的值原样显示 `null`（§8.13）。
- 健康状态沿用 `HealthStatus`（红·阻断、黄·有风险、绿·健康、灰·缺证据），映射到 §7：红 → 失败，黄 → 警告，绿 → 正常，灰 → 待运行。灰表示“缺证据”，不能显示成正常。
- 五维名称沿用 `asset_views._DIMENSION_LABELS`：时效、完整、结构、真实、血缘。设计文档中的“新鲜度、完整性、有效性、一致性、关系”与这五维一一对应，页面使用代码中的现名。

## 设计者决定（执行时写进页面 ⓘ）

- **近 30 次质检**：按 `metric_checks.run_id` 分组，取 `checked_at` 最新的 30 个 run。每个 run 统计：
  - 字段级检查总数；
  - 通过数；
  - 阻断失败数：`passed=false` 且 `severity` 为阻断级；
  - 警告数：`passed=false` 且非阻断。
  - 表级（`dataset_checks`）按同一 run_id 统计 红/黄/绿/灰。
  - 某 run 没有表级记录时，表级四项为 null，不显示为 0。
- **警告来源归类**（只看最近一次 run 的未通过、非阻断记录）：
  - `known_issue == "上游数据自相矛盾"` → “上游自相矛盾（#653）”；
  - 其他非空 `known_issue` → “已登记缺口（#594）”；
  - 空 `known_issue` → “待归类”。
- **表样本**：
  - 物理表取最新分区（`year=*` 中最大的 year；不分区表直接取 `part.parquet`）。
  - 读取该分区最后一个行组的前 20 行。
  - 页面写明“最新分区最后一个行组的前 20 行，只读抽样，不代表全表”。
  - 逻辑视图或文件不存在时，显示后端给的原因。
- **血缘图**：
  - 节点：数据源（`SOURCES`）、各层数据集（按 `DatasetLayer` 分列）、外部消费者（`ext_consumers` 去重）。
  - 边：数据源 → 数据集（来自 `Dataset.sources`），以及 `DEPENDENCIES` 中的上游 → 下游。
  - 节点颜色取健康状态（数据源与消费者为中性色）。
  - 悬停时高亮上下游（ECharts `emphasis.focus: 'adjacency'`）。
- **窄屏侧栏**：视口宽度 < 992px（antd `lg` 断点）时，侧栏自动收起为 56px。手动展开或收起仍可用，并照常记入 `qs-sider`；从窄屏变回宽屏时恢复用户保存的偏好。

## Review Focus

1. **表没有任何质检记录**：
   - 五维全部显示灰·缺证据，不显示绿。
   - 归 Task 2 `test_overview_matrix_gray_without_evidence`。
2. **质检历史中某 run 只有字段级、没有表级记录**：
   - 该 run 的表级计数为 null，不为 0。
   - 归 Task 1 `test_quality_runs_table_counts_null_when_absent`。
3. **抽样的表是逻辑视图或文件缺失**：
   - 返回 `available=false` 与原因，不报 500。
   - 归 Task 1 `test_sample_unavailable_for_logical_or_missing`。
4. **样本中有 null 值**：
   - 原样返回 null，前端显示 `null`。
   - 归 Task 1 `test_sample_keeps_nulls`，以及 Task 4 `sample shows null literally`。
5. **390 宽视口**：
   - 侧栏自动收起，数据页无整页横向溢出。矩阵和血缘图在面板内滚动。
   - 归 Task 6 的 e2e。

---

### Task 1: 质检结构、警告归类、血缘图与表样本的纯函数

**Files:**
- Create: `src/qsys/web/data_app_views.py`
- Test: `tests/web/test_data_app_views.py`

**Interfaces:**
- Produces:
  - `quality_runs(metric_checks: pl.DataFrame, dataset_checks: pl.DataFrame, limit: int = 30) -> list[dict]`：
    - 返回值为 `[{run_id, checked_at, fields: {total, passed, blocking, warning}, tables: {red, yellow, green, gray} | None}]`，按 checked_at 降序。
    - checked_at 取该 run 的最大值。
    - 判断阻断级时，以 `metric_checks.severity` 的实际取值为准（执行时在 `qsys.data.catalog` 中核对常量），不另造字符串。
  - `warning_sources(metric_checks: pl.DataFrame) -> list[dict]`：
    - 返回值为 `[{key: "upstream"|"registered"|"unclassified", label, issue: "#653"|"#594"|None, count, samples: [{metric_id, check_id, detail}]}]`。
    - 只统计最新 run 的未通过、非阻断记录，每类最多 5 条样本。
  - `lineage_graph() -> dict`：
    - 返回值为 `{nodes: [{id, label, kind: "source"|"raw"|"canonical"|"derived"|"view"|"consumer", status: str|None}], edges: [{source, target}]}`。
    - 读取 `DATASETS`、`DEPENDENCIES`、`SOURCES`；状态由调用方另行合并。
    - kind 按 `DatasetLayer` 实际枚举值映射，执行时核对。
  - `table_sample(root: Path, dataset: Dataset, limit: int = 20) -> dict`：
    - 返回值为 `{available, message, file, row_group, columns, rows}`。
    - 用 `pyarrow.parquet.ParquetFile(...).iter_batches(batch_size=limit, row_groups=[last])` 只取第一批。
    - `schema_id` 为 None 或文件不存在时返回 `available=false` 与原因。
    - 日期、时间转 ISO 字符串；null 保留。

- [ ] **Step 1: 写失败测试**

```python
def test_quality_runs_counts_and_order(): ...                     # 两个 run：字段通过/阻断/警告计数正确，新 run 在前
def test_quality_runs_table_counts_null_when_absent(): ...        # run 只有 metric_checks → tables is None
def test_quality_runs_limit_30(): ...
def test_warning_sources_classification(): ...                    # 上游自相矛盾→#653；其他 known_issue→#594；空→待归类；阻断不计入；只看最新 run
def test_lineage_graph_has_sources_datasets_consumers_and_edges(): ...  # 每条 DEPENDENCIES 都成边；节点 id 唯一
def test_sample_reads_last_row_group_limit(tmp_path): ...         # 写 3 个行组的 parquet → 返回最后行组前 20 行
def test_sample_keeps_nulls(tmp_path): ...
def test_sample_unavailable_for_logical_or_missing(tmp_path): ...
```

- [ ] **Step 2: 运行，确认失败**：`uv run pytest tests/web/test_data_app_views.py -q`
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行通过**
- [ ] **Step 5: Commit**：`feat(web): 数据组质检结构、警告归类、血缘图与表样本的派生（#662）`

---

### Task 2: 数据组只读接口

**Files:**
- Create: `src/qsys/web/app_data.py`（`register_app_data(app, *, warehouse_root, assets, catalog)`）
- Modify: `src/qsys/web/app.py`（在 `mount_spa` 之前注册）
- Test: `tests/web/test_app_data.py`（夹具复用 `tests/web/` 中已有的资产与目录测试数据，执行时查找 `test_asset*`、`test_catalog*`）

**Interfaces:**
- Consumes: Task 1；`DataAssetService.tables()/table()/rule()/_assessment()`；`CatalogService.rows()/summary()/detail()`；`DomainService.rows()`；`WarehouseQuery.metric_checks()/dataset_checks()`
- Produces（全部 GET、no-store；依赖服务为 None 时返回 `{"enabled": false}`；序列化统一用 `fastapi.encoders.jsonable_encoder`，枚举转 `.value`）：
  - `/api/app/data/overview`，返回：
    - `enabled`、`dimension_labels`、`status_labels`
    - `tables: [{id, name, layer, domain, domain_label, status, dimensions: {freshness|completeness|structure|validity|lineage: status}}]`
    - `domains`：`DomainService.rows()`
    - `lineage`：`lineage_graph()`，节点 status 已合并表健康状态
    - 经 60 秒缓存。
  - `/api/app/data/tables?include_raw=`，返回：
    - `{enabled, rows: [{id, name, layer, domain_label, status, purpose, grain, coverage: {...}|null, coverage_pending, coverage_failed}]}`
    - 经 60 秒缓存，按 include_raw 分开缓存。
  - `/api/app/data/tables/{id}`：`DatasetDetail` 序列化，加 `status_label`、`dimension_labels`。不存在时返回 404。
  - `/api/app/data/tables/{id}/sample`：`table_sample(...)`，不缓存。
  - `/api/app/data/catalog?dataset=&kind=&status=`，返回 `{enabled, summary, rows, datasets, kinds, statuses}`。
  - `/api/app/data/catalog/{metric_id}`：`MetricDetail` 序列化，其中 `evidence` 为人工抽样记录。不存在时返回 404。
  - `/api/app/data/quality`，返回 `{enabled, runs: quality_runs(...), warning_sources: warning_sources(...), rules: [{id, name, category, scope, known_issues, upstream_self_consistency, latest_status}]}`，经 60 秒缓存。
  - `/api/app/data/quality/rules/{check_id}`：`RuleDetail` 序列化。`latest` 中 `sample_path` 非空的行附 `sample_url = /data/catalog/sample?path=...`（沿用旧下载路由）。不存在时返回 404。

- [ ] **Step 1: 写失败测试**

```python
def test_endpoints_disabled_without_services(...)          # 6 个列表/详情接口 {"enabled": False}
def test_overview_matrix_gray_without_evidence(...)        # 空仓：五维全部 "gray"，status "gray"
def test_overview_lineage_nodes_cover_datasets(...)
def test_table_detail_and_404(...)
def test_table_sample_endpoint(...)                         # 夹具表写一个小 parquet → rows 非空；逻辑视图 → available False
def test_catalog_list_and_detail_with_evidence(...)
def test_quality_endpoint_shapes(...)
def test_rule_detail_has_sample_url(...)
def test_list_endpoints_cached_60s(...)                     # 计数包装：两次请求只算一次
def test_still_no_write_endpoints(...)                      # 全配置下 app 路由方法集合未新增 /api/app/data 写方法
```

- [ ] **Step 2: 运行，确认失败**：`uv run pytest tests/web/test_app_data.py -q`
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行 `uv run pytest tests/web -q`**，确认 PASS
- [ ] **Step 5: Commit**：`feat(web): 新前端数据组只读接口（#662）`

---

### Task 3: 数据总览页

**Files:**
- Create: `frontend/src/api/data.ts`（类型与各 fetch 函数）
- Create: `frontend/src/pages/data/DataOverviewPage.tsx`、`HealthMatrix.tsx`、`LineageGraph.tsx`、`healthStatus.ts`（`HealthStatus → Status` 映射与文字）
- Create: `frontend/src/pages/data-fixtures/`（后端测试夹具导出的真实 JSON，做法同 08c）
- Modify: `frontend/src/page/EChart.tsx`（注册 `GraphChart`）、`frontend/src/pages/registry.ts`（`dataov`）
- Test: `frontend/src/pages/data/DataOverviewPage.test.tsx`

**页面结构：**
- **结论条**：
  - 标题“核心表 N 张：红 a · 黄 b · 绿 c · 灰 d”。
  - 状态取最差的一级：有红为失败，有黄为警告，只有灰为待运行。
- **“核心表健康吗？”** 矩阵：
  - 行为表，按红 → 黄 → 灰 → 绿排序；列为五维与总状态。
  - 单元格用“符号 + 文字”，例如“✕ 红”“○ 灰·缺证据”。
  - 点击行跳转 `/app/tables?id=`。
  - 矩阵在面板内横向滚动。
- **“数据从哪来、被谁用？”** 血缘图：
  - 按 kind 分列布局（source → raw → canonical → derived → view → consumer），x 坐标按列固定，y 坐标在列内均分。
  - 悬停时高亮相邻节点；点击数据集节点跳转表详情。
- **“各数据域怎样？”** 域汇总表：`domains` 原样展示（域、表数、状态分布）。

- [ ] **Step 1: 写失败测试**：
  - `overview hero counts statuses and never shows gray as ok`
  - `matrix rows sorted worst first and cells use symbol+text`
  - `lineage graph option has adjacency focus and fixed columns`（捕获 EChart option）
  - `clicking a matrix row opens table detail`
- [ ] **Step 2–4:** 先运行 `npm test` 确认失败；实现后运行 `npm test && npm run typecheck && npm run build`，确认通过。
- [ ] **Step 5: Commit**：`feat(web): 新前端数据总览页（#662）`

---

### Task 4: 数据表页

**Files:**
- Create: `frontend/src/pages/data/TablesPage.tsx`、`TableDetail.tsx`、`TableSample.tsx`
- Modify: `frontend/src/pages/registry.ts`（`tables`）
- Test: `frontend/src/pages/data/TablesPage.test.tsx`

**页面结构：**
- **列表**：
  - 按域筛选（Select），可勾选“含原始层”（写入 `?raw=1`）。
  - 列：表、层、域、健康、覆盖（行数、标的数、时间范围；覆盖统计中显示“统计中”，失败显示“统计失败”）。
  - 点击写入 `?id=`。
- **详情子标签**（写入 `?tab=`）：
  - **概览**：用途、粒度、主键、时间列、分区键、schema 版本、构建入口、覆盖。
  - **字段**：名称、类型、说明。
  - **质量**：五维、原因列表、证据表、适用规则（链接到 `/app/quality?rule=`）。
  - **血缘**：上游、下游依赖（关系名、键、基数），以及数据源。
  - **样本**：首次切到该标签时才请求；表头写明抽样口径；null 显示为灰色 `null`。

- [ ] **Step 1: 写失败测试**：
  - `tables list filters by domain and shows coverage pending`
  - `detail tabs render overview fields quality lineage`
  - `sample loads lazily and shows null literally`
  - `sample unavailable shows reason`
- [ ] **Step 2–4:** 先运行 `npm test` 确认失败；实现后运行 `npm test && npm run typecheck && npm run build`，确认通过。
- [ ] **Step 5: Commit**：`feat(web): 新前端数据表页（#662）`

---

### Task 5: 指标字典与数据质量页

**Files:**
- Create: `frontend/src/pages/data/CatalogPage.tsx`、`MetricDetail.tsx`、`QualityPage.tsx`、`RuleDrawer.tsx`
- Modify: `frontend/src/pages/registry.ts`（`catalog`、`quality`）
- Test: `frontend/src/pages/data/CatalogPage.test.tsx`、`frontend/src/pages/data/QualityPage.test.tsx`

**指标字典：**
- **结论条**：指标总数、覆盖率、待验证数、最近一次质检时间。
- **列表**：按表、类型、状态筛选（写入 URL）；列为指标、所属表、来源（直取/纯派生）、状态、问题。
- **详情**（`?id=`）：
  - 口径：公式与参数，或直取接口。
  - 上游、下游、外部消费者、根数据源。
  - 最近检查、违规历史折线（null 断开）。
  - **验真证据**：`evidence` 表，列为标的、日期、本系统值、参考值、参考来源、结论、核验人、时间、备注。为空时显示“暂无人工抽样记录”，不显示缺失。

**数据质量：**
- **结论条**：最近一次 run 的字段级阻断/警告数与表级红/黄数。
- **“近 30 次质检结构”**：
  - 堆叠柱图，横轴为 run，字段级通过、警告、阻断分色。
  - 表级计数为 null 的 run 在悬停提示中写“无表级记录”。
- **“警告从哪来？”**：三类卡片，显示计数、Issue 链接（GitHub #653 / #594）和样本。
- **规则表**：id、名称、类别、范围、最新状态、已登记问题。点击打开 RuleDrawer（`?rule=`），展示条件、限制、实现入口、目标、最新结果（违规样本下载链接）和历史。

- [ ] **Step 1: 写失败测试**：
  - `catalog filters write url and detail shows evidence table`
  - `catalog detail without evidence says no manual samples`
  - `quality runs chart stacks counts and marks missing table-level`
  - `warning sources link issues`
  - `rule drawer shows sample download link`
- [ ] **Step 2–4:** 先运行 `npm test` 确认失败；实现后运行 `npm test && npm run typecheck && npm run build`，确认通过。
- [ ] **Step 5: Commit**：`feat(web): 新前端指标字典与数据质量页（#662）`

---

### Task 6: 窄屏侧栏、布局冒烟与本地联调

**Files:**
- Modify: `frontend/src/shell/AppShell.tsx`：Sider 设 `breakpoint="lg"`，`onBreakpoint` 在窄屏收起；宽屏恢复 `qs-sider` 偏好。
- Modify: `frontend/e2e/layout.spec.ts`：新增 `sider collapses automatically below 992px`，断言 390 宽时侧栏宽度 ≤ 56，1280 宽时恢复 196。
- Create: `frontend/e2e/fixtures/data-*.json`（来自 Task 3 导出的样本）
- Modify: `frontend/e2e/pages.spec.ts`：四页在 390/1280/1600 下无整页横向溢出。

- [ ] **Step 1: 先写侧栏用例**，运行确认失败（当前 390 宽时侧栏为 196），实现后通过。
- [ ] **Step 2: 写四页用例**，运行 `npm run build && npx playwright test` 全部通过；在数据总览临时加 2000px 元素，确认 390 用例失败后撤销。
- [ ] **Step 3: 本地联调**：只读预览（空 warehouse）打开四页，显示空状态或灰·缺证据，不报错。实测 `/api/app/data/overview` 与 `/api/app/data/tables` 冷、热耗时并记录到 #662。
- [ ] **Step 4: Commit 并推送**：`test(web): 数据组布局冒烟；窄屏自动收起侧栏（#662）`

---

## 完成后

- 在 #662 评论记录：完成情况、裁决、“设计者决定”中的口径、接口实测耗时。
- 记录待最终合入时确认的事项：旧 `/data` 页中的“退市整理期股票池”和全仓覆盖总数，新数据总览没有对应面板（设计文档 §8.13 未列）。下线旧页前请维护者决定是否保留。
- 本计划不涉及任何生产组件或控制面改动，留在 `feat/web-redesign`。
