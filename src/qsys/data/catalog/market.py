"""行情类指标 90 条。

**R1（控制器裁决）**：`tushare_daily` 与 `paid_daily` 各自单独生成血缘，
不共用一张乘数表。两边虽然列名、含义完全相同（`baidupan.daily` 实测就是
Tushare 转售，28 列字段级吻合、18,224,238 行逐键零差异，见
`catalog/sources.py` 的 `baidupan.daily` caveat），但**单位换算不同**：

  - `tushare_daily.amount`：源给千元，`pipeline/tushare_daily.py`
    `_FROM_DAILY` 记的乘数是 1000.0。
  - `paid_daily.amount`：源给万元，`pipeline/paid_daily.py` `COLUMNS`
    记的乘数是 10000。

复用一张表会把其中一边的单位记错，而这类错误在数值上「看起来正常」，
只有字典对账能发现，所以这里拆成两个独立的生成函数，各自读各自的
pipeline 映射表——「字典与实现分家」在结构上就不可能发生。

**R2**：两张冻结表（`paid_daily`、`bars_daily`）只挂 `C4.frozen_unchanged`。
A1/A3 等恒等式规则的实现只读 `tushare_daily`，给冻结表挂上这些规则就是
字典宣称了一份从未真正跑过的覆盖，比不挂更糟。

**R9**：`paid_daily.symbol` 判 DIRECT——`pipeline/paid_daily.py` 的
`EXCHANGES` 按代码前两位拼交易所后缀（00/30→SZ、60/68→SH、83/92→BJ）是
条件分支，但只是标识符的无损规范化，不改变这行指的是哪只证券，不算
「改数值的逻辑」，因此不必归 DERIVED；但 `Direct.note` 必须写明这条规则，
否则读者会以为源表头本来就带后缀。

**追加裁决（评审后）**：`tushare_daily.name` 从 DIRECT 改判 DERIVED，
`lineage=Computed(upstream=(), ...)`。原因见 `_tushare_daily_metrics`
的函数文档字符串——`compose_daily` 对这一列写的是 null 字面量，不读
任何源，把它记成 `Direct("tushare.namechange", ...)` 会让
`root_sources()` 报出一条生产里不存在的名称链路，即使 `note` 里写明了
真相，结构化字段本身仍在撒谎，这正是 R2 要防的那类错误。
"""

from __future__ import annotations

from qsys.data import schema
from qsys.data.catalog.types import (
    UNCOVERED,
    Computed,
    Direct,
    Kind,
    Metric,
    MetricStatus,
    Validity,
)
from qsys.data.pipeline.paid_daily import COLUMNS as _PAID_COLUMNS
from qsys.data.pipeline.tushare_daily import _FROM_BASIC, _FROM_DAILY

DATASETS = frozenset(
    {"tushare_daily", "bars_daily", "paid_daily",
     "index_bars", "minute_bars", "auction_ticks"}
)

_FROZEN_CHECKS = ("C4.frozen_unchanged",)

# 27 列共享含义（不含 name——tushare_daily.name 恒为 null，paid_daily.name
# 是逐日观测的真值，两边含义不同，分别定义）。依据：schema.py 的 PAID_DAILY
# / TUSHARE_DAILY 注释，pipeline/tushare_daily.py 顶部单位换算实测记录，
# pipeline/paid_daily.py 顶部注释与 COLUMNS 表头，docs/reference/deferred-corrections.md
# 的 D-1/D-2。
_MEANING = {
    "symbol": "标的代码，带交易所后缀（000001.SZ），"
              "见 docs/reference/baidupan-data-source.md §4.2 的文件名约定",
    "dt": "交易日",
    "open": "不复权开盘价（元）",
    "high": "不复权最高价（元）",
    "low": "不复权最低价（元）",
    "close": "不复权收盘价（元）",
    "pre_close": "前收盘价（元）。非除权日等于前一日收盘，除权日是交易所"
                 "公布的除权后参考价——涨跌停按它算，不是按前一日收盘",
    "change": "涨跌额（元）",
    "pct_change": "涨跌幅（%），源端保留 4 位小数。实测等于 "
                  "(收盘价/昨收价 − 1)×100，"
                  "基于**未复权价**（见 docs/reference/baidupan-data-source.md "
                  "§5.3），跨除权日请改用复权价重算，不要直接用这一列",
    "volume": "成交量（手）。带小数是真实精度，取整会丢失",
    "amount": "成交额（元）",
    "factor": "后复权累计因子。**源字段自身不自洽**，非股改期有 688 条价格"
              "反推对不上直取值，另有 12 只「价格有除权缺口而因子不跳」的"
              "漏除权，见 D-1；920 号段北交所另有独立异常簇，见 D-2。"
              "paid_daily 与 tushare_daily 同源零差异，问题同样存在",
    "turnover_ratio": "换手率（%），源端保留 4 位小数",
    "free_turnover_ratio": "自由流通换手率（%）",
    "volume_ratio": "量比",
    "pe": "市盈率。亏损股为 null（业务性空值，见 "
          "docs/reference/baidupan-data-source.md §5.1，实测空值率 26.2%）",
    "pe_ttm": "市盈率（TTM）。亏损股为 null，实测空值率 29.4%",
    "pb": "市净率",
    "ps": "市销率",
    "ps_ttm": "市销率（TTM）",
    "dividend_yield": "股息率（%）。不分红为 null（业务性空值），实测空值率 32.3%",
    "dividend_yield_ttm": "股息率（TTM）（%）。不分红为 null，实测空值率 33.2%",
    "total_shares": "总股本（股）。**必须直取**——反算会让 A+H 股差上亿股"
                    "（旧源曾用 market_cap / close 反算，华虹宏力偏差 1.91 亿股）",
    "circulating_shares": "流通股本（股）",
    "free_shares": "自由流通股本（股）",
    "market_cap": "总市值（元）。A+H/ADR 公司不保证等于 A 股收盘价乘全球总股本",
    "circulating_market_cap": "流通市值（元）",
}

_TUSHARE_NAME_MEANING = (
    "当日证券简称。**生产恒为 null**——daily/daily_basic/adj_factor 三个"
    "接口都不给，compose_daily 把这一列留给 namechange 单独成表（见 "
    "pipeline/tushare_daily.py 顶部注释）。ST 不再读名称（D-11，只认 stock_st）"
)
_PAID_NAME_MEANING = (
    "当日证券简称，**逐日观测到的真实值**（区别于 tushare_daily.name "
    "恒为空）。覆盖 1990 至 2026-09-11，是这段历史名称的唯一真值源，"
    "冻结保留（见 catalog/sources.py 的 baidupan.daily caveat、D-10）。"
    "2026-09-17 起 daily_flags 不再用它判 ST（D-11）"
)

# 生产链路上这些列参与哪些规则（仅 tushare_daily；R2：paid_daily 冻结只挂
# C4.frozen_unchanged，不挂这些）。规则实现见 Task 12-14。
_DAILY_CHECKS = {
    "amount": ("A1.dimension.amount_vs_volume_close", "B3.null_rate"),
    "volume": ("A1.dimension.amount_vs_volume_close", "B3.null_rate"),
    "close": ("A3.minute_vs_daily", "A3.pct_change_identity", "B3.null_rate"),
    "pre_close": ("A3.change_identity", "A3.missed_exdiv", "B3.null_rate"),
    "change": ("A3.change_identity",),
    "pct_change": ("A3.pct_change_identity",),
    "factor": ("A3.missed_exdiv",),
    "market_cap": ("A3.market_cap_vs_shares",),
    "total_shares": ("A3.market_cap_vs_shares",),
    "turnover_ratio": ("A3.turnover_identity",),
    "circulating_shares": ("A3.turnover_identity", "A3.market_cap_vs_shares"),
    "circulating_market_cap": ("A3.market_cap_vs_shares",),
    "open": ("A3.minute_vs_daily", "B3.null_rate"),
    "high": ("A3.minute_vs_daily", "B3.null_rate"),
    "low": ("A3.minute_vs_daily", "B3.null_rate"),
}
_DAILY_DEFAULT_CHECKS = ("B1.calendar", "B5.span")

# D-1/D-2 是源字段自身的事实（付费源与 Tushare 同源零差异，见 sources.py），
# 两张表的 factor 列共用同一条已知问题。
# pre_close 也挂 D-1：A3.missed_exdiv 报的是「价格缺口没有匹配的因子跳变」，
# 而按 D-1 的判据价格是仲裁者（300091.SZ 2026-02-12 的前收价与转增 0.132616
# 逐位吻合，不跳的是因子），所以这类不一致不该记在前收价头上判阻断。
_DAILY_ISSUES = {"factor": ("D-1", "D-2"), "pre_close": ("D-1",)}


def _tushare_daily_metrics() -> list[Metric]:
    """从 `pipeline.tushare_daily._FROM_DAILY` / `_FROM_BASIC` 生成 24 列，
    另外 3 列（symbol/dt/factor）不在这两张映射表里，显式登记。

    `name` 单独处理，见下方**控制器裁决**：`compose_daily` 对这一列写的是
    `pl.lit(None)` 字面量（pipeline/tushare_daily.py:99-100），三个接口
    （daily/daily_basic/adj_factor）都不返回证券简称，它指不到任何
    `(source_id, field)`——不满足 DIRECT 的分类判据，只能是 DERIVED，
    且 `upstream=()`（它确实没有上游）。

    最初把它写成 `Direct("tushare.namechange", "name", note=...)`，note
    里虽然如实写了「并非真的从 namechange 联表取值」，但
    `lineage.source_id` 这个**结构化字段**仍然指向 tushare.namechange——
    `root_sources()` 只读 `Direct.source_id`，不读 `note`，于是
    `root_sources("daily_flags.is_st")` 会经这一列凭空报出一条生产里
    根本不存在的 `tushare.namechange.name` 链路，页面的「来源」列也会
    显示这条假链路。这正是 R2 要防的那类错误——「诚实的文字 + 说谎的
    结构化字段」，只有点开 note 的人才看得到真相。改成
    `Computed(upstream=(), ...)` 后 `root_sources` 对这一列返回 `()`，
    与事实一致。
    """
    lineage: dict[str, Direct] = {
        ours: Direct("tushare.daily", theirs, mult)
        for ours, theirs, mult in _FROM_DAILY
    }
    lineage.update(
        {
            ours: Direct("tushare.daily_basic", theirs, mult)
            for ours, theirs, mult in _FROM_BASIC
        }
    )
    lineage["symbol"] = Direct("tushare.daily", "ts_code")
    lineage["dt"] = Direct("tushare.daily", "trade_date")
    lineage["factor"] = Direct("tushare.adj_factor", "adj_factor")

    out: list[Metric] = []
    for col in schema.PAID_DAILY.names:
        if col == "name":
            out.append(
                Metric(
                    id="tushare_daily.name",
                    kind=Kind.DERIVED,
                    validity=Validity.SESSION,
                    status=MetricStatus.ACTIVE,
                    meaning=_TUSHARE_NAME_MEANING,
                    lineage=Computed(
                        upstream=(),
                        formula="恒 null——daily / daily_basic / adj_factor 三个"
                                "接口都不返回证券简称，compose_daily 写 null "
                                "字面量（pipeline/tushare_daily.py:99-100）。"
                                "此列没有上游；名称真值在 paid_daily / "
                                "name_snapshots / name_history 三个直取源。",
                    ),
                    checks=UNCOVERED(
                        "生产恒为 null，没有值可验；名称真值的验证挂在 "
                        "daily_flags.is_st 上"
                    ),
                )
            )
            continue
        out.append(
            Metric(
                id=f"tushare_daily.{col}",
                kind=Kind.DIRECT,
                validity=Validity.SESSION,
                status=MetricStatus.ACTIVE,
                meaning=_MEANING[col],
                lineage=lineage[col],
                checks=_DAILY_CHECKS.get(col, _DAILY_DEFAULT_CHECKS),
                known_issues=_DAILY_ISSUES.get(col, ()),
            )
        )
    return out


def _paid_daily_metrics() -> list[Metric]:
    """从 `pipeline.paid_daily.COLUMNS`（卖家表头, 仓库列名, 乘数）生成
    28 列，全部指向 `baidupan.daily`。R1：不复用 tushare 的乘数表——
    amount 这里是 ×10000（万元），tushare 是 ×1000（千元）。"""
    lineage: dict[str, Direct] = {
        en: Direct("baidupan.daily", en, float(mult))
        for _cn, en, mult in _PAID_COLUMNS
    }
    # R9：symbol 判 DIRECT，但要在 note 里记下交易所后缀是拼出来的，
    # 源表头（股票代码）本身不带后缀。
    lineage["symbol"] = Direct(
        "baidupan.daily", "symbol",
        note="按 pipeline/paid_daily.py EXCHANGES 拼交易所后缀"
             "（00/30→SZ、60/68→SH、83/92→BJ）；源表头「股票代码」本身"
             "不带后缀，这里只是标识符的无损规范化，不改变所指的证券",
    )

    return [
        Metric(
            id=f"paid_daily.{col}",
            kind=Kind.DIRECT,
            validity=Validity.SESSION,
            status=MetricStatus.FROZEN,
            meaning=_PAID_NAME_MEANING if col == "name" else _MEANING[col],
            lineage=lineage[col],
            checks=_FROZEN_CHECKS,
            known_issues=_DAILY_ISSUES.get(col, ()),
        )
        for col in schema.PAID_DAILY.names
    ]


_INDEX_MEANING = {
    "symbol": "指数代码。生产只用沪深 300（000300.SH），"
              "见 pipeline/index_bars.py 的 CSI300 常量",
    "dt": "交易日",
    "open": "指数开盘点位",
    "high": "指数最高点位",
    "low": "指数最低点位",
    "close": "指数收盘点位",
    "volume": "成交量。**含小数**（全历史 5,270 行中实测 186 行），"
              "2026-09-14 从 int64 无损迁移为 float64",
    "amount": "成交额",
}

_MINUTE_MEANING = {
    "symbol": "标的代码",
    "dt": "该分钟的**收盘时刻**：09:30–09:31 这根记 09:31。"
          "如此 PIT 可见性就是 dt <= as_of，没有差一分钟的歧义",
    "open": "该分钟开盘价", "high": "该分钟最高价",
    "low": "该分钟最低价", "close": "该分钟收盘价",
    "volume": "该分钟成交量（手）", "amount": "该分钟成交额（元）",
}

_AUCTION_MEANING = {
    "symbol": "标的代码",
    "dt": "交易日",
    "ts": "快照时刻（北京时间）。沪市固定 3 秒一笔，深市按盘口变化推送。"
          "QMT 的 tick.time 是毫秒 epoch（UTC），导出时已 +8 小时",
    "bid1": "买一价。竞价期间等于**虚拟开盘价**，09:25 撮合完成后该恒等式失效",
    "ask1": "卖一价。竞价期间等于虚拟开盘价",
    "bid_vol1": "买一量。竞价期间等于**匹配量**（手）",
    "ask_vol1": "卖一量。竞价期间等于匹配量（手）",
    "last_price": "最新成交价。撮合前为 0",
    "volume": "累计成交量（手）。撮合前为 0",
    "amount": "成交额（元）",
}

_BARS_MEANING = {
    "symbol": "标的代码", "dt": "交易日",
    "open": "不复权开盘价（元）", "high": "不复权最高价（元）",
    "low": "不复权最低价（元）", "close": "不复权收盘价（元）",
    "volume": "成交量（手，整数）", "amount": "成交额（元）",
}


def _simple(
    dataset: str, source_id: str, meanings: dict[str, str],
    status: MetricStatus, checks: tuple[str, ...],
    validity: Validity = Validity.SESSION,
) -> list[Metric]:
    return [
        Metric(
            id=f"{dataset}.{col}", kind=Kind.DIRECT, validity=validity,
            status=status, meaning=meanings[col],
            lineage=Direct(source_id, col), checks=checks,
        )
        for col in schema.ALL[dataset].names
    ]


METRICS: tuple[Metric, ...] = tuple(
    _tushare_daily_metrics()
    + _paid_daily_metrics()
    + _simple("index_bars", "tushare.index_daily", _INDEX_MEANING,
              MetricStatus.ACTIVE, ("B1.calendar", "B5.span"))
    + _simple("minute_bars", "qmt.minute", _MINUTE_MEANING,
              MetricStatus.ACTIVE, ("A3.minute_vs_daily",))
    + _simple("auction_ticks", "qmt.tick", _AUCTION_MEANING,
              MetricStatus.ACTIVE, ("B4.arrival",))
    + _simple("bars_daily", "mootdx.kline", _BARS_MEANING,
              MetricStatus.FROZEN, _FROZEN_CHECKS)
)
