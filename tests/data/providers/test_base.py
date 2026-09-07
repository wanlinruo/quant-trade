import pytest

from qsys.data.providers.base import normalize_symbol


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600036", "600036.SH"),
        ("601318", "601318.SH"),
        ("603288", "603288.SH"),
        ("688981", "688981.SH"),
        ("000001", "000001.SZ"),
        ("002415", "002415.SZ"),
        ("300750", "300750.SZ"),
        ("430047", "430047.BJ"),
        ("830799", "830799.BJ"),
        ("920001", "920001.BJ"),
    ],
)
def test_normalize_symbol_infers_market_from_prefix(raw, expected):
    assert normalize_symbol(raw) == expected


def test_normalize_symbol_strips_existing_suffix():
    assert normalize_symbol("600036.SH") == "600036.SH"
    assert normalize_symbol("sh600036") == "600036.SH"


def test_normalize_symbol_rejects_unknown_prefix():
    with pytest.raises(ValueError, match="cannot infer market"):
        normalize_symbol("123456")
