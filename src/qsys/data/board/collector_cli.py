"""打板看板盘中采集器（#648 P2，常驻服务 ``qsys-limit-up-board``）。

- 盘前（08:30 起）：拉取同花顺概念快照与当日 ST 名单（Tushare 直取）；待盘前任务把当日
  stk_limit 写入 warehouse 后，连同昨日连板高度一次性落入看板库。之后当日输入不再变化。
- 盘中（09:15–15:05）：只读 Redis ``state:quote_realtime_latest``，按 ``published_at`` 去重，
  先写当日临时全量，再推进状态机并保存事件与情绪聚合。重启后用临时全量重放恢复状态。
- 收盘后（15:10 起）：压缩为入选票全日序列，校验通过才删除临时全量。
- 盘后（17:30–23:30）：当日 ``limit_list_d`` 入库后对账，只记差异，不改写盘中观测。

只读 Redis 与 warehouse，只写本组件状态目录。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time as time_mod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import structlog

from qsys.data.board.concepts import ConceptSnapshot, build_snapshot
from qsys.data.board.ladder import previous_heights, today_height
from qsys.data.board.live import BoardDay
from qsys.data.board.reconcile import Observed, reconcile
from qsys.data.board.store import BoardStore
from qsys.live.messages import QuoteSnapshot

log = structlog.get_logger("limit-up-board")
TZ = ZoneInfo("Asia/Shanghai")

CONCEPTS_FROM = time(8, 30)
COLLECT_FROM, COLLECT_UNTIL = time(9, 15), time(15, 5)
COMPACT_FROM = time(15, 10)
RECONCILE_FROM, RECONCILE_UNTIL = time(17, 30), time(23, 30)
RECONCILE_RETRY = timedelta(minutes=10)
MEMBER_PAUSE_S = 0.35


@contextmanager
def shared_lock(path: Path | None, timeout_s: float = 120.0) -> Iterator[None]:
    """读 warehouse 时短暂持有共享锁，避免读到写入中的分区；不长期占锁阻塞写入方。"""
    if path is None:
        yield
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        deadline = time_mod.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time_mod.monotonic() >= deadline:
                    raise TimeoutError("warehouse 写锁长时间被占用") from None
                time_mod.sleep(1.0)
        yield
    finally:
        os.close(fd)


def fetch_concepts(provider, day: date, now: Callable[[], datetime],
                   pause: Callable[[float], None] = time_mod.sleep,
                   ) -> tuple[ConceptSnapshot, dict[str, str]]:
    index = provider.ths_index()
    if index.is_empty():
        raise RuntimeError("ths_index 返回空")
    concepts = index.filter(pl.col("type") == "N")
    members = {}
    for code in concepts["ts_code"].to_list():
        pause(MEMBER_PAUSE_S)
        frame = provider.ths_member(code)
        if frame.is_empty():
            # 空成分是真实事实（新建概念等）还是接口失败无法区分；按空集合保存并留 count 对照。
            frame = pl.DataFrame({"con_code": []}, schema={"con_code": pl.String})
        members[code] = frame
    names = dict(zip(concepts["ts_code"].to_list(), concepts["name"].to_list(), strict=True))
    return build_snapshot(day, index, members, now()), names


@dataclass
class Collector:
    store: BoardStore
    query: object  # WarehouseQuery
    provider: object  # TushareProvider
    transport: object  # 只读 Transport（get_key）
    quote_key: str
    lock: Path | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(TZ).replace(tzinfo=None))
    pause: Callable[[float], None] = time_mod.sleep
    board: BoardDay | None = None
    concepts: dict[date, tuple[ConceptSnapshot | None, dict[str, str], str | None]] = field(
        default_factory=dict)
    st: dict[date, set[str] | None] = field(default_factory=dict)
    next_reconcile: datetime | None = None
    status: dict = field(default_factory=dict)

    # ---- 日历 ----
    def sessions(self, day: date) -> list[date]:
        with shared_lock(self.lock):
            cal = self.query.trading_calendar(day - timedelta(days=60), day, open_only=True)
        return sorted(cal["dt"].to_list())

    # ---- 盘前 ----
    def prepare(self, day: date, sessions: list[date]) -> bool:
        if day not in self.concepts:
            try:
                snap, names = fetch_concepts(self.provider, day, self.clock, self.pause)
                self.concepts[day] = (snap, names, None)
            except Exception as exc:
                self.concepts[day] = (None, {}, f"{type(exc).__name__}: {exc}")
        if day not in self.st:
            try:
                frame = self.provider.stock_st(day)
                self.st[day] = set(frame["ts_code"].to_list()) if not frame.is_empty() else None
            except Exception:
                self.st[day] = None
        with shared_lock(self.lock):
            limits_frame = self.query.price_limits(None, day, day)
            if limits_frame.is_empty():
                return False  # 等盘前任务写入当日直取涨跌停价
            limits = {r["symbol"]: (r["limit_up"], r["limit_down"])
                      for r in limits_frame.iter_rows(named=True)}
            prev_day = sessions[-2] if len(sessions) >= 2 else None
            heights, heights_error = None, None
            if prev_day is None:
                heights_error = "交易日历缺前一交易日"
            else:
                start = sessions[0]
                flags = self.query.daily_flags(start, prev_day)
                limit_list = self.query.limit_list(None, start, prev_day)
                if flags.filter(pl.col("dt") == prev_day).is_empty():
                    heights_error = f"{prev_day} daily_flags 未入库"
                elif limit_list.filter(pl.col("dt") == prev_day).is_empty():
                    heights_error = f"{prev_day} limit_list_d 未入库"
                else:
                    heights = previous_heights(prev_day, limit_list, flags, sessions)
        snap, names, concept_error = self.concepts[day]
        self.store.save_preparation(
            day, prev_day=prev_day, limits=limits, prev_heights=heights,
            heights_error=heights_error, st_symbols=self.st[day], concepts=snap,
            concept_names=names, concept_error=concept_error, prepared_at=self.clock())
        log.info("board.prepared", day=str(day), limits=len(limits),
                 concepts=None if snap is None else len(snap.members),
                 concept_error=concept_error, heights_error=heights_error)
        return True

    # ---- 恢复 ----
    def restore(self, day: date) -> BoardDay:
        board = self.store.load_board_day(day)
        last = self.store.last_published(day)
        if self.store.raw_path(day).exists():
            for published_at, quotes in self.store.raw_snapshots(day):
                got = board.ingest(published_at, quotes)
                if got is not None and (last is None or published_at > last):
                    # 全量已落盘而派生结果未保存（上次在两步之间退出）：补存同一派生结果。
                    self.store.save_step(day, published_at, got[0], got[1], board.tracked)
        return board

    # ---- 盘中 ----
    def collect(self, day: date) -> bool:
        raw = self.transport.get_key(self.quote_key)
        if not raw:
            return False
        snapshot = QuoteSnapshot.model_validate_json(raw)
        board = self.board
        assert board is not None
        published = snapshot.published_at.replace(tzinfo=None)
        if published.date() != day or (board.last_published is not None
                                        and published <= board.last_published):
            return False
        self.store.append_raw(day, published, snapshot.quotes)
        got = board.ingest(published, snapshot.quotes)
        if got is None:
            return False
        self.store.save_step(day, published, got[0], got[1], board.tracked)
        self.status.update(last_published=published.isoformat(), quotes=len(snapshot.quotes),
                           sealed=got[1].sealed)
        return True

    # ---- 盘后对账 ----
    def reconcile_day(self, day: date, now: datetime) -> bool:
        with shared_lock(self.lock):
            direct = self.query.limit_list(None, day, day)
        if direct.is_empty():
            return False
        board = self.board if self.board is not None and self.board.day == day \
            else self.store.load_board_day(day)
        observed = []
        for symbol, first_seal, open_times, ever_sealed in self.store.board_rows(day):
            prev = None if board.prev_heights is None else board.prev_heights.get(symbol)
            observed.append(Observed(
                symbol, today_height(prev, bool(ever_sealed)),
                datetime.fromisoformat(first_seal) if first_seal else None, open_times))
        self.store.save_reconcile(day, reconcile(observed, direct), now)
        return True

    # ---- 主循环一步 ----
    def step(self) -> None:
        now = self.clock()
        day, clock = now.date(), now.time()
        self.status = {**self.status, "loop_at": now.isoformat(timespec="seconds"),
                       "day": day.isoformat(), "error": None}
        if clock < CONCEPTS_FROM:
            return
        sessions = self.sessions(day)
        if not sessions or sessions[-1] != day:
            self.status["phase"] = "closed"
            return
        if not self.store.prepared(day):
            self.status["phase"] = "preparing"
            if not self.prepare(day, sessions):
                return
        if self.board is None or self.board.day != day:
            self.board = self.restore(day)
            self.next_reconcile = None
        if COLLECT_FROM <= clock <= COLLECT_UNTIL:
            self.status["phase"] = "collecting"
            self.collect(day)
        elif clock >= COMPACT_FROM and not self.store.compacted(day):
            self.status["phase"] = "compacting"
            error = self.store.compact(day, now)
            if error:
                log.error("board.compact_failed", day=str(day), error=error)
        elif RECONCILE_FROM <= clock <= RECONCILE_UNTIL and not self.store.reconciled(day):
            self.status["phase"] = "reconciling"
            if self.next_reconcile is None or now >= self.next_reconcile:
                self.next_reconcile = now + RECONCILE_RETRY
                self.reconcile_day(day, now)
        else:
            self.status["phase"] = "idle"

    def write_status(self) -> None:
        path = self.store.root / "status.json"
        tmp = path.with_name("status.json.tmp")
        tmp.write_text(json.dumps(self.status, ensure_ascii=False, default=str))
        tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--warehouse-lock", type=Path, default=None)
    parser.add_argument("--poll", type=float, default=5.0)
    args = parser.parse_args()

    from qsys.data.providers.tushare import TushareProvider
    from qsys.data.providers.tushare_client import TushareClient
    from qsys.data.query import WarehouseQuery
    from qsys.data.storage import Warehouse
    from qsys.live.config import load_deploy_config, load_tushare_config
    from qsys.live.readonly_transport import readonly_from_config

    cfg = load_deploy_config(args.config)
    args.state_root.mkdir(parents=True, exist_ok=True)
    collector = Collector(
        store=BoardStore(args.state_root),
        query=WarehouseQuery(Warehouse(args.warehouse)),
        provider=TushareProvider(TushareClient(load_tushare_config(args.config).token)),
        transport=readonly_from_config(cfg),
        quote_key=cfg.streams.quote_latest,
        lock=args.warehouse_lock,
    )
    log.info("board.started")
    while True:
        try:
            collector.step()
        except Exception as exc:  # 常驻：单步失败记录后继续，不伪造数据
            collector.status["error"] = f"{type(exc).__name__}: {exc}"
            log.error("board.step_failed", error=collector.status["error"])
        try:
            collector.write_status()
        except OSError as exc:
            log.error("board.status_failed", error=str(exc))
        time_mod.sleep(max(1.0, args.poll))


if __name__ == "__main__":
    sys.exit(main())
