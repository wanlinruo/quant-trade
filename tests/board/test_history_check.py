from datetime import date

import pyarrow as pa

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from scripts.board_history_check import summarize

D1, D2 = date(2026, 11, 11), date(2026, 11, 12)


def test_summary_without_snapshot_has_no_columns(tmp_path):
    wh = Warehouse(tmp_path / "wh")
    flags = [
        {"symbol": "A.SH", "dt": D1, "is_limit_up": True, "is_st": False},
        {"symbol": "B.SH", "dt": D1, "is_limit_up": True, "is_st": False},
        {"symbol": "C.SH", "dt": D1, "is_limit_up": False, "is_st": False},
        {"symbol": "A.SH", "dt": D2, "is_limit_up": True, "is_st": False},
        {"symbol": "B.SH", "dt": D2, "is_limit_up": False, "is_st": False},
        {"symbol": "C.SH", "dt": D2, "is_limit_up": True, "is_st": False},
    ]
    wh.write("daily_flags", pa.Table.from_pylist(flags, schema=schema.DAILY_FLAGS), year=2026)
    ll = [{"symbol": "A.SH", "dt": D1, "limit_type": "U", "limit_times": 2},
          {"symbol": "B.SH", "dt": D1, "limit_type": "U", "limit_times": 1}]
    wh.write("limit_list_daily", pa.Table.from_pylist(ll, schema=schema.LIMIT_LIST_DAILY), year=2026)
    got = summarize(D2, D1, WarehouseQuery(wh), [D1, D2], None)
    assert got["sealed"] == 2
    assert got["heights"] == {3: 1, 1: 1}
    assert got["unknown_heights"] == 0
    assert got["promotions"] == [
        {"level": 2, "yesterday": ["A.SH"], "promoted": ["A.SH"]},
        {"level": 1, "yesterday": ["B.SH"], "promoted": []},
    ]
    assert got["columns"] is None
