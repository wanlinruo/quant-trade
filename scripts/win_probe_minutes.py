"""Windows 侧探针：实测 xtdata 分钟线的真实形状与吞吐。

**只在 Windows 上运行。** 结果记录到 docs/deploy/windows-node.md，
再据此写导出逻辑——不要凭猜测：时间戳约定差一分钟，整个 PIT 契约就是错的。

必须回答三个问题：
  1. 时间戳是该分钟的开盘时刻还是收盘时刻？
     仓库约定是**收盘时刻**（09:30–09:31 这根记 09:31）。
     若 xtdata 给 09:30，导出时必须 +1 分钟。
  2. 集合竞价成交量归在哪根？折进首根，还是单列一根 09:25？
  3. 单次 download_history_data 要多久？
     × 41,592 组 = 全量估时，决定要不要改下载策略。

用法（Windows PowerShell）：
  python scripts\\win_probe_minutes.py --symbols 600036.SH,000001.SZ --day 2024-01-04
"""

from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="xtdata 分钟线探针")
    parser.add_argument("--symbols", required=True, help="逗号分隔，如 600036.SH")
    parser.add_argument("--day", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    from xtquant import xtdata

    codes = [s.strip() for s in args.symbols.split(",") if s.strip()]
    stamp = args.day.replace("-", "")
    timings: list[float] = []

    for code in codes:
        print(f"\n=== {code} {args.day} ===")
        started = time.time()
        xtdata.download_history_data(
            code, period="1m", start_time=stamp, end_time=stamp
        )
        elapsed = time.time() - started
        timings.append(elapsed)
        print(f"download_history_data 耗时 {elapsed:.2f}s")

        data = xtdata.get_market_data(
            field_list=[],
            stock_list=[code],
            period="1m",
            start_time=stamp,
            end_time=stamp,
            dividend_type="none",
        )
        print("返回类型:", type(data))
        if isinstance(data, dict):
            print("字段:", list(data.keys()))
            for field, frame in data.items():
                print(f"\n--- 字段 {field} ---")
                print("index（标的）:", list(frame.index)[:3])
                columns = list(frame.columns)
                print(f"列数 {len(columns)}")
                print(f"前 5 列（时间戳）: {columns[:5]}")
                print(f"后 3 列: {columns[-3:]}")
                print(frame.iloc[:, :5].head(3))
                break

    if timings:
        average = sum(timings) / len(timings)
        print(f"\n平均单次下载 {average:.2f}s")
        print(f"41,592 组估时约 {average * 41592 / 3600:.1f} 小时")

    print("\n看三点：")
    print("  1. 首个时间戳是 09:30 还是 09:31 —— 决定导出时是否 +1 分钟")
    print("  2. 有没有单独的 09:25 竞价 bar")
    print("  3. 列数应为 240（若含竞价单列则 241）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
