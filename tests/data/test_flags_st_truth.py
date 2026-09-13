"""ST 由真值直取，不再从价格反推。"""

import pyarrow as pa

from qsys.data import schema


def test_daily_flags_has_truth_columns_not_inference_audit_columns():
    """反推没了，反推的审计列也不该留。

    留着它们会让读代码的人以为启发式还在工作，也会让下游继续引用
    一个永远为 False 的列。
    """
    names = set(schema.DAILY_FLAGS.names)
    assert "is_st" in names
    assert "st_source" in names
    assert "st_inferred_raw" not in names
    assert "st_inference_rejected_by_price_evidence" not in names
    assert "st_inferred_final" not in names
    assert schema.DAILY_FLAGS.field("is_st").type == pa.bool_()
    assert schema.DAILY_FLAGS.field("st_source").type == pa.string()


def test_flag_columns_match_the_schema():
    """列清单与 schema 必须一致，否则 cast 会在落库时才炸。"""
    from qsys.data.pipeline.flags import _FLAG_COLUMNS

    assert _FLAG_COLUMNS == list(schema.DAILY_FLAGS.names)
