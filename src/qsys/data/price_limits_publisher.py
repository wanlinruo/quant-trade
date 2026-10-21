"""受限的盘前涨跌停缓存发布协议（SSH forced-command，不提供通用 Redis 代理）。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from qsys.live.config import StreamConfig, load_deploy_config
from qsys.live.redis_transport import RedisTransport

COMMAND = "publish-price-limits-v1"
MAX_BYTES = 4 * 1024 * 1024
MAX_SYMBOLS = 20_000


def _key(streams: StreamConfig, day: date) -> str:
    return f"{streams.price_limits}:{day:%Y%m%d}"


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重复 JSON 字段")
        result[key] = value
    return result


def _json(text: str) -> object:
    return json.loads(text, object_pairs_hook=_object)


def validate_request(raw: bytes) -> tuple[date, str, int]:
    """只验证信封和行情结构；不改值、不推算、不序列化替换原 payload。"""
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("请求大小超限")
    request = _json(raw.decode("utf-8"))
    if not isinstance(request, dict) or set(request) != {"protocol", "day", "value"}:
        raise ValueError("未知请求字段")
    if type(request["protocol"]) is not int or request["protocol"] != 1:
        raise ValueError("未知协议版本")
    if not isinstance(request["day"], str):
        raise ValueError("无效日期")
    day = date.fromisoformat(request["day"])
    if day.isoformat() != request["day"]:
        raise ValueError("日期必须为 ISO 日期")
    value = request["value"]
    if not isinstance(value, str):
        raise ValueError("payload 必须为字符串")
    data = _json(value)
    if not isinstance(data, dict) or not 0 < len(data) <= MAX_SYMBOLS:
        raise ValueError("无效证券集合")
    for symbol, limits in data.items():
        if not re.fullmatch(r"[0-9]{6}\.(?:SH|SZ|BJ)", symbol):
            raise ValueError("无效证券代码")
        if not isinstance(limits, list) or len(limits) != 2:
            raise ValueError("无效涨跌停结构")
        for price in limits:
            if price is not None and (
                type(price) not in (int, float) or not math.isfinite(price)
            ):
                raise ValueError("价格必须为有限数值或 null")
    return day, value, len(data)


def acknowledgement(day: date, value: str, rows: int) -> dict:
    return {"protocol": 1, "day": day.isoformat(), "rows": rows,
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}


@dataclass(frozen=True)
class SshPriceLimitsPublisher:
    """仅实现行情发布所需的 set_key；客户端没有 Redis 凭据。"""

    key: Path
    known_hosts: Path
    streams: StreamConfig
    user: str = "qsys-price-limits-publisher"

    def set_key(self, key: str, value: str) -> None:
        prefix = self.streams.price_limits + ":"
        if not key.startswith(prefix):
            raise ValueError("只允许涨跌停缓存键")
        stamp = key[len(prefix):]
        if not re.fullmatch(r"[0-9]{8}", stamp):
            raise ValueError("无效缓存日期")
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        if key != _key(self.streams, day):
            raise ValueError("无效缓存键")
        raw = json.dumps({"protocol": 1, "day": day.isoformat(), "value": value},
                         ensure_ascii=False).encode("utf-8")
        _, _, rows = validate_request(raw)
        result = subprocess.run(
            ["/usr/bin/ssh", "-F", "/dev/null", "-T", "-o", "BatchMode=yes",
             "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
             "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
             "-o", f"UserKnownHostsFile={self.known_hosts}",
             "-i", str(self.key), f"{self.user}@127.0.0.2", COMMAND],
            input=raw, capture_output=True, timeout=25, check=False,
        )
        if result.returncode:
            # 不将远端输出、私有配置或协议中的行情内容写入错误日志。
            raise RuntimeError(f"受限涨跌停发布失败（exit={result.returncode}）")
        if len(result.stdout) > 1024 or _json(result.stdout.decode()) != acknowledgement(day, value, rows):
            raise RuntimeError("受限涨跌停发布确认不匹配")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        if os.environ.get("SSH_ORIGINAL_COMMAND") != COMMAND:
            raise ValueError("拒绝非发布命令")
        day, value, rows = validate_request(sys.stdin.buffer.read(MAX_BYTES + 1))
        config = load_deploy_config(args.config)
        RedisTransport.from_config(config).set_key(_key(config.streams, day), value)
        print(json.dumps(acknowledgement(day, value, rows)))
        return 0
    except Exception:
        print("受限涨跌停发布失败", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
