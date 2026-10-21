"""D-7：竞价 tick 采集失败必须让归档以失败退出，而不是只打日志。"""

from __future__ import annotations

from argparse import Namespace
import json
import sys
from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from qsys.data.pipeline.auction import ImportResult
from qsys.data import schema
from qsys.data import daily_archive_cli as daily_archive

DAY = date(2026, 9, 16)
ARGS = Namespace(win_root="C:/qsys/daily", win_host="Administrator@win",
                 win_python="python", win_auction_script="auction.py", work_root="var")
WANTED = pl.DataFrame({"symbol": ["600036.SH"], "dt": [DAY]})


def _archive(monkeypatch, tmp_path, *, missing=WANTED, run=None, imported=ImportResult(1, 10, 0)):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(daily_archive, "missing_auction_days", lambda wh, wanted: missing)
    monkeypatch.setattr(daily_archive, "run", run or (lambda *a, **k: "完成"))
    monkeypatch.setattr(daily_archive, "import_auction_ticks", lambda *a, **k: imported)
    return daily_archive._archive_auction(ARGS, None, WANTED, ["ssh"], "key", "20260916")


def test_nothing_missing_is_ok(monkeypatch, tmp_path):
    assert _archive(monkeypatch, tmp_path, missing=WANTED.clear()) is True


def test_successful_download_and_import_is_ok(monkeypatch, tmp_path):
    assert _archive(monkeypatch, tmp_path) is True


def test_windows_failure_is_reported(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("Windows 下载竞价 tick 失败 (rc=1)")
    assert _archive(monkeypatch, tmp_path, run=boom) is False


def test_import_errors_are_reported(monkeypatch, tmp_path):
    assert _archive(monkeypatch, tmp_path, imported=ImportResult(1, 0, 1)) is False


def test_auction_failure_turns_success_into_failure():
    assert daily_archive._exit_code(0, auction_ok=False) == 1
    assert daily_archive._exit_code(0, auction_ok=True) == 0
    assert daily_archive._exit_code(1, auction_ok=True) == 1


def test_minute_manifest_includes_paper_holdings(tmp_path):
    import sqlite3

    store = tmp_path / "workbench.sqlite3"
    with sqlite3.connect(store) as conn:
        conn.execute("CREATE TABLE paper_fills (id INTEGER PRIMARY KEY, account_id TEXT, "
                     "order_id TEXT, symbol TEXT, side TEXT, qty INTEGER, price REAL, "
                     "fee REAL, filled_at TEXT)")
        conn.execute("INSERT INTO paper_fills (account_id, order_id, symbol, side, qty, price, "
                     "fee, filled_at) VALUES ('a', 'o1', '001399.SZ', 'buy', 2000, 23.84, "
                     "14.78, '2026-09-15T09:30:03')")
    fills = daily_archive._paper_fills(store)
    held = daily_archive.held_stock_days(fills, DAY, DAY, [DAY])
    wanted = daily_archive._minute_wanted(WANTED.clear(), WANTED, held)
    assert sorted(wanted["symbol"].to_list()) == ["001399.SZ", "600036.SH"]


def test_late_listing_convergence_can_restrict_the_minute_manifest():
    wanted = pl.DataFrame({
        "symbol": ["600036.SH", "920157.BJ", "300001.SZ"],
        "dt": [DAY, DAY, DAY],
    })
    restricted, requested = daily_archive._restrict_minute_symbols(
        wanted, " 920157.BJ,300001.SZ,920157.BJ "
    )
    assert requested == {"920157.BJ", "300001.SZ"}
    assert restricted["symbol"].to_list() == ["920157.BJ", "300001.SZ"]
    unchanged, requested = daily_archive._restrict_minute_symbols(wanted, "")
    assert requested == set()
    assert unchanged.equals(wanted)


def test_missing_paper_store_is_not_an_error(tmp_path):
    assert daily_archive._paper_fills(tmp_path / "absent.sqlite3").height == 0


def test_full_market_stock_days_respect_board_and_listing_boundaries():
    instruments = pl.DataFrame({
        "symbol": ["600001.SH", "300001.SZ", "920001.BJ", "200001.SZ"],
        "board": ["main", "gem", "bse", "b_share"],
        "list_date": [date(2026, 9, 15), date(2026, 9, 17), None, None],
        "delist_date": [None, None, date(2026, 9, 17), None],
    })
    got = daily_archive._full_market_stock_days(
        instruments,
        [date(2026, 9, 16), date(2026, 9, 17)],
        {"main", "gem", "bse"},
    )
    # 920001.BJ 在 delist_date（9-17）当天不交易，不进分钟采集清单。
    assert got.rows() == [
        ("600001.SH", date(2026, 9, 16)),
        ("920001.BJ", date(2026, 9, 16)),
        ("300001.SZ", date(2026, 9, 17)),
        ("600001.SH", date(2026, 9, 17)),
    ]


def test_delist_day_is_excluded_from_minute_manifest():
    """2026-10-19 现场：601059/601198 当日退市，QMT 空结果曾被判为未知空。"""
    instruments = pl.DataFrame({
        "symbol": ["601059.SH", "601198.SH", "600000.SH"],
        "board": ["main", "main", "main"],
        "list_date": [date(2023, 2, 1), date(2015, 2, 26), None],
        "delist_date": [date(2026, 10, 19), date(2026, 10, 19), None],
    })
    got = daily_archive._full_market_stock_days(
        instruments, [date(2026, 10, 16), date(2026, 10, 19)], {"main"},
    )
    assert got.rows() == [
        ("600000.SH", date(2026, 10, 16)),
        ("601059.SH", date(2026, 10, 16)),
        ("601198.SH", date(2026, 10, 16)),
        ("600000.SH", date(2026, 10, 19)),
    ]


def test_no_missing_rerun_republishes_the_whole_wanted_range():
    wanted = pl.DataFrame({
        "symbol": ["600001.SH", "600001.SH"],
        "dt": [date(2026, 9, 21), date(2026, 9, 30)],
    })
    assert daily_archive._wanted_date_range(wanted, DAY) == (
        date(2026, 9, 21), date(2026, 9, 30)
    )


def test_known_minute_difference_does_not_hide_blocking_issues():
    from qsys.data.quality.minute_rules import MinuteIssue

    known = MinuteIssue("920055.BJ", date(2026, 9, 21), "high_vs_daily", "x", "D-24")
    blocking = MinuteIssue("600001.SH", DAY, "high_vs_daily", "y")
    got_blocking, got_known = daily_archive._split_minute_issues([known, blocking])
    assert got_blocking == [blocking]
    assert got_known == [known]


def test_shared_minute_gate_blocks_unknown_but_not_d24(monkeypatch):
    from qsys.data.quality.minute_rules import MinuteIssue

    known = MinuteIssue("920055.BJ", date(2026, 9, 21), "high_vs_daily", "x", "D-24")
    blocking = MinuteIssue("600001.SH", DAY, "high_vs_daily", "y")
    monkeypatch.setattr(
        daily_archive, "check_minute_bars",
        lambda warehouse, day: [known, blocking],
    )

    class Run:
        def __init__(self):
            self.steps = []

        def add_step(self, *args, **kwargs):
            self.steps.append((args, kwargs))

    run = Run()
    assert daily_archive._check_minute_days(None, [DAY], run) == 1
    assert run.steps[0][1]["status"] == daily_archive.FAILED
    assert "已知差异 1" in run.steps[0][1]["detail"]


def test_structured_minute_progress_is_parsed_and_human_readable():
    payload = {
        "total_stock_days": 5572, "completed_stock_days": 2500,
        "downloaded_stock_days": 2490, "empty_stock_days": 10,
        "failed_symbols": 0, "parquet_files": 3,
        "last_progress_at": "2026-10-08T16:01:02+08:00",
        "estimated_remaining_seconds": 720,
    }
    got = daily_archive.parse_minute_progress(
        daily_archive.PROGRESS_PREFIX + json.dumps(payload)
    )
    assert got == payload
    detail = daily_archive.minute_progress_detail(got)
    assert "2,500/5,572" in detail and "预计剩余 12 分钟" in detail
    assert daily_archive.parse_minute_progress("普通日志") is None
    assert daily_archive.parse_minute_progress("QSYS_PROGRESS {") is None


def test_empty_results_are_split_by_authoritative_full_day_suspensions(warehouse):
    import pyarrow as pa

    warehouse.write("suspend_daily", pa.table({
        "symbol": ["600001.SH", "600002.SH", "600003.SH"],
        "dt": [DAY, DAY, DAY],
        "suspend_type": ["S", "S", "R"],
        "suspend_timing": [None, "10:00-10:30", None],
    }, schema=schema.SUSPEND_DAILY), year=DAY.year)
    authoritative, unknown = daily_archive.classify_minute_empties(warehouse, [
        {"symbol": "600001.SH", "dt": "20260916"},
        {"symbol": "600002.SH", "dt": "20260916"},
        {"symbol": "600003.SH", "dt": "20260916"},
        {"symbol": "600004.SH", "dt": "20260916"},
    ])
    assert authoritative == [("600001.SH", DAY)]
    assert unknown == [
        ("600002.SH", DAY), ("600003.SH", DAY), ("600004.SH", DAY),
    ]


def test_streaming_command_forwards_progress_without_waiting_for_exit(tmp_path):
    payload = {
        "total_stock_days": 10, "completed_stock_days": 5,
        "empty_stock_days": 0, "failed_symbols": 0, "parquet_files": 1,
        "last_progress_at": "2026-10-08T16:00:00+08:00",
        "estimated_remaining_seconds": 10,
    }
    seen = []
    result = daily_archive.run_streaming(
        [sys.executable, "-c", (
            "import json; print('QSYS_PROGRESS ' + json.dumps(" + repr(payload) + "), flush=True)"
        )],
        what="test", timeout=10, stall_timeout=5,
        on_line=lambda line: seen.append(daily_archive.parse_minute_progress(line)) or True,
    )
    assert result.returncode == 0
    assert seen == [payload]


def test_streaming_command_distinguishes_a_stall_from_process_failure():
    result = daily_archive.run_streaming(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        what="test", timeout=10, stall_timeout=1,
        on_line=lambda line: False,
    )
    assert result.returncode == 125
    assert "没有进度" in result.error


# #440：整轮编排保留真实交易日历和候选选择器，外部 I/O 用替身。
@pytest.mark.parametrize("as_of,closed", [
    ("2026-10-20T00:45:00", "2026-10-19"),
    ("2026-10-20T09:00:00", "2026-10-19"),
    ("2026-10-20T14:59:59", "2026-10-19"),
    ("2026-10-20T15:00:00", "2026-10-20"),
    ("2026-10-20T16:00:00", "2026-10-20"),
    ("2026-10-24T00:45:00", "2026-10-23"),
])
def test_archive_only_requests_closed_sessions(monkeypatch, tmp_path, as_of, closed):
    import pyarrow as pa
    from qsys.core.calendar import AShareCalendar
    from qsys.data.storage import Warehouse

    now = datetime.fromisoformat(as_of)
    last_closed = date.fromisoformat(closed)
    sessions = [date(2026, 10, d) for d in (16, 19, 20, 21, 22, 23, 26)]

    class Clock:
        @staticmethod
        def now():
            return now

    warehouse = Warehouse(tmp_path / "warehouse")
    # 每个日子都给一个首板，确保上界错误会产生真实的次日候选。
    rows = [{"symbol": "600001.SH", "dt": day, "is_first_board": True}
            for day in sessions]
    warehouse.write("daily_flags", pa.Table.from_pylist(rows, schema=schema.DAILY_FLAGS),
                    year=2026)
    instruments = pl.DataFrame({
        "symbol": ["600001.SH"], "board": ["main"],
        "list_date": [date(2020, 1, 1)],
        "delist_date": pl.Series([None], dtype=pl.Date),
    })
    monkeypatch.setattr(daily_archive, "datetime", Clock)
    monkeypatch.setattr(daily_archive, "load_trading_calendar",
                        lambda _: AShareCalendar(sessions))
    monkeypatch.setattr(daily_archive, "load_tushare_config",
                        lambda _: SimpleNamespace(token="test"))
    monkeypatch.setattr(daily_archive, "sync_premarket_suspensions",
                        lambda *a: SimpleNamespace(row_count=0, detail="test", accepted=True))
    monkeypatch.setattr(daily_archive.WarehouseQuery, "instruments", lambda _: instruments)
    monkeypatch.setattr(daily_archive, "_paper_fills", lambda _: pl.DataFrame({
        "account_id": ["test"], "symbol": ["600002.SH"], "side": ["buy"],
        "qty": [100], "filled_at": ["2026-10-16T09:30:00"],
    }))
    # 真实候选/持仓/全市场清单均参与合并；只隔离远端采集与质检发布 I/O。
    monkeypatch.setattr(daily_archive, "drop_suspended", lambda _, wanted: wanted)
    captures = {}

    def auction(args, wh, wanted, *unused):
        captures["auction"] = wanted
        return True

    def missing(wh, wanted):
        captures["minute"] = wanted
        return wanted.clear()

    monkeypatch.setattr(daily_archive, "_archive_auction", auction)
    monkeypatch.setattr(daily_archive, "missing_stock_days", missing)
    monkeypatch.setattr(daily_archive, "_check_minute_days", lambda *a: 0)
    monkeypatch.setattr(daily_archive, "_publish_canonical_minutes",
                        lambda wh, start, end, run: captures.update(publish=(start, end)))
    assert daily_archive.main([
        "--warehouse", str(warehouse.root), "--config", "unused.toml",
        "--record-root", str(tmp_path / "records"),
        "--paper-store", str(tmp_path / "absent.sqlite3"),
    ]) == 0
    for kind in ("auction", "minute"):
        days = captures[kind]["dt"].to_list()
        assert days
        assert max(days) == last_closed
        assert all(day <= last_closed for day in days)
    held_days = captures["minute"].filter(pl.col("symbol") == "600002.SH")["dt"].to_list()
    assert max(held_days) == last_closed
    assert captures["publish"][1] == last_closed


def test_archive_without_closed_session_skips_all_collection(monkeypatch, tmp_path):
    from qsys.core.calendar import AShareCalendar

    class Clock:
        @staticmethod
        def now():
            return datetime(2026, 10, 20, 0, 45)

    monkeypatch.setattr(daily_archive, "datetime", Clock)
    monkeypatch.setattr(daily_archive, "load_trading_calendar",
                        lambda _: AShareCalendar([date(2026, 10, 20)]))
    # 没有已收盘交易日时不得访问数据源或创建仓库分区。
    def unexpected(*args, **kwargs):
        raise AssertionError("不应采集")
    monkeypatch.setattr(daily_archive, "load_tushare_config", unexpected)
    assert daily_archive.main([
        "--warehouse", str(tmp_path / "warehouse"),
        "--record-root", str(tmp_path / "records"),
    ]) == 0
    assert not (tmp_path / "warehouse").exists()


@pytest.mark.parametrize('transfer_fails', [False, True])
def test_gateway_minute_flow_keeps_progress_protocol_and_blocks_bad_transfer(
        monkeypatch, tmp_path, transfer_fails):
    from qsys.core.calendar import AShareCalendar
    day=date(2026,10,21)
    class Clock:
        @staticmethod
        def now():return datetime(2026,10,21,20)
    monkeypatch.setattr(daily_archive,'datetime',Clock)
    monkeypatch.setattr(daily_archive,'load_trading_calendar',lambda _:AShareCalendar([day]))
    monkeypatch.setattr(daily_archive,'load_tushare_config',lambda _:SimpleNamespace(token='test'))
    monkeypatch.setattr(daily_archive,'sync_premarket_suspensions',
                        lambda *a:SimpleNamespace(row_count=0,detail='test',accepted=True))
    instruments=pl.DataFrame({'symbol':['600036.SH'],'board':['main'],
        'list_date':[date(2020,1,1)],'delist_date':pl.Series([None],dtype=pl.Date)})
    wanted=pl.DataFrame({'symbol':['600036.SH'],'dt':[day]})
    monkeypatch.setattr(daily_archive.WarehouseQuery,'instruments',lambda _:instruments)
    monkeypatch.setattr(daily_archive,'candidate_stock_days',lambda *a,**k:wanted)
    monkeypatch.setattr(daily_archive,'_paper_fills',lambda _:pl.DataFrame())
    monkeypatch.setattr(daily_archive,'held_stock_days',lambda *a:wanted.clear())
    monkeypatch.setattr(daily_archive,'drop_suspended',lambda q,w:w)
    monkeypatch.setattr(daily_archive,'missing_stock_days',lambda q,w:w)
    monkeypatch.setattr(daily_archive,'run',lambda *a,**k:pytest.fail('legacy transport called'))
    captured={}
    class Transport:
        def __init__(self,host,key,**kw):
            assert kw=={'component':'tushare-sync','known_hosts':'trusted-hosts'}
        def probe(self):return {'protocol':1,'component':'tushare-sync'}
        def prepare(self,job,manifest):
            assert job=='minute_20261021'
            assert '600036.SH,2026-10-21' in manifest.read_text()
        def download_command(self,job,script,run_id,start,end):
            captured['run_id']=run_id
            assert start==end==day and script.is_file()
            return ['fixed-data-command']
        def receive(self,job,destination):
            if transfer_fails:raise ValueError('output hash mismatch')
            (destination/'result.json').write_text(json.dumps({
                'schema_version':1,'run_id':captured['run_id'],'empty':[],'failed':[],
                'total_stock_days':1,'completed_stock_days':1,'downloaded_stock_days':1,
                'empty_stock_days':0,'failed_symbols':0,'parquet_files':1,
                'last_progress_at':'2026-10-21T20:00:00','estimated_remaining_seconds':0}))
            return 1
    monkeypatch.setattr(daily_archive,'WindowsDataTransport',Transport)
    def stream(command,**kw):
        assert command==['fixed-data-command']
        return daily_archive.StreamResult(0,'download complete')
    monkeypatch.setattr(daily_archive,'run_streaming',stream)
    monkeypatch.setattr(daily_archive,'import_minute_bars',lambda *a,**k:ImportResult(1,241,0))
    monkeypatch.setattr(daily_archive,'_check_minute_days',lambda *a:0)
    published=[]
    monkeypatch.setattr(daily_archive,'_publish_canonical_minutes',lambda *a:published.append(True))
    source=tmp_path/'approved.py';source.write_text('approved')
    result=daily_archive.main(['--warehouse',str(tmp_path/'warehouse'),'--record-root',str(tmp_path/'records'),
        '--work-root',str(tmp_path/'work'),'--config','unused','--lookback-days','0','--skip-auction',
        '--win-transport','gateway','--win-component','tushare-sync','--win-known-hosts','trusted-hosts',
        '--win-script-source',str(source)])
    assert result==(1 if transfer_fails else 0)
    assert bool(published) is not transfer_fails
