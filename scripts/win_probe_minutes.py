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
    parser.add_argument("--depth-days", type=int, default=0,
                        help="另测 N 天前的同一标的，用于摸历史深度")
    args = parser.parse_args()

    import pandas as pd
    from xtquant import xtdata

    codes = [s.strip() for s in args.symbols.split(",") if s.strip()]
    stamp = args.day.replace("-", "")
    first_timings: list[float] = []
    repeat_timings: list[float] = []

    for code in codes:
        print(f"\n=== {code} {args.day} ===")
        started = time.time()
        xtdata.download_history_data(code, period="1m", start_time=stamp, end_time=stamp)
        first = time.time() - started
        first_timings.append(first)

        started = time.time()
        xtdata.download_history_data(code, period="1m", start_time=stamp, end_time=stamp)
        repeat = time.time() - started
        repeat_timings.append(repeat)
        print(f"download 首次 {first:.2f}s / 重复 {repeat:.2f}s")

        data = xtdata.get_market_data(
            field_list=[], stock_list=[code], period="1m",
            start_time=stamp, end_time=stamp, dividend_type="none",
        )
        if not isinstance(data, dict):
            print("返回不是 dict:", type(data))
            continue

        print("字段:", list(data.keys()))
        frame = data.get("close")
        if frame is None:
            print("没有 close 字段")
            continue

        columns = list(frame.columns)
        print(f"列数(bar 根数): {len(columns)}")
        print(f"前 4 个时间戳: {columns[:4]}")
        print(f"后 3 个时间戳: {columns[-3:]}")

        volume = data.get("volume")
        if volume is not None:
            head = volume.iloc[0].head(3).tolist()
            print(f"首 3 根成交量: {head}")
            print(f"当日成交量合计: {int(volume.iloc[0].sum())}")
        opens = data.get("open")
        if opens is not None:
            print(f"首根 open: {opens.iloc[0].iloc[0]}")
            print(f"末根 close: {frame.iloc[0].iloc[-1]}")

    if args.depth_days:
        from datetime import datetime as _dt, timedelta as _td

        old_day = (_dt.strptime(args.day, "%Y-%m-%d") - _td(days=args.depth_days))
        old_stamp = old_day.strftime("%Y%m%d")
        code = codes[0]
        print(f"\n=== 历史深度探测 {code} {old_day.date()} ===")
        xtdata.download_history_data(code, period="1m",
                                     start_time=old_stamp, end_time=old_stamp)
        data = xtdata.get_market_data(
            field_list=[], stock_list=[code], period="1m",
            start_time=old_stamp, end_time=old_stamp, dividend_type="none",
        )
        frame = data.get("close") if isinstance(data, dict) else None
        n = 0 if frame is None else len(frame.columns)
        print(f"取到 {n} 根" + ("（无数据，历史深度不足）" if n == 0 else ""))

    if first_timings:
        avg_first = sum(first_timings) / len(first_timings)
        avg_repeat = sum(repeat_timings) / len(repeat_timings)
        print(f"\n平均 首次 {avg_first:.2f}s / 重复 {avg_repeat:.2f}s")
        print(f"41,592 组按首次耗时估约 {avg_first * 41592 / 3600:.1f} 小时")

    print("\n看三点：")
    print("  1. 首个时间戳是 09:30 还是 09:31 —— 决定导出时是否 +1 分钟")
    print("  2. 列数 240 为常规；241 说明竞价单列一根")
    print("  3. 首根成交量是否明显大于第二根 —— 大则说明竞价量折进了首根")
    return 0


if __name__ == "__main__":
    sys.exit(main())
