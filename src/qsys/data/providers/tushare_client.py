"""Tushare Pro 的薄 HTTP client。

**为什么不用 `tushare` 包**：它拖 pandas 且把错误包成 DataFrame，
而我们需要原样拿到 upstream 的 code/msg 才能判断「该不该重试」。
REST 接口本身很简单，标准库足够。

**凭证纪律**：token 只在请求体里出现一次，绝不进日志、异常、repr。
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable

import structlog

log = structlog.get_logger(__name__)

API = "https://api.tushare.pro"


class TushareError(RuntimeError):
    """上游返回非零 code，或重试耗尽。"""


def _http_transport(body: bytes, timeout: float):
    req = urllib.request.Request(
        API, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class TushareClient:
    def __init__(
        self,
        token: str,
        *,
        transport: Callable[[bytes, float], dict] | None = None,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> None:
        self._token = token
        self._transport = transport or _http_transport
        self._max_retries = max_retries
        self._timeout = timeout

    def call(self, api_name: str, params: dict, fields: str = "") -> list[dict]:
        """调一个接口，返回行的列表。

        空结果返回 `[]`——0 行是合法事实（非交易日、无停牌），不是错误。
        """
        body = json.dumps({
            "api_name": api_name, "token": self._token,
            "params": params, "fields": fields,
        }).encode()

        last: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                payload = self._transport(body, self._timeout)
            except Exception as exc:
                last = exc
                if attempt < self._max_retries - 1:
                    time.sleep(2 ** attempt)
                continue

            code = payload.get("code")
            if code != 0:
                msg = payload.get("msg", "")
                # token 不进异常消息——异常会进日志、告警、工单。
                raise TushareError(f"{api_name} 返回 code={code}: {msg}")

            data = payload.get("data") or {}
            names = data.get("fields") or []
            return [dict(zip(names, row)) for row in (data.get("items") or [])]

        raise TushareError(f"{api_name} 重试 {self._max_retries} 次仍失败: {last!r}")
