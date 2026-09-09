"""Windows 侧探针：实测 xtdata tick 数据在**集合竞价时段**的形状与代价。

**只在 Windows 上运行。** 结果记录到 docs/deploy/windows-node.md。

为什么要 tick：策略后续要把买点从 09:30 开盘前移到竞价时段（09:15–09:25），
用竞价过程中的虚拟开盘价与匹配量下限价单，规避开盘瞬间的滑点。
分钟线做不到这件事——09:25 之前根本没有分钟线，09:30 那根也只有一个价格点。

必须回答五个问题：
  1. 09:15–09:25 到底有没有 tick？几秒一笔？
     竞价撮合每 3 秒广播一次快照——若 xtdata 完整落盘，这段应有 ~200 笔。
     早于 09:20 的委托可撤，行情所也照发；09:20 后不可撤，虚拟开盘价才有意义。
  2. 每笔 tick 带哪些字段？关键是 askPrice/bidPrice/askVol/bidVol
     ——没有盘口，就只能拿到成交价，估不出限价单能不能成交。
  3. 竞价段最后一笔的虚拟开盘价是否等于当日 open？
     这条决定前移买点在回测里能不能复现。
  4. 历史能回溯多久？tick 通常只留最近若干月。
  5. 单只单日多大、下多久？决定全市场回补是否可行
     （C 盘只剩 13.5 GB，这是硬约束）。

返回形状（实测）：`get_market_data(period="tick")` 给的是
`{code: numpy 结构化数组}`，**不是 DataFrame**，字段见 TICK_FIELDS。
`time` 是**毫秒 epoch（UTC）**，+8 小时才是北京时间。
askPrice/bidPrice/askVol/bidVol 每笔是 5 档的 list。

用法（Windows PowerShell）：
  python C:\\qsys\\win_probe_ticks.py --symbols 600036.SH,000001.SZ --day 2026-09-08
  python C:\\qsys\\win_probe_ticks.py --symbols 600036.SH --day 2026-09-08 --depth-probe
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
AUCTION_START = "09:15:00"
AUCTION_END = "09:25:59"
# 09:20 起委托不可撤，此后的虚拟开盘价才是可交易的信号
NO_CANCEL_FROM = "09:20:00"


def _free_bytes() -> int:
    """C 盘剩余空间。QMT 有两个 userdata_mini 目录（实盘/模拟），不去猜是哪个——
    直接量盘面剩余的变化，谁写的都算进去。"""
    return shutil.disk_usage("C:\\").free


def _clock(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, CST).strftime("%H:%M:%S")


def _level1(cell) -> float:
    """askPrice/bidPrice 每笔是 5 档 list，取一档。"""
    try:
        return float(cell[0])
    except (TypeError, IndexError):
        return 0.0


def probe_day(xtdata, code: str, day: str) -> dict:
    """下一天的 tick，report 形状。返回给汇总用的指标。"""
    import numpy as np

    stamp = day.replace("-", "")
    print(f"\n=== {code} {day} ===")

    started = time.time()
    xtdata.download_history_data(code, period="tick", start_time=stamp, end_time=stamp)
    elapsed = time.time() - started

    data = xtdata.get_market_data(
        field_list=[], stock_list=[code], period="tick",
        start_time=stamp, end_time=stamp, dividend_type="none",
    )
    blank = {"code": code, "day": day, "ticks": 0, "auction": 0, "seconds": elapsed}
    if not isinstance(data, dict) or code not in data:
        print(f"返回形状异常: {type(data)}")
        return blank

    arr = data[code]
    print(f"download 耗时 {elapsed:.2f}s   tick 总数 {len(arr)}")
    if len(arr) == 0:
        return blank
    print(f"字段: {[n for n in arr.dtype.names]}")

    clocks = np.array([_clock(int(t)) for t in arr["time"]])
    print(f"首笔 {clocks[0]}   末笔 {clocks[-1]}")

    # —— 问题 1：竞价时段覆盖 ——
    mask = (clocks >= AUCTION_START) & (clocks <= AUCTION_END)
    auction, a_clocks = arr[mask], clocks[mask]
    print(f"\n[1] 竞价段 {AUCTION_START}~{AUCTION_END}：{len(auction)} 笔")
    if len(auction) == 0:
        print("    竞价段没有 tick —— 买点前移无法用历史数据回测")
        return {**blank, "ticks": len(arr)}

    print(f"    首笔 {a_clocks[0]}  末笔 {a_clocks[-1]}")
    gaps = np.diff(auction["time"]) / 1000
    if len(gaps):
        print(f"    间隔 中位数 {np.median(gaps):.0f}s  最小 {gaps.min():.0f}s  最大 {gaps.max():.0f}s")
    early = int((a_clocks < NO_CANCEL_FROM).sum())
    print(f"    09:15~09:20（可撤单）{early} 笔 / "
          f"09:20~09:25（不可撤，信号有效）{len(auction) - early} 笔")

    # —— 问题 2：盘口 ——
    print("\n[2] 竞价段盘口（虚拟开盘价 = 买一 = 卖一，量 = 匹配量，单位手）")
    print("    时刻      bid1    ask1   bidVol1  askVol1  lastPrice   volume")
    picks = [0, len(auction) // 4, len(auction) // 2, len(auction) - 2, len(auction) - 1]
    for i in sorted(set(p for p in picks if 0 <= p < len(auction))):
        r = auction[i]
        print(f"    {a_clocks[i]}  {_level1(r['bidPrice']):6.2f}  {_level1(r['askPrice']):6.2f}"
              f"  {_level1(r['bidVol']):7.0f}  {_level1(r['askVol']):7.0f}"
              f"  {r['lastPrice']:9.2f}  {r['volume']:7d}")

    # —— 问题 3：竞价末笔虚拟开盘价 vs 当日 open ——
    last = auction[-1]
    virtual = _level1(last["bidPrice"]) or float(last["lastPrice"])
    daily = xtdata.get_market_data(
        field_list=["open"], stock_list=[code], period="1d",
        start_time=stamp, end_time=stamp, dividend_type="none",
    )
    opened = None
    if isinstance(daily, dict) and "open" in daily:
        try:
            opened = float(daily["open"].iloc[0].iloc[0])
        except Exception:
            opened = None
    same = opened is not None and abs(virtual - opened) < 1e-6
    print(f"\n[3] 竞价末笔虚拟开盘价 {virtual}   日线 open {opened}   "
          f"{'一致' if same else '不一致'}")

    # —— 问题 5：体量 ——
    print(f"\n[5] 单只单日 {len(arr)} 笔")
    return {
        "code": code, "day": day, "ticks": len(arr),
        "auction": len(auction), "seconds": elapsed, "match": same,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="xtdata tick 竞价探针")
    parser.add_argument("--symbols", required=True, help="逗号分隔，如 600036.SH")
    parser.add_argument("--day", required=True, help="YYYY-MM-DD")
    parser.add_argument("--depth-probe", action="store_true",
                        help="按月粗探 tick 历史能回溯到哪天")
    args = parser.parse_args()

    from xtquant import xtdata

    before = _free_bytes()
    print(f"C 盘剩余 {before / 1024**3:.2f} GB")

    codes = [s.strip() for s in args.symbols.split(",") if s.strip()]
    stats = [probe_day(xtdata, code, args.day) for code in codes]

    # —— 问题 4：历史深度。按月粗探，够用了：只要知道能不能覆盖回测区间 ——
    depth = None
    if args.depth_probe:
        print("\n=== [4] 历史深度探测 ===")
        code = codes[0]
        anchor = date.fromisoformat(args.day)
        for months in (1, 3, 6, 12, 18, 24, 36, 48):
            probe = anchor - timedelta(days=30 * months)
            while probe.weekday() >= 5:
                probe -= timedelta(days=1)
            stamp = probe.strftime("%Y%m%d")
            xtdata.download_history_data(code, period="tick",
                                         start_time=stamp, end_time=stamp)
            got = xtdata.get_market_data(
                field_list=[], stock_list=[code], period="tick",
                start_time=stamp, end_time=stamp, dividend_type="none",
            )
            n = len(got[code]) if isinstance(got, dict) and code in got else 0
            print(f"  {probe} (T-{months}月): {n} 笔" + ("" if n else "  <- 已无数据"))
            if n == 0:
                break
            depth = (probe, months)

    grew = before - _free_bytes()
    print(f"\n落盘增量 {grew / 1024**2:.1f} MB  剩余 {_free_bytes() / 1024**3:.2f} GB")
    if grew > 0:
        per = grew / max(len(codes), 1)
        print(f"单只单日 约 {per / 1024**2:.2f} MB")
        print(f"全市场 5,400 只 × 1 天 估约 {per * 5400 / 1024**3:.1f} GB")
        print(f"全市场 × 242 天 估约 {per * 5400 * 242 / 1024**4:.2f} TB")

    ok = [s for s in stats if s["ticks"]]
    if ok:
        print(f"\n平均单只单日 download {sum(s['seconds'] for s in ok) / len(ok):.2f}s"
              f"   竞价段平均 {sum(s['auction'] for s in ok) / len(ok):.0f} 笔")
    if depth:
        print(f"tick 历史至少回溯到 {depth[0]}（T-{depth[1]}月）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
