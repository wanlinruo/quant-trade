"""Tushare 凭证加载。token 绝不能出现在 repr / 日志 / 异常里。"""

import pytest

from qsys.live.config import TushareConfig, load_tushare_config

SAMPLE = """
[tushare]
token = "deadbeefcafe"
points = 5000
purchased = "2026-09-13"
"""


def test_loads_token_points_and_purchase_date(tmp_path):
    cfg_path = tmp_path / "deploy.local.toml"
    cfg_path.write_text(SAMPLE)
    cfg = load_tushare_config(cfg_path)
    assert cfg.token == "deadbeefcafe"
    assert cfg.points == 5000
    assert cfg.purchased == "2026-09-13"


def test_token_is_hidden_from_repr(tmp_path):
    """凭证进了 repr 就会进日志、进异常、进 pytest 失败输出。"""
    cfg_path = tmp_path / "deploy.local.toml"
    cfg_path.write_text(SAMPLE)
    cfg = load_tushare_config(cfg_path)
    assert "deadbeefcafe" not in repr(cfg)


def test_missing_file_says_which_file(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        load_tushare_config(tmp_path / "nope.toml")
    assert "nope.toml" in str(exc.value)


def test_missing_section_is_an_explicit_error(tmp_path):
    """没配凭证要显式报错，不能给个空 token 让调用方在网络层才发现。"""
    cfg_path = tmp_path / "deploy.local.toml"
    cfg_path.write_text("[redis]\nhost = \"127.0.0.1\"\n")
    with pytest.raises(ValueError) as exc:
        load_tushare_config(cfg_path)
    assert "tushare" in str(exc.value)
