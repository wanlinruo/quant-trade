# 打板看板 P1：数据与派生核心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现打板看板的纯函数核心：同花顺概念快照、题材归列、连板高度、今/昨晋级，以及盘后对账。用已入库的历史交易日验证这些逻辑，**不包含**盘中采集、Web 页面和发布。

**Architecture:** 新建包 `src/qsys/data/board/`，每个文件负责一件事：概念快照（拉取与排除名单）、归列、梯队（高度/晋级/今昨对比）、盘后对账。全部是输入 → 输出的纯函数，输入只有直取或纯派生字段，缺失值显示为未知（`None`），不推断。Tushare 访问只新增两个 provider 方法。

**Tech Stack:** Python 3.11、polars、pydantic、pytest（`uv run pytest`）。

**Spec:** `docs/superpowers/specs/2026-11-11-limit-up-board-dashboard-design.md`；探针与排除名单见 `docs/reference/ths-concept-probe.md`。

## Global Constraints

- 数据铁律（AGENTS.md §3）：只允许直取与纯派生；拿不到就用 `None`，不填默认值、不做单位换算、不按名称或价格推断 ST、涨跌停价或退市整理期。
- ST 只认 `daily_flags.is_st`；涨停价只认 `stk_limit` 直取；昨日连板数非 ST 取 `limit_list_daily.limit_times`（`limit_type == "U"`），ST 用 `daily_flags.is_limit_up` 按交易日连续计数，序列中任一日缺行则为 `None`。
- 归列只在概念类（`ths_index` 且 `type == "N"`）中选；按股票反查的结果必须按概念代码集合过滤。
- 排除名单固定为 `ths-concept-probe.md` 中的 32 个概念代码（按代码，不按名称）。
- "当日涨停家数"按**截至当前时刻当日封过板**的家数计，只增不减；ST 计入，北交所（`.BJ`）不计入。
- 题材家数并列时按概念代码升序决胜；所有可归概念都被排除的票归"其他"（`OTHER = "其他"`）。
- 归列稳定性：首次归列后保持不动，只有原概念家数跌到新最高概念家数的一半以下（`<`）才改列。
- 不新建预测模型；本 P1 不读取影子榜单。

## Review Focus

1. **某天 `limit_list_daily` 迟到或为空**：昨日高度全部显示未知，而不是全部变成首板。由 Task 3 的 `test_missing_previous_day_list_makes_heights_unknown` 锁定。
2. **ST 票的连板序列中间缺一天 `daily_flags`**：高度为 `None`，不跳过缺口继续计数。由 Task 3 的 `test_st_gap_makes_height_unknown` 锁定。
3. **同一只票同时属于多个家数相同的概念**：按概念代码升序决出，结果可复现。由 Task 2 的 `test_tie_breaks_by_concept_code` 锁定。
4. **反查结果混入行业或地域指数**：不能被选为题材列。由 Task 2 的 `test_non_concept_index_is_never_assigned` 锁定。
5. **对账时盘中首封与直取首封相差恰好一个快照间隔（15s）**：判为一致；超过才标差异。由 Task 4 的 `test_first_seal_within_one_interval_matches` 锁定。

---

### Task 1: 同花顺概念快照与排除名单

**Files:**
- Modify: `src/qsys/data/providers/tushare.py`（`TushareProvider` 新增两个方法）
- Create: `src/qsys/data/board/__init__.py`、`src/qsys/data/board/concepts.py`
- Test: `tests/board/test_concepts.py`

**Interfaces:**
- Produces:
  - `TushareProvider.ths_index() -> pl.DataFrame`：调用 `ths_index(exchange="A", type="N")`，原样返回列 `ts_code, name, count, exchange, list_date, type`。
  - `TushareProvider.ths_member(ts_code: str) -> pl.DataFrame`：原样返回列 `ts_code, con_code, con_name`。
  - `EXCLUDED_CONCEPTS: frozenset[str]`：32 个 `xxxxxx.TI` 代码，逐字照抄 `ths-concept-probe.md` 的表格。
  - `@dataclass(frozen=True) ConceptSnapshot(day: date, fetched_at: datetime, members: Mapping[str, frozenset[str]], index_count: Mapping[str, int], source_sha: str)`。`members` 的键是概念代码，值是成分股代码集合；`index_count` 记录 `ths_index.count`，与实际行数不一致时并存，不互相替代。
  - `ConceptSnapshot.concepts_of(symbol: str) -> frozenset[str]`：返回该股所属的、**不在排除名单中**的概念代码。
  - `build_snapshot(day: date, index: pl.DataFrame, members: Mapping[str, pl.DataFrame], fetched_at: datetime) -> ConceptSnapshot`：只保留 `type == "N"` 的概念；`source_sha` 是全部输入行按排序后序列化再取 sha256。

- [ ] **Step 1: Write the failing tests**

```python
def test_excluded_concepts_match_probe_doc():
    assert len(EXCLUDED_CONCEPTS) == 32
    assert {"885338.TI", "883300.TI", "885840.TI", "885705.TI"} <= EXCLUDED_CONCEPTS
    assert "885517.TI" not in EXCLUDED_CONCEPTS  # 机器人概念参与

def test_concepts_of_drops_excluded_and_keeps_count_mismatch():
    snap = build_snapshot(date(2026, 11, 12), index_df, {"885338.TI": m1, "885517.TI": m2}, FETCHED)
    assert snap.concepts_of("600000.SH") == {"885517.TI"}
    assert snap.index_count["885338.TI"] == 3844 and len(snap.members["885338.TI"]) == 3  # 不互相替代

def test_snapshot_sha_is_order_independent():
    assert build_snapshot(D, idx, members, T).source_sha == build_snapshot(D, idx.reverse(), members, T).source_sha
```

- [ ] **Step 2: Run** `uv run pytest tests/board/test_concepts.py -v`，预期因模块不存在而 FAIL。
- [ ] **Step 3: Implement**：provider 方法沿用 `limit_list_d` 的写法（`self._client.call` + `pl.DataFrame(rows)`），`concepts.py` 按上面的签名实现。
- [ ] **Step 4: Run** 同一命令，预期 PASS。
- [ ] **Step 5: Commit** `feat(board): 同花顺概念快照与泛概念排除名单 (#648)`

### Task 2: 题材归列

**Files:**
- Create: `src/qsys/data/board/assign.py`
- Test: `tests/board/test_assign.py`

**Interfaces:**
- Consumes: Task 1 的 `ConceptSnapshot.concepts_of`。
- Produces:
  - `OTHER = "其他"`
  - `concept_counts(sealed_today: Iterable[str], snapshot: ConceptSnapshot) -> dict[str, int]`：统计每个概念当日封过板的家数，不计入 `.BJ`。
  - `assign(symbol: str, counts: Mapping[str, int], snapshot: ConceptSnapshot, previous: str | None = None) -> str`：选家数最多的概念，并列时按代码升序；全部被排除时返回 `OTHER`。若 `previous` 不为空且不是 `OTHER`，只有当 `counts[previous] * 2 < counts[best]` 时才改为 `best`，否则保持 `previous`。

- [ ] **Step 1: Write the failing tests**：`test_picks_concept_with_most_sealed`、`test_tie_breaks_by_concept_code`、`test_all_excluded_goes_to_other`、`test_bj_not_counted_but_st_counted`、`test_non_concept_index_is_never_assigned`（反查到的非 `type == "N"` 代码不在快照中，因此不会被选中）、`test_sticky_until_less_than_half`。最后一个用例的断言为：`(prev=4, best=8)` 保持原列，`(prev=3, best=8)` 改列。
- [ ] **Step 2: Run** `uv run pytest tests/board/test_assign.py -v`，预期 FAIL。
- [ ] **Step 3: Implement** `assign.py`。
- [ ] **Step 4: Run** 同一命令，预期 PASS。
- [ ] **Step 5: Commit** `feat(board): 题材归列与改列稳定性 (#648)`

### Task 3: 连板高度、晋级与今/昨对比

**Files:**
- Create: `src/qsys/data/board/ladder.py`
- Test: `tests/board/test_ladder.py`

**Interfaces:**
- Produces:
  - `previous_heights(prev_day: date, limit_list: pl.DataFrame, flags: pl.DataFrame, sessions: Sequence[date]) -> dict[str, int | None]`。`limit_list` 是 `WarehouseQuery.limit_list(None, start, prev_day)` 的结果；`flags` 是 `daily_flags`，覆盖 `sessions` 中截至 `prev_day` 的交易日。非 ST 票取 `limit_type == "U"` 的 `limit_times`；ST 票（当日 `is_st is True`）取 `is_limit_up` 按交易日从 `prev_day` 往回连续计数，遇到缺行返回 `None`。`prev_day` 的 `limit_list` 整天为空时，所有非 ST 票返回 `None`。
  - `today_height(prev: int | None, sealed: bool) -> int | None`：已封板且 `prev` 为 `None` 时返回 `None`（未知不当首板）；已封板时返回 `prev + 1`（`prev == 0` 表示昨日未涨停，结果为首板 `1`）；未封板时返回 `0`。
  - `@dataclass(frozen=True) PromotionRow(level: int, yesterday: tuple[str, ...], promoted: tuple[str, ...])`
  - `promotions(prev: Mapping[str, int | None], sealed_today: Set[str]) -> list[PromotionRow]`：按昨日高度 N ≥ 1 分组，高度从大到小；高度为 `None` 的票不进入任何组。

- [ ] **Step 1: Write the failing tests**：`test_non_st_height_from_limit_times`、`test_st_height_counts_consecutive_flags`、`test_st_gap_makes_height_unknown`、`test_missing_previous_day_list_makes_heights_unknown`、`test_unknown_previous_never_becomes_first_board`（`today_height(None, True) is None`）、`test_promotions_grouped_desc`（断言 `[r.level for r in rows] == [3, 1]` 以及每组的 `promoted` 集合）。
- [ ] **Step 2: Run** `uv run pytest tests/board/test_ladder.py -v`，预期 FAIL。
- [ ] **Step 3: Implement** `ladder.py`。
- [ ] **Step 4: Run** 同一命令，预期 PASS。
- [ ] **Step 5: Commit** `feat(board): 连板高度与今昨晋级 (#648)`

### Task 4: 盘后对账

**Files:**
- Create: `src/qsys/data/board/reconcile.py`
- Test: `tests/board/test_reconcile.py`

**Interfaces:**
- Consumes: Task 3 的高度。
- Produces:
  - `@dataclass(frozen=True) Observed(symbol: str, height: int | None, first_seal: datetime | None, open_times: int)`
  - `@dataclass(frozen=True) Mismatch(symbol: str, field: str, observed: object, direct: object)`
  - `reconcile(observed: Sequence[Observed], limit_list_day: pl.DataFrame, interval_s: float = 15.0) -> list[Mismatch]`：只对 `limit_list_day` 中 `limit_type == "U"` 的票比较 `height` 与 `limit_times`、`first_seal` 与 `first_time`（HHMMSS 解析为当日时刻），以及 `open_times`。首封时间相差不超过 `interval_s` 视为一致。直取值为 null 时不比较，也不报差异。

- [ ] **Step 1: Write the failing tests**：`test_first_seal_within_one_interval_matches`（相差 15s 时无差异，16s 时报 `first_seal`）、`test_height_and_open_mismatch_reported`、`test_null_direct_field_is_not_compared`。
- [ ] **Step 2: Run** `uv run pytest tests/board/test_reconcile.py -v`，预期 FAIL。
- [ ] **Step 3: Implement** `reconcile.py`。
- [ ] **Step 4: Run** 同一命令，预期 PASS。
- [ ] **Step 5: Commit** `feat(board): 盘中观测与直取盘后对账 (#648)`

### Task 5: 历史交易日只读验收脚本

**Files:**
- Create: `src/qsys/data/board/history_cli.py`
- Modify: `tests/scripts/test_scripts_import.py`（把新脚本加进导入清单）
- Test: `tests/board/test_history_check.py`

**Interfaces:**
- Consumes: Tasks 1–3。
- Produces: `summarize(day: date, prev_day: date, query: WarehouseQuery, sessions: Sequence[date], snapshot: ConceptSnapshot | None) -> dict`。返回 `{"sealed": int, "heights": {level: count}, "unknown_heights": int, "promotions": [{"level", "yesterday", "promoted"}], "columns": [(concept, count)] | None}`。`snapshot` 为 `None` 时（上线前的历史日）`columns` 也为 `None`，不分题材。CLI 用法为 `--warehouse --day [--concepts-json]`，只读运行，结果打印为 JSON。

- [ ] **Step 1: Write the failing test** `test_summary_without_snapshot_has_no_columns`，在临时 warehouse 中写入两天的 `daily_flags` 与 `limit_list_daily`，断言 `heights` 与 `promotions`。
- [ ] **Step 2: Run** 后预期 FAIL；**Step 3** 实现；**Step 4** 运行后预期 PASS。
- [ ] **Step 5: 生产只读验收**：在节点上用 service-checks 的 venv 对 2026-11-11 运行脚本（只读 warehouse），把输出与当日 `limit_list_d` 的连板数分布对照，结论写进 #648 评论。
- [ ] **Step 6: Commit** `feat(board): 历史交易日只读汇总 (#648)`
