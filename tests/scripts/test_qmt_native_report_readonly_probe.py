from __future__ import annotations

import ast
from datetime import datetime
import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "docs/reference/qmt-native-report-readonly-probe.py"


def load():
    spec = importlib.util.spec_from_file_location("qmt_native_report_probe", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Context:
    def __init__(self):
        self.accounts: list[str] = []

    def set_account(self, account: str) -> None:
        self.accounts.append(account)


class Row:
    def __init__(self, **values):
        self.__dict__.update(values)


def enable(module) -> None:
    module.ENABLED = True
    module.CLIENT_BUILD = "2.1.33.0"
    module.PROBE_DATE = datetime.now().strftime("%Y%m%d")
    module.RUN_ID = "a" * 32
    module.ACCOUNT_TYPE = "STOCK"
    module.READ_ONLY_ACK = "READ_REAL_ACCOUNT_FACTS_WITHOUT_TRADING"
    module.account = "private-account"


def records(capsys):
    return [ast.literal_eval(line) for line in capsys.readouterr().out.splitlines()]


def test_disabled_probe_does_not_bind_or_query(capsys):
    module = load()
    queried = []
    module.get_trade_detail_data = lambda *args: queried.append(args)
    context = Context()

    module.init(context)

    assert context.accounts == []
    assert queried == []
    assert records(capsys)[0]["status"] == "rejected"


def test_enabled_probe_queries_exact_readonly_datasets_and_emits_no_values(capsys):
    module = load()
    enable(module)
    calls = []
    secret_values = {
        "private-account", "600036", "broker-order-7", "trade-9", "12345.67"
    }

    def query(account, account_type, kind):
        calls.append((account, account_type, kind))
        common = dict(m_strAccountID=account, m_strAccountKey="secret-key")
        if kind == "ACCOUNT":
            return [Row(**common, m_nBrokerType=2, m_strStatus="logged-in",
                        m_strTradingDate="20261106", m_dBalance=12345.67,
                        m_dAvailable=100.0, m_dFrozenCash=0.0,
                        m_dInstrumentValue=10.0, m_dAssureAsset=12345.67)]
        if kind == "POSITION":
            return [Row(**common, m_strExchangeID="SH", m_strInstrumentID="600036",
                        m_nVolume=100, m_nCanUseVolume=100, m_nFrozenVolume=0,
                        m_dOpenPrice=10.0, m_dMarketValue=1000.0,
                        m_strTradingDay="20261106")]
        if kind == "ORDER":
            return [Row(**common, m_strExchangeID="SH", m_strInstrumentID="600036",
                        m_strOrderSysID="broker-order-7", m_strOrderRef="ref",
                        m_strRemark="remark", m_nOffsetFlag=48,
                        m_nVolumeTotalOriginal=100, m_nVolumeTraded=0,
                        m_nVolumeTotal=100, m_nOrderStatus=50, m_nErrorID=0,
                        m_strErrorMsg="", m_strCancelInfo="",
                        m_strInsertDate="20261106", m_strInsertTime="150000")]
        return [Row(**common, m_strExchangeID="SH", m_strInstrumentID="600036",
                    m_strTradeID="trade-9", m_strOrderSysID="broker-order-7",
                    m_strOrderRef="ref", m_strRemark="remark", m_nOffsetFlag=48,
                    m_dPrice=10.0, m_nVolume=100, m_strTradeDate="20261106",
                    m_strTradeTime="150001", m_dComssion=1.0)]

    module.get_trade_detail_data = query
    context = Context()
    module.init(context)
    output = capsys.readouterr().out
    parsed = [ast.literal_eval(line) for line in output.splitlines()]

    assert context.accounts == ["private-account"]
    assert calls == [("private-account", "STOCK", kind) for kind in module._QUERY_KINDS]
    assert [row["check"] for row in parsed] == [
        "configuration", "account", "position", "order", "deal", "scope"
    ]
    assert all(value not in output for value in secret_values)
    assert parsed[1]["account_match"] is True
    assert parsed[1]["row_count_emitted"] is False
    assert "row_count" not in parsed[1]
    assert parsed[-1]["trading_calls"] == 0
    assert parsed[-1]["normalization_performed"] is False


def test_missing_fields_remain_explicit_and_are_not_filled(capsys):
    module = load()
    enable(module)
    module.get_trade_detail_data = lambda account, account_type, kind: (
        [Row(m_strAccountID=account)] if kind == "ORDER" else []
    )

    module.init(Context())
    parsed = records(capsys)
    order = next(row for row in parsed if row["check"] == "order")

    assert "m_strOrderSysID" in order["missing_every_row"]
    assert order["field_coverage"]["m_strOrderSysID"] == "none"
    assert order["field_types"]["m_strOrderSysID"] == []
    assert order["normalization_performed"] is False


def test_callbacks_are_shape_only_and_bounded(capsys):
    module = load()
    enable(module)
    module.get_trade_detail_data = lambda *args: []
    module.init(Context())
    capsys.readouterr()
    row = Row(m_strAccountID="private-account", m_strOrderSysID="private-order")

    for _ in range(8):
        module.order_callback(Context(), row)

    output = capsys.readouterr().out
    parsed = [ast.literal_eval(line) for line in output.splitlines()]
    assert len(parsed) == module._MAX_CALLBACK_OBSERVATIONS
    assert all(item["source"] == "callback" for item in parsed)
    assert "private-account" not in output
    assert "private-order" not in output


def test_query_errors_are_not_retried_and_are_counted_as_reads(capsys):
    module = load()
    enable(module)
    calls = []

    def fail(account, account_type, kind):
        calls.append(kind)
        raise RuntimeError("private failure detail")

    module.get_trade_detail_data = fail
    module.init(Context())
    output = capsys.readouterr().out
    parsed = [ast.literal_eval(line) for line in output.splitlines()]

    assert calls == list(module._QUERY_KINDS)
    assert output.count("query_error") == 4
    assert "private failure detail" not in output
    assert parsed[-1]["query_calls"] == 4
    assert parsed[-1]["account_reads"] == 4


def test_oversized_dataset_rejects_without_emitting_size(capsys):
    module = load()
    enable(module)
    rows = [Row(m_strAccountID="private-account")] * (module._MAX_QUERY_ROWS + 1)
    module.get_trade_detail_data = lambda account, account_type, kind: (
        rows if kind == "ORDER" else []
    )

    module.init(Context())
    order = next(row for row in records(capsys) if row["check"] == "order")

    assert order["status"] == "rejected"
    assert order["row_count_emitted"] is False
    assert "row_count" not in order


def test_source_has_no_trading_or_cancel_calls():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called_names.isdisjoint({
        "passorder", "cancel", "algo_passorder", "smart_algo_passorder"
    })
