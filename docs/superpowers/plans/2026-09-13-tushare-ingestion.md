# Tushare Pro 接入（计划 C） —— 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把日线真值源从「百度网盘转售 + 人工转存」换成 Tushare Pro API 直连，并接入 `stk_limit` / `suspend_d` / `index_daily` / `stock_basic` / `namechange` / `limit_list_d` 六个接口，把涨跌停价、停牌、退市股、ST 状态、首板炸板从派生改为直取。

**Architecture:** 新增一个薄 HTTP client（直调 REST，不引 `tushare` 包）与一个 provider 层；日线三接口（`daily` + `daily_basic` + `adj_factor`）按交易日拉取后合成现有 `PAID_DAILY` 的 28 列 schema，落入新表 `tushare_daily`；全量重拉后与现有 `paid_daily` 逐键对账，报告过目再切换生产查询。其余六张真值表各自独立成表，互不混源。

**Tech Stack:** Python 3.12 / polars / pyarrow / urllib（标准库，不引新依赖）/ structlog / pytest

**Spec:** `docs/superpowers/specs/2026-09-12-paid-daily-truth-source-design.md`（§2.1/§8/§9 已于 2026-09-13 作废，本计划承接其作废后的空缺）

**前置依赖**：计划 A（`docs/superpowers/plans/2026-09-13-paid-daily-truth-source.md`）的 Task 0–2b 已完成。本计划的 Task 4/7/8 会与计划 A 的 Task 6/7 在 `flags.py` 上相遇，届时以本计划的直取值优先。

## Global Constraints

以下每一条都来自 spec，**每个任务的要求都隐含包含本节**：

- **禁止编造数据。** 每一列只允许两类：直取（接口原值）、纯派生（直取列的确定性算术，公式写进代码注释，可逐位重算）。拿不到就记 null 由质检报出来，**不准填**。
- **本规矩当前无任何例外。** spec §2.1 的 ST 结转例外已于 2026-09-13 撤回。
- **单位换算只做乘法，且必须实测确认后再写死。** 已实测：`daily.amount` 千元 ×1000 = 元；`daily.vol` 手（不换算）；`daily_basic` 的 `total_share`/`float_share`/`free_share` 万股 ×10000 = 股；`total_mv`/`circ_mv` 万元 ×10000 = 元。
- **凭证只读 `config/deploy.local.toml`（已 gitignore）**，绝不进代码库、日志、异常消息、测试夹具。`repr` 必须屏蔽。
- **所有测试不得联网。** client 走依赖注入，测试注入假 transport。
- **`paid_daily` 与 `bars_daily` 均为冻结表**，本计划只读不写。
- **不引入新的第三方依赖。** Tushare 的 REST 接口用标准库 `urllib` 即可，`tushare` 包会拖 pandas 且无必要。
- 每个任务结束必须全量 `pytest` 通过再提交。**本机 `pytest -q` 不打印最终汇总行**（退出码正常），统计数量用 `-v` 或 `-rfE --tb=no`。
- 注释与文档用中文，解释「为什么」而非复述代码。

## 已实测的事实（写代码的依据，不要重新假设）

2026-09-13 探针结论，完整记录见 `artifacts/tushare_probe_20260913.json`：

| 项 | 实测值 |
|---|---|
| 账号档位 | 5000 积分（¥500/年，2026-09-13 购入，**一年有效、到期回收**） |
| 连通性 | 开发机与生产节点均直连 `https://api.tushare.pro` 正常，节点单次 0.38 秒 |
| 频次 | 连打 12 次 `daily` 全成功，累计 5.71 秒，未触发限流 |
| `daily` / `daily_basic` / `adj_factor` | 2026-09-11 分别 5550 / 5550 / 5562 行 |
| `stk_limit` 历史 | 至少回溯到 2010-01-04（1671 行）；2005-01-04 及更早返回 0 行 |
| `limit_list_d` 历史 | 2020-01-02 有 84 行、2019-01-02 为 0 → **自 2020 年起**，且不含 ST |
| `stock_basic` | 在市 5562、退市 339 |
| `index_daily` | `000300.SH` 可取，字段含 open/high/low/close/pre_close/vol/amount |
| `suspend_d` | 2026-09-11 有 13 只，字段 `ts_code/trade_date/suspend_timing/suspend_type`（S 停牌 / R 复牌） |
| `namechange` | **区间表**，字段 `ts_code/name/start_date/end_date/ann_date/change_reason` |

### 与现有 `paid_daily` 的逐键对账（2026-09-11，生产节点执行）

- 5550 行全部可连接，两边各自独有 **0** 条。
- `open`/`high`/`low`/`close`/`pre_close`/`volume`/`amount`/`factor` 及 11 个估值比率列：
  最大差 **0**，非零 **0** 条。
- **有差异的 4 列**：

| 列 | 最大差 | 非零条数 | 判定 |
|---|---:|---:|---|
| `dividend_yield` | 0.0007 | 2 | 舍入级 |
| `dividend_yield_ttm` | 0.0007 | 1 | 舍入级 |
| `circulating_shares` | 77 股 | 2 | 见下 |
| `total_shares` | **190,768,392 股** | 4 | **实质差异** |

`total_shares` 差异最大的四只是 `601633.SH` 长城汽车、`603259.SH` 药明康德、
`688131.SH` 皓元医药、`688347.SH` 华虹宏力。**四只的 `market_cap` 完全一致**，
且用 `market_cap / close` 反算得到的股本**正好等于付费源的 `total_shares`**
（华虹宏力：380,683,722,448 / 219.07 = 1,737,726,402，与付费源逐位相同）。

其中三只是 A+H 股：Tushare 的 `total_share` 是含 H 股的总股本，`total_mv` 只计 A 股，
两者本就不满足 `total_share × close = total_mv`。

**结论：卖家的 `total_shares` 是用「市值 ÷ 收盘价」反算的，不是直取。**
按 Global Constraints 第一条，反算属明令禁止。**换源修掉了一个既存违规**，
本计划必须落 Tushare 的原值，不得为「与旧值一致」而复刻那个反算。

---

## File Structure

**新建**

| 文件 | 职责 |
|---|---|
| `src/qsys/data/providers/tushare_client.py` | 薄 HTTP client：签名、重试、错误原样上报。不含任何业务语义 |
| `src/qsys/data/providers/tushare.py` | provider 层：每个接口一个方法，返回规范化的 polars DataFrame（改名、单位换算、类型） |
| `src/qsys/data/pipeline/tushare_daily.py` | 三接口合成 28 列，按年落 `tushare_daily` |
| `src/qsys/data/pipeline/tushare_reference.py` | `stk_limit` / `suspend_d` / `index_daily` / `stock_basic` / `namechange` / `limit_list_d` 六张表的落库 |
| `src/qsys/data/quality/tushare_reconcile.py` | `tushare_daily` vs `paid_daily` 的逐键对账 |
| `scripts/tushare_backfill.py` | 全量/区间重拉 CLI |
| `scripts/tushare_daily_sync.py` | 每日增量 CLI |

**修改**

| 文件 | 变化 |
|---|---|
| `src/qsys/live/config.py` | 新增 `TushareConfig`（token 屏蔽 repr）与加载 |
| `src/qsys/data/schema.py` | 新增 6 张表 schema 并注册进 `PARTITIONED`/`UNPARTITIONED` |
| `src/qsys/data/query.py` | 新增 6 个读取方法 |

---

## Task 1: 凭证配置 + Tushare HTTP client

**Files:**
- Modify: `src/qsys/live/config.py`
- Create: `src/qsys/data/providers/tushare_client.py`
- Test: `tests/live/test_tushare_config.py`、`tests/data/providers/test_tushare_client.py`

**Interfaces:**
- Produces:
  - `qsys.live.config.TushareConfig(token: str, points: int, purchased: str)`，`token` 字段 `repr=False`
  - `qsys.live.config.load_tushare_config(path=None) -> TushareConfig`
  - `qsys.data.providers.tushare_client.TushareClient(token: str, *, transport=None, max_retries: int = 3)`
  - `TushareClient.call(api_name: str, params: dict, fields: str = "") -> list[dict]`
    —— 成功返回行的列表（每行是 `{字段名: 值}`）；失败抛 `TushareError`
  - `qsys.data.providers.tushare_client.TushareError`

- [ ] **Step 1: 写失败测试**

```python
# tests/live/test_tushare_config.py

"""Tushare 凭证加载。token 绝不能出现在 repr / 日志 / 异常里。"""

import pytest

from qsys.live.config import TushareConfig, load_tushare_config

SAMPLE = """
[tushare]
token = "deadbeefcafe"
points = 5000
purchased = "2026-09-13"
"""


def test_loads_token_points_and_purchase_date(tmp_path):
    cfg_path = tmp_path / "deploy.local.toml"
    cfg_path.write_text(SAMPLE)
    cfg = load_tushare_config(cfg_path)
    assert cfg.token == "deadbeefcafe"
    assert cfg.points == 5000
    assert cfg.purchased == "2026-09-13"


def test_token_is_hidden_from_repr(tmp_path):
    """凭证进了 repr 就会进日志、进异常、进 pytest 失败输出。"""
    cfg_path = tmp_path / "deploy.local.toml"
    cfg_path.write_text(SAMPLE)
    cfg = load_tushare_config(cfg_path)
    assert "deadbeefcafe" not in repr(cfg)


def test_missing_file_says_which_file(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        load_tushare_config(tmp_path / "nope.toml")
    assert "nope.toml" in str(exc.value)


def test_missing_section_is_an_explicit_error(tmp_path):
    """没配凭证要显式报错，不能给个空 token 让调用方在网络层才发现。"""
    cfg_path = tmp_path / "deploy.local.toml"
    cfg_path.write_text("[redis]\nhost = \"127.0.0.1\"\n")
    with pytest.raises(ValueError) as exc:
        load_tushare_config(cfg_path)
    assert "tushare" in str(exc.value)
```

```python
# tests/data/providers/test_tushare_client.py

"""Tushare HTTP client。不联网——transport 由测试注入。"""

import json

import pytest

from qsys.data.providers.tushare_client import TushareClient, TushareError


class FakeTransport:
    """记录请求、按脚本返回响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, body: bytes, timeout: float):
        self.requests.append(json.loads(body))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok_payload(fields, items):
    return {"code": 0, "msg": "", "data": {"fields": fields, "items": items}}


def test_returns_rows_as_dicts():
    t = FakeTransport([ok_payload(["ts_code", "close"],
                                  [["000001.SZ", 11.7], ["600000.SH", 8.3]])])
    client = TushareClient("tok", transport=t)
    rows = client.call("daily", {"trade_date": "20260911"})
    assert rows == [{"ts_code": "000001.SZ", "close": 11.7},
                    {"ts_code": "600000.SH", "close": 8.3}]


def test_sends_api_name_params_and_fields():
    t = FakeTransport([ok_payload(["ts_code"], [["000001.SZ"]])])
    TushareClient("tok", transport=t).call("daily", {"trade_date": "20260911"},
                                           "ts_code,close")
    sent = t.requests[0]
    assert sent["api_name"] == "daily"
    assert sent["params"] == {"trade_date": "20260911"}
    assert sent["fields"] == "ts_code,close"


def test_an_empty_result_is_an_empty_list_not_an_error():
    """接口返回 0 行是合法事实（如非交易日），不是错误。"""
    t = FakeTransport([ok_payload(["ts_code"], [])])
    assert TushareClient("tok", transport=t).call("daily", {}) == []


def test_api_error_raises_with_the_upstream_message():
    t = FakeTransport([{"code": 40203, "msg": "抱歉，您没有访问该接口的权限", "data": None}])
    with pytest.raises(TushareError) as exc:
        TushareClient("tok", transport=t).call("limit_list_d", {})
    assert "40203" in str(exc.value)
    assert "没有访问该接口的权限" in str(exc.value)


def test_the_token_never_appears_in_the_exception():
    """异常信息会进日志、进告警、进工单。"""
    t = FakeTransport([{"code": 40001, "msg": "token 无效", "data": None}])
    with pytest.raises(TushareError) as exc:
        TushareClient("s3cr3t-token", transport=t).call("daily", {})
    assert "s3cr3t-token" not in str(exc.value)


def test_retries_transient_failures_then_succeeds():
    t = FakeTransport([TimeoutError("boom"), TimeoutError("boom"),
                       ok_payload(["ts_code"], [["000001.SZ"]])])
    client = TushareClient("tok", transport=t, max_retries=3)
    assert client.call("daily", {}) == [{"ts_code": "000001.SZ"}]
    assert len(t.requests) == 3


def test_gives_up_after_max_retries():
    t = FakeTransport([TimeoutError("boom")] * 3)
    with pytest.raises(TushareError):
        TushareClient("tok", transport=t, max_retries=3).call("daily", {})


def test_does_not_retry_a_permission_error():
    """权限不足重试多少次都一样，只会白白消耗额度。"""
    t = FakeTransport([{"code": 40203, "msg": "没有权限", "data": None}])
    with pytest.raises(TushareError):
        TushareClient("tok", transport=t, max_retries=3).call("limit_list_d", {})
    assert len(t.requests) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/live/test_tushare_config.py tests/data/providers/test_tushare_client.py -v`
Expected: FAIL，`ImportError: cannot import name 'TushareConfig'`

- [ ] **Step 3: 加配置**

```python
# src/qsys/live/config.py —— 在 DeployConfig 之后插入

@dataclass(frozen=True, slots=True)
class TushareConfig:
    """Tushare Pro 凭据。

    **积分一年有效期、到期回收**——到期未续费则日线源直接中断。
    `purchased` 留作到期提醒的依据。
    """

    token: str = field(default="", repr=False)  # 不进 repr / 日志 / 异常
    points: int = 0
    purchased: str = ""


def load_tushare_config(path: str | Path | None = None) -> TushareConfig:
    target = Path(path) if path is not None else DEFAULT_PATH
    if not target.exists():
        raise FileNotFoundError(f"{target} 不存在。Tushare 凭据只写在这里（已 gitignore）。")
    raw = tomllib.loads(target.read_text())
    section = raw.get("tushare")
    if not section or not section.get("token"):
        # 显式报错而不是给个空 token——空 token 要到网络层才暴露，
        # 那时错误信息是「token 无效」，查起来绕远路。
        raise ValueError(f"{target} 里没有 [tushare] token")
    return TushareConfig(
        token=section["token"],
        points=int(section.get("points", 0)),
        purchased=str(section.get("purchased", "")),
    )
```

- [ ] **Step 4: 实现 client**

```python
# src/qsys/data/providers/tushare_client.py

"""Tushare Pro 的薄 HTTP client。

**为什么不用 `tushare` 包**：它拖 pandas 且把错误包成 DataFrame，
而我们需要原样拿到 upstream 的 code/msg 才能判断「该不该重试」。
REST 接口本身很简单，标准库足够。

**凭证纪律**：token 只在请求体里出现一次，绝不进日志、异常、repr。
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable

import structlog

log = structlog.get_logger(__name__)

API = "https://api.tushare.pro"

class TushareError(RuntimeError):
    """上游返回非零 code，或重试耗尽。"""


def _http_transport(body: bytes, timeout: float):
    req = urllib.request.Request(
        API, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class TushareClient:
    def __init__(
        self,
        token: str,
        *,
        transport: Callable[[bytes, float], dict] | None = None,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> None:
        self._token = token
        self._transport = transport or _http_transport
        self._max_retries = max_retries
        self._timeout = timeout

    def call(self, api_name: str, params: dict, fields: str = "") -> list[dict]:
        """调一个接口，返回行的列表。

        空结果返回 `[]`——0 行是合法事实（非交易日、无停牌），不是错误。
        """
        body = json.dumps({
            "api_name": api_name, "token": self._token,
            "params": params, "fields": fields,
        }).encode()

        last: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                payload = self._transport(body, self._timeout)
            except Exception as exc:
                last = exc
                if attempt < self._max_retries - 1:
                    time.sleep(2 ** attempt)
                continue

            code = payload.get("code")
            if code != 0:
                msg = payload.get("msg", "")
                # token 不进异常消息——异常会进日志、告警、工单。
                raise TushareError(f"{api_name} 返回 code={code}: {msg}")

            data = payload.get("data") or {}
            names = data.get("fields") or []
            return [dict(zip(names, row)) for row in (data.get("items") or [])]

        raise TushareError(f"{api_name} 重试 {self._max_retries} 次仍失败: {last!r}")
```

**关于重试语义**：只有 transport 层异常（超时、连接断）才重试；上游返回非零
`code` 一律立即抛出，不重试——权限不足、token 无效、参数错误重试多少次都一样，
只会白白消耗额度。`test_does_not_retry_a_permission_error` 守着这条。

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/live/test_tushare_config.py tests/data/providers/test_tushare_client.py -v`
Expected: PASS（12 个）

- [ ] **Step 6: 跑全量测试**

Run: `.venv/bin/python -m pytest -rfE --tb=no -q`
Expected: 失败数不增加（计划 A 未完成期间仍有既有失败）

- [ ] **Step 7: 提交**

```bash
git add src/qsys/live/config.py src/qsys/data/providers/tushare_client.py tests/live/test_tushare_config.py tests/data/providers/test_tushare_client.py
git commit -m "feat(data): Tushare 凭证配置与 HTTP client"
```

---

## Task 2: 日线三接口合成 28 列

**Files:**
- Create: `src/qsys/data/providers/tushare.py`
- Create: `src/qsys/data/pipeline/tushare_daily.py`
- Modify: `src/qsys/data/schema.py`
- Test: `tests/data/providers/test_tushare_provider.py`、`tests/data/pipeline/test_tushare_daily.py`

**Interfaces:**
- Consumes: `TushareClient.call`（Task 1）、`schema.PAID_DAILY`（计划 A Task 1）
- Produces:
  - `qsys.data.providers.tushare.TushareProvider(client)`
  - `TushareProvider.daily(trade_date: date) -> pl.DataFrame`（原始列名，未换算）
  - `TushareProvider.daily_basic(trade_date: date) -> pl.DataFrame`
  - `TushareProvider.adj_factor(trade_date: date) -> pl.DataFrame`
  - `qsys.data.pipeline.tushare_daily.compose_daily(daily, daily_basic, adj_factor) -> pl.DataFrame`（28 列，符合 `schema.TUSHARE_DAILY`）
  - `qsys.data.pipeline.tushare_daily.write_year(warehouse, frame, year) -> int`
  - `schema.TUSHARE_DAILY`（28 列，与 `PAID_DAILY` 同名同序同类型，metadata 的 provider 改为 `tushare-pro`）

- [ ] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_tushare_daily.py

"""三接口合成 28 列。单位换算全部实测确认过，不得凭文档假设。"""

from datetime import date

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.pipeline.tushare_daily import compose_daily

DAY = date(2026, 9, 11)


def _daily():
    return pl.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["20260911"],
        "open": [11.76], "high": [11.79], "low": [11.69], "close": [11.70],
        "pre_close": [11.70], "change": [0.0], "pct_chg": [0.0],
        "vol": [582305.98], "amount": [682946.78266],   # amount 单位：千元
    })


def _basic():
    return pl.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["20260911"], "close": [11.70],
        "turnover_rate": [0.44], "turnover_rate_f": [1.06], "volume_ratio": [1.0],
        "pe": [5.39], "pe_ttm": [5.29], "pb": [0.49], "ps": [1.75],
        "ps_ttm": [1.73], "dv_ratio": [5.05], "dv_ttm": [5.03],
        "total_share": [1940591.8198],      # 万股
        "float_share": [1940568.4991],      # 万股
        "free_share": [816056.5553],        # 万股
        "total_mv": [22996013.067],         # 万元
        "circ_mv": [22995736.725],          # 万元
    })


def _factor():
    return pl.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260911"],
                         "adj_factor": [139.008]})


def test_produces_exactly_the_twenty_eight_columns():
    got = compose_daily(_daily(), _basic(), _factor())
    assert got.columns == list(schema.TUSHARE_DAILY.names)


def test_amount_is_converted_from_thousand_yuan_to_yuan():
    """实测确认：Tushare amount 单位是千元，x1000 后与旧源逐位相同。"""
    got = compose_daily(_daily(), _basic(), _factor())
    assert got["amount"].to_list() == [682946782.66]


def test_volume_stays_in_lots_and_keeps_its_decimals():
    """手数带小数是源的真实精度，取整是有损变换。"""
    got = compose_daily(_daily(), _basic(), _factor())
    assert got["volume"].to_list() == [582305.98]


def test_share_counts_are_converted_from_ten_thousand_to_one():
    got = compose_daily(_daily(), _basic(), _factor())
    assert got["total_shares"].to_list() == [19405918198.0]
    assert got["circulating_shares"].to_list() == [19405684991.0]
    assert got["free_shares"].to_list() == [8160565553.0]


def test_market_caps_are_converted_from_ten_thousand_yuan_to_yuan():
    got = compose_daily(_daily(), _basic(), _factor())
    assert got["market_cap"].to_list() == [229960130670.0]
    assert got["circulating_market_cap"].to_list() == [229957367250.0]


def test_ratios_are_taken_as_is():
    """百分数保持百分数，不除以 100——旧源如此，换算会让两版对不上。"""
    got = compose_daily(_daily(), _basic(), _factor())
    assert got["turnover_ratio"].to_list() == [0.44]
    assert got["pe"].to_list() == [5.39]
    assert got["dividend_yield"].to_list() == [5.05]


def test_total_shares_is_taken_directly_not_derived_from_market_cap():
    """**不得复刻旧源的反算。**

    旧源的 total_shares 是 market_cap / close 反算的，A+H 股因此与 Tushare
    原值相差上亿股（华虹宏力差 1.91 亿）。反算属明令禁止，此处必须直取。
    """
    d, b, f = _daily(), _basic(), _factor()
    # 造一个 total_mv/close 与 total_share 不自洽的 A+H 情形
    b = b.with_columns(pl.lit(192849.4794).alias("total_share"))  # 万股
    got = compose_daily(d, b, f)
    assert got["total_shares"].to_list() == [1928494794.0]
    # 若复刻反算，会得到 market_cap/close = 229960130670/11.70 ≈ 1.966e10
    assert got["total_shares"].to_list() != [229960130670.0 / 11.70]


def test_symbol_and_date_are_normalised():
    got = compose_daily(_daily(), _basic(), _factor())
    assert got["symbol"].to_list() == ["000001.SZ"]
    assert got["dt"].to_list() == [DAY]


def test_a_symbol_missing_from_daily_basic_gets_nulls_not_zeros():
    """拿不到就记 null。补 0 会让下游把「没有数据」当成「数值为零」。"""
    d = pl.concat([_daily(), _daily().with_columns(pl.lit("600000.SH").alias("ts_code"))])
    got = compose_daily(d, _basic(), _factor())
    row = got.filter(pl.col("symbol") == "600000.SH").to_dicts()[0]
    assert row["pe"] is None
    assert row["total_shares"] is None
    assert row["close"] == 11.70   # daily 侧的值仍在


def test_a_symbol_missing_from_daily_is_dropped_entirely():
    """daily 没有的标的当日没有行情，不能只靠 daily_basic 造出一行。"""
    b = pl.concat([_basic(), _basic().with_columns(pl.lit("600000.SH").alias("ts_code"))])
    got = compose_daily(_daily(), b, _factor())
    assert got["symbol"].to_list() == ["000001.SZ"]


def test_empty_inputs_produce_an_empty_frame_with_the_right_schema():
    empty = _daily().clear()
    got = compose_daily(empty, _basic().clear(), _factor().clear())
    assert got.height == 0
    assert got.columns == list(schema.TUSHARE_DAILY.names)
```


**provider 层的测试**（本文件在计划初稿里被遗漏，由 codex 在执行 Task 2 时报出，
2026-09-13 补入）：

```python
# tests/data/providers/test_tushare_provider.py

"""TushareProvider：只做取数与类型规范化，不做单位换算。

单位换算是业务语义，属 pipeline 层（见 test_tushare_daily.py）。
本层的契约只有三条：接口名对、参数格式对、数值列是 Float64 且原值不动。
"""

from datetime import date

import polars as pl

from qsys.data.providers.tushare import TushareProvider


class RecordingClient:
    """记录调用、按脚本返回行。不联网。"""

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.calls = []

    def call(self, api_name, params, fields=""):
        self.calls.append((api_name, params, fields))
        return list(self.rows)


def test_daily_calls_the_daily_api_with_a_yyyymmdd_date():
    """trade_date 必须是 YYYYMMDD 字符串——传 date 对象或带横线的格式，
    上游会返回空结果而不是报错，静默拿到 0 行最难查。"""
    c = RecordingClient()
    TushareProvider(c).daily(date(2026, 9, 11))
    assert c.calls[0][0] == "daily"
    assert c.calls[0][1] == {"trade_date": "20260911"}


def test_daily_basic_calls_its_own_api():
    c = RecordingClient()
    TushareProvider(c).daily_basic(date(2026, 9, 11))
    assert c.calls[0][0] == "daily_basic"
    assert c.calls[0][1] == {"trade_date": "20260911"}


def test_adj_factor_calls_its_own_api():
    c = RecordingClient()
    TushareProvider(c).adj_factor(date(2026, 9, 11))
    assert c.calls[0][0] == "adj_factor"
    assert c.calls[0][1] == {"trade_date": "20260911"}


def test_numeric_columns_become_float64():
    """上游把数值也当字符串返回过。不显式转型，下游的算术会静默变成字符串拼接。"""
    c = RecordingClient([{"ts_code": "000001.SZ", "trade_date": "20260911",
                          "open": "11.76", "high": "11.79", "low": "11.69",
                          "close": "11.70", "pre_close": "11.70",
                          "change": "0.0", "pct_chg": "0.0",
                          "vol": "582305.98", "amount": "682946.78"}])
    got = TushareProvider(c).daily(date(2026, 9, 11))
    assert got["close"].dtype == pl.Float64
    assert got["vol"].dtype == pl.Float64


def test_values_are_not_converted_in_the_provider_layer():
    """**本层不做单位换算。** amount 仍是上游的千元原值，x1000 在 pipeline 层做。
    两层都换算就会换两次，而且谁都不会报错。"""
    c = RecordingClient([{"ts_code": "000001.SZ", "trade_date": "20260911",
                          "open": 11.76, "high": 11.79, "low": 11.69,
                          "close": 11.70, "pre_close": 11.70, "change": 0.0,
                          "pct_chg": 0.0, "vol": 582305.98,
                          "amount": 682946.78266}])
    got = TushareProvider(c).daily(date(2026, 9, 11))
    assert got["amount"].to_list() == [682946.78266]
    assert got["vol"].to_list() == [582305.98]


def test_column_names_are_left_as_upstream_returns_them():
    """改名也在 pipeline 层做。本层原样透传，便于对着接口文档核对。"""
    c = RecordingClient([{"ts_code": "000001.SZ", "trade_date": "20260911",
                          "close": 11.70, "vol": 1.0, "amount": 1.0}])
    got = TushareProvider(c).daily(date(2026, 9, 11))
    assert "ts_code" in got.columns
    assert "symbol" not in got.columns


def test_an_empty_result_is_an_empty_frame_not_an_error():
    """非交易日返回 0 行是合法事实。"""
    got = TushareProvider(RecordingClient([])).daily(date(2026, 9, 12))
    assert got.height == 0


def test_a_missing_numeric_column_does_not_raise():
    """上游偶尔少给列。少了就少了，不要凭空造一列 0 出来。"""
    c = RecordingClient([{"ts_code": "000001.SZ", "trade_date": "20260911",
                          "close": 11.70}])
    got = TushareProvider(c).daily(date(2026, 9, 11))
    assert "amount" not in got.columns
    assert got["close"].dtype == pl.Float64
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_tushare_daily.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.data.pipeline.tushare_daily'`

同时跑 provider 测试：

Run: `.venv/bin/python -m pytest tests/data/providers/test_tushare_provider.py -v`
Expected: FAIL，`ImportError: cannot import name 'TushareProvider'`

- [ ] **Step 3: 加 schema**

```python
# src/qsys/data/schema.py —— 在 PAID_DAILY 之后插入

# Tushare Pro 日线，28 列。与 PAID_DAILY 同名、同序、同类型——换源不改 schema，
# 只改 metadata 的来源标记。这样计划 A 建立的查询层与派生链路无需任何改动。
TUSHARE_DAILY = pa.schema(
    list(PAID_DAILY),
    metadata={
        b"provider": b"tushare-pro",
        b"volume_unit": b"lot=100 shares",
        b"money_unit": b"CNY",
        b"share_capital_unit": b"share",
        b"percentage_unit": b"percent",
        b"factor": b"cumulative backward adjustment",
        b"apis": b"daily+daily_basic+adj_factor",
    },
)
```

并在 `PARTITIONED` 字典里加 `"tushare_daily": TUSHARE_DAILY`。

- [ ] **Step 4: 实现 provider 与合成**

```python
# src/qsys/data/providers/tushare.py

"""Tushare Pro provider：一个接口一个方法，只做取数与列名/类型规范化。

单位换算不在这里做——换算是业务语义，属 pipeline 层。本层只保证
「拿到的就是接口原样返回的值，类型正确」。
"""

from __future__ import annotations

from datetime import date

import polars as pl

from qsys.data.providers.tushare_client import TushareClient


def _rows_to_frame(rows: list[dict], floats: tuple[str, ...]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    frame = pl.DataFrame(rows)
    return frame.with_columns(
        [pl.col(c).cast(pl.Float64, strict=False) for c in floats if c in frame.columns]
    )


_DAILY_FLOATS = ("open", "high", "low", "close", "pre_close", "change",
                 "pct_chg", "vol", "amount")
_BASIC_FLOATS = ("close", "turnover_rate", "turnover_rate_f", "volume_ratio",
                 "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm",
                 "total_share", "float_share", "free_share", "total_mv", "circ_mv")


class TushareProvider:
    def __init__(self, client: TushareClient) -> None:
        self._client = client

    def daily(self, trade_date: date) -> pl.DataFrame:
        rows = self._client.call("daily", {"trade_date": trade_date.strftime("%Y%m%d")})
        return _rows_to_frame(rows, _DAILY_FLOATS)

    def daily_basic(self, trade_date: date) -> pl.DataFrame:
        rows = self._client.call("daily_basic",
                                 {"trade_date": trade_date.strftime("%Y%m%d")})
        return _rows_to_frame(rows, _BASIC_FLOATS)

    def adj_factor(self, trade_date: date) -> pl.DataFrame:
        rows = self._client.call("adj_factor",
                                 {"trade_date": trade_date.strftime("%Y%m%d")})
        return _rows_to_frame(rows, ("adj_factor",))
```

```python
# src/qsys/data/pipeline/tushare_daily.py

"""三接口合成仓库的 28 列。

**单位换算全部实测确认过**（2026-09-13，与旧源逐键比对）：
  daily.amount     千元 x1000  -> 元
  daily.vol        手           -> 手（不换算，小数是真实精度）
  total/float/free_share  万股 x10000 -> 股
  total_mv / circ_mv      万元 x10000 -> 元
  各比率列                 百分数原样保留

**total_shares 必须直取。** 旧源那一列是 market_cap / close 反算的，
A+H 股因此与原值相差上亿股（华虹宏力 1.91 亿）。反算是明令禁止的，
此处落 Tushare 原值，**不得为了与旧值一致而复刻反算**。
"""

from __future__ import annotations

from datetime import date, datetime

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

# (仓库列名, 源列名, 乘数)
_FROM_DAILY = [
    ("open", "open", 1.0), ("high", "high", 1.0), ("low", "low", 1.0),
    ("close", "close", 1.0), ("pre_close", "pre_close", 1.0),
    ("change", "change", 1.0), ("pct_change", "pct_chg", 1.0),
    ("volume", "vol", 1.0), ("amount", "amount", 1000.0),
]
_FROM_BASIC = [
    ("turnover_ratio", "turnover_rate", 1.0),
    ("free_turnover_ratio", "turnover_rate_f", 1.0),
    ("volume_ratio", "volume_ratio", 1.0),
    ("pe", "pe", 1.0), ("pe_ttm", "pe_ttm", 1.0), ("pb", "pb", 1.0),
    ("ps", "ps", 1.0), ("ps_ttm", "ps_ttm", 1.0),
    ("dividend_yield", "dv_ratio", 1.0), ("dividend_yield_ttm", "dv_ttm", 1.0),
    ("total_shares", "total_share", 10000.0),
    ("circulating_shares", "float_share", 10000.0),
    ("free_shares", "free_share", 10000.0),
    ("market_cap", "total_mv", 10000.0),
    ("circulating_market_cap", "circ_mv", 10000.0),
]


def _scaled(frame: pl.DataFrame, mapping: list[tuple[str, str, float]]) -> list[pl.Expr]:
    out = []
    for ours, theirs, mult in mapping:
        if theirs in frame.columns:
            expr = pl.col(theirs) if mult == 1.0 else pl.col(theirs) * mult
            out.append(expr.alias(ours))
        else:
            out.append(pl.lit(None, dtype=pl.Float64).alias(ours))
    return out


def compose_daily(
    daily: pl.DataFrame,
    daily_basic: pl.DataFrame,
    adj_factor: pl.DataFrame,
) -> pl.DataFrame:
    """合成 28 列。以 `daily` 为准：它没有的标的当日没有行情，整行不产生。

    `daily_basic` / `adj_factor` 缺的标的，对应列记 **null**（不是 0）——
    0 是一个真实数值，会被下游当成「成交额为零」「因子为零」使用。
    """
    empty = pl.DataFrame(schema={n: pl.Date if n == "dt" else
                                 (pl.Utf8 if n in ("symbol", "name") else pl.Float64)
                                 for n in schema.TUSHARE_DAILY.names})
    if daily.height == 0:
        return empty

    base = daily.select(
        [
            pl.col("ts_code").alias("symbol"),
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("dt"),
        ]
        + _scaled(daily, _FROM_DAILY)
    )

    if daily_basic.height:
        basic = daily_basic.select(
            [pl.col("ts_code").alias("symbol")] + _scaled(daily_basic, _FROM_BASIC)
        )
        base = base.join(basic, on="symbol", how="left")
    else:
        base = base.with_columns(_scaled(pl.DataFrame(), _FROM_BASIC))

    if adj_factor.height:
        fac = adj_factor.select([pl.col("ts_code").alias("symbol"),
                                 pl.col("adj_factor").alias("factor")])
        base = base.join(fac, on="symbol", how="left")
    else:
        base = base.with_columns(pl.lit(None, dtype=pl.Float64).alias("factor"))

    # name 不在这三个接口里——由 namechange 单独成表（Task 7），此处留 null。
    base = base.with_columns(pl.lit(None, dtype=pl.Utf8).alias("name"))
    return base.select(schema.TUSHARE_DAILY.names).sort(["symbol", "dt"])


def write_year(warehouse: Warehouse, frame: pl.DataFrame, year: int) -> int:
    """按年合并写入。分区是整年覆盖写的，必须与同年既有数据合并。"""
    from qsys.data.query import WarehouseQuery

    if frame.height == 0:
        return 0
    query = WarehouseQuery(warehouse)
    lo, hi = date(year, 1, 1), date(year, 12, 31)
    merged = (
        pl.concat([query.tushare_daily(None, lo, hi), frame], how="vertical")
        .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
        .sort(["symbol", "dt"])
        .select(schema.TUSHARE_DAILY.names)
    )
    warehouse.write("tushare_daily",
                    merged.to_arrow().cast(schema.TUSHARE_DAILY), year=year)
    log.info("tushare_daily.written", year=year, rows=frame.height)
    return frame.height
```

- [ ] **Step 5: 加查询方法**

```python
# src/qsys/data/query.py —— 复用 Task 2（计划 A）建立的 _dated_query

def tushare_daily(
    self,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
) -> pl.DataFrame:
    """Tushare 日线全部 28 列。切换生产查询之前，只有对账与回填读它。"""
    columns = ", ".join(schema.TUSHARE_DAILY.names)
    return self._dated_query("tushare_daily", columns, schema.TUSHARE_DAILY,
                             symbols, start, end)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_tushare_daily.py tests/data/providers/test_tushare_provider.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/query.py src/qsys/data/providers/tushare.py src/qsys/data/pipeline/tushare_daily.py tests/
git commit -m "feat(data): Tushare 日线三接口合成 28 列，total_shares 直取不反算"
```

---

## Task 3: 全量重拉与逐键对账

**Files:**
- Create: `scripts/tushare_backfill.py`
- Create: `src/qsys/data/quality/tushare_reconcile.py`
- Test: `tests/data/quality/test_tushare_reconcile.py`

**Interfaces:**
- Consumes: `TushareProvider`、`compose_daily`、`write_year`（Task 2）
- Produces:
  - `qsys.data.quality.tushare_reconcile.reconcile(query, start, end) -> ReconcileReport`
  - `ReconcileReport` dataclass：`only_in_tushare`、`only_in_paid`、`value_diffs`（列 `symbol, dt, field, tushare, paid, abs_diff`）、`summary: dict`

- [ ] **Step 1: 写失败测试**

```python
# tests/data/quality/test_tushare_reconcile.py

"""换源对账：差异逐键列出，不能只给总数。"""

from datetime import date

import polars as pl
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.quality.tushare_reconcile import reconcile
from qsys.data.query import WarehouseQuery

DAY = date(2026, 9, 11)


def _row(symbol, **over):
    row = {n: None for n in schema.PAID_DAILY.names}
    row |= {"dt": DAY, "symbol": symbol, "name": "平安银行",
            "open": 11.76, "high": 11.79, "low": 11.69, "close": 11.70,
            "pre_close": 11.70, "volume": 582305.98, "amount": 682946782.66,
            "factor": 139.008, "total_shares": 1.94e10}
    row |= over
    return row


def _write(warehouse, dataset, rows, arrow_schema):
    frame = pl.DataFrame(rows)
    path = warehouse.root / dataset / "year=2026" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(frame.to_arrow().cast(arrow_schema), path)


def test_identical_data_reports_zero_differences(warehouse):
    _write(warehouse, "paid_daily", [_row("000001.SZ")], schema.PAID_DAILY)
    _write(warehouse, "tushare_daily", [_row("000001.SZ")], schema.TUSHARE_DAILY)
    rep = reconcile(WarehouseQuery(warehouse), DAY, DAY)
    assert rep.summary["common_rows"] == 1
    assert rep.value_diffs.height == 0


def test_lists_every_differing_field_key_by_key(warehouse):
    _write(warehouse, "paid_daily", [_row("000001.SZ")], schema.PAID_DAILY)
    _write(warehouse, "tushare_daily",
           [_row("000001.SZ", total_shares=1.928e10, close=11.71)],
           schema.TUSHARE_DAILY)
    rep = reconcile(WarehouseQuery(warehouse), DAY, DAY)
    fields = sorted(rep.value_diffs["field"].to_list())
    assert fields == ["close", "total_shares"]


def test_rows_only_in_one_side_are_reported_separately(warehouse):
    """只在一边的标的要单列，不能混进数值差异淹没真问题。"""
    _write(warehouse, "paid_daily", [_row("000001.SZ")], schema.PAID_DAILY)
    _write(warehouse, "tushare_daily",
           [_row("000001.SZ"), _row("600000.SH")], schema.TUSHARE_DAILY)
    rep = reconcile(WarehouseQuery(warehouse), DAY, DAY)
    assert rep.only_in_tushare["symbol"].to_list() == ["600000.SH"]
    assert rep.only_in_paid.height == 0


def test_null_on_both_sides_is_not_a_difference(warehouse):
    """两边都没有这个数，不算差异——记 null 是规矩允许的结果。"""
    _write(warehouse, "paid_daily", [_row("000001.SZ", pe=None)], schema.PAID_DAILY)
    _write(warehouse, "tushare_daily", [_row("000001.SZ", pe=None)],
           schema.TUSHARE_DAILY)
    rep = reconcile(WarehouseQuery(warehouse), DAY, DAY)
    assert rep.value_diffs.filter(pl.col("field") == "pe").height == 0


def _empty_frame(arrow_schema) -> pl.DataFrame:
    return pl.DataFrame(schema={
        n: (pl.Date if n == "dt" else pl.Utf8 if n in ("symbol", "name") else pl.Float64)
        for n in arrow_schema.names
    })


class RecordingQuery:
    """记录每次读取的区间。**守的是实现约束，不是语义。**

    跨年汇总的语义测试在「一次性载入全历史」的实现上同样会通过，所以它们
    守不住「必须逐年」。而逐年正是为了避免 OOM 才改的（2026-09-13 生产节点
    实测 7.24 GiB RSS 被 OOM killer 杀），因此必须单独守一条。
    """

    def __init__(self):
        self.calls: list[tuple[str, object, object]] = []

    def tushare_daily(self, symbols, start, end):
        self.calls.append(("tushare_daily", start, end))
        return _empty_frame(schema.TUSHARE_DAILY)

    def paid_daily(self, symbols, start, end):
        self.calls.append(("paid_daily", start, end))
        return _empty_frame(schema.PAID_DAILY)


def test_every_read_is_confined_to_a_single_year():
    """任何一次读取都不得跨年——跨年就意味着可能一次载入全历史。"""
    import datetime as _dt

    q = RecordingQuery()
    reconcile(q, _dt.date(2025, 3, 4), _dt.date(2026, 9, 11))

    assert q.calls, "reconcile 没有发生任何读取"
    for name, lo, hi in q.calls:
        assert lo.year == hi.year, (
            f"{name} 一次读了 {lo} ~ {hi}，跨了年。"
            f"全历史一次性载入约 8.2 GiB，生产节点只有 7.5 GiB。"
        )


def test_one_read_per_table_per_year():
    """2025-2026 两年，每张表各读两次，不多不少。

    多读说明有重复 IO，少读说明漏了年份。
    """
    import datetime as _dt

    q = RecordingQuery()
    reconcile(q, _dt.date(2025, 3, 4), _dt.date(2026, 9, 11))

    assert [c[0] for c in q.calls].count("tushare_daily") == 2
    assert [c[0] for c in q.calls].count("paid_daily") == 2


def test_year_windows_are_clipped_to_the_requested_range():
    """年窗口要被请求区间裁剪，不能读到区间之外去。"""
    import datetime as _dt

    q = RecordingQuery()
    reconcile(q, _dt.date(2025, 3, 4), _dt.date(2026, 9, 11))

    windows = sorted({(lo, hi) for _, lo, hi in q.calls})
    assert windows == [
        (_dt.date(2025, 3, 4), _dt.date(2025, 12, 31)),
        (_dt.date(2026, 1, 1), _dt.date(2026, 9, 11)),
    ]


def test_summary_aggregates_across_years(warehouse):
    """逐年处理不能改变跨年汇总的结果。

    全历史两张表同时载入约 8.2 GiB，生产节点只有 7.5 GiB——2026-09-13 实测
    被 OOM killer 杀掉，因此改为逐年。这条测试守的就是「改了实现、没改语义」。
    """
    import datetime as _dt

    day_2025 = _dt.date(2025, 3, 4)
    rows_2025 = [_row("000001.SZ") | {"dt": day_2025}]
    rows_2026 = [_row("000001.SZ"), _row("600000.SH")]

    for dataset, arrow_schema in (("paid_daily", schema.PAID_DAILY),
                                  ("tushare_daily", schema.TUSHARE_DAILY)):
        for year, rows in ((2025, rows_2025), (2026, rows_2026)):
            frame = pl.DataFrame(rows)
            path = warehouse.root / dataset / f"year={year}" / "part.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(frame.to_arrow().cast(arrow_schema), path)

    rep = reconcile(WarehouseQuery(warehouse), day_2025, DAY)
    assert rep.summary["common_rows"] == 3        # 2025 的 1 行 + 2026 的 2 行
    assert rep.summary["only_in_tushare"] == 0
    assert rep.summary["only_in_paid"] == 0


def test_a_difference_in_an_earlier_year_is_not_lost(warehouse):
    """早年的差异必须出现在最终清单里，不能被后一年覆盖掉。"""
    import datetime as _dt

    day_2025 = _dt.date(2025, 3, 4)
    for dataset, arrow_schema, close in (("paid_daily", schema.PAID_DAILY, 11.70),
                                         ("tushare_daily", schema.TUSHARE_DAILY, 11.71)):
        frame = pl.DataFrame([_row("000001.SZ") | {"dt": day_2025, "close": close}])
        path = warehouse.root / dataset / "year=2025" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(frame.to_arrow().cast(arrow_schema), path)
        frame26 = pl.DataFrame([_row("000001.SZ")])
        path26 = warehouse.root / dataset / "year=2026" / "part.parquet"
        path26.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(frame26.to_arrow().cast(arrow_schema), path26)

    rep = reconcile(WarehouseQuery(warehouse), day_2025, DAY)
    hit = rep.value_diffs.filter(pl.col("field") == "close")
    assert hit.height == 1
    assert hit["dt"].to_list() == [day_2025]


def test_null_on_one_side_only_is_a_difference(warehouse):
    """一边有值一边没有，必须报出来——这正是要查的东西。"""
    _write(warehouse, "paid_daily", [_row("000001.SZ", pe=5.39)], schema.PAID_DAILY)
    _write(warehouse, "tushare_daily", [_row("000001.SZ", pe=None)],
           schema.TUSHARE_DAILY)
    rep = reconcile(WarehouseQuery(warehouse), DAY, DAY)
    assert rep.value_diffs.filter(pl.col("field") == "pe").height == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/quality/test_tushare_reconcile.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.data.quality.tushare_reconcile'`

**若本任务是在已有一个全量载入版实现的基础上返工**（2026-09-13 的情形）：
跨年语义的两条测试会**直接通过**——全量载入同样满足那些语义，它们守不住
「必须逐年」。真正会红的是 `test_every_read_is_confined_to_a_single_year`
与 `test_year_windows_are_clipped_to_the_requested_range`，**以它们的失败
作为 Step 2 的确认依据**。

- [ ] **Step 3: 实现对账**

```python
# src/qsys/data/quality/tushare_reconcile.py

"""tushare_daily vs paid_daily 逐键对账。

**为什么必须逐键**：两者已实测同源，因此任何差异都值得逐条解释，而不是
用一个「一致率 99.9%」掩盖过去。2026-09-11 单日已发现 total_shares 在 4 只
A+H 股上相差上亿股（旧源是 market_cap/close 反算的），这类问题只有逐键清单
才看得见。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.query import WarehouseQuery

_KEYS = ["symbol", "dt"]
_VALUE_FIELDS = [n for n in schema.PAID_DAILY.names if n not in ("symbol", "dt", "name")]


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    only_in_tushare: pl.DataFrame
    only_in_paid: pl.DataFrame
    value_diffs: pl.DataFrame
    summary: dict[str, int | float]


def _reconcile_year(query: WarehouseQuery, lo: date, hi: date):
    """比对一年。返回 (only_ts, only_paid, value_diffs, 本年计数)。

    **逐年处理是可行性前提，不是优化。** 全历史两张表同时载入约 8.2 GiB
    （18.2M 行 x 28 列 x 8 字节 x 2），而生产节点只有 7.5 GiB——2026-09-13
    实测被 OOM killer 杀掉。单年峰值约 450 MiB，宽裕。
    仓库各处（universe.py / flags.py / build_daily_flags）都是同一套逐年模式。
    """
    ts = query.tushare_daily(None, lo, hi)
    paid = query.paid_daily(None, lo, hi)

    only_ts = ts.join(paid.select(_KEYS), on=_KEYS, how="anti").sort(_KEYS)
    only_paid = paid.join(ts.select(_KEYS), on=_KEYS, how="anti").sort(_KEYS)
    common = ts.join(paid, on=_KEYS, how="inner", suffix="_paid")

    diffs: list[pl.DataFrame] = []
    for field in _VALUE_FIELDS:
        other = f"{field}_paid"
        if field not in common.columns or other not in common.columns:
            continue
        # 两边都 null 不算差异；一边 null 一边有值算差异。
        both_null = pl.col(field).is_null() & pl.col(other).is_null()
        differs = (~both_null) & (
            pl.col(field).is_null() | pl.col(other).is_null()
            | (pl.col(field) != pl.col(other))
        )
        hit = common.filter(differs)
        if hit.height == 0:
            continue
        diffs.append(hit.select(
            _KEYS + [
                pl.lit(field).alias("field"),
                pl.col(field).alias("tushare"),
                pl.col(other).alias("paid"),
                (pl.col(field) - pl.col(other)).abs().alias("abs_diff"),
            ]
        ))

    year_diffs = (
        pl.concat(diffs, how="vertical") if diffs else _empty_diffs()
    )
    counts = {
        "tushare_rows": ts.height,
        "paid_rows": paid.height,
        "common_rows": common.height,
        "only_in_tushare": only_ts.height,
        "only_in_paid": only_paid.height,
    }
    return only_ts, only_paid, year_diffs, counts


def _empty_diffs() -> pl.DataFrame:
    return pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date, "field": pl.Utf8,
                                "tushare": pl.Float64, "paid": pl.Float64,
                                "abs_diff": pl.Float64})


def reconcile(query: WarehouseQuery, start: date, end: date) -> ReconcileReport:
    """逐年比对，跨年汇总。接口与产物与逐次全量版完全一致。

    **不要改回一次性载入全历史**——那会在生产节点 OOM（见 `_reconcile_year`）。
    """
    only_ts_parts: list[pl.DataFrame] = []
    only_paid_parts: list[pl.DataFrame] = []
    diff_parts: list[pl.DataFrame] = []
    totals = {"tushare_rows": 0, "paid_rows": 0, "common_rows": 0,
              "only_in_tushare": 0, "only_in_paid": 0}

    for year in range(start.year, end.year + 1):
        lo = max(start, date(year, 1, 1))
        hi = min(end, date(year, 12, 31))
        if lo > hi:
            continue
        only_ts, only_paid, year_diffs, counts = _reconcile_year(query, lo, hi)
        for k, v in counts.items():
            totals[k] += v
        if only_ts.height:
            only_ts_parts.append(only_ts)
        if only_paid.height:
            only_paid_parts.append(only_paid)
        if year_diffs.height:
            diff_parts.append(year_diffs)

    only_ts_all = (pl.concat(only_ts_parts, how="vertical").sort(_KEYS)
                   if only_ts_parts else pl.DataFrame())
    only_paid_all = (pl.concat(only_paid_parts, how="vertical").sort(_KEYS)
                     if only_paid_parts else pl.DataFrame())
    value_diffs = (
        pl.concat(diff_parts, how="vertical").sort(["field", "abs_diff"],
                                                   descending=[False, True])
        if diff_parts else _empty_diffs()
    )

    by_field = (
        value_diffs.group_by("field").len().sort("len", descending=True).to_dicts()
        if value_diffs.height else []
    )
    summary = dict(totals)
    summary["diff_rows"] = value_diffs.height
    summary["diff_by_field"] = {d["field"]: d["len"] for d in by_field}
    return ReconcileReport(only_ts_all, only_paid_all, value_diffs, summary)
```

- [ ] **Step 4: 写回填 CLI**

```python
# scripts/tushare_backfill.py

"""按交易日区间从 Tushare 回填 tushare_daily。

用法:
    uv run python scripts/tushare_backfill.py \\
        --warehouse /data/qsys/warehouse --start 1990-12-19 --end 2026-09-11
"""

from __future__ import annotations

import argparse
import time
from datetime import date
from pathlib import Path

import polars as pl

from qsys.data.pipeline.tushare_daily import compose_daily, write_year
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.storage import Warehouse
from qsys.live.config import load_tushare_config


def trading_days(provider_client: TushareClient, start: date, end: date) -> list[date]:
    """交易日历取自 trade_cal，不自行推算——节假日规则年年变。"""
    rows = provider_client.call("trade_cal", {
        "exchange": "SSE", "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"), "is_open": "1",
    })
    return sorted(date.fromisoformat(
        f"{r['cal_date'][:4]}-{r['cal_date'][4:6]}-{r['cal_date'][6:]}") for r in rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--sleep", type=float, default=0.15,
                        help="每个交易日之间的间隔秒数，避免触发限流")
    args = parser.parse_args()

    client = TushareClient(load_tushare_config(args.config).token)
    provider = TushareProvider(client)
    warehouse = Warehouse(args.warehouse)

    days = trading_days(client, args.start, args.end)
    print(f"共 {len(days)} 个交易日：{days[0]} ~ {days[-1]}")

    by_year: dict[int, list[pl.DataFrame]] = {}
    started = time.time()
    for i, day in enumerate(days, 1):
        frame = compose_daily(provider.daily(day), provider.daily_basic(day),
                              provider.adj_factor(day))
        if frame.height:
            by_year.setdefault(day.year, []).append(frame)
        time.sleep(args.sleep)

        # 攒满一年就落盘，避免整段历史驻留内存。
        done_years = [y for y in by_year if y < day.year]
        for y in done_years:
            write_year(warehouse, pl.concat(by_year.pop(y), how="vertical"), y)
        if i % 100 == 0:
            print(f"  {i}/{len(days)} {day} 已用 {(time.time()-started)/60:.1f} 分钟",
                  flush=True)

    for y, frames in by_year.items():
        write_year(warehouse, pl.concat(frames, how="vertical"), y)

    print(f"完成，耗时 {(time.time()-started)/60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/quality/test_tushare_reconcile.py -v`
Expected: PASS（10 个）

- [ ] **Step 6: 在节点上全量回填**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  "cd /home/ubuntu/qsys && nohup .venv/bin/python scripts/tushare_backfill.py \
   --warehouse warehouse --start 1990-12-19 --end 2026-09-11 \
   > var/tushare_backfill.log 2>&1 &"
```

按实测 0.38 秒/次 × 3 接口 × 约 8,700 交易日，预计 **2.5–3.5 小时**。
过程中**不要**同时跑回测——节点 4 核，并发会拖慢。

- [ ] **Step 7: 出对账报告**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  "cd /home/ubuntu/qsys && .venv/bin/python -c \"
from datetime import date
from pathlib import Path
import json
from qsys.data.quality.tushare_reconcile import reconcile
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
rep = reconcile(WarehouseQuery(Warehouse(Path('warehouse'))),
                date(1990,12,19), date(2026,9,11))
print(json.dumps(rep.summary, ensure_ascii=False, indent=2))
rep.value_diffs.write_parquet('artifacts/tushare_reconcile_value_diffs.parquet')
rep.only_in_tushare.write_parquet('artifacts/tushare_reconcile_only_tushare.parquet')
rep.only_in_paid.write_parquet('artifacts/tushare_reconcile_only_paid.parquet')
\""
```

**验收标准**：`open/high/low/close/pre_close/volume/amount/factor` 的差异行数应为 0
或极少且可逐条解释；`total_shares`/`circulating_shares` 的差异**预期存在**
（旧源反算所致），需按年统计并确认集中在 A+H 股。**任何价格类差异都必须查清再往下走。**

- [ ] **Step 8: 提交**

```bash
git add src/qsys/data/quality/tushare_reconcile.py scripts/tushare_backfill.py tests/data/quality/test_tushare_reconcile.py
git commit -m "feat(data): Tushare 全量回填与逐键对账"
```

---

## Task 4: `stk_limit` —— 涨跌停价真值表

**Files:**
- Modify: `src/qsys/data/schema.py`、`src/qsys/data/query.py`
- Create: `src/qsys/data/pipeline/tushare_reference.py`
- Test: `tests/data/pipeline/test_tushare_reference.py`

**Interfaces:**
- Consumes: `TushareProvider`（Task 2）
- Produces:
  - `schema.PRICE_LIMITS_DAILY`：`symbol`(string) / `dt`(date32) / `pre_close`(float64) / `limit_up`(float64) / `limit_down`(float64)
  - `TushareProvider.stk_limit(trade_date) -> pl.DataFrame`
  - `qsys.data.pipeline.tushare_reference.ingest_price_limits(provider, warehouse, days) -> int`
  - `WarehouseQuery.price_limits(symbols, start, end) -> pl.DataFrame`
  - `qsys.data.pipeline.tushare_reference._merge_write(warehouse, dataset, frame, arrow_schema, keys, reader) -> int`
    —— 按年合并写入的私有辅助，**Task 5 / 6 / 8 都复用它**，不要各写一份

- [ ] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_tushare_reference.py

"""六张真值表的落库。涨跌停价、停牌、指数、标的、ST、首板炸板。"""

from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.pipeline.tushare_reference import ingest_price_limits
from qsys.data.query import WarehouseQuery

DAY = date(2026, 9, 11)


class FakeProvider:
    def __init__(self, **tables):
        self.tables = tables
        self.calls = []

    def stk_limit(self, trade_date):
        self.calls.append(("stk_limit", trade_date))
        return self.tables.get("stk_limit", pl.DataFrame())


def test_price_limits_are_stored_as_direct_values(warehouse):
    """涨跌停价直取。此前是由前收 x 板块档位算出来的纯派生。"""
    p = FakeProvider(stk_limit=pl.DataFrame({
        "ts_code": ["000001.SZ", "300750.SZ"],
        "trade_date": ["20260911", "20260911"],
        "pre_close": [11.70, 250.0],
        "up_limit": [12.87, 300.0],
        "down_limit": [10.53, 200.0],
    }))
    rows = ingest_price_limits(p, warehouse, [DAY])
    assert rows == 2
    got = WarehouseQuery(warehouse).price_limits(None, DAY, DAY).sort("symbol")
    assert got["limit_up"].to_list() == [12.87, 300.0]
    assert got["limit_down"].to_list() == [10.53, 200.0]
    assert got["pre_close"].to_list() == [11.70, 250.0]


def test_an_empty_day_writes_nothing_and_does_not_fail(warehouse):
    """非交易日返回 0 行是合法事实。"""
    p = FakeProvider(stk_limit=pl.DataFrame())
    assert ingest_price_limits(p, warehouse, [DAY]) == 0


def test_reingesting_the_same_day_is_idempotent(warehouse):
    p = FakeProvider(stk_limit=pl.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["20260911"],
        "pre_close": [11.70], "up_limit": [12.87], "down_limit": [10.53],
    }))
    ingest_price_limits(p, warehouse, [DAY])
    ingest_price_limits(p, warehouse, [DAY])
    assert WarehouseQuery(warehouse).price_limits(None, DAY, DAY).height == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_tushare_reference.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.data.pipeline.tushare_reference'`

- [ ] **Step 3: 加 schema、provider 方法与落库**

```python
# src/qsys/data/schema.py —— 新增并注册进 PARTITIONED

# 每日涨跌停价，**直取**。此前由「除权参考价 x 板块档位」纯派生得到，
# 依赖先判对是不是 ST；直取之后这条派生链整段可以降级为校验。
# 实测历史至少回溯到 2010-01-04。
PRICE_LIMITS_DAILY = pa.schema([
    ("symbol", pa.string()),
    ("dt", pa.date32()),
    ("pre_close", pa.float64()),
    ("limit_up", pa.float64()),
    ("limit_down", pa.float64()),
])
```

```python
# src/qsys/data/providers/tushare.py 追加

    def stk_limit(self, trade_date: date) -> pl.DataFrame:
        rows = self._client.call("stk_limit",
                                 {"trade_date": trade_date.strftime("%Y%m%d")})
        return _rows_to_frame(rows, ("pre_close", "up_limit", "down_limit"))
```

```python
# src/qsys/data/pipeline/tushare_reference.py

"""Tushare 的参考数据落库：涨跌停价、停牌、指数、标的、曾用名、涨跌停列表。

每张表一个函数，互不混源。写入一律按年合并——分区是整年覆盖写的。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)


def _merge_write(warehouse: Warehouse, dataset: str, frame: pl.DataFrame,
                 arrow_schema, keys: list[str], reader) -> int:
    if frame.height == 0:
        return 0
    total = 0
    for year in sorted({d.year for d in frame["dt"].to_list()}):
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        chunk = frame.filter(pl.col("dt").is_between(lo, hi))
        merged = (
            pl.concat([reader(lo, hi), chunk], how="vertical")
            .unique(subset=keys, keep="last", maintain_order=True)
            .sort(keys)
            .select(arrow_schema.names)
        )
        warehouse.write(dataset, merged.to_arrow().cast(arrow_schema), year=year)
        total += chunk.height
    return total


def ingest_price_limits(provider, warehouse: Warehouse,
                        days: Sequence[date]) -> int:
    """每日涨跌停价。直取 up_limit / down_limit，不做任何换算。"""
    from qsys.data.query import WarehouseQuery

    query = WarehouseQuery(warehouse)
    frames = []
    for day in days:
        raw = provider.stk_limit(day)
        if raw.height == 0:
            continue
        frames.append(raw.select([
            pl.col("ts_code").alias("symbol"),
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("dt"),
            pl.col("pre_close"),
            pl.col("up_limit").alias("limit_up"),
            pl.col("down_limit").alias("limit_down"),
        ]))
    if not frames:
        return 0
    frame = pl.concat(frames, how="vertical")
    written = _merge_write(
        warehouse, "price_limits_daily", frame, schema.PRICE_LIMITS_DAILY,
        ["symbol", "dt"], lambda lo, hi: query.price_limits(None, lo, hi),
    )
    log.info("price_limits.ingested", rows=written, days=len(days))
    return written
```

```python
# src/qsys/data/query.py 追加

def price_limits(
    self,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
) -> pl.DataFrame:
    """每日涨跌停价真值（Tushare `stk_limit`）。"""
    columns = ", ".join(schema.PRICE_LIMITS_DAILY.names)
    return self._dated_query("price_limits_daily", columns,
                             schema.PRICE_LIMITS_DAILY, symbols, start, end)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_tushare_reference.py -v`
Expected: PASS（3 个）

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/query.py src/qsys/data/providers/tushare.py src/qsys/data/pipeline/tushare_reference.py tests/data/pipeline/test_tushare_reference.py
git commit -m "feat(data): stk_limit 落库，涨跌停价改直取"
```

---

## Task 5: `suspend_d` —— 停牌真值表

**Files:**
- Modify: `src/qsys/data/schema.py`、`src/qsys/data/query.py`、`src/qsys/data/providers/tushare.py`、`src/qsys/data/pipeline/tushare_reference.py`
- Test: `tests/data/pipeline/test_tushare_reference.py`（追加）

**Interfaces:**
- Produces:
  - `schema.SUSPEND_DAILY`：`symbol`(string) / `dt`(date32) / `suspend_type`(string) / `suspend_timing`(string)
  - `TushareProvider.suspend_d(trade_date) -> pl.DataFrame`
  - `ingest_suspensions(provider, warehouse, days) -> int`
  - `WarehouseQuery.suspensions(symbols, start, end) -> pl.DataFrame`

spec §3.6/§3.7 记录过：付费源里停牌日整行缺席，`volume == 0` 的行数为 0，
因此旧的「四价等于前收 + 量为 0」判据在新源下**永远不会命中**。本表是它的替代。

- [ ] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_tushare_reference.py 追加

from qsys.data.pipeline.tushare_reference import ingest_suspensions


class FakeSuspendProvider:
    def __init__(self, frame):
        self.frame = frame

    def suspend_d(self, trade_date):
        return self.frame


def test_suspensions_are_stored_with_type_and_timing(warehouse):
    """停牌真值。此前靠「四价等于前收 + 量为 0」猜，而付费源里零成交行数为 0，
    那条判据在新源下永远不会命中——本表是它的替代。"""
    p = FakeSuspendProvider(pl.DataFrame({
        "ts_code": ["000001.SZ", "600000.SH"],
        "trade_date": ["20260911", "20260911"],
        "suspend_type": ["S", "R"],
        "suspend_timing": ["盘中停牌", None],
    }))
    assert ingest_suspensions(p, warehouse, [DAY]) == 2
    got = WarehouseQuery(warehouse).suspensions(None, DAY, DAY).sort("symbol")
    assert got["suspend_type"].to_list() == ["S", "R"]
    assert got["suspend_timing"].to_list() == ["盘中停牌", None]


def test_a_day_with_no_suspensions_writes_nothing(warehouse):
    """没有停牌是常态，0 行不是错误。"""
    assert ingest_suspensions(FakeSuspendProvider(pl.DataFrame()), warehouse, [DAY]) == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_tushare_reference.py -k suspen -v`
Expected: FAIL，`ImportError: cannot import name 'ingest_suspensions'`

- [ ] **Step 3: 实现**

```python
# src/qsys/data/schema.py —— 新增并注册进 PARTITIONED

# 每日停复牌，**直取**。suspend_type: S 停牌 / R 复牌；
# suspend_timing 是日内停牌时间段，全天停牌时为 null。
# 此前停牌只能靠「四价等于前收 + 量为 0」反推，而付费源里零成交行数为 0，
# 那条判据在新源下永远不会命中（见 spec §3.7）。
SUSPEND_DAILY = pa.schema([
    ("symbol", pa.string()),
    ("dt", pa.date32()),
    ("suspend_type", pa.string()),
    ("suspend_timing", pa.string()),
])
```

```python
# src/qsys/data/providers/tushare.py 追加

    def suspend_d(self, trade_date: date) -> pl.DataFrame:
        rows = self._client.call("suspend_d",
                                 {"trade_date": trade_date.strftime("%Y%m%d")})
        return pl.DataFrame(rows) if rows else pl.DataFrame()
```

```python
# src/qsys/data/pipeline/tushare_reference.py 追加

def ingest_suspensions(provider, warehouse: Warehouse,
                       days: Sequence[date]) -> int:
    """每日停复牌。缺 suspend_timing 记 null——全天停牌本就没有日内时段。"""
    from qsys.data.query import WarehouseQuery

    query = WarehouseQuery(warehouse)
    frames = []
    for day in days:
        raw = provider.suspend_d(day)
        if raw.height == 0:
            continue
        timing = (pl.col("suspend_timing").cast(pl.Utf8)
                  if "suspend_timing" in raw.columns
                  else pl.lit(None, dtype=pl.Utf8))
        frames.append(raw.select([
            pl.col("ts_code").alias("symbol"),
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("dt"),
            pl.col("suspend_type").cast(pl.Utf8),
            timing.alias("suspend_timing"),
        ]))
    if not frames:
        return 0
    frame = pl.concat(frames, how="vertical")
    written = _merge_write(
        warehouse, "suspend_daily", frame, schema.SUSPEND_DAILY,
        ["symbol", "dt"], lambda lo, hi: query.suspensions(None, lo, hi),
    )
    log.info("suspensions.ingested", rows=written, days=len(days))
    return written
```

```python
# src/qsys/data/query.py 追加

def suspensions(
    self,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
) -> pl.DataFrame:
    """每日停复牌真值（Tushare `suspend_d`）。"""
    columns = ", ".join(schema.SUSPEND_DAILY.names)
    return self._dated_query("suspend_daily", columns, schema.SUSPEND_DAILY,
                             symbols, start, end)
```

- [ ] **Step 4: 跑测试确认通过 + 全量测试**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_tushare_reference.py -v && .venv/bin/python -m pytest -rfE --tb=no -q`
Expected: 新测试 PASS；全量失败数不增加

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/query.py src/qsys/data/providers/tushare.py src/qsys/data/pipeline/tushare_reference.py tests/data/pipeline/test_tushare_reference.py
git commit -m "feat(data): suspend_d 落库，停牌改直取"
```

---

## Task 6: `index_daily` —— 补上指数日线缺口

**Files:**
- Modify: `src/qsys/data/providers/tushare.py`、`src/qsys/data/pipeline/tushare_reference.py`
- Test: `tests/data/pipeline/test_tushare_reference.py`（追加）

**Interfaces:**
- Produces:
  - `TushareProvider.index_daily(ts_code, start, end) -> pl.DataFrame`
  - `ingest_index_daily(provider, warehouse, ts_codes, start, end) -> int`（写入既有的 `index_bars` 表）

`index_bars` 表与 `schema.INDEX_BARS` 已存在，mootdx 失效后无源（停在 2026-09-09）。
`market_regime` 的 `index_ma20_slope_5d` 依赖它，S6 Gate 要用。本任务只换来源，
**不改 schema**。

- [ ] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_tushare_reference.py 追加

from qsys.data.pipeline.tushare_reference import ingest_index_daily


class FakeIndexProvider:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def index_daily(self, ts_code, start, end):
        self.calls.append((ts_code, start, end))
        return self.frame


def test_index_daily_fills_the_existing_index_bars_table(warehouse):
    """mootdx 失效后 index_bars 无源。只换来源，不改 schema。"""
    p = FakeIndexProvider(pl.DataFrame({
        "ts_code": ["000300.SH"], "trade_date": ["20260911"],
        "open": [4000.0], "high": [4050.0], "low": [3990.0], "close": [4020.0],
        "pre_close": [3995.0], "vol": [1.2e8], "amount": [1.5e9],
    }))
    rows = ingest_index_daily(p, warehouse, ["000300.SH"], DAY, DAY)
    assert rows == 1
    got = WarehouseQuery(warehouse).index_bars(["000300.SH"], DAY, DAY)
    assert got["close"].to_list() == [4020.0]


def test_a_fractional_index_volume_is_refused_not_truncated(warehouse):
    """INDEX_BARS.volume 是 int64，Tushare 给浮点。宁可报错，不可悄悄丢精度。"""
    import pytest

    p = FakeIndexProvider(pl.DataFrame({
        "ts_code": ["000300.SH"], "trade_date": ["20260911"],
        "open": [4000.0], "high": [4050.0], "low": [3990.0], "close": [4020.0],
        "pre_close": [3995.0], "vol": [123456.78], "amount": [1.5e6],
    }))
    with pytest.raises(ValueError) as exc:
        ingest_index_daily(p, warehouse, ["000300.SH"], DAY, DAY)
    assert "int64" in str(exc.value)


def test_index_amount_is_converted_from_thousand_yuan(warehouse):
    """index_daily 的 amount 与 daily 同为千元。"""
    p = FakeIndexProvider(pl.DataFrame({
        "ts_code": ["000300.SH"], "trade_date": ["20260911"],
        "open": [4000.0], "high": [4050.0], "low": [3990.0], "close": [4020.0],
        "pre_close": [3995.0], "vol": [1.2e8], "amount": [1500000.0],
    }))
    ingest_index_daily(p, warehouse, ["000300.SH"], DAY, DAY)
    got = WarehouseQuery(warehouse).index_bars(["000300.SH"], DAY, DAY)
    assert got["amount"].to_list() == [1500000000.0]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_tushare_reference.py -k index -v`
Expected: FAIL，`ImportError: cannot import name 'ingest_index_daily'`

- [ ] **Step 3: 实现**

```python
# src/qsys/data/providers/tushare.py 追加

    def index_daily(self, ts_code: str, start: date, end: date) -> pl.DataFrame:
        rows = self._client.call("index_daily", {
            "ts_code": ts_code,
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        })
        return _rows_to_frame(rows, ("open", "high", "low", "close",
                                     "pre_close", "vol", "amount"))
```

```python
# src/qsys/data/pipeline/tushare_reference.py 追加

def ingest_index_daily(provider, warehouse: Warehouse,
                       ts_codes: Sequence[str], start: date, end: date) -> int:
    """指数日线。写入既有的 index_bars 表，schema 不变。

    amount 与个股 daily 同为**千元**，x1000 转元。
    """
    from qsys.data.query import WarehouseQuery

    query = WarehouseQuery(warehouse)
    frames = []
    for code in ts_codes:
        raw = provider.index_daily(code, start, end)
        if raw.height == 0:
            continue
        # INDEX_BARS.volume 是 int64，而 Tushare 的 vol 是浮点。直接 cast 会
        # 静默截断小数。先断言无损再转——**宁可报错，不可悄悄丢精度**。
        fractional = raw.filter(
            (pl.col("vol") - pl.col("vol").round(0)).abs() > 1e-9
        )
        if fractional.height:
            raise ValueError(
                f"{code} 的 index_daily vol 含非整数值 "
                f"{fractional['vol'].to_list()[:5]}，"
                f"而 INDEX_BARS.volume 是 int64。不截断、不写入。"
            )
        frames.append(raw.select([
            pl.col("ts_code").alias("symbol"),
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("dt"),
            pl.col("open"), pl.col("high"), pl.col("low"), pl.col("close"),
            pl.col("vol").round(0).cast(pl.Int64).alias("volume"),
            (pl.col("amount") * 1000.0).alias("amount"),
        ]))
    if not frames:
        return 0
    frame = pl.concat(frames, how="vertical")
    written = _merge_write(
        warehouse, "index_bars", frame, schema.INDEX_BARS,
        ["symbol", "dt"], lambda lo, hi: query.index_bars(None, lo, hi),
    )
    log.info("index_bars.ingested", rows=written, codes=list(ts_codes))
    return written
```

`schema.INDEX_BARS` 的实际列已核对：`symbol`(string) / `dt`(date32) /
`open`/`high`/`low`/`close`(float64) / **`volume`(int64)** / `amount`(float64)。
上面的 `select` 与之逐列对应。**不要改 schema**——`volume` 的 int64 是既有
`index_bars` 数据的类型，改了会让已落库的分区读不出来。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_tushare_reference.py -k index -v`
Expected: PASS（3 个）

- [ ] **Step 5: 回填 CSI300 全历史**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  "cd /home/ubuntu/qsys && .venv/bin/python -c \"
from datetime import date
from pathlib import Path
from qsys.data.pipeline.tushare_reference import ingest_index_daily
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.storage import Warehouse
from qsys.live.config import load_tushare_config
p = TushareProvider(TushareClient(load_tushare_config().token))
print('写入', ingest_index_daily(p, Warehouse(Path('warehouse')), ['000300.SH'],
                                 date(2005,1,1), date(2026,9,11)), '行')
\""
```

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/providers/tushare.py src/qsys/data/pipeline/tushare_reference.py tests/data/pipeline/test_tushare_reference.py
git commit -m "feat(data): index_daily 补上 mootdx 失效后的指数日线缺口"
```

---

## Task 7: `stock_basic` + `namechange` —— 标的主表与 ST 真值

**Files:**
- Modify: `src/qsys/data/schema.py`、`src/qsys/data/query.py`、`src/qsys/data/providers/tushare.py`、`src/qsys/data/pipeline/tushare_reference.py`
- Test: `tests/data/pipeline/test_tushare_reference.py`（追加）

**Interfaces:**
- Produces:
  - `schema.NAME_HISTORY`：`symbol`(string) / `name`(string) / `start_date`(date32) / `end_date`(date32, 可 null) / `ann_date`(date32, 可 null) / `change_reason`(string)
  - `TushareProvider.stock_basic(list_status) -> pl.DataFrame`
  - `TushareProvider.namechange() -> pl.DataFrame`
  - `ingest_instruments(provider, warehouse) -> int`（写既有 `instruments` 表，含退市）
  - `ingest_name_history(provider, warehouse) -> int`
  - `WarehouseQuery.name_history(symbols=None) -> pl.DataFrame`
  - `qsys.data.pipeline.tushare_reference.st_on(name_history, day) -> pl.DataFrame`（列 `symbol`, `is_st`, `name`）

**ST 用区间表展开，不用字符串匹配。** `namechange` 带 `start_date`/`end_date`/
`change_reason`，按区间展开成每日状态属**纯派生**（可逐位重算）；
而 `name.str.contains("ST")` 会把名称中恰好含 "ST" 的正常标的误判。

- [ ] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_tushare_reference.py 追加

from qsys.data.pipeline.tushare_reference import (
    ingest_instruments, ingest_name_history, st_on,
)


class FakeBasicProvider:
    def __init__(self, listed, delisted, names):
        self._listed, self._delisted, self._names = listed, delisted, names

    def stock_basic(self, list_status):
        return self._listed if list_status == "L" else self._delisted

    def namechange(self):
        return self._names


def _basic_frame(codes, status):
    return pl.DataFrame({
        "ts_code": codes,
        "name": [f"股票{i}" for i in range(len(codes))],
        "list_date": ["20100101"] * len(codes),
        "delist_date": ([None] * len(codes)) if status == "L"
                       else ["20240601"] * len(codes),
    })


def test_instruments_include_delisted_symbols(warehouse):
    """广度池此前平均少 14.6 只，就是因为免费源拿不到退市股。"""
    p = FakeBasicProvider(_basic_frame(["000001.SZ"], "L"),
                          _basic_frame(["600001.SH"], "D"),
                          pl.DataFrame())
    assert ingest_instruments(p, warehouse) == 2
    got = WarehouseQuery(warehouse).instruments()
    assert set(got["symbol"].to_list()) == {"000001.SZ", "600001.SH"}
    delisted = got.filter(pl.col("symbol") == "600001.SH")
    assert delisted["delist_date"].to_list() == [date(2024, 6, 1)]


def test_instruments_carry_market_and_board(warehouse):
    """INSTRUMENTS 有 6 列，漏任何一列 cast 都会失败。

    board 必须与 markets.ashare.board_of 一致——计划 A 已把它提升为共享函数，
    这里复用而不是另写一份，否则两处会对同一只票给出不同板块。
    """
    p = FakeBasicProvider(
        pl.DataFrame({"ts_code": ["300750.SZ"], "name": ["宁德时代"],
                      "list_date": ["20180611"], "delist_date": [None]}),
        pl.DataFrame(), pl.DataFrame())
    ingest_instruments(p, warehouse)
    got = WarehouseQuery(warehouse).instruments()
    assert got["market"].to_list() == ["SZ"]
    assert got["board"].to_list() == ["gem"]


def test_name_history_keeps_the_interval_form(warehouse):
    """区间表原样落库。展开成每日状态是纯派生，落库的是直取值。"""
    p = FakeBasicProvider(pl.DataFrame(), pl.DataFrame(), pl.DataFrame({
        "ts_code": ["000010.SZ", "000010.SZ"],
        "name": ["*ST美丽", "美丽生态"],
        "start_date": ["20260430", "20200722"],
        "end_date": [None, "20260429"],
        "ann_date": ["20260429", "20200721"],
        "change_reason": ["*ST", "撤销*ST"],
    }))
    assert ingest_name_history(p, warehouse) == 2
    got = WarehouseQuery(warehouse).name_history(["000010.SZ"]).sort("start_date")
    assert got["change_reason"].to_list() == ["撤销*ST", "*ST"]
    assert got["end_date"].to_list() == [date(2026, 4, 29), None]


def test_st_on_expands_the_interval_to_a_given_day():
    """2026-04-30 起是 *ST，之前不是。阶跃必须落在正确的一天。"""
    hist = pl.DataFrame({
        "symbol": ["000010.SZ", "000010.SZ"],
        "name": ["美丽生态", "*ST美丽"],
        "start_date": [date(2020, 7, 22), date(2026, 4, 30)],
        "end_date": [date(2026, 4, 29), None],
        "ann_date": [date(2020, 7, 21), date(2026, 4, 29)],
        "change_reason": ["撤销*ST", "*ST"],
    })
    before = st_on(hist, date(2026, 4, 29))
    after = st_on(hist, date(2026, 4, 30))
    assert before["is_st"].to_list() == [False]
    assert before["name"].to_list() == ["美丽生态"]
    assert after["is_st"].to_list() == [True]
    assert after["name"].to_list() == ["*ST美丽"]


def test_st_on_returns_nothing_for_a_day_before_any_interval():
    """区间之前没有名称记录，就不给结论——不默认成「不是 ST」。"""
    hist = pl.DataFrame({
        "symbol": ["000010.SZ"], "name": ["*ST美丽"],
        "start_date": [date(2026, 4, 30)], "end_date": [None],
        "ann_date": [date(2026, 4, 29)], "change_reason": ["*ST"],
    })
    assert st_on(hist, date(2019, 1, 1)).height == 0


def test_st_on_recognises_plain_st_not_only_star_st():
    hist = pl.DataFrame({
        "symbol": ["000078.SZ"], "name": ["ST海王"],
        "start_date": [date(2025, 5, 6)], "end_date": [None],
        "ann_date": [date(2025, 5, 5)], "change_reason": ["ST"],
    })
    assert st_on(hist, date(2026, 9, 11))["is_st"].to_list() == [True]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_tushare_reference.py -k "instrument or name_history or st_on" -v`
Expected: FAIL，`ImportError: cannot import name 'ingest_instruments'`

- [ ] **Step 3: 实现**

```python
# src/qsys/data/schema.py —— 新增，注册进 UNPARTITIONED（无日期分区）

# 曾用名区间表，**直取**。ST 状态由它按区间展开得到（纯派生）。
# 用区间表而非逐日字符串匹配：后者会把名称中恰好含 "ST" 的正常标的误判。
NAME_HISTORY = pa.schema([
    ("symbol", pa.string()),
    ("name", pa.string()),
    ("start_date", pa.date32()),
    ("end_date", pa.date32()),      # 当前仍在用的名称为 null
    ("ann_date", pa.date32()),
    ("change_reason", pa.string()),
])
```

```python
# src/qsys/data/providers/tushare.py 追加

    def stock_basic(self, list_status: str) -> pl.DataFrame:
        rows = self._client.call(
            "stock_basic", {"exchange": "", "list_status": list_status},
            "ts_code,symbol,name,area,industry,list_date,delist_date",
        )
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    def namechange(self) -> pl.DataFrame:
        rows = self._client.call(
            "namechange", {},
            "ts_code,name,start_date,end_date,ann_date,change_reason",
        )
        return pl.DataFrame(rows) if rows else pl.DataFrame()
```

```python
# src/qsys/data/pipeline/tushare_reference.py 追加

from qsys.markets.ashare import board_of

_ST_REASONS = ("ST",)   # change_reason 含 "ST" 即为 ST 类变更（含 *ST、撤销*ST）


def _to_date(col: str) -> pl.Expr:
    return pl.col(col).str.strptime(pl.Date, "%Y%m%d", strict=False)


def ingest_instruments(provider, warehouse: Warehouse) -> int:
    """标的主表，**含退市股**。

    此前来自 akshare，拿不到退市股，广度池平均少 14.6 只。
    """
    parts = []
    for status in ("L", "D"):
        raw = provider.stock_basic(status)
        if raw.height == 0:
            continue
        parts.append(raw)
    if not parts:
        return 0
    frame = pl.concat(parts, how="diagonal")
    # INSTRUMENTS 的实际列：symbol / name / market / list_date / delist_date / board。
    # market 直接取 ts_code 的交易所后缀（SZ / SH / BJ），board 按号段判。
    out = frame.select([
        pl.col("ts_code").alias("symbol"),
        pl.col("name"),
        pl.col("ts_code").str.split(".").list.last().alias("market"),
        _to_date("list_date").alias("list_date"),
        _to_date("delist_date").alias("delist_date"),
        pl.col("ts_code").map_elements(
            lambda s: board_of(s.split(".")[0]), return_dtype=pl.Utf8
        ).alias("board"),
    ]).unique(subset=["symbol"], keep="last").sort("symbol")
    warehouse.write("instruments",
                    out.to_arrow().cast(schema.INSTRUMENTS))
    log.info("instruments.ingested", rows=out.height)
    return out.height


def ingest_name_history(provider, warehouse: Warehouse) -> int:
    """曾用名区间表，原样落库。展开成每日状态是消费方的纯派生。"""
    raw = provider.namechange()
    if raw.height == 0:
        return 0
    out = raw.select([
        pl.col("ts_code").alias("symbol"),
        pl.col("name"),
        _to_date("start_date").alias("start_date"),
        _to_date("end_date").alias("end_date"),
        _to_date("ann_date").alias("ann_date"),
        pl.col("change_reason").cast(pl.Utf8),
    ]).unique(subset=["symbol", "start_date"], keep="last").sort(
        ["symbol", "start_date"]
    )
    warehouse.write("name_history", out.to_arrow().cast(schema.NAME_HISTORY))
    log.info("name_history.ingested", rows=out.height)
    return out.height


def st_on(name_history: pl.DataFrame, day: date) -> pl.DataFrame:
    """把区间表展开到某一天，返回 `symbol` / `is_st` / `name`。

    **纯派生**：区间覆盖当日则取该区间的名称，`end_date` 为 null 表示仍在用。
    当日不落在任何区间内的标的**不返回行**——没有名称记录就不给 ST 结论，
    不默认成「不是 ST」。默认 False 会让 ST 股按 10% 档算涨停价。

    ST 判定用 `name` 是否以 "ST" 或 "*ST" 开头，而不是包含 "ST"——
    包含会把名称中恰好含这两个字母的正常标的误判。
    """
    if name_history.height == 0:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "is_st": pl.Boolean,
                                    "name": pl.Utf8})
    covering = name_history.filter(
        (pl.col("start_date") <= day)
        & (pl.col("end_date").is_null() | (pl.col("end_date") >= day))
    )
    if covering.height == 0:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "is_st": pl.Boolean,
                                    "name": pl.Utf8})
    latest = (
        covering.sort(["symbol", "start_date"])
        .group_by("symbol", maintain_order=True)
        .last()
    )
    return latest.select([
        pl.col("symbol"),
        (pl.col("name").str.starts_with("ST")
         | pl.col("name").str.starts_with("*ST")).alias("is_st"),
        pl.col("name"),
    ]).sort("symbol")
```

```python
# src/qsys/data/query.py 追加

def name_history(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
    """曾用名区间表（Tushare `namechange`）。ST 状态由它派生。"""
    glob = self._glob("name_history", partitioned=False)
    if glob is None:
        return _empty(schema.NAME_HISTORY)
    columns = ", ".join(schema.NAME_HISTORY.names)
    sql = f"SELECT {columns} FROM {_read(glob)}"
    params: list[object] = []
    if symbols is not None:
        if not symbols:
            return _empty(schema.NAME_HISTORY)
        placeholders = ", ".join("?" for _ in symbols)
        sql += f" WHERE symbol IN ({placeholders})"
        params.extend(symbols)
    return self._con.execute(sql + " ORDER BY symbol, start_date", params).pl()
```

`schema.INSTRUMENTS` 的实际列已核对：`symbol` / `name` / `market` /
`list_date` / `delist_date` / `board`（注释写明 `main / gem / star / bse`）。
上面的 `select` 与之逐列同名同序。**不要改 schema**。

注意 `board` 的取值必须与 `qsys.markets.ashare.board_of` 一致——
计划 A 的 Task 0 已把它提升为共享函数，这里直接复用，不要另写一份。

- [ ] **Step 4: 跑测试确认通过 + 全量测试**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_tushare_reference.py -v && .venv/bin/python -m pytest -rfE --tb=no -q`
Expected: 新测试 PASS；全量失败数不增加

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/query.py src/qsys/data/providers/tushare.py src/qsys/data/pipeline/tushare_reference.py tests/data/pipeline/test_tushare_reference.py
git commit -m "feat(data): stock_basic 含退市股，namechange 区间表给 ST 真值"
```

---

## Task 8: `limit_list_d` —— 首板与炸板直取

**Files:**
- Modify: `src/qsys/data/schema.py`、`src/qsys/data/query.py`、`src/qsys/data/providers/tushare.py`、`src/qsys/data/pipeline/tushare_reference.py`
- Test: `tests/data/pipeline/test_tushare_reference.py`（追加）

**Interfaces:**
- Produces:
  - `schema.LIMIT_LIST_DAILY`：`symbol` / `dt` / `limit_type`(string) / `limit_times`(int64) / `open_times`(int64) / `first_time`(string) / `fd_amount`(float64) / `limit_amount`(float64) / `float_mv`(float64)
  - `TushareProvider.limit_list_d(trade_date, limit_type) -> pl.DataFrame`
  - `ingest_limit_list(provider, warehouse, days) -> int`
  - `WarehouseQuery.limit_list(symbols, start, end) -> pl.DataFrame`

**实测限制（写进代码注释）**：数据**自 2020 年起**（2020-01-02 有 84 行、
2019-01-02 为 0），且**不含 ST 股票的统计**。策略本就 `filter_st_paused_stock`，
回测区间 2023 起，两条限制都不影响当前用途——但必须记录，否则将来有人
拿它做 2020 年之前的统计会得到静默的空结果。

- [ ] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_tushare_reference.py 追加

from qsys.data.pipeline.tushare_reference import ingest_limit_list


class FakeLimitListProvider:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def limit_list_d(self, trade_date, limit_type):
        self.calls.append((trade_date, limit_type))
        return self.frame


def test_limit_list_stores_consecutive_and_open_times(warehouse):
    """首板 = limit_times == 1，炸板 = open_times > 0。此前靠差集与高低价派生。"""
    p = FakeLimitListProvider(pl.DataFrame({
        "ts_code": ["000001.SZ", "600000.SH"],
        "trade_date": ["20260911", "20260911"],
        "limit_times": [1, 3], "open_times": [0, 2],
        "first_time": ["09:31:00", "10:15:00"],
        "fd_amount": [1.2e8, 3.4e7], "limit_amount": [5.0e8, 6.0e8],
        "float_mv": [1.0e10, 2.0e10],
    }))
    assert ingest_limit_list(p, warehouse, [DAY]) == 2
    got = WarehouseQuery(warehouse).limit_list(None, DAY, DAY).sort("symbol")
    assert got["limit_times"].to_list() == [1, 3]
    assert got["open_times"].to_list() == [0, 2]
    assert got["limit_type"].to_list() == ["U", "U"]


def test_limit_list_before_2020_is_empty_not_an_error(warehouse):
    """实测该接口自 2020 年起。更早返回 0 行是事实，不是故障。"""
    p = FakeLimitListProvider(pl.DataFrame())
    assert ingest_limit_list(p, warehouse, [date(2019, 1, 2)]) == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_tushare_reference.py -k limit_list -v`
Expected: FAIL，`ImportError: cannot import name 'ingest_limit_list'`

- [ ] **Step 3: 实现**

```python
# src/qsys/data/schema.py —— 新增并注册进 PARTITIONED

# 每日涨跌停/炸板列表，**直取**。
#   limit_times 连板数：== 1 即首板
#   open_times  炸板次数：> 0 即当日曾开板
# 此前首板靠「昨日涨停集合减前日涨停集合」的差集、炸板靠「high 触板但 close 未封」
# 派生得到，两者都依赖先算对涨跌停价。
#
# **实测限制**：数据自 2020 年起（2020-01-02 有 84 行、2019-01-02 为 0），
# 且不含 ST 股票的统计。用它做 2020 年之前的统计会得到静默的空结果。
LIMIT_LIST_DAILY = pa.schema([
    ("symbol", pa.string()),
    ("dt", pa.date32()),
    ("limit_type", pa.string()),      # U 涨停 / D 跌停 / Z 炸板
    ("limit_times", pa.int64()),
    ("open_times", pa.int64()),
    ("first_time", pa.string()),
    ("fd_amount", pa.float64()),
    ("limit_amount", pa.float64()),
    ("float_mv", pa.float64()),
])
```

```python
# src/qsys/data/providers/tushare.py 追加

    def limit_list_d(self, trade_date: date, limit_type: str) -> pl.DataFrame:
        rows = self._client.call("limit_list_d", {
            "trade_date": trade_date.strftime("%Y%m%d"), "limit_type": limit_type,
        })
        return _rows_to_frame(rows, ("fd_amount", "limit_amount", "float_mv"))
```

```python
# src/qsys/data/pipeline/tushare_reference.py 追加

def ingest_limit_list(provider, warehouse: Warehouse,
                      days: Sequence[date], limit_type: str = "U") -> int:
    """每日涨跌停列表。默认只取涨停（U）——策略的候选池是首板与炸板。"""
    from qsys.data.query import WarehouseQuery

    query = WarehouseQuery(warehouse)
    frames = []
    for day in days:
        raw = provider.limit_list_d(day, limit_type)
        if raw.height == 0:
            continue
        frames.append(raw.select([
            pl.col("ts_code").alias("symbol"),
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("dt"),
            pl.lit(limit_type, dtype=pl.Utf8).alias("limit_type"),
            pl.col("limit_times").cast(pl.Int64),
            pl.col("open_times").cast(pl.Int64),
            pl.col("first_time").cast(pl.Utf8),
            pl.col("fd_amount"), pl.col("limit_amount"), pl.col("float_mv"),
        ]))
    if not frames:
        return 0
    frame = pl.concat(frames, how="vertical")
    written = _merge_write(
        warehouse, "limit_list_daily", frame, schema.LIMIT_LIST_DAILY,
        ["symbol", "dt", "limit_type"],
        lambda lo, hi: query.limit_list(None, lo, hi),
    )
    log.info("limit_list.ingested", rows=written, days=len(days))
    return written
```

```python
# src/qsys/data/query.py 追加

def limit_list(
    self,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
) -> pl.DataFrame:
    """每日涨跌停列表（Tushare `limit_list_d`）。**数据自 2020 年起**。"""
    columns = ", ".join(schema.LIMIT_LIST_DAILY.names)
    return self._dated_query("limit_list_daily", columns,
                             schema.LIMIT_LIST_DAILY, symbols, start, end)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_tushare_reference.py -k limit_list -v`
Expected: PASS（2 个）

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/query.py src/qsys/data/providers/tushare.py src/qsys/data/pipeline/tushare_reference.py tests/data/pipeline/test_tushare_reference.py
git commit -m "feat(data): limit_list_d 落库，首板与炸板改直取"
```

---

## Task 9: 每日增量任务

**Files:**
- Create: `src/qsys/data/pipeline/tushare_sync.py`
- Create: `scripts/tushare_daily_sync.py`
- Test: `tests/data/pipeline/test_tushare_sync.py`

**Interfaces:**
- Consumes: `compose_daily` / `write_year`（`pipeline/tushare_daily.py`，Task 2）、
  `TushareProvider.daily/daily_basic/adj_factor`（Task 1）、
  `load_tushare_config`（`qsys.live.config`）
- Produces: `sync(provider, warehouse, day) -> dict[str, int]`，
  以及模块级 `_REFERENCE_STEPS: list[tuple[str, Callable]]`——Task 4-8 各自把
  自己的 `ingest_*` 追加进去

**2026-09-14 订正**（依据写在下面，原文的问题一并记录）：

原文的 `sync` 从 `qsys.data.pipeline.tushare_reference` 导入
`ingest_price_limits` / `ingest_suspensions` / `ingest_limit_list` /
`ingest_index_daily` 四个函数——**那个模块由 Task 4-8 创建，现在不存在**。
照原文写会 `ImportError`，任务根本跑不起来。

本任务提前到 Task 4-8 之前执行，理由是生产链路正断着：mootdx 已死，
自 2026-09-10 起没有任何日线自动入库，`paid_daily` 靠手动转存、
`tushare_daily` 靠一次性回填。先接上每日数据流，再补真值表。

订正为：`sync` 只做当前存在的数据集（`tushare_daily`），
参考数据集改为模块级 `_REFERENCE_STEPS` 列表，当前为空，Task 4-8 各自追加。

**并加一道接线守卫测试**：一旦 `tushare_reference` 模块存在，其公开的每个
`ingest_*` 函数都必须出现在 `_REFERENCE_STEPS` 里，否则测试失败。
这一条不是多余——计划 A 的 Task 8 就踩过同一个坑：`build_daily_flags`
加了 `boards` 参数却没有任何生产调用点在传，派生表一天天被污染回去且不报错。
新增一个采集函数却忘了接进每日任务，是同一类缺陷。

**承接 spec §8 作废后仍成立的两条**：

1. **没拿到新数据必须显式报错退出**，不许静默当成正常。2026-09-10/11 的归档
   超时事故根因就是上游静默返回空而系统当成正常。
2. **15:40 的既有归档不依赖当日日线**——竞价 tick、股本、分钟线的候选均只需
   T-1 的 `daily_flags`。故本任务不改动既有归档的时点。

- [ ] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_tushare_sync.py

"""每日增量同步。拿不到数据必须显式失败，不许静默当成正常。"""

import importlib
from datetime import date

import polars as pl
import pytest

from qsys.data.pipeline import tushare_sync
from qsys.data.pipeline.tushare_sync import sync

DAY = date(2026, 9, 11)


class FakeProvider:
    """只实现 sync 真正会调的三个方法。

    多实现没用到的方法会掩盖「sync 调了不该调的接口」——每次调用都是积分。
    """

    def __init__(self, daily_rows: int = 1) -> None:
        self.daily_rows = daily_rows
        self.calls: list[str] = []

    def daily(self, d: date) -> pl.DataFrame:
        self.calls.append("daily")
        if self.daily_rows == 0:
            return pl.DataFrame()
        return pl.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": ["20260911"],
            "open": [11.76], "high": [11.79], "low": [11.69], "close": [11.70],
            "pre_close": [11.70], "change": [0.0], "pct_chg": [0.0],
            "vol": [100.0], "amount": [1000.0],
        })

    def daily_basic(self, d: date) -> pl.DataFrame:
        self.calls.append("daily_basic")
        return pl.DataFrame()

    def adj_factor(self, d: date) -> pl.DataFrame:
        self.calls.append("adj_factor")
        return pl.DataFrame()


def test_reports_row_counts_per_dataset(warehouse):
    got = sync(FakeProvider(), warehouse, DAY)
    assert got["tushare_daily"] == 1


def test_an_empty_daily_is_an_explicit_failure(warehouse):
    """日线 0 行 = 上游没给数据。静默当成正常正是 09-10/11 事故的根因。"""
    with pytest.raises(RuntimeError) as exc:
        sync(FakeProvider(daily_rows=0), warehouse, DAY)
    assert "20260911" in str(exc.value)


def test_an_empty_daily_writes_nothing(warehouse):
    """报错之前不能已经写了一半——半张表比没有表更难查。"""
    from qsys.data.query import WarehouseQuery

    with pytest.raises(RuntimeError):
        sync(FakeProvider(daily_rows=0), warehouse, DAY)
    assert WarehouseQuery(warehouse).tushare_daily().height == 0


def test_does_not_call_interfaces_it_does_not_need(warehouse):
    """每次接口调用都是积分。只该调 daily / daily_basic / adj_factor 三个。"""
    provider = FakeProvider()
    sync(provider, warehouse, DAY)
    assert sorted(provider.calls) == ["adj_factor", "daily", "daily_basic"]


def test_every_reference_ingest_is_wired_into_sync():
    """Task 4-8 新增的 ingest_* 必须接进每日任务，否则采集不到却不报错。

    计划 A 的 Task 8 踩过同一个坑：build_daily_flags 加了 boards 参数却没有
    生产调用点在传，派生表一天天被污染回去且不报错。

    tushare_reference 尚不存在时本条不生效——此时 _REFERENCE_STEPS 应为空。
    """
    wired = {name for name, _ in tushare_sync._REFERENCE_STEPS}
    try:
        ref = importlib.import_module("qsys.data.pipeline.tushare_reference")
    except ModuleNotFoundError:
        assert wired == set(), "参考模块还不存在，_REFERENCE_STEPS 应为空"
        return
    exported = {n for n in dir(ref) if n.startswith("ingest_")}
    missing = {n for n in exported if not any(fn.__name__ == n
                                              for _, fn in tushare_sync._REFERENCE_STEPS)}
    assert not missing, f"这些采集函数没接进每日同步：{sorted(missing)}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/pipeline/test_tushare_sync.py -v`
Expected: FAIL，`ModuleNotFoundError: qsys.data.pipeline.tushare_sync`

- [ ] **Step 3: 实现**

`scripts/` 不是 Python 包，测试无法 `from scripts.x import`（计划 A 的 pre-flight
已经踩过）。按本仓库既有分层——纯逻辑进模块、脚本只做薄 CLI
（`scripts/ingest_paid_daily.py` 55 行之于 `pipeline/paid_daily.py` 241 行）——
`sync` 放进 `src/qsys/data/pipeline/tushare_sync.py`。

```python
# src/qsys/data/pipeline/tushare_sync.py

"""每日增量同步：一天的全部 Tushare 数据集。

**日线为空必须显式失败。** 2026-09-10/11 的归档在 7200 秒超时被杀，根因是
mootdx 上游静默返回空表而系统当成正常，连采了两小时空数据都没报错。
停牌、涨跌停列表为空则是常态，不视为失败——这两类的判据在各自的
ingest_* 函数里，不在这里。

`_REFERENCE_STEPS` 当前为空：涨跌停价、停牌、涨跌停列表、指数日线四张表
由计划 C 的 Task 4-8 建，各自把自己的 ingest_* 追加进来。
tests/data/pipeline/test_tushare_sync.py 有一条守卫测试盯着这件事——
新增采集函数却忘了接线，是「采集不到却不报错」的经典形态。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import structlog

from qsys.data.pipeline.tushare_daily import compose_daily, write_year
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

CSI300 = "000300.SH"

# (数据集名, 采集函数)。函数签名统一为 fn(provider, warehouse, [day]) -> int。
# Task 4: ingest_price_limits / Task 5: ingest_suspensions
# Task 6: ingest_index_daily  / Task 8: ingest_limit_list
_REFERENCE_STEPS: list[tuple[str, Callable[..., int]]] = []


def sync(provider, warehouse: Warehouse, day: date) -> dict[str, int]:
    """同步一个交易日。返回 {数据集: 写入行数}。

    日线为空直接抛错，**且在抛错之前不写任何表**——半张表比没有表更难查。
    """
    daily = provider.daily(day)
    if daily.height == 0:
        # 非交易日由调用方用 trade_cal 提前排除；走到这里就是该有数据却没有。
        raise RuntimeError(
            f"Tushare daily 在 {day:%Y%m%d} 返回 0 行——该交易日应有数据。"
            f"不写入任何表，退出。"
        )

    frame = compose_daily(daily, provider.daily_basic(day), provider.adj_factor(day))
    counts = {"tushare_daily": write_year(warehouse, frame, day.year)}
    for name, ingest in _REFERENCE_STEPS:
        counts[name] = ingest(provider, warehouse, [day])

    log.info("tushare_sync.done", day=str(day), **counts)
    return counts
```

```python
# scripts/tushare_daily_sync.py

"""每日增量同步的命令行入口。逻辑在 qsys.data.pipeline.tushare_sync。

用法:
    uv run python scripts/tushare_daily_sync.py --warehouse /data/qsys/warehouse
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from qsys.data.pipeline.tushare_sync import sync
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.storage import Warehouse
from qsys.live.config import load_tushare_config


def last_open_day(client: TushareClient, today: date) -> date | None:
    """最近一个已收盘的交易日。交易日历取自 trade_cal，不自行推算。"""
    rows = client.call("trade_cal", {
        "exchange": "SSE",
        "start_date": (today - timedelta(days=30)).strftime("%Y%m%d"),
        "end_date": today.strftime("%Y%m%d"), "is_open": "1",
    })
    days = sorted(r["cal_date"] for r in rows)
    if not days:
        return None
    stamp = days[-1]
    return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--day", type=date.fromisoformat, default=None,
                        help="指定交易日；不给则取最近一个已收盘交易日")
    args = parser.parse_args()

    client = TushareClient(load_tushare_config(args.config).token)
    day = args.day or last_open_day(client, date.today())
    if day is None:
        print("最近 30 天没有交易日，退出")
        return 0

    counts = sync(TushareProvider(client), Warehouse(args.warehouse), day)
    print(f"=== {day} ===")
    for name, rows in counts.items():
        print(f"  {name:22s} {rows:>9,} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过 + 全量测试**

Run: `uv run pytest tests/data/pipeline/test_tushare_sync.py -v && uv run pytest -q`
Expected: 新测试全 PASS；全量 exit=0

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/pipeline/tushare_sync.py scripts/tushare_daily_sync.py \
        tests/data/pipeline/test_tushare_sync.py
git commit -m "feat(data): Tushare 每日增量同步"
```

- [ ] **Step 6: 在节点上装 systemd 单元（由控制方执行，不派实现者）**

生产节点的写操作留在控制方手里——`warehouse` 是指向 `/data/qsys` 的软链接，
`ProtectSystem=strict` 少写一条 `ReadWritePaths` 就会在第一次写入时失败，
这个坑本仓库已经踩过一次。

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 "sudo tee /etc/systemd/system/qsys-tushare-sync.service >/dev/null <<'UNIT'
[Unit]
Description=qsys Tushare 每日增量同步
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/qsys
ExecStart=/home/ubuntu/qsys/.venv/bin/python -u scripts/tushare_daily_sync.py \
          --warehouse /home/ubuntu/qsys/warehouse
TimeoutStartSec=1800
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
# warehouse 是指向数据盘的软链接；少了 /data/qsys 会在第一次写入时失败。
ReadWritePaths=/home/ubuntu/qsys /data/qsys

[Install]
WantedBy=multi-user.target
UNIT
sudo tee /etc/systemd/system/qsys-tushare-sync.timer >/dev/null <<'UNIT'
[Unit]
Description=交易日收盘后同步 Tushare 数据

[Timer]
# 16:30。日线盘后即可取，早于此可能拿到未结算的数据。
# 非交易日脚本自己会识别（trade_cal）并空转。
OnCalendar=Mon..Fri 16:30 Asia/Shanghai
Persistent=true
RandomizedDelaySec=180

[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable --now qsys-tushare-sync.timer
systemctl list-timers qsys-tushare-sync --no-pager"
```

- [ ] **Step 7: 实跑一次并核对**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  "sudo systemctl start qsys-tushare-sync.service; \
   systemctl show qsys-tushare-sync -p Result -p ExecMainStatus; \
   sudo journalctl -u qsys-tushare-sync.service -n 20 --no-pager -o cat"
```

Expected：`Result=success`，`tushare_daily` 行数约 5,500。

**节点上必须先有 `config/deploy.local.toml`**（已 gitignore，不随 git 同步）。
装单元之前先确认它在，否则 `load_tushare_config` 会 `FileNotFoundError`。

---

## Task 10: 切换生产查询、冻结旧源、文档

> **与计划 A Task 9 的衔接（2026-09-13 补）：**生产日线切到
> `tushare_daily` 时，复权因子落库入口也必须从 `paid_daily.factor` 切到
> `tushare_daily.factor`。只改 `daily_bars()` 会让价格行情持续更新而
> `adj_factors.cum_factor` 停在旧源最后一天，形成静默的跨源断层。

**Files:**
- Modify: `src/qsys/data/query.py`
- Modify: `docs/reference/baidupan-data-source.md`
- Modify: `docs/reference/strategy-data-dependencies.md`
- Create: `artifacts/Tushare换源对账报告_<日期>.md`

- [ ] **Step 1: 对账报告过目**

把 Task 3 Step 7 的三个 parquet 与 summary 整理成
`artifacts/Tushare换源对账报告_<日期>.md`，**必须包含**：

- 各字段差异行数与占比，按年分布；
- `total_shares` / `circulating_shares` 差异的标的清单与 A+H 归属核对；
- 价格类字段（open/high/low/close/pre_close）的差异**逐条列出并解释**，
  没有解释的差异不算通过；
- 只在一边的股票日清单。

- [ ] **Step 2: 切换 `daily_bars()` 的底层**

```python
# src/qsys/data/query.py —— daily_bars 的 dataset 由 "paid_daily" 改为 "tushare_daily"

    glob = self._glob("tushare_daily", partitioned=True)
```

docstring 相应更新：说明 2026-09-13 由 `paid_daily` 再次换底，依据是对账报告。
**`paid_daily` 随之冻结**，与 `bars_daily` 同等对待：新增
`WarehouseQuery.legacy_paid_daily()` 供对账，生产路径不再读它。

同一步还要把计划 A Task 9 建立的直取因子入口切到底层 `tushare_daily`，并加
回归测试断言新到交易日能进入 `adj_factors.cum_factor`。价格与因子必须原子换源。

- [ ] **Step 3: 跑全量测试**

Run: `.venv/bin/python -m pytest -rfE --tb=no -q`
Expected: 全绿（前提是计划 A 已完成）

- [ ] **Step 4: 更新参考文档**

`docs/reference/baidupan-data-source.md` 头部加：

```markdown
> **已于 2026-09-13 停用**：日线源改为 Tushare Pro API 直连。实测证实本卖家
> 转售 Tushare（2026-09-11 逐字段零差异），换源不改变数据，只消除人工转存环节。
> 本文档作为历史记录保留；接入细节见
> `docs/superpowers/plans/2026-09-13-tushare-ingestion.md`。
```

`docs/reference/strategy-data-dependencies.md` 的映射表里，把「Tushare 对应」
一列从计划状态改为实际的表名与查询方法。

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/query.py docs/ artifacts/
git commit -m "feat(data): 生产查询切到 tushare_daily，百度网盘源冻结"
```

---

## 不在本计划范围

| 项 | 原因 |
|---|---|
| **竞价逐笔** | Tushare 无等价接口。`stk_auction` 是 9:25 撮合后的最终结果，而策略取的是「第一条」（09:26 路线）与「09:24 那条」（早单路线），口径对不上，替代会引入前视偏差。只能由 QMT 每日实时采集。详见 `docs/reference/strategy-data-dependencies.md` |
| `flags.py` 改用 `price_limits` 直取 | 属计划 A 的 Task 7 范围。计划 A 先按 `pre_close` 纯派生实现，本计划的表就位后再切直取，切换时以直取值优先 |
| 删除 `paid_daily` / `bars_daily` | 去重方案的批次 B，须待对账报告与计划 A 的三段式验收都通过 |
| 分钟线 | 2009—2025 已自行落库 16 亿行；2026 走 QMT。Tushare 历史分钟 ¥2000/年不必买 |
| 龙虎榜 / 融资融券 / 资金流向 | 5000 积分档已含，但当前策略不使用。用到再接 |

## 风险

1. **积分一年有效期、到期回收**（2026-09-13 购入）。到期未续费则日线源直接中断。
   须设到期提醒，建议在 Task 10 的文档里写明续费日期。
2. **与百度网盘卖家同源**，因此二者**不构成两票**，不能互为交叉验证。
   独立验真仍靠同花顺抽样。
3. **全量回填期间不要并发跑回测**。节点 4 核，2026-09-10 实测并行三个回测会让
   全市场采集从 6 分钟劣化到 335 分钟。
4. **`limit_list_d` 自 2020 年起且不含 ST**。用它做更早年份或 ST 相关的统计会得到
   静默的空结果。
5. **`stk_limit` 的确切起始年份未定**。实测 2010-01-04 有数据、2005-01-04 无数据，
   起点在两者之间。若将来回测区间前移到 2010 年之前，涨跌停价仍需回退到纯派生。
