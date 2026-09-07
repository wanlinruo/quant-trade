"""一次性探针：打印并保存外部数据源的真实返回结构。

单元测试不触网，一律读 tests/fixtures/ 下的录制样本。
本脚本手动运行，用于建立或刷新这些样本。

每项独立捕获异常——免费源接口会变，一项失败不应中断整轮探测。

用法：uv run python scripts/record_fixtures.py
"""

from __future__ import annotations

import traceback
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def dump(name: str, fn) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    print(f"\n{'=' * 70}\n=== {name}\n{'=' * 70}")
    try:
        frame = fn()
    except Exception:
        print("!!! FAILED")
        traceback.print_exc(limit=3)
        return
    if frame is None or len(frame) == 0:
        print("!!! EMPTY RESULT")
        return
    path = FIXTURES / f"{name}.csv"
    frame.to_csv(path, index=False)
    print(f"saved -> {path}  ({len(frame)} rows)")
    print("columns:", list(frame.columns))
    print("dtypes:")
    print(frame.dtypes)
    print("head:")
    print(frame.head(5).to_string())


def main() -> None:
    import akshare as ak

    dump("ak_trade_dates", lambda: ak.tool_trade_date_hist_sina())
    dump("ak_code_name", lambda: ak.stock_info_a_code_name())
    dump(
        "ak_daily_600036",
        lambda: ak.stock_zh_a_hist(
            symbol="600036", period="daily",
            start_date="20240101", end_date="20240131", adjust="",
        ),
    )
    dump("ak_dividend_600036", lambda: ak.stock_fhps_detail_em(symbol="600036"))
    dump("ak_delist_sh", lambda: ak.stock_info_sh_delist())
    dump("ak_delist_sz", lambda: ak.stock_info_sz_delist())

    def mootdx_daily():
        from mootdx.quotes import Quotes

        client = Quotes.factory(market="std")
        return client.bars(symbol="600036", frequency=9, offset=30)

    dump("mootdx_daily_600036", mootdx_daily)


if __name__ == "__main__":
    main()
