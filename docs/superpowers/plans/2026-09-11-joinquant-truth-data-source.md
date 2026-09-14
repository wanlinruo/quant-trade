# 聚宽真值数据源接入 —— 实施计划

> **已作废（2026-09-11）**：真值源改为百度网盘付费数据，本方案的聚宽采集部分
> （Task 1-6、8、9）不再执行。消费侧改造（Task 7、10-14）的目标仍然成立，
> 已并入 `docs/superpowers/specs/2026-09-12-paid-daily-truth-source-design.md`，
> 并已由该 spec 的实施计划
> `docs/superpowers/plans/2026-09-13-paid-daily-truth-source.md` 完成。
> **例外：停牌真值不在付费源方案内**——付费源没有停牌标志，停牌日直接不产出行
> （付费源 spec §3.6/§3.7），该目标改由计划 C 的 Tushare `suspend_d` 承接。
>
> **2026-09-13 再次变更**：日线源进一步改为 Tushare Pro API 直连
> （付费源实测即 Tushare 转售），见
> `docs/superpowers/plans/2026-09-13-tushare-ingestion.md`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 ST 判定、涨跌停价、停牌、退市股这四项从「价格反推」换成聚宽直接返回的真值，使生产路径上不再存在任何推断。

**Architecture:** 新增 `joinquant` provider（批量取数，一次请求一批标的）与两张事实表 `daily_reference` / `st_daily`；`bars_daily` 的 8 列 schema 不动、数据全量由聚宽重建；`daily_flags` 保留列名、值改为真值 join，下游 76 处引用零改动；`flags.py` 从 342 行降到约 150 行，ST 三条启发式规则与除权参考价公式整段删除。

**Tech Stack:** Python 3.11+ / polars / pyarrow / duckdb / structlog / pytest / jqdatasdk 1.9.8（可选依赖组）

**Spec:** `docs/superpowers/specs/2026-09-11-joinquant-truth-data-source-design.md`

## Global Constraints

以下每一条都来自 spec，**每个任务的要求都隐含包含本节**：

- **禁止编造数据。** 仓库每一列只允许两类：直取（接口原值）、纯派生（直取列的确定性算术）。拿不到就记 null 由质检报出来，**不准填**。禁止启发式、阈值猜测、缺失值填充、近似替代。
- **`get_price` 三个参数必须显式写死**：`fq=None`、`fill_paused=False`、`skip_paused=False`。默认值 `fill_paused=True` 会用停牌前的 close 填充停牌日；默认 `fq='pre'` 前复权到请求日、落盘即腐烂。必须有测试守着这三个取值。
- **`panel=False`**：返回长表（多出 `code` / `time` 两列），不用 Panel。
- **NaN 必须转成 null**：`fill_paused=False` 下停牌日价格是 NaN。NaN 是一个真实浮点值，会污染比较运算，落盘前一律 `fill_nan(None)`。
- **不猜聚宽的未知项**：北交所代码后缀、计费口径、`volume` 单位、财经表列名等，一律由 `scripts/jq_probe.py` 实测后再定，未确认前显式抛错，不给默认值。
- **`volume` 单位在全量采集之前必须定论**（Task 7）。现有 `bars_daily.volume` 存的是**手**（`sim_broker.py:28` 实测 99.88% 对 0%）。禁止在采集时 `/100` 转换——那是变换，且零碎成交会产生非整数。存真值、改消费点。
- **凭据只进 `config/deploy.local.toml`**（已 gitignore），绝不进代码库、日志、异常消息。
- **采集期间关闭每日归档定时器**（Task 1 步骤 1）。2026-09-10 实测并行跑回测会让日线增量从 6 分钟劣化到 335 分钟。
- 分批规模 **1000 只 × 1 年**（约 24 万行 / 30 MB）。节点实况：4 核 / 7.5 GB 内存（可用 5.6 GB）/ 118 GB 磁盘（可用 82 GB）。
- 所有测试**不得联网**：provider 走依赖注入，测试注入假 client。

---

## File Structure

**新建**

| 文件 | 职责 |
|---|---|
| `src/qsys/data/providers/joinquant.py` | 聚宽适配器：代码映射、client 协议、真实 client、批量取数方法 |
| `src/qsys/data/pipeline/jq_daily.py` | 日线 + `daily_reference` 的分批采集流水线（续传、额度闸门） |
| `src/qsys/data/pipeline/jq_st.py` | `st_daily` 采集流水线 |
| `scripts/jq_probe.py` | 9 项未知量的实测探针，结果落 `var/` |
| `scripts/ingest_joinquant.py` | 采集入口（日线 / ST / instruments 三个子命令） |
| `scripts/sample_verify.py` | 随机抽样打印直取字段，供与同花顺人工比对 |
| `docs/reference/joinquant-data-source.md` | 探针结论与字段血缘的落地记录 |
| `tests/data/providers/test_joinquant.py` | 映射、参数守护、NaN→null、拆表 |
| `tests/data/pipeline/test_jq_daily.py` | 分批、续传、额度闸门 |
| `tests/data/pipeline/test_jq_st.py` | ST 采集 |
| `tests/data/test_sample_verify.py` | 抽样脚本的确定性（固定种子） |

**修改**

| 文件 | 改什么 |
|---|---|
| `src/qsys/data/schema.py` | 新增 `DAILY_REFERENCE` / `ST_DAILY`，注册进 `PARTITIONED`；`DAILY_FLAGS` 删 4 列、加 `is_st` |
| `src/qsys/data/query.py` | 新增 `daily_reference()` / `st_daily()` 读取方法 |
| `src/qsys/data/pipeline/ingest_log.py` | 新增 `record_many()`，一批一次写盘 |
| `src/qsys/live/config.py` | 新增 `JoinquantConfig`，挂进 `DeployConfig` |
| `config/deploy.example.toml` | 新增 `[joinquant]` 段 |
| `pyproject.toml` | 新增可选依赖组 `jq` |
| `src/qsys/data/pipeline/flags.py` | 342 → 约 150 行，删 ST 反推与参考价公式 |
| `src/qsys/data/pipeline/adjust.py` | `cum_factor` 直取聚宽 `factor`，`daily_factor()` 降级为校验 |
| `src/qsys/data/quality/truth_rules.py`（新建） | 规则 4：5 条真值一致性校验 |
| `src/qsys/data/quality/rules.py` · `report.py` | 空值安全（停牌日 OHLC 为 null 会让现有规则抛 TypeError）+ 接线 |
| `src/qsys/backtest/sim_broker.py` · `src/qsys/data/pipeline/valuation.py` | `volume` 单位定论后的消费点修正（Task 7） |
| `docs/deploy/linux-core.md` | 机器实况更新（1.9 GB/40 GB → 7.5 GB/118 GB） |

---

## Task 1: 凭据配置 + 代码映射 + JQ client 抽象

**Files:**
- Create: `src/qsys/data/providers/joinquant.py`
- Create: `tests/data/providers/test_joinquant.py`
- Modify: `src/qsys/live/config.py`
- Modify: `config/deploy.example.toml`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `qsys.data.providers.base.normalize_symbol`（六位裸码 → `600036.SH`）
- Produces: `to_jq_code(symbol) -> str`、`UnknownJQMarket`、`DAILY_FIELDS: list[str]`、`JQClient` Protocol、`JQDataClient(username, password)`、`JoinquantConfig(username, password)`

- [ ] **Step 0: 关闭每日归档定时器并给 warehouse 打快照**（在节点上执行，一次性）

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  'sudo systemctl disable --now qsys-daily-archive.timer && \
   tar czf ~/warehouse-before-jq-$(date +%F).tar.gz -C ~/qsys warehouse && \
   ls -lh ~/warehouse-before-jq-*.tar.gz'
```

采集与派生层切换期间仓库处于中间状态，定时器会用旧代码重算派生表。**Task 13 才重新启用。**

- [ ] **Step 1: 写失败测试**

```python
# tests/data/providers/test_joinquant.py
import pytest

from qsys.data.providers.joinquant import UnknownJQMarket, to_jq_code


def test_to_jq_code_maps_shanghai_and_shenzhen():
    assert to_jq_code("600036.SH") == "600036.XSHG"
    assert to_jq_code("000158.SZ") == "000158.XSHE"
    assert to_jq_code("301379.SZ") == "301379.XSHE"
    assert to_jq_code("688981.SH") == "688981.XSHG"


def test_to_jq_code_refuses_beijing_until_probe_settles_it():
    # 北交所后缀是 spec §7.5 的未决项。不猜 —— 显式抛错。
    with pytest.raises(UnknownJQMarket, match="探针"):
        to_jq_code("430047.BJ")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/providers/test_joinquant.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qsys.data.providers.joinquant'`

- [ ] **Step 3: 实现映射与 client 抽象**

```python
# src/qsys/data/providers/joinquant.py
"""聚宽 JQData 适配器。

**第一硬约束**：`get_price` 的三个参数必须显式写死，绝不用默认值——
默认 `fill_paused=True` 会「用close价格填充」停牌日（jqdatasdk 1.9.8
api.py:27 的 docstring 原文），默认 `fq='pre'` 把价格前复权到请求日、
落盘即腐烂。`_daily_kwargs()` 集中这三个值，`tests` 里有一条断言守着。

代码后缀依据同一 wheel 的 api.py:666 与 utils.py:405/414：沪 XSHG、
深 XSHE。北交所后缀未确认，显式抛错，不猜。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

# 聚宽后缀。北交所（.BJ）属探针未决项，缺席即拒绝转换。
_JQ_SUFFIX = {"SH": "XSHG", "SZ": "XSHE"}

# get_price 支持的字段，全部要：前 6 个进 bars_daily，后 6 个进 daily_reference。
DAILY_FIELDS = [
    "open", "high", "low", "close", "volume", "money",
    "pre_close", "high_limit", "low_limit", "paused", "factor", "avg",
]


class UnknownJQMarket(ValueError):
    """无法映射到聚宽后缀的市场。"""


def to_jq_code(symbol: str) -> str:
    """`'600036.SH'` → `'600036.XSHG'`。"""
    code, _, market = symbol.partition(".")
    suffix = _JQ_SUFFIX.get(market)
    if suffix is None:
        raise UnknownJQMarket(
            f"聚宽后缀未确认，需先跑探针（spec §7 第 5 项）: {symbol!r}"
        )
    return f"{code}.{suffix}"


def daily_kwargs(jq_codes: list[str], start: date, end: date) -> dict[str, Any]:
    """日线请求的完整参数。三个反污染参数在此集中写死。"""
    return {
        "security": jq_codes,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "frequency": "daily",
        "fields": DAILY_FIELDS,
        "skip_paused": False,   # 保住停牌行，不丢
        "fill_paused": False,   # 停牌日价格为 NaN，绝不用前值填充
        "fq": None,             # 原始价，复权交给 factor 列
        "panel": False,         # 长表，多出 code / time 两列
        "count": None,
        "round": True,          # 价格本就是两位小数
    }


class JQClient(Protocol):
    """jqdatasdk 的最小接口。测试注入假实现，不联网。"""

    def get_price(self, **kwargs: Any) -> Any: ...
    def get_extras(self, **kwargs: Any) -> Any: ...
    def get_all_securities(self, **kwargs: Any) -> Any: ...
    def get_trade_days(self, **kwargs: Any) -> Any: ...
    def get_query_count(self, **kwargs: Any) -> Any: ...


class JQDataClient:
    """真实客户端。`jqdatasdk` 在方法内 import，实盘运行时不依赖它。"""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._sdk: Any | None = None

    def _module(self) -> Any:
        if self._sdk is None:
            import jqdatasdk

            jqdatasdk.auth(self._username, self._password)
            log.info("joinquant.authenticated", user=self._username[:3] + "***")
            self._sdk = jqdatasdk
        return self._sdk

    def get_price(self, **kwargs: Any) -> Any:
        return self._module().get_price(**kwargs)

    def get_extras(self, **kwargs: Any) -> Any:
        return self._module().get_extras(**kwargs)

    def get_all_securities(self, **kwargs: Any) -> Any:
        return self._module().get_all_securities(**kwargs)

    def get_trade_days(self, **kwargs: Any) -> Any:
        return self._module().get_trade_days(**kwargs)

    def get_query_count(self, **kwargs: Any) -> Any:
        return self._module().get_query_count(**kwargs)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/providers/test_joinquant.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 加凭据配置**

```python
# src/qsys/live/config.py —— 在 StreamConfig 之后插入
@dataclass(frozen=True, slots=True)
class JoinquantConfig:
    username: str = ""
    password: str = field(default="", repr=False)  # 不进 repr / 日志
```

`DeployConfig` 增加字段：

```python
    joinquant: JoinquantConfig = field(default_factory=JoinquantConfig)
```

`load_deploy_config` 的返回值增加：

```python
        joinquant=JoinquantConfig(**raw.get("joinquant", {})),
```

`config/deploy.example.toml` 末尾追加：

```toml
# 聚宽 JQData 凭据。真实值只写 deploy.local.toml（已 gitignore）。
[joinquant]
username = ""
password = ""
```

`pyproject.toml` 的 `[dependency-groups]` 增加：

```toml
# 只有采集脚本用。jqdatasdk 拖 pandas + sqlalchemy + thrift，
# 不进实盘运行时依赖。
jq = [
    "jqdatasdk>=1.9.8",
    "pandas>=2.0",
]
```

- [ ] **Step 6: 跑全量测试**

Run: `uv run pytest -q`
Expected: 全部通过（新增 2 条）

- [ ] **Step 7: 提交**

```bash
git add src/qsys/data/providers/joinquant.py tests/data/providers/test_joinquant.py \
        src/qsys/live/config.py config/deploy.example.toml pyproject.toml
git commit -m "feat(data): 聚宽适配器地基——代码映射、client 抽象、反污染参数集中写死"
```

---

## Task 2: 探针脚本 —— 9 项未知量实测

**Files:**
- Create: `scripts/jq_probe.py`
- Create: `docs/reference/joinquant-data-source.md`

**Interfaces:**
- Consumes: Task 1 的 `JQDataClient`、`daily_kwargs`、`to_jq_code`、`load_deploy_config`
- Produces: `var/jq_probe_<YYYY-MM-DD>.json`，键名固定为 `quota` / `billing_unit` / `securities_columns` / `earliest_daily` / `beijing_suffix` / `finance_dividend_table` / `call_auction_rows` / `paused_fields` / `volume_unit`。Task 6 读 `billing_unit`，Task 7 读 `volume_unit`

- [ ] **Step 1: 写探针脚本**

```python
# scripts/jq_probe.py
"""聚宽未知量实测探针。

spec §7 列了 9 项没有权威依据的事情。这个脚本逐项实测，**结论落盘**，
之后所有预算与单位换算都引用它的输出，不引用任何人的记忆。

用法：
    uv run --group jq python scripts/jq_probe.py --out var/
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from qsys.data.providers.joinquant import JQDataClient, daily_kwargs
from qsys.live.config import load_deploy_config

# 探针样本：一只沪主板、一只深创业。选活跃股，避免停牌整段导致空结果。
_SAMPLE_SH = "600036.XSHG"
_SAMPLE_SZ = "300750.XSHE"


def probe_quota(client) -> dict:
    """§7.1 额度。"""
    return {"raw": client.get_query_count(field=None)}


def probe_billing_unit(client) -> dict:
    """§7.2 计费口径：按行还是按行×字段。

    用 spare 夹一个 1 只 × 10 个交易日 × 12 字段的请求做差值。
    行数由返回的 DataFrame 实际长度给出，不假设 10。
    """
    before = int(client.get_query_count(field="spare"))
    end = date.today() - timedelta(days=1)
    raw = client.get_price(**daily_kwargs([_SAMPLE_SH], end - timedelta(days=20), end))
    rows = 0 if raw is None else len(raw)
    after = int(client.get_query_count(field="spare"))
    consumed = before - after
    return {
        "spare_before": before,
        "spare_after": after,
        "consumed": consumed,
        "rows_returned": rows,
        "fields_requested": 12,
        "per_row": None if rows == 0 else consumed / rows,
        # per_row ≈ 1 则按行计；≈ 12 则按行×字段计
        "verdict": None if rows == 0 else ("per_row" if consumed <= rows * 1.5 else "per_cell"),
    }


def probe_securities(client) -> dict:
    """§7.3 标的主表列名 + 含退市股总数；§7.5 北交所后缀。"""
    listed_all = client.get_all_securities(types=["stock"], date=None)
    listed_today = client.get_all_securities(types=["stock"], date=date.today().isoformat())
    codes = list(listed_all.index)
    suffixes = sorted({c.split(".")[-1] for c in codes})
    beijing = sorted(c for c in codes if c[:2] in ("43", "83", "87", "92"))
    return {
        "columns": list(listed_all.columns),
        "index_name": listed_all.index.name,
        "count_all_dates": len(listed_all),
        "count_listed_today": len(listed_today),
        "delisted_count": len(listed_all) - len(listed_today),
        "suffixes_seen": suffixes,
        "beijing_examples": beijing[:5],
        "beijing_suffix": sorted({c.split(".")[-1] for c in beijing}),
        "first_rows": listed_all.head(3).to_dict(orient="index"),
    }


def probe_earliest_daily(client) -> dict:
    """§7.4 日线最早可得日期。用 1990 年起点探一只老股。"""
    raw = client.get_price(**daily_kwargs([_SAMPLE_SH], date(1990, 1, 1), date(2000, 1, 1)))
    if raw is None or len(raw) == 0:
        return {"earliest": None, "note": "1990-2000 区间无数据"}
    return {"earliest": str(raw["time"].min()), "rows": len(raw)}


def probe_paused_fields(client) -> dict:
    """§7.8 `paused=True` 时各字段的实际返回值。

    在 fill_paused=False 下，价格是否全 NaN、high_limit/low_limit 是否仍有值。
    这一项决定「不准填」如何落地。
    """
    end = date.today() - timedelta(days=1)
    raw = client.get_price(**daily_kwargs([_SAMPLE_SH, _SAMPLE_SZ], end - timedelta(days=400), end))
    if raw is None or len(raw) == 0:
        return {"note": "无数据"}
    paused = raw[raw["paused"] > 0.5]
    return {
        "paused_rows": len(paused),
        "sample": paused.head(3).to_dict(orient="records") if len(paused) else [],
        "null_counts": {c: int(paused[c].isna().sum()) for c in paused.columns} if len(paused) else {},
        "dtypes": {c: str(t) for c, t in raw.dtypes.items()},
    }


def probe_volume_unit(client) -> dict:
    """§7.9 volume 单位：股还是手。

    判据不看文档也不猜：对同一批 bar 分别按两种解释算 amount/volume，
    看哪一种落在 [low, high] 区间内。现有 mootdx 数据上这个方法给出过
    99.88% 对 0% 的明确结论（sim_broker.py:28），可直接复用。
    """
    end = date.today() - timedelta(days=1)
    raw = client.get_price(**daily_kwargs([_SAMPLE_SH, _SAMPLE_SZ], end - timedelta(days=400), end))
    usable = raw[(raw["volume"] > 0) & raw["low"].notna() & raw["high"].notna()]
    total = len(usable)
    if total == 0:
        return {"verdict": None, "note": "无可用样本"}
    as_share = usable["money"] / usable["volume"]
    as_lot = usable["money"] / (usable["volume"] * 100)
    hit_share = int(((as_share >= usable["low"]) & (as_share <= usable["high"])).sum())
    hit_lot = int(((as_lot >= usable["low"]) & (as_lot <= usable["high"])).sum())
    return {
        "samples": total,
        "hit_as_share": hit_share,
        "hit_rate_as_share": hit_share / total,
        "hit_as_lot": hit_lot,
        "hit_rate_as_lot": hit_lot / total,
        "verdict": "share" if hit_share > hit_lot else "lot",
    }


def probe_call_auction(client) -> dict:
    """§7.7 集合竞价单股-日行数 + 最早可得日期。"""
    import jqdatasdk

    end = date.today() - timedelta(days=5)
    recent = jqdatasdk.get_call_auction(
        security=[_SAMPLE_SH], start_date=end.isoformat(), end_date=end.isoformat()
    )
    out = {
        "columns": [] if recent is None else list(recent.columns),
        "rows_one_stock_one_day": 0 if recent is None else len(recent),
    }
    for year in (2016, 2018, 2020, 2022):
        probe_day = date(year, 6, 15)
        got = jqdatasdk.get_call_auction(
            security=[_SAMPLE_SH],
            start_date=probe_day.isoformat(),
            end_date=(probe_day + timedelta(days=5)).isoformat(),
        )
        out[f"rows_{year}"] = 0 if got is None else len(got)
    return out


def probe_finance_dividend(client) -> dict:
    """§7.6 财经库里分红送配表的表名与列名。

    财经表由服务端 get_table_orm 动态下发，不假设表名——先列出所有
    包含 XR / XDR / DIVIDEND 字样的表，再打印其列。
    """
    import jqdatasdk
    from jqdatasdk import finance

    names = [n for n in dir(finance) if not n.startswith("_")]
    candidates = [n for n in names if any(k in n.upper() for k in ("XR", "XDR", "DIVID"))]
    detail = {}
    for name in candidates:
        try:
            detail[name] = jqdatasdk.get_table_info(getattr(finance, name))
        except Exception as exc:  # 单表失败不影响其余
            detail[name] = f"ERROR: {exc}"
    return {"all_table_names": names, "candidates": candidates, "detail": detail}


PROBES = {
    "quota": probe_quota,
    "billing_unit": probe_billing_unit,
    "securities_columns": probe_securities,
    "earliest_daily": probe_earliest_daily,
    "paused_fields": probe_paused_fields,
    "volume_unit": probe_volume_unit,
    "call_auction_rows": probe_call_auction,
    "finance_dividend_table": probe_finance_dividend,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="var")
    args = parser.parse_args()

    cfg = load_deploy_config().joinquant
    if not cfg.username:
        raise SystemExit("config/deploy.local.toml 缺少 [joinquant] username/password")
    client = JQDataClient(cfg.username, cfg.password)

    results: dict[str, object] = {"probed_at": date.today().isoformat()}
    for name, fn in PROBES.items():
        try:
            results[name] = fn(client)
        except Exception as exc:  # 单项失败不中断，失败本身也是结论
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"[{name}] {json.dumps(results[name], ensure_ascii=False, default=str)[:400]}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"jq_probe_{date.today().isoformat()}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print(f"\n写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 在节点上跑探针**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  'cd ~/qsys && uv run --group jq python scripts/jq_probe.py --out var/'
```

Expected: 8 项全部有输出（`volume_unit.verdict` 为 `"share"` 或 `"lot"`，`billing_unit.verdict` 为 `"per_row"` 或 `"per_cell"`）。任一项 `error` 都要先解决再往下走。

- [ ] **Step 3: 把结论写进参考文档**

新建 `docs/reference/joinquant-data-source.md`，逐项抄录探针输出的**实测数字**（不是转述），至少覆盖：额度 total/spare、计费口径判定、标的主表列名与退市股数量、日线最早可得日、北交所后缀、`paused=True` 时哪些列为 null、`volume` 单位判定（含两种解释的命中率）、竞价单股-日行数与最早可得年份、分红送配表名与列名。

- [ ] **Step 4: 提交**

```bash
git add scripts/jq_probe.py docs/reference/joinquant-data-source.md
git commit -m "feat(data): 聚宽探针脚本 + 9 项未知量实测结论落档"
```

---

## Task 3: 两张真值表的 schema 与读取方法

**Files:**
- Modify: `src/qsys/data/schema.py`
- Modify: `src/qsys/data/query.py`
- Test: `tests/data/test_query.py`

**Interfaces:**
- Produces: `schema.DAILY_REFERENCE`、`schema.ST_DAILY`（均注册进 `schema.PARTITIONED`）；`WarehouseQuery.daily_reference(symbols, start, end)`、`WarehouseQuery.st_daily(symbols, start, end)`，返回列顺序与 schema 一致

- [ ] **Step 1: 写失败测试**

```python
# tests/data/test_query.py 追加
from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.query import WarehouseQuery


def test_daily_reference_roundtrip(tmp_path):
    from qsys.data.storage import Warehouse

    wh = Warehouse(tmp_path)
    frame = pl.DataFrame(
        {
            "symbol": ["000158.SZ", "000158.SZ"],
            "dt": [date(2026, 6, 9), date(2026, 6, 10)],
            "pre_close": [13.913, 15.30],
            "high_limit": [15.30, 16.83],
            "low_limit": [12.52, 13.77],
            "paused": [False, False],
            "factor": [1.0, 1.0],
            "avg": [14.8, 16.1],
        }
    )
    wh.write("daily_reference", frame.to_arrow().cast(schema.DAILY_REFERENCE), year=2026)

    got = WarehouseQuery(wh).daily_reference(None, date(2026, 6, 1), date(2026, 6, 30))
    assert got.height == 2
    assert got.columns == list(schema.DAILY_REFERENCE.names)
    assert got["high_limit"].to_list() == [15.30, 16.83]


def test_st_daily_roundtrip(tmp_path):
    from qsys.data.storage import Warehouse

    wh = Warehouse(tmp_path)
    frame = pl.DataFrame(
        {
            "symbol": ["600421.SH", "600036.SH"],
            "dt": [date(2026, 6, 9), date(2026, 6, 9)],
            "is_st": [True, False],
        }
    )
    wh.write("st_daily", frame.to_arrow().cast(schema.ST_DAILY), year=2026)

    got = WarehouseQuery(wh).st_daily(["600421.SH"], date(2026, 6, 9), date(2026, 6, 9))
    assert got.height == 1
    assert got["is_st"].to_list() == [True]


def test_daily_reference_empty_when_absent(tmp_path):
    from qsys.data.storage import Warehouse

    got = WarehouseQuery(Warehouse(tmp_path)).daily_reference(
        None, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert got.height == 0
    assert got.columns == list(schema.DAILY_REFERENCE.names)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_query.py -k "daily_reference or st_daily" -v`
Expected: FAIL — `KeyError: 'unknown dataset: daily_reference'`

- [ ] **Step 3: 加 schema 与读取方法**

```python
# src/qsys/data/schema.py —— 在 DAILY_FLAGS 之前插入
# 聚宽直取的真值字段。这些都**不可派生**：涨跌停价、停牌、复权因子、
# 前收，免费源都给不出真值，故单独一张事实表。
DAILY_REFERENCE = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("pre_close", pa.float64()),   # 直取 get_price 的 pre_close
        ("high_limit", pa.float64()),  # 直取：涨停价真值，不再自己按参考价算
        ("low_limit", pa.float64()),
        ("paused", pa.bool_()),        # 直取：停牌真值，不再靠四价等于前收去猜
        ("factor", pa.float64()),      # 直取：聚宽后复权因子
        ("avg", pa.float64()),         # 直取：当日均价
    ]
)

# ST 真值。get_extras('is_st') 是独立通道，与 get_price 分开采集，
# 故分表——两条通道的进度必须能各自断点续传。
ST_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("is_st", pa.bool_()),
    ]
)
```

`PARTITIONED` 字典增加两行：

```python
    "daily_reference": DAILY_REFERENCE,
    "st_daily": ST_DAILY,
```

```python
# src/qsys/data/query.py —— 在 daily_flags 之前插入
    def daily_reference(
        self,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
    ) -> pl.DataFrame:
        """聚宽真值字段。列顺序与 schema.DAILY_REFERENCE 一致。"""
        return self._read_daily_keyed(
            "daily_reference", schema.DAILY_REFERENCE, symbols, start, end
        )

    def st_daily(
        self,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
    ) -> pl.DataFrame:
        return self._read_daily_keyed("st_daily", schema.ST_DAILY, symbols, start, end)

    def _read_daily_keyed(
        self,
        dataset: str,
        arrow_schema: pa.Schema,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
    ) -> pl.DataFrame:
        """(symbol, dt) 主键的分区表通用读法。"""
        if symbols is not None and len(symbols) == 0:
            return _empty(arrow_schema)
        glob = self._glob(dataset, partitioned=True)
        if glob is None:
            return _empty(arrow_schema)
        columns = _projection(self._con, glob, arrow_schema)
        sql = f"SELECT {columns} FROM {_read(glob)} WHERE dt BETWEEN ? AND ?"
        params: list[object] = [start, end]
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        return self._con.execute(sql + " ORDER BY symbol, dt", params).pl()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/test_query.py -v`
Expected: PASS（新增 3 条）

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/query.py tests/data/test_query.py
git commit -m "feat(data): 新增 daily_reference 与 st_daily 两张真值事实表"
```

---

## Task 4: 标的主表重建（含退市股）

**Files:**
- Modify: `src/qsys/data/providers/joinquant.py`
- Test: `tests/data/providers/test_joinquant.py`

**Interfaces:**
- Consumes: Task 1 的 `JQClient`；`qsys.data.providers.base.normalize_symbol`
- Produces: `JoinquantProvider(client)`，`name = "joinquant"`；`fetch_instruments() -> pl.DataFrame`，列为 `symbol, name, market, list_date, delist_date, board`（与 `schema.INSTRUMENTS` 一致）

- [ ] **Step 1: 写失败测试**

```python
# tests/data/providers/test_joinquant.py 追加
from datetime import date

import pandas as pd

from qsys.data.providers.joinquant import JoinquantProvider


class FakeClient:
    """记录调用参数的假 client。测试绝不联网。"""

    def __init__(self, securities=None, price=None, extras=None, spare=10**9):
        self.calls: list[tuple[str, dict]] = []
        self._securities = securities
        self._price = price
        self._extras = extras
        self._spare = spare

    def get_all_securities(self, **kw):
        self.calls.append(("get_all_securities", kw))
        return self._securities

    def get_price(self, **kw):
        self.calls.append(("get_price", kw))
        return self._price

    def get_extras(self, **kw):
        self.calls.append(("get_extras", kw))
        return self._extras

    def get_query_count(self, **kw):
        self.calls.append(("get_query_count", kw))
        return self._spare

    def get_trade_days(self, **kw):
        self.calls.append(("get_trade_days", kw))
        return []


def _securities_frame():
    # 列名以探针实测为准：display_name / name / start_date / end_date / type
    return pd.DataFrame(
        {
            "display_name": ["招商银行", "退市太和", "宁德时代"],
            "name": ["ZSYH", "TSTH", "NDSD"],
            "start_date": [date(2002, 4, 9), date(1996, 10, 21), date(2018, 6, 11)],
            # 未退市的 end_date 聚宽填 2200-01-01 这类远期哨兵值
            "end_date": [date(2200, 1, 1), date(2026, 7, 1), date(2200, 1, 1)],
            "type": ["stock", "stock", "stock"],
        },
        index=["600036.XSHG", "605081.XSHG", "300750.XSHE"],
    )


def test_fetch_instruments_includes_delisted():
    client = FakeClient(securities=_securities_frame())
    got = JoinquantProvider(client).fetch_instruments()

    assert got.columns == ["symbol", "name", "market", "list_date", "delist_date", "board"]
    assert got["symbol"].to_list() == ["600036.SH", "605081.SH", "300750.SZ"]
    # 远期哨兵值必须变成 null，不能当成真实退市日
    assert got["delist_date"].to_list() == [None, date(2026, 7, 1), None]
    assert got["board"].to_list() == ["main", "main", "gem"]
    # date=None 才含退市股（api.py:397 的 docstring）
    assert client.calls[0][1]["date"] is None


def test_fetch_instruments_asks_for_all_dates():
    client = FakeClient(securities=_securities_frame())
    JoinquantProvider(client).fetch_instruments()
    name, kwargs = client.calls[0]
    assert name == "get_all_securities"
    assert kwargs["types"] == ["stock"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/providers/test_joinquant.py -k instruments -v`
Expected: FAIL — `ImportError: cannot import name 'JoinquantProvider'`

- [ ] **Step 3: 实现**

```python
# src/qsys/data/providers/joinquant.py 追加
import polars as pl

from qsys.data.providers.base import normalize_symbol

# 板块由号段判定，与 base.normalize_symbol 同源。
_BOARD_PREFIXES = (
    ("688", "star"), ("689", "star"),
    ("300", "gem"), ("301", "gem"), ("302", "gem"),
    ("430", "bse"), ("83", "bse"), ("87", "bse"), ("92", "bse"),
)

# 聚宽对未退市标的填远期哨兵日期，不是真实退市日。超过此阈值视为未退市。
_NOT_DELISTED_AFTER = date(2100, 1, 1)

INSTRUMENT_COLUMNS = ["symbol", "name", "market", "list_date", "delist_date", "board"]


def _board_of(code: str) -> str:
    for prefix, board in _BOARD_PREFIXES:
        if code.startswith(prefix):
            return board
    return "main"


class JoinquantProvider:
    """聚宽数据源。批量取数——`get_price` 接受标的列表，一批一次请求。"""

    name = "joinquant"

    def __init__(self, client: JQClient) -> None:
        self._client = client

    def fetch_instruments(self) -> pl.DataFrame:
        """全部标的，**含退市股**。

        `date=None` 是关键：api.py:397 的 docstring 写「默认值为 None,
        表示获取所有日期的股票信息」。传当天只会得到当天在册的，
        退市股缺失正是广度因子平均少 14.6 只的根因。
        """
        raw = self._client.get_all_securities(types=["stock"], date=None)
        if raw is None or len(raw) == 0:
            return pl.from_arrow(schema.INSTRUMENTS.empty_table())

        rows = []
        for jq_code, row in raw.iterrows():
            symbol = normalize_symbol(str(jq_code))
            code = symbol.split(".")[0]
            end = row["end_date"]
            end_date = None if end is None or end >= _NOT_DELISTED_AFTER else end
            rows.append(
                {
                    "symbol": symbol,
                    "name": str(row["display_name"]),
                    "market": symbol.split(".")[1],
                    "list_date": row["start_date"],
                    "delist_date": end_date,
                    "board": _board_of(code),
                }
            )
        return pl.DataFrame(rows, schema={
            "symbol": pl.Utf8, "name": pl.Utf8, "market": pl.Utf8,
            "list_date": pl.Date, "delist_date": pl.Date, "board": pl.Utf8,
        }).select(INSTRUMENT_COLUMNS)
```

同时在文件顶部 import 里补 `from qsys.data import schema`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/providers/test_joinquant.py -v`
Expected: PASS（新增 2 条）

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/providers/joinquant.py tests/data/providers/test_joinquant.py
git commit -m "feat(data): 聚宽标的主表（含退市股）"
```

---

## Task 5: 日线面板取数 —— NaN→null、停牌不填、拆两张表

**Files:**
- Modify: `src/qsys/data/providers/joinquant.py`
- Test: `tests/data/providers/test_joinquant.py`

**Interfaces:**
- Consumes: Task 1 的 `daily_kwargs`、`DAILY_FIELDS`；Task 4 的 `JoinquantProvider`
- Produces: `JoinquantProvider.fetch_daily_panel(symbols, start, end) -> pl.DataFrame`（列：`symbol, dt, open, high, low, close, volume, amount, pre_close, high_limit, low_limit, paused, factor, avg`）；`split_panel(panel) -> tuple[bars, reference]`；常量 `PANEL_COLUMNS`、`REFERENCE_COLUMNS`

- [ ] **Step 0: 用探针实测结果校对假数据的形状**

```bash
python3 -c "import json,glob; d=json.load(open(sorted(glob.glob('var/jq_probe_*.json'))[-1])); \
print(json.dumps(d['paused_fields'].get('dtypes'), indent=2, ensure_ascii=False)); \
print(json.dumps(d['paused_fields'].get('sample'), indent=2, ensure_ascii=False, default=str))"
```

下面测试里的假 DataFrame 形状（`code` / `time` 两个额外列、`paused` 是 0.0/1.0 浮点、
停牌日价格为 NaN）来自 `api.py:27` 的 docstring **推断**，不是实测。若探针输出的列名
或 dtype 与之不同（例如 `time` 是索引而非列），**以探针为准改假数据，再改实现**。
假数据形状错了，测试全绿也没有意义。

- [ ] **Step 1: 写失败测试**

```python
# tests/data/providers/test_joinquant.py 追加
import math

from qsys.data.providers.joinquant import split_panel


def _price_frame():
    """panel=False 的长表：多出 code 与 time 两列（api.py:27 docstring）。

    第二行是停牌日：fill_paused=False 下价格全 NaN，volume 为 0。
    """
    return pd.DataFrame(
        {
            "code": ["000158.XSHE", "000158.XSHE"],
            "time": [pd.Timestamp("2026-06-09"), pd.Timestamp("2026-06-10")],
            "open": [13.95, math.nan],
            "high": [15.30, math.nan],
            "low": [13.80, math.nan],
            "close": [15.30, math.nan],
            "volume": [12_345_600.0, 0.0],
            "money": [186_000_000.0, 0.0],
            "pre_close": [13.913, 15.30],
            "high_limit": [15.30, 16.83],
            "low_limit": [12.52, 13.77],
            "paused": [0.0, 1.0],
            "factor": [1.0, 1.0],
            "avg": [15.07, math.nan],
        }
    )


def test_fetch_daily_panel_writes_dead_parameters():
    """三个反污染参数必须显式传，不能依赖默认值。这是 spec §2.1 的守护测试。"""
    client = FakeClient(price=_price_frame())
    JoinquantProvider(client).fetch_daily_panel(
        ["000158.SZ"], date(2026, 6, 1), date(2026, 6, 30)
    )
    name, kwargs = client.calls[0]
    assert name == "get_price"
    assert kwargs["fq"] is None              # 原始价，不前复权
    assert kwargs["fill_paused"] is False    # 停牌日不用前值填充
    assert kwargs["skip_paused"] is False    # 保住停牌行
    assert kwargs["panel"] is False
    assert kwargs["security"] == ["000158.XSHE"]
    assert kwargs["fields"] == [
        "open", "high", "low", "close", "volume", "money",
        "pre_close", "high_limit", "low_limit", "paused", "factor", "avg",
    ]


def test_fetch_daily_panel_converts_nan_to_null():
    """NaN 是真实浮点值，会污染比较运算。落盘前必须变 null。"""
    client = FakeClient(price=_price_frame())
    got = JoinquantProvider(client).fetch_daily_panel(
        ["000158.SZ"], date(2026, 6, 1), date(2026, 6, 30)
    )
    paused_row = got.filter(pl.col("dt") == date(2026, 6, 10))
    assert paused_row["close"].to_list() == [None]
    assert paused_row["open"].to_list() == [None]
    assert paused_row["avg"].to_list() == [None]
    # 停牌日的涨跌停价仍是真值，不是 null —— 不准擅自抹掉
    assert paused_row["high_limit"].to_list() == [16.83]
    assert got["paused"].to_list() == [False, True]
    assert got["symbol"].to_list() == ["000158.SZ", "000158.SZ"]


def test_fetch_daily_panel_volume_is_integer():
    client = FakeClient(price=_price_frame())
    got = JoinquantProvider(client).fetch_daily_panel(
        ["000158.SZ"], date(2026, 6, 1), date(2026, 6, 30)
    )
    assert got["volume"].dtype == pl.Int64
    assert got["volume"].to_list() == [12_345_600, 0]


def test_split_panel_matches_both_schemas():
    client = FakeClient(price=_price_frame())
    panel = JoinquantProvider(client).fetch_daily_panel(
        ["000158.SZ"], date(2026, 6, 1), date(2026, 6, 30)
    )
    bars, ref = split_panel(panel)
    assert bars.columns == list(schema.BARS_DAILY.names)
    assert ref.columns == list(schema.DAILY_REFERENCE.names)
    # 两张表行数必须一致：质检有一条规则卡这个
    assert bars.height == ref.height == 2
    # cast 不报错即证明 dtype 对得上
    bars.to_arrow().cast(schema.BARS_DAILY)
    ref.to_arrow().cast(schema.DAILY_REFERENCE)


def test_fetch_daily_panel_empty_result():
    got = JoinquantProvider(FakeClient(price=pd.DataFrame())).fetch_daily_panel(
        ["000158.SZ"], date(2026, 6, 1), date(2026, 6, 30)
    )
    assert got.height == 0
    assert got.columns == [
        "symbol", "dt", "open", "high", "low", "close", "volume", "amount",
        "pre_close", "high_limit", "low_limit", "paused", "factor", "avg",
    ]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/providers/test_joinquant.py -k "panel or split" -v`
Expected: FAIL — `AttributeError: 'JoinquantProvider' object has no attribute 'fetch_daily_panel'`

- [ ] **Step 3: 实现**

```python
# src/qsys/data/providers/joinquant.py 追加

PANEL_COLUMNS = [
    "symbol", "dt", "open", "high", "low", "close", "volume", "amount",
    "pre_close", "high_limit", "low_limit", "paused", "factor", "avg",
]
REFERENCE_COLUMNS = list(schema.DAILY_REFERENCE.names)
_BARS_COLUMNS = list(schema.BARS_DAILY.names)
# 需要 NaN→null 的浮点列。volume 单独处理（要转 Int64）。
_FLOAT_COLUMNS = [
    "open", "high", "low", "close", "amount",
    "pre_close", "high_limit", "low_limit", "factor", "avg",
]

_EMPTY_PANEL_SCHEMA = {
    "symbol": pl.Utf8, "dt": pl.Date,
    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Int64, "amount": pl.Float64,
    "pre_close": pl.Float64, "high_limit": pl.Float64, "low_limit": pl.Float64,
    "paused": pl.Boolean, "factor": pl.Float64, "avg": pl.Float64,
}


def split_panel(panel: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """一次取数拆成两张事实表。行数必然相同。"""
    return panel.select(_BARS_COLUMNS), panel.select(REFERENCE_COLUMNS)


# 以下方法加进 JoinquantProvider
    def fetch_daily_panel(
        self, symbols: Sequence[str], start: date, end: date
    ) -> pl.DataFrame:
        """一次请求取一批标的的日线 + 真值字段。

        **停牌日不填充**：`fill_paused=False` 下价格返回 NaN，这里转成
        null 落盘。停牌日的 `high_limit` / `low_limit` 仍是真值，保留。
        `money` 重命名为 `amount` 以对齐仓库既有列名。
        """
        jq_codes = [to_jq_code(s) for s in symbols]
        raw = self._client.get_price(**daily_kwargs(jq_codes, start, end))
        if raw is None or len(raw) == 0:
            return pl.DataFrame(schema=_EMPTY_PANEL_SCHEMA).select(PANEL_COLUMNS)

        frame = pl.from_pandas(raw)
        code_map = dict(zip(jq_codes, symbols))
        return (
            frame.rename({"money": "amount"})
            .with_columns(
                pl.col("code").replace_strict(code_map, return_dtype=pl.Utf8).alias("symbol"),
                pl.col("time").cast(pl.Date).alias("dt"),
                # NaN 是真实浮点值，会让 close >= high_limit 这类比较静默出错
                *[pl.col(c).cast(pl.Float64).fill_nan(None) for c in _FLOAT_COLUMNS],
                pl.col("volume").cast(pl.Float64).fill_nan(None).round(0).cast(pl.Int64),
                # 聚宽 paused 是 0.0 / 1.0 的浮点。null 保持 null——不知道就是不知道
                (pl.col("paused") > 0.5).alias("paused"),
            )
            .select(PANEL_COLUMNS)
            .sort(["symbol", "dt"])
        )
```

文件顶部 import 补 `from collections.abc import Sequence`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/providers/test_joinquant.py -v`
Expected: PASS（新增 5 条）

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/providers/joinquant.py tests/data/providers/test_joinquant.py
git commit -m "feat(data): 聚宽日线面板取数——停牌不填、NaN 转 null、拆两张真值表"
```

---

## Task 6: 分批采集流水线（续传 + 额度闸门）

**Files:**
- Create: `src/qsys/data/pipeline/jq_daily.py`
- Create: `tests/data/pipeline/test_jq_daily.py`
- Modify: `src/qsys/data/pipeline/ingest_log.py`
- Create: `scripts/ingest_joinquant.py`

**Interfaces:**
- Consumes: Task 5 的 `fetch_daily_panel` / `split_panel`；`IngestLog`；`WarehouseQuery.daily_bars` / `daily_reference`
- Produces: `ingest_jq_daily(provider, warehouse, symbols, start, end, *, complete_through, batch_size=1000, cost_per_row=1.0, min_spare=0) -> IngestProgress`（复用 `qsys.data.pipeline.universe.IngestProgress`）；`IngestLog.record_many(rows)`

- [ ] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_jq_daily.py
from datetime import date

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.pipeline.jq_daily import ingest_jq_daily
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


class StubProvider:
    """按批返回固定面板的假 provider。记录每批拿到的标的。"""

    name = "joinquant"

    def __init__(self, spare=10**9, fail_on=()):
        self.batches: list[list[str]] = []
        self.spare = spare
        self._fail_on = set(fail_on)

    def spare_quota(self) -> int:
        return self.spare

    def fetch_daily_panel(self, symbols, start, end):
        self.batches.append(list(symbols))
        if self._fail_on & set(symbols):
            raise RuntimeError("模拟接口报错")
        rows = []
        for s in symbols:
            rows.append(
                {
                    "symbol": s, "dt": date(2026, 6, 9),
                    "open": 10.0, "high": 11.0, "low": 9.5, "close": 11.0,
                    "volume": 1000, "amount": 10500.0,
                    "pre_close": 10.0, "high_limit": 11.0, "low_limit": 9.0,
                    "paused": False, "factor": 1.0, "avg": 10.5,
                }
            )
        return pl.DataFrame(rows)


def test_ingest_writes_both_tables(tmp_path):
    wh = Warehouse(tmp_path)
    provider = StubProvider()
    progress = ingest_jq_daily(
        provider, wh, ["600036.SH", "000158.SZ"],
        date(2026, 6, 1), date(2026, 6, 30),
        complete_through=date(2026, 6, 30), batch_size=2,
    )
    assert progress.done == 2

    query = WarehouseQuery(wh)
    bars = query.daily_bars(None, date(2026, 6, 1), date(2026, 6, 30))
    ref = query.daily_reference(None, date(2026, 6, 1), date(2026, 6, 30))
    assert bars.height == ref.height == 2
    assert bars.columns == list(schema.BARS_DAILY.names)
    assert ref.columns == list(schema.DAILY_REFERENCE.names)


def test_ingest_batches_by_batch_size(tmp_path):
    provider = StubProvider()
    ingest_jq_daily(
        provider, Warehouse(tmp_path), ["a.SH", "b.SH", "c.SH"],
        date(2026, 6, 1), date(2026, 6, 30),
        complete_through=date(2026, 6, 30), batch_size=2,
    )
    assert [len(b) for b in provider.batches] == [2, 1]


def test_ingest_skips_completed_symbols_on_rerun(tmp_path):
    wh = Warehouse(tmp_path)
    args = dict(start=date(2026, 6, 1), end=date(2026, 6, 30),
                complete_through=date(2026, 6, 30), batch_size=10)
    first = StubProvider()
    ingest_jq_daily(first, wh, ["600036.SH", "000158.SZ"], **args)

    second = StubProvider()
    progress = ingest_jq_daily(second, wh, ["600036.SH", "000158.SZ", "300750.SZ"], **args)
    # 只补没采过的那只
    assert second.batches == [["300750.SZ"]]
    assert progress.done == 3


def test_ingest_stops_cleanly_when_quota_low(tmp_path):
    provider = StubProvider(spare=5)
    progress = ingest_jq_daily(
        provider, Warehouse(tmp_path), ["a.SH", "b.SH"],
        date(2026, 6, 1), date(2026, 6, 30),
        complete_through=date(2026, 6, 30), batch_size=1,
        cost_per_row=1.0, min_spare=1_000_000,
    )
    # 额度不足时一批都不采，干净退出
    assert provider.batches == []
    assert progress.done == 0


def test_ingest_drops_bars_after_complete_through(tmp_path):
    """盘中采到的「今日」只有半天成交量，必须按 complete_through 丢掉。"""
    wh = Warehouse(tmp_path)
    ingest_jq_daily(
        StubProvider(), wh, ["600036.SH"],
        date(2026, 6, 1), date(2026, 6, 30),
        complete_through=date(2026, 6, 8), batch_size=1,
    )
    bars = WarehouseQuery(wh).daily_bars(None, date(2026, 6, 1), date(2026, 6, 30))
    assert bars.height == 0


def test_failed_batch_is_not_marked_done(tmp_path):
    wh = Warehouse(tmp_path)
    provider = StubProvider(fail_on=["bad.SH"])
    progress = ingest_jq_daily(
        provider, wh, ["bad.SH"], date(2026, 6, 1), date(2026, 6, 30),
        complete_through=date(2026, 6, 30), batch_size=1,
    )
    assert progress.failed == 1
    # 重跑必须重试，不能被当成已完成
    again = StubProvider()
    ingest_jq_daily(
        again, wh, ["bad.SH"], date(2026, 6, 1), date(2026, 6, 30),
        complete_through=date(2026, 6, 30), batch_size=1,
    )
    assert again.batches == [["bad.SH"]]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/pipeline/test_jq_daily.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qsys.data.pipeline.jq_daily'`

- [ ] **Step 3: 给 IngestLog 加批量写**

```python
# src/qsys/data/pipeline/ingest_log.py 追加
    def record_many(
        self,
        dataset: str,
        dt: date,
        providers: Sequence[str],
        row_count: int = 0,
        ok: bool = True,
        message: str = "",
    ) -> None:
        """一批标的一次写盘。

        `record()` 每次都读-改-写整个年分区。一批 1000 只标的调 1000 次
        就是 1000 次分区重写——采集会被记账拖死。
        """
        if not providers:
            return
        year = dt.year
        now = datetime.now()
        new = pl.DataFrame(
            {
                "dataset": [dataset] * len(providers),
                "dt": [dt] * len(providers),
                "provider": list(providers),
                "ingested_at": [now] * len(providers),
                "row_count": [row_count] * len(providers),
                "ok": [ok] * len(providers),
                "message": [message] * len(providers),
            },
            schema=_LOG_SCHEMA,
        )
        merged = (
            pl.concat([self.entries(year), new], how="vertical")
            .unique(subset=_KEY, keep="last", maintain_order=True)
            .sort(_KEY)
        )
        self._wh.write("ingest_log", merged.to_arrow().cast(schema.INGEST_LOG), year=year)
```

顶部 import 补 `from collections.abc import Sequence`。

- [ ] **Step 4: 实现流水线**

```python
# src/qsys/data/pipeline/jq_daily.py
"""聚宽日线 + 真值字段的分批采集。

与 `pipeline/universe.py` 的区别：聚宽的 `get_price` 接受标的**列表**，
一批一次请求，不是一只一次。这既省额度也省时间，但也意味着一批里有一只
出问题，整批都要重试——所以批次要小（默认 1000），失败批不记账。

续传沿用 `universe.py` 立下的约定：ingest_log 的 (dataset, dt, provider)
三元组被复用为 (区间键, 区间末日, 标的)。

额度闸门：每批前查剩余条数，不够就**干净退出**，不做部分写入。试用额度
按天重置，退出比硬撑更划算。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.pipeline.ingest_log import IngestLog
from qsys.data.pipeline.universe import IngestProgress
from qsys.data.providers.joinquant import split_panel
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_DATASET = "jq_daily"


def _log_key(start: date, end: date) -> str:
    return f"{_DATASET}:{start.isoformat()}:{end.isoformat()}"


def completed_symbols(warehouse: Warehouse, start: date, end: date) -> set[str]:
    key = _log_key(start, end)
    ingest_log = IngestLog(warehouse)
    done: set[str] = set()
    for year in range(start.year, end.year + 1):
        entries = ingest_log.entries(year)
        if entries.height == 0:
            continue
        hit = entries.filter((pl.col("dataset") == key) & pl.col("ok"))
        done.update(hit["provider"].to_list())
    return done


def ingest_jq_daily(
    provider,
    warehouse: Warehouse,
    symbols: Sequence[str],
    start: date,
    end: date,
    *,
    complete_through: date,
    batch_size: int = 1000,
    cost_per_row: float = 1.0,
    min_spare: int = 0,
    skip_done: bool = True,
) -> IngestProgress:
    """分批采集。返回最终进度。

    `complete_through`：已收盘的最后一个交易日，晚于它的 bar 一律丢弃。
    没有默认值是故意的——盘中采到的「今日」收盘价其实是采集那一刻的价格，
    落盘后涨停判定会一路错下去且不报错。

    `cost_per_row`：探针 §7.2 实测的每行条数成本。
    `min_spare`：低于此剩余额度就不再开新批次。
    """
    targets = list(symbols)
    if skip_done:
        already = completed_symbols(warehouse, start, end)
        targets = [s for s in targets if s not in already]

    total = len(symbols)
    done = len(symbols) - len(targets)
    failed = 0
    ingest_log = IngestLog(warehouse)
    key = _log_key(start, end)
    span_days = (end - start).days + 1

    for offset in range(0, len(targets), batch_size):
        batch = targets[offset : offset + batch_size]

        if min_spare > 0:
            spare = int(provider.spare_quota())
            # 交易日约占日历日的 68%，按 0.7 估上界
            estimate = len(batch) * span_days * 0.7 * cost_per_row
            if spare < max(min_spare, estimate):
                log.warning(
                    "jq_daily.quota_exhausted",
                    spare=spare, estimate=int(estimate), remaining=len(targets) - offset,
                )
                break

        try:
            panel = provider.fetch_daily_panel(batch, start, end)
        except Exception as exc:  # 整批重试，不记账
            failed += len(batch)
            log.warning("jq_daily.batch_failed", size=len(batch), error=str(exc))
            continue

        panel = panel.filter(pl.col("dt") <= complete_through)
        if panel.height:
            _write_batch(warehouse, panel)
        ingest_log.record_many(key, end, batch, panel.height, True)
        done += len(batch)
        log.info("jq_daily.progress", total=total, done=done, failed=failed, rows=panel.height)
        del panel

    return IngestProgress(total, done, failed)


def _write_batch(warehouse: Warehouse, panel: pl.DataFrame) -> None:
    """按年合并写两张表。WarehouseQuery 在函数内构造，确保读到磁盘当前状态。"""
    bars, reference = split_panel(panel)
    query = WarehouseQuery(warehouse)
    for year in sorted({d.year for d in panel["dt"].to_list()}):
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        for dataset, fresh, arrow_schema, existing in (
            ("bars_daily", bars, schema.BARS_DAILY, query.daily_bars(None, lo, hi)),
            (
                "daily_reference", reference, schema.DAILY_REFERENCE,
                query.daily_reference(None, lo, hi),
            ),
        ):
            incoming = fresh.filter(pl.col("dt").is_between(lo, hi))
            if incoming.height == 0:
                continue
            merged = (
                pl.concat([existing, incoming], how="vertical")
                .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
                .sort(["symbol", "dt"])
                .select(list(arrow_schema.names))
            )
            warehouse.write(dataset, merged.to_arrow().cast(arrow_schema), year=year)
```

`JoinquantProvider` 追加额度查询：

```python
    def spare_quota(self) -> int:
        """当日剩余可请求条数。"""
        return int(self._client.get_query_count(field="spare"))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/data/pipeline/test_jq_daily.py -v`
Expected: PASS（6 passed）

- [ ] **Step 6: 写采集入口脚本**

```python
# scripts/ingest_joinquant.py
"""聚宽采集入口。

用法：
    uv run --group jq python scripts/ingest_joinquant.py instruments
    uv run --group jq python scripts/ingest_joinquant.py daily \
        --start 2022-09-01 --end 2026-09-10 --complete-through 2026-09-10 \
        --cost-per-row 1.0 --min-spare 200000
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import structlog

from qsys.data import schema
from qsys.data.pipeline.jq_daily import ingest_jq_daily
from qsys.data.providers.joinquant import JQDataClient, JoinquantProvider
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.live.config import load_deploy_config

log = structlog.get_logger(__name__)


def _provider() -> JoinquantProvider:
    cfg = load_deploy_config().joinquant
    if not cfg.username:
        raise SystemExit("config/deploy.local.toml 缺少 [joinquant] 段")
    return JoinquantProvider(JQDataClient(cfg.username, cfg.password))


def _iso(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["instruments", "daily"])
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--start", type=_iso)
    parser.add_argument("--end", type=_iso)
    parser.add_argument("--complete-through", type=_iso)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--cost-per-row", type=float, default=1.0)
    parser.add_argument("--min-spare", type=int, default=0)
    args = parser.parse_args()

    warehouse = Warehouse(Path(args.warehouse))
    provider = _provider()

    if args.command == "instruments":
        frame = provider.fetch_instruments()
        warehouse.write("instruments", frame.to_arrow().cast(schema.INSTRUMENTS))
        delisted = frame.filter(frame["delist_date"].is_not_null()).height
        print(f"instruments: {frame.height} 只，其中已退市 {delisted} 只")
        return 0

    for name in ("start", "end", "complete_through"):
        if getattr(args, name) is None:
            raise SystemExit(f"daily 子命令需要 --{name.replace('_', '-')}")

    symbols = WarehouseQuery(warehouse).instruments()["symbol"].to_list()
    if not symbols:
        raise SystemExit("instruments 为空，先跑 instruments 子命令")

    progress = ingest_jq_daily(
        provider, warehouse, symbols, args.start, args.end,
        complete_through=args.complete_through,
        batch_size=args.batch_size,
        cost_per_row=args.cost_per_row,
        min_spare=args.min_spare,
    )
    print(f"done={progress.done} failed={progress.failed} remaining={progress.remaining}")
    return 0 if progress.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: 提交**

```bash
git add src/qsys/data/pipeline/jq_daily.py tests/data/pipeline/test_jq_daily.py \
        src/qsys/data/pipeline/ingest_log.py src/qsys/data/providers/joinquant.py \
        scripts/ingest_joinquant.py
git commit -m "feat(data): 聚宽日线分批采集——标的级续传 + 额度闸门 + 批量记账"
```

---

## Task 7: `volume` 单位定论与消费点修正

**Files:**
- Modify: `src/qsys/backtest/sim_broker.py:28-32,117,124`
- Modify: `src/qsys/data/pipeline/valuation.py:65,113`
- Modify: `src/qsys/strategy/structural/entry.py`（docstring）
- Test: `tests/backtest/test_sim_broker.py`、`tests/data/test_valuation_derived.py`

**Interfaces:**
- Consumes: Task 2 探针输出的 `volume_unit.verdict`
- Produces: 全仓库统一的 `volume` 单位语义，写进 `docs/reference/joinquant-data-source.md`

**这是决策闸门，必须在全量采集之前完成**，否则 1,800 万行落盘后发现单位不对要全量重采。

- [ ] **Step 1: 读探针结论**

```bash
python3 -c "import json,glob; p=sorted(glob.glob('var/jq_probe_*.json'))[-1]; \
d=json.load(open(p))['volume_unit']; print(p); print(json.dumps(d, indent=2))"
```

`verdict` 为 `"share"` 走 Step 2-4；为 `"lot"` 走 Step 5。两种命中率都低于 90% 时**停下报告**，不要选一个凑——那说明 `money` 或 `volume` 的含义与假设不同。

- [ ] **Step 2（verdict == "share"）: 写失败测试**

```python
# tests/backtest/test_sim_broker.py 追加
def test_volume_cap_treats_bar_volume_as_shares():
    """聚宽日线 volume 单位是股（探针实测），上限不再乘 100。"""
    # 构造：bar.volume = 10_000 股，volume_cap = 5% → 上限 500 股
    broker = _broker(volume_cap=0.05)
    bar = _bar(volume=10_000)
    filled = _submit(broker, bar, qty=1_000)
    assert filled == 500
```

（`_broker` / `_bar` / `_submit` 用该文件里已有的夹具，保持同名同签名。）

- [ ] **Step 3（verdict == "share"）: 改三处消费点**

`src/qsys/backtest/sim_broker.py` 第 28-32 行的注释与常量：

```python
# 日线 volume 与订单 qty 的单位**都是股**（聚宽 get_price，探针 §7.9 实测）。
# 历史上 mootdx 给的是手，这里曾乘 100 换算；换源后不再需要。
# 反过来的错误更贵：当年把手当股，成交量上限被收紧了 100 倍，小资金撞不到，
# 159.6 万本金对账时一天冒出 155 次 volume_capped 才暴露。
```

删掉 `LOT = 100` 及其两处引用：

```python
        cap = int(bar.volume * self._volume_cap)
```

```python
                f"volume cap {self._volume_cap:.0%} of {bar.volume} shares: "
```

`src/qsys/data/pipeline/valuation.py`：删掉第 65 行的 `_LOT = 100`，第 113 行改为

```python
            (pl.col("volume") / pl.col("circulating") * 100.0).alias(
                "turnover_ratio"
            ),
```

`src/qsys/strategy/structural/entry.py` 的 docstring 里 `amount/(volume*100) = 41.00` 改为 `amount/volume = 41.00`，并补一句「volume 单位为股（聚宽）」。

- [ ] **Step 4（verdict == "share"）: 跑测试**

Run: `uv run pytest tests/backtest tests/data/test_valuation_derived.py -v`
Expected: PASS。若有其他测试因单位假设失败，逐条改正断言中的期望值，**不要改生产代码去迁就旧断言**。

- [ ] **Step 5（verdict == "lot"）: 加一条守护测试，不改代码**

```python
# tests/data/providers/test_joinquant.py 追加
def test_joinquant_volume_unit_is_lot_per_probe():
    """探针判定聚宽日线 volume 为手，与现有仓库语义一致，消费点无需改。

    这条测试是语义锚点：若将来换源或聚宽改口径，它会先失败。
    """
    frame = _price_frame()
    implied = frame["money"][0] / (frame["volume"][0] * 100)
    assert frame["low"][0] <= implied <= frame["high"][0]
```

- [ ] **Step 6: 把结论写进参考文档并提交**

在 `docs/reference/joinquant-data-source.md` 的 `volume` 单位一节记下：样本数、两种解释的命中率、判定、以及改了哪几处消费点。

```bash
git add -A
git commit -m "fix(data): volume 单位按探针实测定论，同步修正消费点"
```

---

## Task 8: ST 真值采集

**Files:**
- Modify: `src/qsys/data/providers/joinquant.py`
- Create: `src/qsys/data/pipeline/jq_st.py`
- Create: `tests/data/pipeline/test_jq_st.py`
- Modify: `scripts/ingest_joinquant.py`

**Interfaces:**
- Consumes: Task 1 的 `JQClient`、`to_jq_code`
- Produces: `JoinquantProvider.fetch_is_st(symbols, start, end) -> pl.DataFrame`（列 `symbol, dt, is_st`）；`ingest_jq_st(provider, warehouse, symbols, start, end, *, batch_size=1000, min_spare=0) -> IngestProgress`

- [ ] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_jq_st.py
from datetime import date

import pandas as pd
import polars as pl

from qsys.data import schema
from qsys.data.pipeline.jq_st import ingest_jq_st
from qsys.data.providers.joinquant import JoinquantProvider
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


class FakeExtrasClient:
    """get_extras 返回宽表：行是日期、列是标的（api.py:70 的 docstring）。"""

    def __init__(self):
        self.calls = []

    def get_extras(self, **kw):
        self.calls.append(kw)
        return pd.DataFrame(
            {"600421.XSHG": [True, True], "600036.XSHG": [False, False]},
            index=[pd.Timestamp("2026-06-09"), pd.Timestamp("2026-06-10")],
        )

    def get_query_count(self, **kw):
        return 10**9


def test_fetch_is_st_reshapes_wide_to_long():
    provider = JoinquantProvider(FakeExtrasClient())
    got = provider.fetch_is_st(
        ["600421.SH", "600036.SH"], date(2026, 6, 9), date(2026, 6, 10)
    )
    assert got.columns == ["symbol", "dt", "is_st"]
    assert got.height == 4
    st_rows = got.filter(pl.col("is_st"))
    assert st_rows["symbol"].unique().to_list() == ["600421.SH"]
    assert got["dt"].dtype == pl.Date


def test_fetch_is_st_requests_the_is_st_info():
    client = FakeExtrasClient()
    JoinquantProvider(client).fetch_is_st(
        ["600421.SH"], date(2026, 6, 9), date(2026, 6, 10)
    )
    assert client.calls[0]["info"] == "is_st"
    assert client.calls[0]["security_list"] == ["600421.XSHG"]
    assert client.calls[0]["df"] is True


def test_ingest_jq_st_writes_table(tmp_path):
    wh = Warehouse(tmp_path)
    provider = JoinquantProvider(FakeExtrasClient())
    progress = ingest_jq_st(
        provider, wh, ["600421.SH", "600036.SH"],
        date(2026, 6, 9), date(2026, 6, 10), batch_size=2,
    )
    assert progress.done == 2
    got = WarehouseQuery(wh).st_daily(None, date(2026, 6, 1), date(2026, 6, 30))
    assert got.height == 4
    assert got.columns == list(schema.ST_DAILY.names)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/pipeline/test_jq_st.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qsys.data.pipeline.jq_st'`

- [ ] **Step 3: 实现 provider 方法**

```python
# src/qsys/data/providers/joinquant.py 追加进 JoinquantProvider
    def fetch_is_st(
        self, symbols: Sequence[str], start: date, end: date
    ) -> pl.DataFrame:
        """ST 真值。返回长表 (symbol, dt, is_st)。

        `get_extras` 返回宽表：行索引是日期、列是标的（api.py:70）。
        这里 unpivot 成长表以对齐仓库的 (symbol, dt) 主键。
        """
        jq_codes = [to_jq_code(s) for s in symbols]
        raw = self._client.get_extras(
            info="is_st",
            security_list=jq_codes,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            df=True,
        )
        if raw is None or len(raw) == 0:
            return pl.DataFrame(
                schema={"symbol": pl.Utf8, "dt": pl.Date, "is_st": pl.Boolean}
            )

        wide = pl.from_pandas(raw.reset_index(names="dt"))
        code_map = dict(zip(jq_codes, symbols))
        return (
            wide.unpivot(index="dt", variable_name="code", value_name="is_st")
            .with_columns(
                pl.col("code").replace_strict(code_map, return_dtype=pl.Utf8).alias("symbol"),
                pl.col("dt").cast(pl.Date),
                pl.col("is_st").cast(pl.Boolean),
            )
            .select(["symbol", "dt", "is_st"])
            .sort(["symbol", "dt"])
        )
```

- [ ] **Step 4: 实现流水线**

```python
# src/qsys/data/pipeline/jq_st.py
"""ST 真值采集。

与 jq_daily 分开是因为 get_extras 是独立通道：额度、失败、进度都要各自
记账。ST 是本次真值化的头号目标——它替掉的是三条启发式反推规则。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.pipeline.ingest_log import IngestLog
from qsys.data.pipeline.universe import IngestProgress
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_DATASET = "jq_st"


def _log_key(start: date, end: date) -> str:
    return f"{_DATASET}:{start.isoformat()}:{end.isoformat()}"


def completed_symbols(warehouse: Warehouse, start: date, end: date) -> set[str]:
    key = _log_key(start, end)
    ingest_log = IngestLog(warehouse)
    done: set[str] = set()
    for year in range(start.year, end.year + 1):
        entries = ingest_log.entries(year)
        if entries.height == 0:
            continue
        hit = entries.filter((pl.col("dataset") == key) & pl.col("ok"))
        done.update(hit["provider"].to_list())
    return done


def ingest_jq_st(
    provider,
    warehouse: Warehouse,
    symbols: Sequence[str],
    start: date,
    end: date,
    *,
    batch_size: int = 1000,
    min_spare: int = 0,
    skip_done: bool = True,
) -> IngestProgress:
    targets = list(symbols)
    if skip_done:
        already = completed_symbols(warehouse, start, end)
        targets = [s for s in targets if s not in already]

    total = len(symbols)
    done = len(symbols) - len(targets)
    failed = 0
    ingest_log = IngestLog(warehouse)
    key = _log_key(start, end)

    for offset in range(0, len(targets), batch_size):
        batch = targets[offset : offset + batch_size]
        if min_spare > 0 and int(provider.spare_quota()) < min_spare:
            log.warning("jq_st.quota_exhausted", remaining=len(targets) - offset)
            break
        try:
            frame = provider.fetch_is_st(batch, start, end)
        except Exception as exc:
            failed += len(batch)
            log.warning("jq_st.batch_failed", size=len(batch), error=str(exc))
            continue

        if frame.height:
            _write_batch(warehouse, frame)
        ingest_log.record_many(key, end, batch, frame.height, True)
        done += len(batch)
        log.info("jq_st.progress", total=total, done=done, failed=failed)
        del frame

    return IngestProgress(total, done, failed)


def _write_batch(warehouse: Warehouse, fresh: pl.DataFrame) -> None:
    query = WarehouseQuery(warehouse)
    for year in sorted({d.year for d in fresh["dt"].to_list()}):
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        incoming = fresh.filter(pl.col("dt").is_between(lo, hi))
        if incoming.height == 0:
            continue
        merged = (
            pl.concat([query.st_daily(None, lo, hi), incoming], how="vertical")
            .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
            .sort(["symbol", "dt"])
            .select(list(schema.ST_DAILY.names))
        )
        warehouse.write("st_daily", merged.to_arrow().cast(schema.ST_DAILY), year=year)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/data/pipeline/test_jq_st.py -v`
Expected: PASS（3 passed）

- [ ] **Step 6: 接进入口脚本**

`scripts/ingest_joinquant.py` 的 `command` choices 增加 `"st"`，并在 `daily` 分支之后加：

```python
    if args.command == "st":
        from qsys.data.pipeline.jq_st import ingest_jq_st

        progress = ingest_jq_st(
            provider, warehouse, symbols, args.start, args.end,
            batch_size=args.batch_size, min_spare=args.min_spare,
        )
        print(f"done={progress.done} failed={progress.failed}")
        return 0 if progress.failed == 0 else 1
```

（`symbols` 的取得与 `daily` 分支相同，把那两行提到分支之前共用。）

- [ ] **Step 7: 提交**

```bash
git add src/qsys/data/providers/joinquant.py src/qsys/data/pipeline/jq_st.py \
        tests/data/pipeline/test_jq_st.py scripts/ingest_joinquant.py
git commit -m "feat(data): ST 真值采集（get_extras is_st），替代价格反推"
```

---

## Task 9: 全量采集执行

**Files:** 无代码改动。这是一次长跑运维任务，产出物是数据与一份实测记录。

**Interfaces:**
- Consumes: Task 6 / Task 8 的采集入口；Task 2 的 `billing_unit` 与 `quota`
- Produces: 节点上的 `bars_daily` / `daily_reference` / `st_daily` 全历史数据；`docs/reference/joinquant-data-source.md` 的采集实测一节

- [ ] **Step 1: 同步代码到节点**

```bash
rsync -az --delete \
  --exclude '.venv/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
  --exclude 'warehouse/' --exclude 'var/' --exclude '*.pyc' \
  -e "ssh -i ~/.ssh/qmt_bridge_ed25519" \
  ./ ubuntu@110.42.205.144:/home/ubuntu/qsys/
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 'cd ~/qsys && uv sync --group jq && uv run pytest -q'
```

- [ ] **Step 2: 重建标的主表**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  'cd ~/qsys && uv run --group jq python scripts/ingest_joinquant.py instruments'
```

Expected: 打印总数与已退市只数。**已退市只数必须显著大于 0**——这正是原来缺的那一块；若为 0，说明 `date=None` 没生效，停下排查。

- [ ] **Step 3: 先采一年验证，再放全量**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 'cd ~/qsys && \
  uv run --group jq python scripts/ingest_joinquant.py daily \
    --start 2025-09-10 --end 2026-09-08 --complete-through 2026-09-08 \
    --batch-size 1000 --cost-per-row <探针值> --min-spare 200000'
```

采完这一年立刻做三项抽查（结果记进文档）：

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 'cd ~/qsys && uv run python - <<PY
from datetime import date
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
import polars as pl

q = WarehouseQuery(Warehouse("warehouse"))
bars = q.daily_bars(None, date(2025, 9, 10), date(2026, 9, 8))
ref  = q.daily_reference(None, date(2025, 9, 10), date(2026, 9, 8))
print("bars", bars.height, "ref", ref.height)           # 必须相等
print("停牌行", ref.filter(pl.col("paused")).height)
joined = bars.join(ref, on=["symbol", "dt"])
alive = joined.filter(~pl.col("paused") & pl.col("close").is_not_null())
bad = alive.filter((pl.col("high") > pl.col("high_limit")) | (pl.col("low") < pl.col("low_limit")))
print("越界行", bad.height)                              # 必须为 0
print("2026-06-11 涨停家数", alive.filter(
    (pl.col("dt") == date(2026, 6, 11)) & (pl.col("close") >= pl.col("high_limit"))).height)
PY'
```

三项判据：两表行数相等；越界行为 0；停牌行数量级合理（每天几十到几百）。任一项不对就停下排查，**不要继续采全历史**。

- [ ] **Step 4: 放全量（按探针给出的最早可得日起）**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 'cd ~/qsys && \
  nohup nice -n 10 uv run --group jq python scripts/ingest_joinquant.py daily \
    --start <探针最早日> --end 2026-09-10 --complete-through 2026-09-10 \
    --batch-size 1000 --cost-per-row <探针值> --min-spare 200000 \
    > var/jq_daily_ingest.log 2>&1 &'
```

额度耗尽会干净退出（日志出现 `jq_daily.quota_exhausted`）。**第二天重跑同一条命令即续传**，已采标的自动跳过。

- [ ] **Step 5: 采 ST 真值**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 'cd ~/qsys && \
  nohup nice -n 10 uv run --group jq python scripts/ingest_joinquant.py st \
    --start <探针最早日> --end 2026-09-10 --batch-size 1000 --min-spare 200000 \
    > var/jq_st_ingest.log 2>&1 &'
```

- [ ] **Step 6: 记录实测并提交**

在 `docs/reference/joinquant-data-source.md` 增「采集实测」一节：区间、行数、只数、耗时、消耗条数、磁盘占用（`du -sh warehouse/*`）、Step 3 三项判据的实际数字。

```bash
git add docs/reference/joinquant-data-source.md
git commit -m "docs(data): 聚宽全历史采集实测记录"
```

---

## Task 10: 真值一致性质检 + 空值安全

**Files:**
- Create: `src/qsys/data/quality/truth_rules.py`
- Create: `tests/data/quality/test_truth_rules.py`
- Modify: `src/qsys/data/quality/rules.py:69-93`（`check_price_limits` 空值安全）、`:95-120`（`check_internal_consistency` 空值安全）、`:18-21`（注释）
- Modify: `src/qsys/data/quality/report.py:58-82`（接线）
- Test: `tests/data/quality/test_rules.py`

**Interfaces:**
- Consumes: `daily_reference`、`st_daily`、`bars_daily`；`qsys.data.quality.rules.Violation` / `Severity`（**已存在的类型，不要另造**：`Violation(rule, severity, symbol, dt, detail)`，`Severity` 是 `StrEnum`，值为 `BLOCKING` / `WARNING`）
- Produces: `check_truth_consistency(warehouse, start, end) -> list[Violation]`，规则名 `row_count_mismatch`（BLOCKING）、`price_outside_limits`（BLOCKING）、`paused_with_volume`（WARNING）、`unexpected_limit_pct`（WARNING）、`st_coverage_gap`（WARNING）

**先修一个会直接崩的问题。** Task 5 之后停牌日的 OHLC 是 **null**（原来 mootdx 填的是四价等于前收）。现有两条规则会当场抛 `TypeError`：

- `rules.py:78` `change = abs(row["close"] / prev - 1.0)` —— `None / float`
- `rules.py:101` `min(o, h, l, c)` —— `min` 遇到 `None` 直接炸

这不是可选的健壮性改进，是空值落盘后的必然崩溃点。

- [ ] **Step 1: 写空值安全的失败测试**

```python
# tests/data/quality/test_rules.py 追加
from datetime import date

import polars as pl

from qsys.data.quality.rules import (
    Severity,
    check_internal_consistency,
    check_price_limits,
)


def _bars_with_paused_row():
    """停牌日：OHLC 全 null、volume 0。这是聚宽 fill_paused=False 的真实形状。"""
    return pl.DataFrame(
        {
            "symbol": ["600036.SH", "600036.SH"],
            "dt": [date(2026, 6, 8), date(2026, 6, 9)],
            "open": [10.0, None],
            "high": [10.5, None],
            "low": [9.8, None],
            "close": [10.2, None],
            "volume": [1000, 0],
            "amount": [10_200.0, 0.0],
        }
    )


def test_price_limits_skips_null_close():
    violations = check_price_limits(_bars_with_paused_row(), {"600036.SH": "main"})
    assert violations == []


def test_internal_consistency_skips_null_ohlc():
    violations = check_internal_consistency(_bars_with_paused_row())
    assert violations == []


def test_internal_consistency_still_catches_real_disorder():
    bad = pl.DataFrame(
        {
            "symbol": ["600036.SH"], "dt": [date(2026, 6, 9)],
            "open": [10.0], "high": [9.0], "low": [9.5], "close": [9.8],
            "volume": [100], "amount": [980.0],
        }
    )
    violations = check_internal_consistency(bad)
    assert [v.rule for v in violations] == ["ohlc_order"]
    assert violations[0].severity is Severity.BLOCKING
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/quality/test_rules.py -k null -v`
Expected: FAIL — `TypeError: unsupported operand type(s) for /: 'NoneType' and 'float'`（以及 `min` 的 TypeError）

- [ ] **Step 3: 改两条规则为空值安全**

`check_price_limits` 的循环体开头，把现有的

```python
        prev = row["prev_close"]
        if prev is None or prev <= 0:
            continue
```

改为

```python
        prev = row["prev_close"]
        # 停牌日 close 为 null（聚宽 fill_paused=False）——不知道就是不知道，跳过。
        # 绝不能在这里拿前值补，那正是 spec §2 禁止的填充。
        if prev is None or prev <= 0 or row["close"] is None:
            continue
```

`check_internal_consistency` 的循环体，在取出 `o, h, l, c` 之后插入：

```python
        if None in (o, h, l, c):
            # 停牌日或未上市：没有价格是事实，不是错误。
            # 缺失由 completeness / truth_rules 的 row_count 规则负责，不在这里报。
            continue
```

`rules.py` 顶部 `LIMIT_BY_BOARD` 上方的注释改为：

```python
# 各板块日涨跌幅上限。**这只是粗筛**——ST 的 5% 与上市首日无限制都盖不住，
# 真正的越界判定由 quality/truth_rules.check_truth_consistency 用聚宽的
# high_limit / low_limit 真值做。此处保留为不依赖真值表的兜底。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/quality/test_rules.py -v`
Expected: PASS

- [ ] **Step 5: 写真值一致性规则的失败测试**

```python
# tests/data/quality/test_truth_rules.py
from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.quality.rules import Severity
from qsys.data.quality.truth_rules import check_truth_consistency
from qsys.data.storage import Warehouse


def _bar(symbol="600036.SH", dt=date(2026, 6, 9), high=11.0, low=9.5, close=10.5, volume=1000):
    return {"symbol": symbol, "dt": dt, "open": 10.0, "high": high, "low": low,
            "close": close, "volume": volume, "amount": 10_500.0}


def _ref(symbol="600036.SH", dt=date(2026, 6, 9), pre_close=10.0, high_limit=11.0,
         low_limit=9.0, paused=False):
    return {"symbol": symbol, "dt": dt, "pre_close": pre_close, "high_limit": high_limit,
            "low_limit": low_limit, "paused": paused, "factor": 1.0, "avg": 10.5}


def _st(symbol="600036.SH", dt=date(2026, 6, 9), is_st=False):
    return {"symbol": symbol, "dt": dt, "is_st": is_st}


def _write(tmp_path, bars, refs, sts=()):
    wh = Warehouse(tmp_path)
    wh.write("bars_daily", pl.DataFrame(bars).to_arrow().cast(schema.BARS_DAILY), year=2026)
    wh.write("daily_reference", pl.DataFrame(refs).to_arrow().cast(schema.DAILY_REFERENCE), year=2026)
    if sts:
        wh.write("st_daily", pl.DataFrame(list(sts)).to_arrow().cast(schema.ST_DAILY), year=2026)
    return wh


def _rules(violations):
    return {v.rule for v in violations}


def test_clean_data_has_no_violations(tmp_path):
    wh = _write(tmp_path, [_bar()], [_ref()], [_st()])
    assert check_truth_consistency(wh, date(2026, 6, 1), date(2026, 6, 30)) == []


def test_row_count_mismatch_is_blocking(tmp_path):
    wh = _write(tmp_path, [_bar(), _bar(symbol="000158.SZ")], [_ref()], [_st()])
    violations = check_truth_consistency(wh, date(2026, 6, 1), date(2026, 6, 30))
    hit = [v for v in violations if v.rule == "row_count_mismatch"]
    assert len(hit) == 1
    assert hit[0].severity is Severity.BLOCKING


def test_price_outside_limits_is_blocking(tmp_path):
    # high 12.0 > high_limit 11.0：制度上不可能成交
    wh = _write(tmp_path, [_bar(high=12.0)], [_ref()], [_st()])
    violations = check_truth_consistency(wh, date(2026, 6, 1), date(2026, 6, 30))
    hit = [v for v in violations if v.rule == "price_outside_limits"]
    assert len(hit) == 1
    assert hit[0].severity is Severity.BLOCKING
    assert hit[0].symbol == "600036.SH"
    assert hit[0].dt == date(2026, 6, 9)


def test_paused_day_with_volume_is_warning(tmp_path):
    wh = _write(tmp_path, [_bar(volume=500)], [_ref(paused=True)], [_st()])
    violations = check_truth_consistency(wh, date(2026, 6, 1), date(2026, 6, 30))
    hit = [v for v in violations if v.rule == "paused_with_volume"]
    assert len(hit) == 1
    assert hit[0].severity is Severity.WARNING


def test_paused_day_with_null_prices_is_clean(tmp_path):
    """停牌日价格为 null 是正确形状，不得报越界。"""
    bar = _bar(high=None, low=None, close=None, volume=0)
    bar["open"] = None
    wh = _write(tmp_path, [bar], [_ref(paused=True)], [_st()])
    assert check_truth_consistency(wh, date(2026, 6, 1), date(2026, 6, 30)) == []


def test_unexpected_limit_pct_is_warning(tmp_path):
    # 10.70 / 10.00 - 1 = 0.07，不在 {0.05, 0.10, 0.20, 0.30} 内
    wh = _write(tmp_path, [_bar(high=10.6, close=10.6)], [_ref(high_limit=10.70)], [_st()])
    violations = check_truth_consistency(wh, date(2026, 6, 1), date(2026, 6, 30))
    hit = [v for v in violations if v.rule == "unexpected_limit_pct"]
    assert len(hit) == 1
    assert hit[0].severity is Severity.WARNING
    assert "0.07" in hit[0].detail


def test_st_coverage_gap_is_warning(tmp_path):
    wh = _write(tmp_path, [_bar()], [_ref()])  # st_daily 整张缺
    violations = check_truth_consistency(wh, date(2026, 6, 1), date(2026, 6, 30))
    hit = [v for v in violations if v.rule == "st_coverage_gap"]
    assert len(hit) == 1
    assert hit[0].severity is Severity.WARNING
```

- [ ] **Step 6: 跑测试确认失败**

Run: `uv run pytest tests/data/quality/test_truth_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qsys.data.quality.truth_rules'`

- [ ] **Step 7: 实现**

```python
# src/qsys/data/quality/truth_rules.py
"""规则 4：真值一致性。

`rules.py` 的规则 1-3 只看单张 bars 表，判不出「涨跌停到底是多少」——
一期没有真值，只能按板块粗筛。现在 `daily_reference` 里有交易所的
`high_limit` / `low_limit` / `paused` 真值，于是可以卡**制度上不可能发生**
的事情：

  · 两张表行数不等         → 采集漏了，BLOCKING
  · 非停牌日价格走出涨跌停 → 制度不可能，BLOCKING
  · 停牌日却有成交量       → 真值自相矛盾，WARNING
  · 涨跌幅不在四档之内     → 上市首日等特例，WARNING（要人看一眼）
  · 没有 ST 真值覆盖       → 采集缺口，WARNING

这些都是**事实校验**，不依赖数据来自哪个源：某一方的口径解释不掉它们。
"""

from __future__ import annotations

from datetime import date

import polars as pl

from qsys.data.quality.rules import Severity, Violation
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

# 现行 A 股四档涨跌幅。ST 5% / 主板 10% / 创业板科创板 20% / 北交所 30%。
_LEGAL_LIMIT_PCT = [0.05, 0.10, 0.20, 0.30]
# 报多少条明细。全市场全历史可能有上万条，日志里只留头部，总数写进 detail。
_MAX_DETAIL_ROWS = 50


def check_truth_consistency(
    warehouse: Warehouse, start: date, end: date
) -> list[Violation]:
    query = WarehouseQuery(warehouse)
    bars = query.daily_bars(None, start, end)
    reference = query.daily_reference(None, start, end)
    st = query.st_daily(None, start, end)
    violations: list[Violation] = []

    if bars.height != reference.height:
        violations.append(
            Violation(
                rule="row_count_mismatch",
                severity=Severity.BLOCKING,
                symbol="",
                dt=start,
                detail=f"bars_daily {bars.height} 行 vs daily_reference {reference.height} 行",
            )
        )

    if bars.height == 0:
        return violations

    joined = bars.join(reference, on=["symbol", "dt"], how="inner")

    # 只看有价格的非停牌日。null 不参与比较——不知道就是不知道。
    alive = joined.filter(
        ~pl.col("paused").fill_null(False)
        & pl.col("high").is_not_null()
        & pl.col("low").is_not_null()
        & pl.col("high_limit").is_not_null()
        & pl.col("low_limit").is_not_null()
    )
    outside = alive.filter(
        (pl.col("high") > pl.col("high_limit")) | (pl.col("low") < pl.col("low_limit"))
    )
    for row in outside.head(_MAX_DETAIL_ROWS).iter_rows(named=True):
        violations.append(
            Violation(
                rule="price_outside_limits",
                severity=Severity.BLOCKING,
                symbol=row["symbol"],
                dt=row["dt"],
                detail=(
                    f"high={row['high']} low={row['low']} 走出 "
                    f"[{row['low_limit']}, {row['high_limit']}]，共 {outside.height} 例"
                ),
            )
        )

    noisy = joined.filter(pl.col("paused").fill_null(False) & (pl.col("volume") > 0))
    for row in noisy.head(_MAX_DETAIL_ROWS).iter_rows(named=True):
        violations.append(
            Violation(
                rule="paused_with_volume",
                severity=Severity.WARNING,
                symbol=row["symbol"],
                dt=row["dt"],
                detail=f"停牌日 volume={row['volume']}，共 {noisy.height} 例",
            )
        )

    pct = joined.filter(
        pl.col("pre_close").is_not_null()
        & (pl.col("pre_close") > 0)
        & pl.col("high_limit").is_not_null()
    ).with_columns(
        (pl.col("high_limit") / pl.col("pre_close") - 1.0).round(4).alias("limit_pct")
    )
    odd = pct.filter(~pl.col("limit_pct").is_in(_LEGAL_LIMIT_PCT))
    for row in odd.head(_MAX_DETAIL_ROWS).iter_rows(named=True):
        violations.append(
            Violation(
                rule="unexpected_limit_pct",
                severity=Severity.WARNING,
                symbol=row["symbol"],
                dt=row["dt"],
                detail=f"limit_pct={row['limit_pct']} 不在 {_LEGAL_LIMIT_PCT} 内，共 {odd.height} 例",
            )
        )

    missing = bars.select(["symbol", "dt"]).join(
        st.select(["symbol", "dt"]), on=["symbol", "dt"], how="anti"
    )
    for row in missing.head(_MAX_DETAIL_ROWS).iter_rows(named=True):
        violations.append(
            Violation(
                rule="st_coverage_gap",
                severity=Severity.WARNING,
                symbol=row["symbol"],
                dt=row["dt"],
                detail=f"无 ST 真值，共 {missing.height} 例",
            )
        )

    return violations
```

- [ ] **Step 8: 跑测试确认通过**

Run: `uv run pytest tests/data/quality/test_truth_rules.py -v`
Expected: PASS（7 passed）

- [ ] **Step 9: 接进质量报告**

`src/qsys/data/quality/report.py` 增加 import：

```python
from qsys.data.quality.truth_rules import check_truth_consistency
```

`run_quality_checks` 需要 warehouse 才能读真值表，而它现在只拿到 `query`。加一个可选参数，默认不跑——**不改变现有调用方的行为**：

```python
def run_quality_checks(
    query: WarehouseQuery,
    calendar: TradingCalendar,
    expected_symbols: Sequence[str],
    start: date,
    end: date,
    *,
    warehouse: Warehouse | None = None,
) -> QualityReport:
    bars = query.daily_bars(list(expected_symbols), start, end)
    instruments = query.instruments()
    boards = dict(zip(instruments["symbol"].to_list(), instruments["board"].to_list()))

    violations = [
        *check_completeness(bars, calendar, expected_symbols, start, end),
        *check_price_limits(bars, boards),
        *check_internal_consistency(bars),
    ]
    # 真值表存在时才跑规则 4：迁移期间 daily_reference 可能还没采到这一段。
    if warehouse is not None:
        violations.extend(check_truth_consistency(warehouse, start, end))
```

顶部 import 补 `from qsys.data.storage import Warehouse`。

- [ ] **Step 10: 跑全量测试**

Run: `uv run pytest -q`
Expected: 全部通过。

- [ ] **Step 11: 在节点上对全量数据跑一次**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 'cd ~/qsys && uv run python - <<PY
from collections import Counter
from datetime import date
from qsys.data.quality.truth_rules import check_truth_consistency
from qsys.data.storage import Warehouse

v = check_truth_consistency(Warehouse("warehouse"), date(2005, 1, 1), date(2026, 9, 10))
print(Counter((x.rule, x.severity.value) for x in v))
for x in v[:20]:
    print(x)
PY'
```

判据：`price_outside_limits` 与 `row_count_mismatch` **必须为 0**。`unexpected_limit_pct` 出现上市首日之类是预期的，要逐类确认能解释（把清单记进文档）。`st_coverage_gap` 应只覆盖 ST 采集还没跑到的区间。

- [ ] **Step 12: 提交**

```bash
git add src/qsys/data/quality/ tests/data/quality/
git commit -m "feat(data): 真值一致性质检（规则 4）+ 空值安全修正"
```

---

## Task 11: 抽样验真脚本

**Files:**
- Create: `scripts/sample_verify.py`
- Create: `tests/data/test_sample_verify.py`

**Interfaces:**
- Consumes: `WarehouseQuery.daily_bars` / `daily_reference` / `st_daily`
- Produces: `sample_rows(warehouse, start, end, n, seed) -> pl.DataFrame`（列：`symbol, dt, open, high, low, close, volume, amount, pre_close, high_limit, low_limit, paused, is_st, factor, avg, limit_pct`）

- [ ] **Step 1: 写失败测试**

```python
# tests/data/test_sample_verify.py
from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.storage import Warehouse

from scripts.sample_verify import sample_rows


def _seed_warehouse(tmp_path):
    wh = Warehouse(tmp_path)
    bars, refs, sts = [], [], []
    for i in range(10):
        symbol = f"60003{i}.SH"
        bars.append({"symbol": symbol, "dt": date(2026, 6, 9), "open": 10.0, "high": 11.0,
                     "low": 9.5, "close": 10.5, "volume": 1000, "amount": 10_500.0})
        refs.append({"symbol": symbol, "dt": date(2026, 6, 9), "pre_close": 10.0,
                     "high_limit": 11.0, "low_limit": 9.0, "paused": False,
                     "factor": 1.0, "avg": 10.5})
        sts.append({"symbol": symbol, "dt": date(2026, 6, 9), "is_st": False})
    wh.write("bars_daily", pl.DataFrame(bars).to_arrow().cast(schema.BARS_DAILY), year=2026)
    wh.write("daily_reference", pl.DataFrame(refs).to_arrow().cast(schema.DAILY_REFERENCE), year=2026)
    wh.write("st_daily", pl.DataFrame(sts).to_arrow().cast(schema.ST_DAILY), year=2026)
    return wh


def test_sample_is_deterministic_for_a_seed(tmp_path):
    wh = _seed_warehouse(tmp_path)
    a = sample_rows(wh, date(2026, 6, 1), date(2026, 6, 30), n=3, seed=42)
    b = sample_rows(wh, date(2026, 6, 1), date(2026, 6, 30), n=3, seed=42)
    assert a.to_dicts() == b.to_dicts()
    assert a.height == 3


def test_sample_carries_every_direct_field(tmp_path):
    wh = _seed_warehouse(tmp_path)
    got = sample_rows(wh, date(2026, 6, 1), date(2026, 6, 30), n=2, seed=1)
    for column in ["open", "high", "low", "close", "volume", "amount", "pre_close",
                   "high_limit", "low_limit", "paused", "is_st", "factor", "avg", "limit_pct"]:
        assert column in got.columns


def test_sample_handles_n_larger_than_population(tmp_path):
    wh = _seed_warehouse(tmp_path)
    got = sample_rows(wh, date(2026, 6, 1), date(2026, 6, 30), n=999, seed=7)
    assert got.height == 10
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_sample_verify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.sample_verify'`（若 `scripts` 不是包，在 `tests/data/test_sample_verify.py` 顶部用 `sys.path.insert(0, "scripts")` 后 `import sample_verify`，与仓库既有脚本测试的做法保持一致）

- [ ] **Step 3: 实现**

```python
# scripts/sample_verify.py
"""抽样验真：随机抽股-日，打印全部直取字段与来源，供与同花顺人工比对。

spec §2.3。每一列都注明来自哪个聚宽接口——比对时能立刻定位是哪一层
出了问题，而不是只知道「对不上」。

用法：
    uv run python scripts/sample_verify.py --n 100 --seed 42 \
        --start 2022-09-01 --end 2026-09-10
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import polars as pl

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

# 每一列的来源，打印在表头下方。派生列写公式。
PROVENANCE = {
    "open": "get_price.open（直取）",
    "high": "get_price.high（直取）",
    "low": "get_price.low（直取）",
    "close": "get_price.close（直取）",
    "volume": "get_price.volume（直取，单位见 joinquant-data-source.md）",
    "amount": "get_price.money（直取）",
    "pre_close": "get_price.pre_close（直取）",
    "high_limit": "get_price.high_limit（直取）",
    "low_limit": "get_price.low_limit（直取）",
    "paused": "get_price.paused（直取）",
    "factor": "get_price.factor（直取）",
    "avg": "get_price.avg（直取）",
    "is_st": "get_extras('is_st')（直取）",
    "limit_pct": "派生：round(high_limit / pre_close - 1, 4)",
}


def sample_rows(
    warehouse: Warehouse, start: date, end: date, n: int, seed: int
) -> pl.DataFrame:
    """随机抽 n 个股-日的全部直取字段。种子固定则结果可复现。"""
    query = WarehouseQuery(warehouse)
    bars = query.daily_bars(None, start, end)
    if bars.height == 0:
        return pl.DataFrame()
    ref = query.daily_reference(None, start, end)
    st = query.st_daily(None, start, end)

    joined = (
        bars.join(ref, on=["symbol", "dt"], how="left")
        .join(st, on=["symbol", "dt"], how="left")
        .with_columns(
            pl.when(pl.col("pre_close") > 0)
            .then((pl.col("high_limit") / pl.col("pre_close") - 1.0).round(4))
            .otherwise(None)
            .alias("limit_pct")
        )
    )
    take = min(n, joined.height)
    return joined.sample(n=take, seed=seed, shuffle=True).sort(["dt", "symbol"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    frame = sample_rows(
        Warehouse(Path(args.warehouse)), args.start, args.end, args.n, args.seed
    )
    if frame.height == 0:
        print("仓库在该区间无数据")
        return 1

    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=400):
        print(frame)
    print("\n字段来源：")
    for column, source in PROVENANCE.items():
        if column in frame.columns:
            print(f"  {column:<12} {source}")
    print(f"\n样本 {frame.height} 行，seed={args.seed}（复现用同一 seed）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/test_sample_verify.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 在节点上抽一批人工比对**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 'cd ~/qsys && \
  uv run python scripts/sample_verify.py --n 100 --seed 42 \
    --start 2022-09-01 --end 2026-09-10'
```

输出交需求方与同花顺逐字段比对。不一致的条目**逐条记进** `docs/reference/joinquant-data-source.md` 的「抽样验真」一节，含 symbol、dt、字段、两边的值——不要只记「有差异」。

- [ ] **Step 6: 提交**

```bash
git add scripts/sample_verify.py tests/data/test_sample_verify.py
git commit -m "feat(data): 抽样验真脚本——直取字段带来源打印，供外部比对"
```

---

## Task 12: `flags.py` 重写 —— 删掉全部反推

**Files:**
- Modify: `src/qsys/data/pipeline/flags.py`（342 → 约 150 行）
- Modify: `src/qsys/data/schema.py`（`DAILY_FLAGS` 删 4 列、加 `is_st`）
- Test: `tests/data/test_flags.py`（重写）
- Modify: `tests/data/test_candidates.py`、`tests/strategy/structural/test_structural_market_feed.py`、`tests/strategy/structural/test_structural_selection.py`（夹具去掉已删列）

**Interfaces:**
- Consumes: `daily_reference`、`st_daily`
- Produces: `build_daily_flags(warehouse, start, end) -> int` 签名不变；`daily_flags` 的列：`dt, symbol, prev_close, limit_up, limit_down, is_limit_up, is_limit_down, touched_limit_up, is_broken_board, is_first_board, limit_pct, is_st`

- [ ] **Step 1: 写新的失败测试**

```python
# tests/data/test_flags.py —— 替换掉所有 ST 反推相关的测试
from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def _seed(wh, bars, refs, sts):
    wh.write("bars_daily", pl.DataFrame(bars).to_arrow().cast(schema.BARS_DAILY), year=2026)
    wh.write("daily_reference", pl.DataFrame(refs).to_arrow().cast(schema.DAILY_REFERENCE), year=2026)
    wh.write("st_daily", pl.DataFrame(sts).to_arrow().cast(schema.ST_DAILY), year=2026)


def test_limit_up_uses_truth_not_computation(tmp_path):
    """000158.SZ 2026-06-09 除权：前收 13.94、每股派 0.027。

    按前收算涨停价是 15.33（漏判），聚宽真值是 15.30，当天收在 15.30 —— 是涨停。
    真值化之后不需要任何参考价公式就能判对。
    """
    wh = Warehouse(tmp_path)
    _seed(
        wh,
        [{"symbol": "000158.SZ", "dt": date(2026, 6, 9), "open": 14.0, "high": 15.30,
          "low": 13.9, "close": 15.30, "volume": 1000, "amount": 15_000.0}],
        [{"symbol": "000158.SZ", "dt": date(2026, 6, 9), "pre_close": 13.913,
          "high_limit": 15.30, "low_limit": 12.52, "paused": False,
          "factor": 1.0, "avg": 15.0}],
        [{"symbol": "000158.SZ", "dt": date(2026, 6, 9), "is_st": False}],
    )
    assert build_daily_flags(wh, date(2026, 6, 1), date(2026, 6, 30)) == 1

    flags = WarehouseQuery(wh).daily_flags(date(2026, 6, 1), date(2026, 6, 30))
    row = flags.row(0, named=True)
    assert row["limit_up"] == 15.30
    assert row["is_limit_up"] is True
    assert row["touched_limit_up"] is True
    assert row["is_broken_board"] is False
    assert row["prev_close"] == 13.913


def test_is_st_comes_from_truth_table(tmp_path):
    wh = Warehouse(tmp_path)
    _seed(
        wh,
        [{"symbol": "600421.SH", "dt": date(2026, 6, 9), "open": 1.0, "high": 1.05,
          "low": 0.98, "close": 1.05, "volume": 100, "amount": 103.0}],
        [{"symbol": "600421.SH", "dt": date(2026, 6, 9), "pre_close": 1.00,
          "high_limit": 1.05, "low_limit": 0.95, "paused": False,
          "factor": 1.0, "avg": 1.02}],
        [{"symbol": "600421.SH", "dt": date(2026, 6, 9), "is_st": True}],
    )
    build_daily_flags(wh, date(2026, 6, 1), date(2026, 6, 30))
    row = WarehouseQuery(wh).daily_flags(date(2026, 6, 1), date(2026, 6, 30)).row(0, named=True)
    assert row["is_st"] is True
    assert row["limit_pct"] == 0.05
    assert row["is_limit_up"] is True


def test_non_st_five_percent_is_not_forced_to_st(tmp_path):
    """真值里 is_st=False 但涨跌幅恰好 5%（如退市整理期）时，不得反过来判成 ST。"""
    wh = Warehouse(tmp_path)
    _seed(
        wh,
        [{"symbol": "605081.SH", "dt": date(2026, 6, 9), "open": 0.44, "high": 0.46,
          "low": 0.43, "close": 0.46, "volume": 100, "amount": 45.0}],
        [{"symbol": "605081.SH", "dt": date(2026, 6, 9), "pre_close": 0.44,
          "high_limit": 0.46, "low_limit": 0.42, "paused": False,
          "factor": 1.0, "avg": 0.45}],
        [{"symbol": "605081.SH", "dt": date(2026, 6, 9), "is_st": False}],
    )
    build_daily_flags(wh, date(2026, 6, 1), date(2026, 6, 30))
    row = WarehouseQuery(wh).daily_flags(date(2026, 6, 1), date(2026, 6, 30)).row(0, named=True)
    assert row["is_st"] is False


def test_first_board_requires_yesterday_not_limit_up(tmp_path):
    wh = Warehouse(tmp_path)
    bars, refs, sts = [], [], []
    for day, close, limit in ((8, 11.0, 11.0), (9, 12.1, 12.1)):
        bars.append({"symbol": "600036.SH", "dt": date(2026, 6, day), "open": 10.5,
                     "high": close, "low": 10.4, "close": close, "volume": 1000,
                     "amount": 11_000.0})
        refs.append({"symbol": "600036.SH", "dt": date(2026, 6, day),
                     "pre_close": round(limit / 1.1, 2), "high_limit": limit,
                     "low_limit": round(limit / 1.1 * 0.9, 2), "paused": False,
                     "factor": 1.0, "avg": close})
        sts.append({"symbol": "600036.SH", "dt": date(2026, 6, day), "is_st": False})
    _seed(wh, bars, refs, sts)
    build_daily_flags(wh, date(2026, 6, 1), date(2026, 6, 30))
    flags = WarehouseQuery(wh).daily_flags(date(2026, 6, 1), date(2026, 6, 30)).sort("dt")
    assert flags["is_first_board"].to_list() == [True, False]


def test_paused_day_has_null_prices_and_no_limit_up(tmp_path):
    """停牌日价格是 null（不是填充值），不能被判成涨停。"""
    wh = Warehouse(tmp_path)
    _seed(
        wh,
        [{"symbol": "600036.SH", "dt": date(2026, 6, 9), "open": None, "high": None,
          "low": None, "close": None, "volume": 0, "amount": 0.0}],
        [{"symbol": "600036.SH", "dt": date(2026, 6, 9), "pre_close": 10.0,
          "high_limit": 11.0, "low_limit": 9.0, "paused": True,
          "factor": 1.0, "avg": None}],
        [{"symbol": "600036.SH", "dt": date(2026, 6, 9), "is_st": False}],
    )
    build_daily_flags(wh, date(2026, 6, 1), date(2026, 6, 30))
    row = WarehouseQuery(wh).daily_flags(date(2026, 6, 1), date(2026, 6, 30)).row(0, named=True)
    assert row["is_limit_up"] is False
    assert row["touched_limit_up"] is False


def test_daily_flags_schema_has_no_inference_columns():
    """反推的审计字段必须从 schema 里消失 —— 它们的存在本身就是污染入口。"""
    names = set(schema.DAILY_FLAGS.names)
    assert "st_inferred_raw" not in names
    assert "st_inferred_final" not in names
    assert "st_inference_rejected_by_price_evidence" not in names
    assert "adj_prev_close" not in names
    assert "is_st" in names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_flags.py -v`
Expected: FAIL — 现有 `build_daily_flags` 不读 `daily_reference`，`is_st` 列不存在

- [ ] **Step 3: 改 schema**

`src/qsys/data/schema.py` 的 `DAILY_FLAGS` 替换为：

```python
DAILY_FLAGS = pa.schema(
    [
        ("dt", pa.date32()),
        ("symbol", pa.string()),
        # 以下四列均由 daily_reference 直取真值（聚宽 get_price），
        # 不再自算：涨跌停要按除权除息参考价算这件事，交易所自己已经算好了。
        ("prev_close", pa.float64()),
        ("limit_up", pa.float64()),
        ("limit_down", pa.float64()),
        ("is_st", pa.bool_()),          # 直取 st_daily（get_extras('is_st')）
        ("is_limit_up", pa.bool_()),
        ("is_limit_down", pa.bool_()),
        ("touched_limit_up", pa.bool_()),  # 盘中触及涨停（含封住）
        ("is_broken_board", pa.bool_()),   # 炸板：触及但未封住
        ("is_first_board", pa.bool_()),    # 首板：今日涨停且昨日未涨停
        # 实际生效的涨跌幅。纯派生：round(limit_up / prev_close - 1, 4)
        ("limit_pct", pa.float64()),
    ]
)
```

- [ ] **Step 4: 重写 `flags.py`**

```python
"""涨跌停与首板判定。

策略的候选池完全建立在这上面：
  涨停   close >= limit_up
  炸板   high >= limit_up 但 close < limit_up
  首板   今日涨停且昨日未涨停（连板不是首板）

**涨跌停价与 ST 都是真值，不是算出来的、更不是推出来的。**
`limit_up` / `limit_down` / `prev_close` 直取 `daily_reference`（聚宽
`get_price` 的 `high_limit` / `low_limit` / `pre_close`），`is_st` 直取
`st_daily`（`get_extras('is_st')`）。

历史上这里有过两套自算逻辑，都已删除：
- 按除权除息参考价算涨跌停（漏了会单向漏判涨停，全区间实测 144 个股-日）
- 从价格行为反推 ST（三条启发式规则，近窗 Gate 一致率只有 93.8%）

删除它们不是简化，是消除污染源。任何「拿不到真值就先估一个」的想法，
参见 spec §2：拿不到就记 null 由质检报出来。

**全表用 polars 表达式算，不逐行 iter_rows**：全市场全历史约 1,800 万行。

**逐年处理**。不再需要跨年回看——`prev_close` 与 `is_st` 都来自真值表，
不靠 shift 补前收、也不靠滚动窗口确认 ST。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_FLAG_COLUMNS = list(schema.DAILY_FLAGS.names)


def build_daily_flags(warehouse: Warehouse, start: date, end: date) -> int:
    """从日线 + 真值表派生涨跌停与首板标记。返回写入行数。"""
    query = WarehouseQuery(warehouse)
    written = 0

    for year in range(start.year, end.year + 1):
        lo_year, hi_year = date(year, 1, 1), date(year, 12, 31)
        lo, hi = max(start, lo_year), min(end, hi_year)
        if lo > hi:
            continue

        # 首板要看前一天，故多读一天；写回时再裁到 [lo, hi]
        bars = query.daily_bars(None, lo_year, hi)
        if bars.height == 0:
            continue
        reference = query.daily_reference(None, lo_year, hi)
        st = query.st_daily(None, lo_year, hi)

        flags = _compute(bars, reference, st).filter(pl.col("dt").is_between(lo, hi))
        if flags.height == 0:
            continue

        chunk = flags.sort(["dt", "symbol"]).select(_FLAG_COLUMNS)
        # 分区是整年覆盖写的，必须与同年既有数据合并：只重算一段区间时
        # 直接写会把同年其余日期的标记抹掉。新行在后，keep="last" 让重算覆盖旧值。
        merged = (
            pl.concat([query.daily_flags(lo_year, hi_year), chunk], how="vertical")
            .unique(subset=["dt", "symbol"], keep="last", maintain_order=True)
            .sort(["dt", "symbol"])
            .select(_FLAG_COLUMNS)
        )
        warehouse.write(
            "daily_flags", merged.to_arrow().cast(schema.DAILY_FLAGS), year=year
        )
        written += chunk.height

    log.info("daily_flags.built", rows=written, start=str(start), end=str(end))
    return written


def _compute(
    bars: pl.DataFrame, reference: pl.DataFrame, st: pl.DataFrame
) -> pl.DataFrame:
    """join 真值 → 比较 → 首板移位。三步，没有第四步。"""
    frame = (
        bars.join(reference, on=["symbol", "dt"], how="left")
        .join(st, on=["symbol", "dt"], how="left")
        .rename({"pre_close": "prev_close", "high_limit": "limit_up",
                 "low_limit": "limit_down"})
        .sort(["symbol", "dt"])
    )

    # 价格与涨跌停价都是两位小数的真值，直接比较，无需容差。
    # null（停牌日价格、或真值缺失）一律按 False，不猜。
    is_up = (pl.col("close") >= pl.col("limit_up")).fill_null(False)
    touched = (pl.col("high") >= pl.col("limit_up")).fill_null(False)

    frame = frame.with_columns(
        is_up.alias("is_limit_up"),
        (pl.col("close") <= pl.col("limit_down")).fill_null(False).alias("is_limit_down"),
        touched.alias("touched_limit_up"),
        (touched & ~is_up).alias("is_broken_board"),
        pl.when(pl.col("prev_close") > 0)
        .then((pl.col("limit_up") / pl.col("prev_close") - 1.0).round(4))
        .otherwise(None)
        .alias("limit_pct"),
        pl.col("is_st").fill_null(False),
    )

    return frame.with_columns(
        (
            pl.col("is_limit_up")
            & ~pl.col("is_limit_up").shift(1, fill_value=False).over("symbol")
        ).alias("is_first_board")
    ).select(_FLAG_COLUMNS)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/data/test_flags.py -v`
Expected: PASS（6 passed）

- [ ] **Step 6: 修其他受影响的夹具**

Run: `uv run pytest -q`

`tests/data/test_candidates.py`、`tests/strategy/structural/test_structural_market_feed.py`、`tests/strategy/structural/test_structural_selection.py` 里构造 `daily_flags` 的夹具会因删列而失败。逐个把 `adj_prev_close` / `st_inferred_*` 三列删掉、补上 `is_st`。**不要为了让旧断言通过而保留已删的列。**

- [ ] **Step 7: 提交**

```bash
git add src/qsys/data/pipeline/flags.py src/qsys/data/schema.py tests/
git commit -m "refactor(data): flags 改读真值，删掉 ST 反推与除权参考价自算"
```

---

## Task 13: `adjust.py` 改造 —— 因子直取，自算降级为校验

**Files:**
- Modify: `src/qsys/data/pipeline/adjust.py`
- Test: `tests/data/pipeline/test_adjust.py`

**Interfaces:**
- Consumes: `daily_reference.factor`、`corporate_actions`
- Produces: `build_adj_factors(warehouse) -> int` 签名不变；`daily_factor(prev_close, cash, stock, rights, rights_price) -> float` 保留（仅供校验）；新增 `check_factor_agreement(warehouse, tolerance=1e-6) -> list[tuple[str, date, float, float]]`

- [ ] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_adjust.py 追加
from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.pipeline.adjust import build_adj_factors, check_factor_agreement
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def _seed_for_factor(tmp_path):
    wh = Warehouse(tmp_path)
    wh.write("bars_daily", pl.DataFrame([
        {"symbol": "000158.SZ", "dt": date(2026, 6, 8), "open": 13.9, "high": 14.0,
         "low": 13.8, "close": 13.94, "volume": 1000, "amount": 13_900.0},
        {"symbol": "000158.SZ", "dt": date(2026, 6, 9), "open": 14.0, "high": 15.3,
         "low": 13.9, "close": 15.30, "volume": 1000, "amount": 15_000.0},
    ]).to_arrow().cast(schema.BARS_DAILY), year=2026)
    wh.write("daily_reference", pl.DataFrame([
        {"symbol": "000158.SZ", "dt": date(2026, 6, 8), "pre_close": 13.90,
         "high_limit": 15.29, "low_limit": 12.51, "paused": False,
         "factor": 1.0, "avg": 13.95},
        # 除权日：聚宽 factor 从 1.0 跳到 13.94/13.913 = 1.001941...
        {"symbol": "000158.SZ", "dt": date(2026, 6, 9), "pre_close": 13.913,
         "high_limit": 15.30, "low_limit": 12.52, "paused": False,
         "factor": 1.0019407, "avg": 15.0},
    ]).to_arrow().cast(schema.DAILY_REFERENCE), year=2026)
    wh.write("corporate_actions", pl.DataFrame([
        {"symbol": "000158.SZ", "ex_date": date(2026, 6, 9), "cash_per_share": 0.027,
         "stock_per_share": 0.0, "rights_per_share": 0.0, "rights_price": 0.0},
    ]).to_arrow().cast(schema.CORPORATE_ACTIONS))
    return wh


def test_cum_factor_is_taken_from_truth(tmp_path):
    wh = _seed_for_factor(tmp_path)
    assert build_adj_factors(wh) == 2
    got = WarehouseQuery(wh).adj_factors().sort("dt")
    assert got["cum_factor"].to_list() == [1.0, 1.0019407]


def test_share_factor_is_still_computed(tmp_path):
    """聚宽 factor 是价格因子，不含股本比例——送转要自己算。"""
    wh = _seed_for_factor(tmp_path)
    build_adj_factors(wh)
    got = WarehouseQuery(wh).adj_factors().sort("dt")
    # 纯派现不改股数
    assert got["cum_share_factor"].to_list() == [1.0, 1.0]


def test_factor_agreement_check_passes_on_consistent_data(tmp_path):
    wh = _seed_for_factor(tmp_path)
    build_adj_factors(wh)
    assert check_factor_agreement(wh) == []


def test_factor_agreement_check_reports_divergence(tmp_path):
    wh = _seed_for_factor(tmp_path)
    # 把真值因子改成明显错的值，校验必须报出来
    wh.write("daily_reference", WarehouseQuery(wh).daily_reference(
        None, date(2026, 1, 1), date(2026, 12, 31)
    ).with_columns(
        pl.when(pl.col("dt") == date(2026, 6, 9)).then(1.5)
        .otherwise(pl.col("factor")).alias("factor")
    ).to_arrow().cast(schema.DAILY_REFERENCE), year=2026)
    build_adj_factors(wh)
    diverged = check_factor_agreement(wh)
    assert len(diverged) == 1
    assert diverged[0][0] == "000158.SZ"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/pipeline/test_adjust.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_factor_agreement'`

- [ ] **Step 3: 实现**

`build_adj_factors` 的函数体替换为（`daily_factor` / `share_factor` 两个函数保留不动）：

```python
def build_adj_factors(warehouse: Warehouse) -> int:
    """价格因子直取聚宽真值，股本因子自算。

    原先这里逐行调 daily_factor 算价格因子——聚宽的 `factor` 就是真值，
    自算版本降级为 `check_factor_agreement` 的校验基准。

    股本因子仍要自算：聚宽 `factor` 是价格因子，含现金分红那一项，拿它
    去调成交量会多调。实测 301379.SZ 2026-06-05（10 送 4 派 0.2）价格
    因子倒数 1.4075、股本比例 1.4000，差 0.5%——落在阈值上就能翻转判定。
    """
    query = WarehouseQuery(warehouse)
    reference = query.daily_reference(None, _EPOCH, _HORIZON)
    if reference.height == 0:
        return 0

    actions = query.corporate_actions()
    if actions.height == 0:
        shares = reference.select(
            "symbol", "dt", pl.lit(1.0).alias("cum_share_factor")
        )
    else:
        ratio = (
            pl.lit(1.0)
            + pl.col("stock_per_share").fill_null(0.0)
            + pl.col("rights_per_share").fill_null(0.0)
        )
        shares = (
            reference.select("symbol", "dt")
            .join(
                actions.rename({"ex_date": "dt"}).with_columns(ratio.alias("_ratio")),
                on=["symbol", "dt"],
                how="left",
            )
            .with_columns(pl.col("_ratio").fill_null(1.0))
            .sort(["symbol", "dt"])
            .with_columns(
                pl.col("_ratio").cum_prod().over("symbol").alias("cum_share_factor")
            )
            .select("symbol", "dt", "cum_share_factor")
        )

    result = (
        reference.select("symbol", "dt", pl.col("factor").alias("cum_factor"))
        .join(shares, on=["symbol", "dt"], how="left")
        .with_columns(pl.col("cum_share_factor").fill_null(1.0))
        .sort(["symbol", "dt"])
        .select("symbol", "dt", "cum_factor", "cum_share_factor")
    )
    warehouse.write("adj_factors", result.to_arrow().cast(schema.ADJ_FACTORS))
    log.info("adj_factors.built", rows=result.height)
    return result.height


def check_factor_agreement(
    warehouse: Warehouse, tolerance: float = 1e-6
) -> list[tuple[str, date, float, float]]:
    """交叉校验：自算的单日因子 vs 聚宽 factor 的比值。

    返回超出容差的 (symbol, dt, 自算值, 真值比值) 列表。这是原自算逻辑
    的唯一去处——它不再决定数据，只负责在两边不一致时报警。
    """
    query = WarehouseQuery(warehouse)
    reference = query.daily_reference(None, _EPOCH, _HORIZON)
    actions = query.corporate_actions()
    if reference.height == 0 or actions.height == 0:
        return []

    frame = (
        reference.sort(["symbol", "dt"])
        .with_columns(
            (pl.col("factor") / pl.col("factor").shift(1).over("symbol")).alias("truth_ratio")
        )
        .join(actions.rename({"ex_date": "dt"}), on=["symbol", "dt"], how="inner")
    )

    diverged: list[tuple[str, date, float, float]] = []
    for row in frame.iter_rows(named=True):
        if row["truth_ratio"] is None or row["pre_close"] is None:
            continue
        # pre_close 在除权日已是参考价，自算要用**除权前**的收盘价：
        # prev_close = pre_close * truth_ratio 的关系在此不成立，故用
        # 参考价反推：自算比值 = prev_close / reference_price
        computed = daily_factor(
            row["pre_close"] * row["truth_ratio"],
            row["cash_per_share"] or 0.0,
            row["stock_per_share"] or 0.0,
            row["rights_per_share"] or 0.0,
            row["rights_price"] or 0.0,
        )
        if abs(computed - row["truth_ratio"]) > tolerance:
            diverged.append((row["symbol"], row["dt"], computed, row["truth_ratio"]))
    return diverged
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/pipeline/test_adjust.py -v`
Expected: PASS。若 `check_factor_agreement` 的反推关系与实际数据不符，以**测试里的真实数字**为准调整实现，不要调松容差。

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/pipeline/adjust.py tests/data/pipeline/test_adjust.py
git commit -m "refactor(data): 复权因子直取聚宽真值，自算逻辑降级为交叉校验"
```

---

## Task 14: 派生表重算 + 黄金回归 + 文档

**Files:**
- Create: `tests/regression/test_golden_numbers.py`
- Modify: `docs/deploy/linux-core.md`
- Modify: `docs/reference/joinquant-alignment.md`
- Modify: `docs/reference/joinquant-data-source.md`

**Interfaces:**
- Consumes: 前 13 个任务的全部产出
- Produces: `tests/regression/test_golden_numbers.py` 中固定区间的选股集合与回测净值断言

- [ ] **Step 1: 在节点上重算全部派生表**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 'cd ~/qsys && uv run python - <<PY
from datetime import date
from qsys.data.pipeline.adjust import build_adj_factors, check_factor_agreement
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.storage import Warehouse

wh = Warehouse("warehouse")
print("adj_factors", build_adj_factors(wh))
bad = check_factor_agreement(wh)
print("因子不一致", len(bad), bad[:5])
print("daily_flags", build_daily_flags(wh, date(2005, 1, 1), date(2026, 9, 10)))
print("market_regime", build_market_regime(wh, date(2005, 1, 1), date(2026, 9, 10)))
PY'
```

（`build_market_regime` 的实际函数名与签名以 `src/qsys/data/pipeline/regime.py` 为准。）

- [ ] **Step 2: 跑对账回测，记录新数字**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 'cd ~/qsys && \
  uv run python scripts/run_structural_backtest.py \
    --start 2025-09-10 --end 2026-09-08 --cash 100000 --jq-compat \
    --out var/bt_jq_truth.csv'
```

- [ ] **Step 2b: 跑与聚宽的逐条对账，核对验收标准**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 'cd ~/qsys && \
  uv run python scripts/run_selection.py --start 2025-09-10 --end 2026-09-08 \
    --out var/qsys_selection_truth.csv && \
  uv run python scripts/reconcile_joinquant.py'
```

spec §14 的三条门槛在此核对：候选池逐日集合是否完全相同、Gate 判定是否 241/241
一致、共同买入的成交价是否逐笔一致。**不达标不等于本计划失败**——剩余差异此时只
可能来自策略逻辑或 Plan B 的行为变化项。把实际数字与差异清单记进
`docs/reference/joinquant-alignment.md`，作为后续计划的输入。

- [ ] **Step 3: 把实测数字写成黄金回归**

```python
# tests/regression/test_golden_numbers.py
"""黄金回归：固定区间的口径锚点。

这是「人工翻聚宽对账」的替代物。任何口径改动都会让这里失败，必须显式
更新数字并说明原因——而不是事后发现收益变了却不知道是谁改的。

数字来自 Task 14 Step 2 的实测，区间 2025-09-10 ~ 2026-09-08、
初始 10 万、--jq-compat。仓库里没有全量数据时自动跳过。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

WAREHOUSE = Path("warehouse")
START, END = date(2025, 9, 10), date(2026, 9, 8)

# ↓↓↓ 以 Step 2 的实测输出填写，不要凭估计 ↓↓↓
EXPECTED_TRADING_DAYS = 241
EXPECTED_FLAG_ROWS: int | None = None      # daily_flags 在该区间的行数
EXPECTED_FIRST_BOARD_COUNT: int | None = None
EXPECTED_ST_COUNT: int | None = None
# ↑↑↑ 填好后把 None 全部换成实测整数 ↑↑↑

pytestmark = pytest.mark.skipif(
    not (WAREHOUSE / "daily_flags").exists(),
    reason="需要全量仓库，CI 与开发机上跳过",
)


def _flags():
    return WarehouseQuery(Warehouse(WAREHOUSE)).daily_flags(START, END)


def test_trading_days_in_window():
    assert _flags()["dt"].n_unique() == EXPECTED_TRADING_DAYS


def test_flag_rows_unchanged():
    assert EXPECTED_FLAG_ROWS is not None, "先用实测值替换 None"
    assert _flags().height == EXPECTED_FLAG_ROWS


def test_first_board_count_unchanged():
    assert EXPECTED_FIRST_BOARD_COUNT is not None, "先用实测值替换 None"
    flags = _flags()
    assert flags.filter(flags["is_first_board"]).height == EXPECTED_FIRST_BOARD_COUNT


def test_st_count_unchanged():
    assert EXPECTED_ST_COUNT is not None, "先用实测值替换 None"
    flags = _flags()
    assert flags.filter(flags["is_st"]).height == EXPECTED_ST_COUNT
```

**Step 3 未把四个 `None` 换成实测整数，本任务不算完成。** 数字从 Step 1/2 的输出里取。

- [ ] **Step 4: 更新三份文档**

`docs/deploy/linux-core.md`：把「机器实况」一节的 1.9 GB 内存 / 40 GB 磁盘改为实测的 4 核 / 7.5 GB（可用 5.6 GB）/ 118 GB（可用 82 GB）/ swap 9.9 GB，并删掉「两条硬约束」里已不成立的内存触顶结论（保留境外网络那条）；在「待办」里勾掉内存扩容。

`docs/reference/joinquant-alignment.md`：
- 第三节整节（ST 反推标定表、三条规则、修正效果）改写为「已由真值替代，见 joinquant-data-source.md」，保留历史结论作为一段简短记录
- 第六节的 `touched_limit_up_count`、退市股两行状态改为「已消除」
- 补上 `056cae8` 之后一直没写进来的数字：ST 制度硬否决后 jq_compat 总收益 +90.17% → +78.06%、买入 79 笔
- 第七节的收益表补一行真值化之后的实测

`docs/reference/joinquant-data-source.md`：补「派生表重算」与「黄金回归」两节，记下 Step 1/2 的全部输出数字。

- [ ] **Step 5: 跑全量测试**

Run: `uv run pytest -q`
Expected: 全部通过。

- [ ] **Step 6: 重新启用每日归档定时器**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  'sudo systemctl enable --now qsys-daily-archive.timer && \
   systemctl list-timers qsys-daily-archive --no-pager'
```

- [ ] **Step 7: 提交**

```bash
git add tests/regression/test_golden_numbers.py docs/
git commit -m "feat(test): 黄金回归锚点 + 真值化后的文档同步"
```

---

## 后续计划（不在本计划内）

本计划交付后，生产路径上已无任何推断。剩下三块另开计划：

**Plan B — 行为变化与分红送配真值化**
`universe.py:81` 的 ST 过滤从 `limit_pct == 0.05` 代理改为直接读 `is_st`（会改变候选池，必须单独对账）；`corporate_actions` 主源从 QMT `get_divid_factors` 换成聚宽财经表（列名见探针 §7.6）。

**Plan C — 竞价与分钟线**
历史集合竞价（`get_call_auction`，聚宽独有、QMT 只回溯 20 个交易日）与近 3 年候选股分钟线。注意 `AUCTION_TICKS` 现有 schema 是 QMT 形状（`bid1/ask1/bid_vol1/ask_vol1`），聚宽返回的是 `current/b1_p~b5_p/a1_p~a5_p`，需要一次字段映射设计。

**Plan D — QMT 接缝**
`T_jq` 之后的每日真值归档（Windows 侧 `get_instrument_detail`），以及 `T_jq` 之后 `is_st` 的真值源探针（QMT 字段是否带 ST 标记 / akshare 当日 ST 列表）。两条都不成立时进入「待确认区间」，由质检显式报出，**不退回反推**。
