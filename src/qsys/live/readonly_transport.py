"""只读消费者入口；配置受限 socket 时禁止回退为直连 Redis。"""
from __future__ import annotations

import json
from math import isfinite
import os
import socket

from qsys.live.config import DeployConfig
from qsys.live.transport import Transport

MAX_RESPONSE = 32 * 1024 * 1024


class ReadonlyTransport(Transport):
    def __init__(self, path: str, timeout: float) -> None:
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("只读入口超时必须是有限的正数")
        self.path = path
        self.timeout = timeout

    def get_key(self, key: str) -> str | None:
        raw = json.dumps({"protocol": 1, "key": key}, ensure_ascii=False).encode() + b"\n"
        if len(raw) > 4096:
            raise ValueError("只读请求过大")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(self.timeout)
            conn.connect(self.path)
            conn.sendall(raw)
            with conn.makefile("rb") as reader:
                reply = reader.readline(MAX_RESPONSE + 1)
        if len(reply) > MAX_RESPONSE or not reply.endswith(b"\n"):
            raise RuntimeError("只读入口响应不完整或过大")
        result = json.loads(reply)
        if (not isinstance(result, dict) or set(result) != {"protocol", "value"}
                or type(result["protocol"]) is not int or result["protocol"] != 1
                or result["value"] is not None and not isinstance(result["value"], str)):
            raise RuntimeError("只读入口拒绝请求或返回无效响应")
        return result["value"]

    def _denied(self, *args, **kwargs):
        raise PermissionError("只读入口仅允许 get_key")

    append = ensure_group = reset_group = read_group = ack = pending = set_key = delete_key = _denied


def readonly_from_config(cfg: DeployConfig) -> Transport:
    """旧环境仍用既有协议；生产专属 unit 显式启用 socket，失败不降级。"""
    if "QSYS_REDIS_READER_SOCKET" in os.environ:
        path = os.environ["QSYS_REDIS_READER_SOCKET"]
        if not path.startswith("/") or "\x00" in path:
            raise ValueError("只读入口必须是绝对 socket 路径")
        return ReadonlyTransport(path, cfg.redis.command_timeout_seconds)
    from qsys.live.redis_transport import RedisTransport
    return RedisTransport.from_config(cfg)
