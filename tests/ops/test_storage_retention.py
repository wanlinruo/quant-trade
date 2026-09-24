import os
from datetime import datetime, timedelta

from qsys.ops import storage as S

NOW = datetime(2026, 9, 21, 20, 0)


def _file(path, size=4096, age_days=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def _age_dir(path, age_days):
    stamp = (NOW - timedelta(days=age_days)).timestamp()
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            os.utime(os.path.join(dirpath, name), (stamp, stamp))
        os.utime(dirpath, (stamp, stamp))


def _by_name(items):
    return {item.path.name: item for item in items}


def test_hardlinked_copy_frees_only_its_own_inodes(tmp_path):
    base = tmp_path / "qsys"
    shared = _file(base / "warehouse/paid/part.parquet", size=64 * 1024)
    copy = base / "warehouse/paid.pre-x"
    copy.mkdir(parents=True)
    os.link(shared, copy / "part.parquet")           # 与 warehouse 共用
    _file(copy / "only-here.parquet", size=8 * 1024)  # 只在副本里

    apparent, exclusive, _newest = S.measure(copy)

    assert apparent >= 72 * 1024
    assert 8 * 1024 <= exclusive < 64 * 1024


def test_day_based_rollback_copies_expire(tmp_path):
    base = tmp_path / "qsys"
    _file(base / "warehouse/backups/old/part.parquet")
    _file(base / "warehouse/backups/new/part.parquet")
    _age_dir(base / "warehouse/backups/old", 45)
    _age_dir(base / "warehouse/backups/new", 3)

    items = _by_name(S.inventory(base, None, NOW))

    assert items["old"].verdict == S.ELIGIBLE
    assert items["new"].verdict == S.KEEP


def test_latest_snapshots_and_pointer_target_are_kept(tmp_path):
    base = tmp_path / "qsys"
    snaps = base / "test-env/warehouse-snapshots"
    for name, age in (("a", 30), ("b", 20), ("c", 10), ("d", 1)):
        _file(snaps / name / "part.parquet")
        _age_dir(snaps / name, age)
    (base / "test-env/warehouse-current").symlink_to(snaps / "a")

    items = _by_name(S.inventory(base, None, NOW))

    assert items["d"].verdict == S.KEEP
    assert items["c"].verdict == S.KEEP
    assert items["b"].verdict == S.ELIGIBLE
    assert items["a"].verdict == S.PROTECTED


def test_permanent_sources_never_expire(tmp_path):
    base = tmp_path / "qsys"
    _file(base / "baidupan/2020.zip")
    _age_dir(base / "baidupan", 400)

    items = _by_name(S.inventory(base, None, NOW))

    assert items["baidupan"].verdict == S.PERMANENT


def test_scratch_entries_expire_after_two_weeks(tmp_path):
    base = tmp_path / "qsys"
    base.mkdir()
    scratch = tmp_path / "scratch"
    _file(scratch / "probe.py", age_days=20)
    _file(scratch / "today.py", age_days=0)

    items = _by_name(S.inventory(base, scratch, NOW))

    assert items["probe.py"].verdict == S.ELIGIBLE
    assert items["today.py"].verdict == S.KEEP


def test_unregistered_top_level_entries_are_reported(tmp_path):
    base = tmp_path / "qsys"
    for name in ("warehouse", "baidupan", "mystery-copy", "test-env/warehouse-snapshots",
                 "test-env/var", "test-env/new-thing"):
        (base / name).mkdir(parents=True)

    names = {path.name for path in S.unregistered(base)}

    assert names == {"mystery-copy", "new-thing"}


def test_disk_levels():
    gib = S.GIB
    assert S.DiskStatus(100 * gib, 50 * gib, 50 * gib).level == "ok"
    assert S.DiskStatus(100 * gib, 85 * gib, 15 * gib).level == "warn"
    assert S.DiskStatus(100 * gib, 95 * gib, 5 * gib).level == "fail"


def test_2026_paid_staging_is_permanent_while_other_rebuild_output_expires(tmp_path):
    base = tmp_path / "qsys"
    rebuild = base / "minute-rebuild/warehouse"
    for name in ("paid_minute_bars_staging", "paid_minute_quarantine",
                 "paid_minute_quality", "tushare_daily"):
        _file(rebuild / name / "part.parquet")
        _age_dir(rebuild / name, 60)

    items = S.inventory(base, None, NOW)
    by = _by_name(items)

    assert len([i for i in items if i.path.name == "paid_minute_bars_staging"]) == 1
    assert by["paid_minute_bars_staging"].verdict == S.PERMANENT
    assert by["paid_minute_quarantine"].verdict == S.PERMANENT
    assert by["paid_minute_quality"].verdict == S.PERMANENT
    assert by["tushare_daily"].verdict == S.ELIGIBLE
    assert S.unregistered(base) == []


def test_the_research_output_directory_is_registered(tmp_path):
    """月度研究产物必须有保留策略，否则每天的质检都报"未登记目录"。"""
    base = tmp_path / "qsys"
    _file(base / "research/auction-timing/2026-09/report.json")

    items = _by_name(S.inventory(base, None, NOW))

    assert items["research"].category == "研究产物"
    assert items["research"].policy == S.PERMANENT
    assert S.unregistered(base) == []
