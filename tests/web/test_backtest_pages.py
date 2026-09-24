"""回测页面：列表、详情、下载，以及不给的东西一律 404。"""

import json

import pytest
from fastapi.testclient import TestClient

from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.backtest_views import BacktestService
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService

STATUS = (
    "run_id,strategy,start,end,sessions,initial_value,total_value,"
    "cumulative_return,fills,buys,sells,rejects,closed_lots,gate_decisions,"
    "gate_allowed,gate_rejected\n"
    "abc123,StructuralStrategy,2025-09-10,2026-09-08,241,100000.0,173386.25,"
    "0.7338625,168,84,84,2,84,235,109,126\n"
)


@pytest.fixture
def client(warehouse, tmp_path) -> TestClient:
    run = tmp_path / "jq100k"
    run.mkdir()
    (run / "run_status.csv").write_text(STATUS, encoding="utf-8")
    (run / "config.json").write_text(
        json.dumps({"cost_model": "聚宽默认", "generated_at": "2026-09-10T13:48:00"}),
        encoding="utf-8")
    (run / "daily_nav.csv").write_text(
        "date,total_value,available_cash,position_count,daily_return\n"
        "2025-09-10,100000.0,100000.0,0,0.0\n"
        "2025-09-11,101000.0,0.0,1,0.01\n", encoding="utf-8")
    (run / "gate_decisions.csv").write_text(
        "date,stock,symbol,strategy_tag,s7_state,reason,real_allow\n"
        "2025-09-10,600001.XSHG,600001.SH,LOW_MAIN,CLEAN,ok,true\n"
        "2025-09-11,300001.XSHE,300001.SZ,HIGH_CHINEXT,RISK,weak,false\n",
        encoding="utf-8",
    )
    (run / "jq100k.zip").write_bytes(b"PK\x03\x04fake")

    cfg = WebConfig()
    service = MonitorService(SessionJournal(warehouse), config=cfg)
    return TestClient(create_app(service, cfg, None, BacktestService(tmp_path)))


def test_the_index_lists_the_run(client):
    body = client.get("/backtests").text
    assert "jq100k" in body
    assert "+73.39%" in body


def test_the_detail_page_shows_the_headline_and_files(client):
    body = client.get("/backtests/jq100k").text
    assert "StructuralStrategy" in body
    assert "jq100k.zip" in body
    assert "daily_nav.csv" in body
    assert "<svg" in body   # 净值曲线


def test_the_detail_page_explains_backtest_frequency(client):
    body = client.get("/backtests/jq100k").text
    assert "回测运行口径" in body
    assert "主驱动" in body and "日级" in body
    assert "分钟级按需" in body
    assert "不逐分钟遍历全天" in body
    assert "每天记录一个净值点" in body
    assert "不使用秒级行情" in body


def test_gate_decisions_belong_to_the_specific_backtest(client):
    body = client.get("/backtests/jq100k").text
    assert "候选与 Gate" in body
    assert "600001.SH" in body
    assert "HIGH_CHINEXT" in body
    assert "50.0%" in body


def test_a_csv_downloads_as_an_attachment(client):
    r = client.get("/backtests/jq100k/file/daily_nav.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.startswith("date,total_value")


def test_the_zip_downloads_too(client):
    r = client.get("/backtests/jq100k/file/jq100k.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"


def test_an_unknown_run_is_404(client):
    assert client.get("/backtests/nope").status_code == 404


def test_an_unknown_file_is_404(client):
    assert client.get("/backtests/jq100k/file/absent.csv").status_code == 404


def test_the_console_is_404_when_not_enabled(warehouse):
    cfg = WebConfig()
    c = TestClient(create_app(MonitorService(SessionJournal(warehouse), config=cfg), cfg))
    assert c.get("/backtests").status_code == 404


def _run_with_config(tmp_path, name, config):
    run = tmp_path / name
    run.mkdir()
    (run / "run_status.csv").write_text(STATUS, encoding="utf-8")
    (run / "config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return run


def test_the_detail_page_shows_the_strategy_parameters(client, tmp_path):
    """做回测对比要能一眼看到参数；早于参数留痕的运行则明说没有。"""
    _run_with_config(tmp_path, "withparams", {
        "cost_model": "qsys 实盘档位（佣金万0.75最低5元 / 滑点 0.05% / 印花税 0.05% / 含过户费）",
        "entry_fill": "09:31 分钟线连续竞价判定（缺分钟线退回开盘价上浮 0.50%）",
        "entry_fill_mode": "continuous",
        "params": {"require_auction_volume": True, "auction_at": "09:25:10",
                   "entry_price_buffer": 0.03, "sizing_price_buffer": 0.005},
    })

    body = client.get("/backtests/withparams").text

    assert "策略参数" in body
    assert "竞价决策时刻" in body and "09:25:10" in body
    assert "保护价上浮" in body and "3%（开盘价 ×1.03）" in body
    assert "要求竞价成交量" in body and "是" in body
    assert "连续竞价判定" in body
    assert "万0.75最低5元" in body


def test_older_runs_without_params_fall_back_to_the_flat_config(client, tmp_path):
    _run_with_config(tmp_path, "flatparams", {
        "cost_model": "聚宽默认", "strategy_version": "abc",
        "require_auction_volume": True, "entry_price_buffer": 0.002,
    })

    body = client.get("/backtests/flatparams").text

    assert "保护价上浮" in body and "0.2%（开盘价 ×1.002）" in body
    assert "strategy_version" not in body      # 运行口径的键不当成策略参数


def test_a_run_with_no_parameters_says_so(client):
    body = client.get("/backtests/jq100k").text
    assert "没有记录策略参数" in body
    assert "开盘价成交（原口径）" in body      # 没有入场口径字段时按原口径显示


def test_runs_carry_a_source_label(client, tmp_path):
    """命令行跑的研究也要在页面上看得到，并标明不是网页排队任务。"""
    _run_with_config(tmp_path, "cliresearch", {"cost_model": "qsys", "source": "cli"})
    _run_with_config(tmp_path, "webjob", {"cost_model": "qsys", "source": "web"})

    index = client.get("/backtests").text
    assert "命令行研究" in index and "网页提交" in index
    assert "cliresearch" in index and "webjob" in index
    assert "命令行研究" in client.get("/backtests/cliresearch").text
    assert "网页提交" in client.get("/backtests/webjob").text
    # 老运行没有该字段时按命令行处理，不隐藏
    assert "jq100k" in index
