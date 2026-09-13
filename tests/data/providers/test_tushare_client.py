"""Tushare HTTP client。不联网——transport 由测试注入。"""

import json

import pytest

from qsys.data.providers.tushare_client import TushareClient, TushareError


class FakeTransport:
    """记录请求、按脚本返回响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, body: bytes, timeout: float):
        self.requests.append(json.loads(body))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok_payload(fields, items):
    return {"code": 0, "msg": "", "data": {"fields": fields, "items": items}}


def test_returns_rows_as_dicts():
    t = FakeTransport([ok_payload(["ts_code", "close"],
                                  [["000001.SZ", 11.7], ["600000.SH", 8.3]])])
    client = TushareClient("tok", transport=t)
    rows = client.call("daily", {"trade_date": "20260911"})
    assert rows == [{"ts_code": "000001.SZ", "close": 11.7},
                    {"ts_code": "600000.SH", "close": 8.3}]


def test_sends_api_name_params_and_fields():
    t = FakeTransport([ok_payload(["ts_code"], [["000001.SZ"]])])
    TushareClient("tok", transport=t).call("daily", {"trade_date": "20260911"},
                                           "ts_code,close")
    sent = t.requests[0]
    assert sent["api_name"] == "daily"
    assert sent["params"] == {"trade_date": "20260911"}
    assert sent["fields"] == "ts_code,close"


def test_an_empty_result_is_an_empty_list_not_an_error():
    """接口返回 0 行是合法事实（如非交易日），不是错误。"""
    t = FakeTransport([ok_payload(["ts_code"], [])])
    assert TushareClient("tok", transport=t).call("daily", {}) == []


def test_api_error_raises_with_the_upstream_message():
    t = FakeTransport([{"code": 40203, "msg": "抱歉，您没有访问该接口的权限", "data": None}])
    with pytest.raises(TushareError) as exc:
        TushareClient("tok", transport=t).call("limit_list_d", {})
    assert "40203" in str(exc.value)
    assert "没有访问该接口的权限" in str(exc.value)


def test_the_token_never_appears_in_the_exception():
    """异常信息会进日志、进告警、进工单。"""
    t = FakeTransport([{"code": 40001, "msg": "token 无效", "data": None}])
    with pytest.raises(TushareError) as exc:
        TushareClient("s3cr3t-token", transport=t).call("daily", {})
    assert "s3cr3t-token" not in str(exc.value)


def test_retries_transient_failures_then_succeeds():
    t = FakeTransport([TimeoutError("boom"), TimeoutError("boom"),
                       ok_payload(["ts_code"], [["000001.SZ"]])])
    client = TushareClient("tok", transport=t, max_retries=3)
    assert client.call("daily", {}) == [{"ts_code": "000001.SZ"}]
    assert len(t.requests) == 3


def test_gives_up_after_max_retries():
    t = FakeTransport([TimeoutError("boom")] * 3)
    with pytest.raises(TushareError):
        TushareClient("tok", transport=t, max_retries=3).call("daily", {})


def test_does_not_retry_a_permission_error():
    """权限不足重试多少次都一样，只会白白消耗额度。"""
    t = FakeTransport([{"code": 40203, "msg": "没有权限", "data": None}])
    with pytest.raises(TushareError):
        TushareClient("tok", transport=t, max_retries=3).call("limit_list_d", {})
    assert len(t.requests) == 1
