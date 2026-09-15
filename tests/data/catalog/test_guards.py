"""registry 的守护测试。

这些测试是字典不腐烂的全部依靠。每一条都对应一种真实事故，
删任何一条都等于把那种事故放回来。
"""

from qsys.data import schema
from qsys.data.catalog import (
    OWNED_DATASETS,
    REGISTRY,
    expected_metric_ids,
    landed,
)


def test_expected_ids_cover_schema_and_paid_daily():
    """206 = schema.ALL 的 178 列 + PAID_DAILY 的 28 列。

    paid_daily 故意不在 schema.ALL 里（那会让 Warehouse.write 成为可能），
    但它是 2026-09-11 前名称的唯一真值源，必须进字典。
    """
    ids = expected_metric_ids()
    assert len(ids) == 206, (
        f"期望 206 条（schema.ALL 的 178 列 + PAID_DAILY 的 28 列），实得 {len(ids)}。"
        "schema 若有增删，先确认变动是否预期，再改这个数字。"
    )


def test_registry_covers_exactly_the_datasets_it_claims_to_own():
    """条目模块声明负责哪些表，就必须把那些表的列登记完整。

    这条在建设期就能跑绿——它管的是「已认领的有没有做完」。
    """
    for dataset in OWNED_DATASETS:
        cols = set(schema.ALL[dataset].names) if dataset in schema.ALL else set(
            schema.PAID_DAILY.names
        )
        got = {m.column for m in REGISTRY.values() if m.dataset == dataset}
        assert got == cols, f"{dataset} 登记不全：缺 {cols - got}，多 {got - cols}"


def test_all_datasets_are_owned_by_some_module():
    """**这条在 Task 4-8 全部完成前是红的，那是它的用途。**

    它红着就说明还有表没人认领。不设允许清单——允许清单会被忘掉，
    红色测试不会。
    """
    every = set(schema.ALL) | {"paid_daily"}
    assert OWNED_DATASETS == every, f"还没认领：{every - OWNED_DATASETS}"


def test_registry_ids_are_exactly_the_expected_landed_ids():
    """T1 完备性：缺一个失败，多一个也失败。

    拦的是「往 schema 加列忘登记」——页面上会少一个指标而无人知晓。
    """
    assert set(landed()) == expected_metric_ids()


def test_frozen_datasets_are_not_writable():
    """T6：把 schema.py 注释里那句「不要注册进 PARTITIONED」升级成测试。

    注册进去就让 Warehouse.write("paid_daily", ...) 成为可能，
    而 paid_daily 是永久只读的历史证据。
    """
    assert "paid_daily" not in schema.PARTITIONED
    assert "paid_daily" not in schema.UNPARTITIONED
