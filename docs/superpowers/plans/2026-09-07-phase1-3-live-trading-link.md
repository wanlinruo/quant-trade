# 一期 1-3：实盘链路 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成核心侧与 Windows 执行代理之间的实盘链路——Redis Stream 三通道协议、幂等下单、风控闸门与双路径 kill switch、`xtquant` 执行代理、影子模式、三层对账与心跳监控。产出：一个能影子运行、能真实下单、能逐笔对账的实盘系统。

**Architecture:** 实盘 `LiveBroker` 实现与 `SimBroker` 相同的 `Broker` 接口，回测与实盘的差异只可能来自这一个边界。所有跨机通信走 Redis **Stream**（不用 pub/sub——断线即丢且不可追溯）。`client_order_id` 由核心侧生成，Windows 侧维护已处理集合，**网络重传永不导致重复下单**。影子模式是独立的 `ShadowBroker` 类而非布尔开关——一个能静默决定真钱动不动的参数太危险。

**Tech Stack:** Python 3.11+ · uv · redis-py · pydantic · structlog · pytest · （Windows 侧）xtquant

**Spec:** `docs/superpowers/specs/2026-09-06-personal-quant-system-design.md`（实现 §6.2 QmtBroker 全部、§3.2 原则一、§3.3 部署约束、§7.1 独立 kill switch 路径、§9 心跳）

## Global Constraints

- Python **3.11+**
- **必须用 Redis Stream，不得用 pub/sub**——pub/sub 断线即丢且不可追溯（spec §6.2）
- **`client_order_id` 由核心侧生成**；Windows 侧维护已处理集合，重复指令直接忽略并回放旧结果。网络重传永不导致重复下单（spec §6.2）
- **Windows 侧不放任何策略逻辑**——只把标准化指令翻译成 `xtquant` 调用，把回报翻译回标准化事件。代码量应小到可一眼读完（spec §3.2 原则一）
- **kill switch 必须有一条不依赖 Web 的独立路径**（spec §7.1）
- **持仓不一致 = 立即停止下单**，转人工介入（spec §6.2 故障矩阵）
- 单元测试**不得连接真实 Redis、不得导入 xtquant**——一律用内存实现与假适配器
- Redis 若跨公网必须 TLS + 强密码 + IP 白名单；**部署地址只允许出现在 gitignored 的本地配置**，仓库内只提交带占位符的 example（spec §3.3）
- 日志使用 **structlog**；每条指令与回报都必须可追溯到 `client_order_id`
- 环境隔离：所有命令前置
  `unset VIRTUAL_ENV; export PATH="$HOME/.local/bin:$(echo "$PATH" | tr ':' '\n' | grep -v 'project-002/.venv' | paste -sd: -)"`

---

## File Structure

```
src/qsys/live/
  __init__.py
  messages.py       OrderCommand / FillEvent / RejectEvent / AccountSnapshot
                    ControlCommand / Heartbeat —— pydantic 模型与 JSON 序列化
  transport.py      Transport 抽象 / StreamMessage / MemoryTransport
  redis_transport.py  RedisTransport（真实 Redis Stream）
  config.py         部署配置加载
  idempotency.py    IdempotencyGuard
  kill_switch.py    KillSwitch（Redis key + 本地文件双路径）
  risk.py           RiskRule 集 + RiskGate
  live_broker.py    LiveBroker（核心侧，实现 Broker）
  shadow_broker.py  ShadowBroker（只记录意图，永不发单）
  heartbeat.py      HeartbeatMonitor
  reconcile.py      三层对账
src/qsys/agent/
  __init__.py
  adapter.py        XtQuantAdapter 协议 + FakeAdapter（测试用）
  xtquant_adapter.py  真实 xtquant 适配器（仅 Windows 可导入）
  execution_agent.py  执行代理主循环
config/
  deploy.example.toml   占位符配置（提交）
  deploy.local.toml     真实地址（gitignored）
scripts/
  kill.py           独立 kill switch —— 不依赖 Web、不依赖核心进程
  run_agent.py      Windows 侧启动入口
tests/live/
  test_messages.py test_transport.py test_idempotency.py test_kill_switch.py
  test_risk.py test_live_broker.py test_shadow_broker.py
  test_heartbeat.py test_reconcile.py
tests/agent/
  test_execution_agent.py
tests/
  test_live_link_e2e.py   端到端：假 Transport + 假 xtquant，跑完整交易日
```

---

## Task 1: 消息协议

**Files:**
- Create: `src/qsys/live/__init__.py`
- Create: `src/qsys/live/messages.py`
- Test: `tests/live/test_messages.py`

**Interfaces:**
- Consumes: 无（独立于 core，便于 Windows 侧最小依赖）
- Produces: `OrderCommand`、`FillEvent`、`RejectEvent`、`PositionSnapshot`、`AccountSnapshot`、`ControlCommand`、`Heartbeat`（均为 pydantic `BaseModel`），公共基类 `Message` 提供 `to_payload() -> dict[str, str]` 与类方法 `from_payload(dict) -> Self`

- [ ] **Step 1: 写失败测试**

Create `tests/live/test_messages.py`:

```python
from datetime import datetime

import pytest

from qsys.live.messages import (
    AccountSnapshot,
    ControlCommand,
    FillEvent,
    Heartbeat,
    OrderCommand,
    PositionSnapshot,
    RejectEvent,
)

NOW = datetime(2024, 1, 3, 9, 31, 12)


def order() -> OrderCommand:
    return OrderCommand(
        client_order_id="c-1", symbol="600036.SH", side="buy", qty=100,
        order_type="market", limit_price=None, created_at=NOW,
    )


def test_order_round_trips_through_payload():
    restored = OrderCommand.from_payload(order().to_payload())
    assert restored == order()


def test_payload_values_are_all_strings():
    """Redis Stream 的字段值必须是字符串。"""
    payload = order().to_payload()
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in payload.items())


def test_payload_carries_a_type_tag():
    assert order().to_payload()["type"] == "OrderCommand"


def test_order_rejects_unknown_side():
    with pytest.raises(Exception):
        OrderCommand(client_order_id="c", symbol="600036.SH", side="hold", qty=1,
                     order_type="market", limit_price=None, created_at=NOW)


def test_order_rejects_non_positive_qty():
    with pytest.raises(Exception):
        OrderCommand(client_order_id="c", symbol="600036.SH", side="buy", qty=0,
                     order_type="market", limit_price=None, created_at=NOW)


def test_limit_order_requires_price():
    with pytest.raises(ValueError, match="limit_price"):
        OrderCommand(client_order_id="c", symbol="600036.SH", side="buy", qty=100,
                     order_type="limit", limit_price=None, created_at=NOW)


def test_fill_round_trips():
    fill = FillEvent(client_order_id="c-1", symbol="600036.SH", side="buy", qty=100,
                     price=27.58, fee=5.01, filled_at=NOW, broker_order_id="B123")
    assert FillEvent.from_payload(fill.to_payload()) == fill


def test_reject_round_trips():
    rej = RejectEvent(client_order_id="c-1", symbol="600036.SH",
                      reason="limit up", at=NOW)
    assert RejectEvent.from_payload(rej.to_payload()) == rej


def test_account_snapshot_round_trips():
    snap = AccountSnapshot(
        at=NOW, cash=98765.43,
        positions={"600036.SH": PositionSnapshot(total_qty=500, available_qty=500,
                                                 avg_cost=27.63)},
    )
    restored = AccountSnapshot.from_payload(snap.to_payload())
    assert restored == snap
    assert restored.positions["600036.SH"].total_qty == 500


def test_empty_account_snapshot_round_trips():
    snap = AccountSnapshot(at=NOW, cash=100_000.0, positions={})
    assert AccountSnapshot.from_payload(snap.to_payload()) == snap


def test_control_command_round_trips():
    cmd = ControlCommand(action="kill", at=NOW, reason="manual")
    assert ControlCommand.from_payload(cmd.to_payload()) == cmd


def test_control_rejects_unknown_action():
    with pytest.raises(Exception):
        ControlCommand(action="explode", at=NOW)


def test_heartbeat_round_trips():
    hb = Heartbeat(source="agent", at=NOW)
    assert Heartbeat.from_payload(hb.to_payload()) == hb


def test_from_payload_rejects_wrong_type_tag():
    payload = order().to_payload()
    with pytest.raises(ValueError, match="expected FillEvent"):
        FillEvent.from_payload(payload)


def test_datetime_survives_to_the_second():
    restored = OrderCommand.from_payload(order().to_payload())
    assert restored.created_at == NOW
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_messages.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.live'`

- [ ] **Step 3: 实现**

```bash
mkdir -p src/qsys/live tests/live
touch src/qsys/live/__init__.py
```

Create `src/qsys/live/messages.py`:

```python
"""跨机消息协议。

刻意不依赖 qsys.core —— Windows 侧执行代理只需要这个模块和 redis-py，
依赖越少越不容易在 Windows 上出问题（spec §11）。

Redis Stream 的字段值必须是字符串，故整条消息序列化为单个 JSON payload。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, model_validator


class Message(BaseModel):
    def to_payload(self) -> dict[str, str]:
        return {"type": type(self).__name__, "data": self.model_dump_json()}

    @classmethod
    def from_payload(cls, payload: dict[str, str]) -> Self:
        tag = payload.get("type")
        if tag != cls.__name__:
            raise ValueError(f"expected {cls.__name__}, got {tag!r}")
        return cls.model_validate_json(payload["data"])


class OrderCommand(Message):
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    order_type: Literal["market", "limit"]
    limit_price: float | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.qty <= 0:
            raise ValueError(f"qty must be positive: {self.qty}")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit order requires limit_price")
        return self


class FillEvent(Message):
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    price: float
    fee: float
    filled_at: datetime
    broker_order_id: str | None = None


class RejectEvent(Message):
    client_order_id: str
    symbol: str
    reason: str
    at: datetime


class PositionSnapshot(BaseModel):
    total_qty: int
    available_qty: int
    avg_cost: float


class AccountSnapshot(Message):
    at: datetime
    cash: float
    positions: dict[str, PositionSnapshot]


class ControlCommand(Message):
    action: Literal["kill", "resume", "cancel_all", "query"]
    at: datetime
    reason: str = ""


class Heartbeat(Message):
    source: str
    at: datetime
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live/test_messages.py -v`
Expected: PASS —— 15 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live tests/live/test_messages.py
git commit -m "feat(live): 跨机消息协议"
```

---

## Task 2: Transport 抽象与内存实现

内存实现是全部实盘测试的基础——单元测试不得连接真实 Redis。

**Files:**
- Create: `src/qsys/live/transport.py`
- Test: `tests/live/test_transport.py`

**Interfaces:**
- Consumes: 无
- Produces: `StreamMessage(id, payload)`；`Transport` (ABC: `append(stream, payload) -> str`、`ensure_group(stream, group)`、`read_group(stream, group, consumer, count=10) -> list[StreamMessage]`、`ack(stream, group, message_id)`、`pending(stream, group) -> list[StreamMessage]`、`set_key(key, value)`、`get_key(key) -> str | None`、`delete_key(key)`)；`MemoryTransport()`

- [ ] **Step 1: 写失败测试**

Create `tests/live/test_transport.py`:

```python
import pytest

from qsys.live.transport import MemoryTransport, StreamMessage

STREAM = "cmd:orders"
GROUP = "core"


@pytest.fixture
def t() -> MemoryTransport:
    tr = MemoryTransport()
    tr.ensure_group(STREAM, GROUP)
    return tr


def test_append_returns_an_id(t):
    mid = t.append(STREAM, {"type": "X", "data": "1"})
    assert isinstance(mid, str) and mid


def test_ids_are_monotonic(t):
    ids = [t.append(STREAM, {"n": str(i)}) for i in range(5)]
    assert ids == sorted(ids)


def test_read_group_returns_appended_messages(t):
    t.append(STREAM, {"type": "X", "data": "1"})
    msgs = t.read_group(STREAM, GROUP, "c1")
    assert len(msgs) == 1
    assert msgs[0].payload["data"] == "1"


def test_read_group_does_not_redeliver_unacked_to_same_consumer(t):
    t.append(STREAM, {"n": "1"})
    assert len(t.read_group(STREAM, GROUP, "c1")) == 1
    assert t.read_group(STREAM, GROUP, "c1") == []


def test_unacked_messages_stay_pending(t):
    t.append(STREAM, {"n": "1"})
    t.read_group(STREAM, GROUP, "c1")
    assert len(t.pending(STREAM, GROUP)) == 1


def test_ack_clears_pending(t):
    t.append(STREAM, {"n": "1"})
    msg = t.read_group(STREAM, GROUP, "c1")[0]
    t.ack(STREAM, GROUP, msg.id)
    assert t.pending(STREAM, GROUP) == []


def test_messages_survive_a_consumer_restart(t):
    """Stream 的核心价值：断线期间的消息不丢，重启后仍可消费。"""
    t.append(STREAM, {"n": "1"})
    t.append(STREAM, {"n": "2"})
    first = t.read_group(STREAM, GROUP, "c1")
    assert len(first) == 2
    # 未 ack 就"崩溃"，换个消费者名重启后仍能从 pending 取回
    recovered = t.pending(STREAM, GROUP)
    assert [m.payload["n"] for m in recovered] == ["1", "2"]


def test_count_limits_the_batch(t):
    for i in range(5):
        t.append(STREAM, {"n": str(i)})
    assert len(t.read_group(STREAM, GROUP, "c1", count=2)) == 2


def test_read_group_on_unknown_group_raises(t):
    t.append(STREAM, {"n": "1"})
    with pytest.raises(KeyError, match="no such group"):
        t.read_group(STREAM, "nope", "c1")


def test_ensure_group_is_idempotent(t):
    t.ensure_group(STREAM, GROUP)
    t.ensure_group(STREAM, GROUP)
    t.append(STREAM, {"n": "1"})
    assert len(t.read_group(STREAM, GROUP, "c1")) == 1


def test_group_created_later_does_not_see_earlier_messages(t):
    """与 Redis XGROUP CREATE ... $ 一致：新组只看新消息。"""
    t.append(STREAM, {"n": "old"})
    t.ensure_group(STREAM, "late")
    t.append(STREAM, {"n": "new"})
    msgs = t.read_group(STREAM, "late", "c1")
    assert [m.payload["n"] for m in msgs] == ["new"]


def test_key_set_get_delete(t):
    assert t.get_key("k") is None
    t.set_key("k", "v")
    assert t.get_key("k") == "v"
    t.delete_key("k")
    assert t.get_key("k") is None


def test_delete_missing_key_is_not_an_error(t):
    t.delete_key("nope")


def test_all_messages_bypasses_group_semantics(t):
    """测试自省：不受消费组 $ 语义影响，能看到全部消息。"""
    t.append(STREAM, {"n": "1"})
    t.append(STREAM, {"n": "2"})
    assert [m.payload["n"] for m in t.all_messages(STREAM)] == ["1", "2"]


def test_all_messages_on_unknown_stream_is_empty(t):
    assert t.all_messages("nope") == []


def test_stream_message_is_frozen():
    msg = StreamMessage("1-0", {"a": "b"})
    with pytest.raises(Exception):
        msg.id = "2-0"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_transport.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.live.transport'`

- [ ] **Step 3: 实现 `src/qsys/live/transport.py`**

```python
"""传输层抽象。

必须用 Stream 而非 pub/sub —— pub/sub 断线即丢且不可追溯，
断线期间的成交回报会永久丢失。Stream 有持久化与消费确认，
核心侧重启后能从上次位置继续消费。

MemoryTransport 是全部实盘测试的基础：单元测试不得连接真实 Redis。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import count


@dataclass(frozen=True, slots=True)
class StreamMessage:
    id: str
    payload: dict[str, str]


class Transport(ABC):
    @abstractmethod
    def append(self, stream: str, payload: dict[str, str]) -> str: ...

    @abstractmethod
    def ensure_group(self, stream: str, group: str) -> None: ...

    @abstractmethod
    def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[StreamMessage]: ...

    @abstractmethod
    def ack(self, stream: str, group: str, message_id: str) -> None: ...

    @abstractmethod
    def pending(self, stream: str, group: str) -> list[StreamMessage]: ...

    @abstractmethod
    def set_key(self, key: str, value: str) -> None: ...

    @abstractmethod
    def get_key(self, key: str) -> str | None: ...

    @abstractmethod
    def delete_key(self, key: str) -> None: ...


class MemoryTransport(Transport):
    """语义对齐 Redis Stream 的内存实现。"""

    def __init__(self) -> None:
        self._streams: dict[str, list[StreamMessage]] = {}
        self._cursors: dict[tuple[str, str], int] = {}
        self._pending: dict[tuple[str, str], list[str]] = {}
        self._keys: dict[str, str] = {}
        self._seq = count(1)

    def append(self, stream: str, payload: dict[str, str]) -> str:
        message = StreamMessage(f"{next(self._seq):020d}-0", dict(payload))
        self._streams.setdefault(stream, []).append(message)
        return message.id

    def ensure_group(self, stream: str, group: str) -> None:
        key = (stream, group)
        if key in self._cursors:
            return
        # 与 XGROUP CREATE ... $ 一致：新组只看新消息
        self._cursors[key] = len(self._streams.get(stream, []))
        self._pending[key] = []

    def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[StreamMessage]:
        key = (stream, group)
        if key not in self._cursors:
            raise KeyError(f"no such group {group!r} on {stream!r}")
        messages = self._streams.get(stream, [])
        start = self._cursors[key]
        batch = messages[start : start + count]
        self._cursors[key] = start + len(batch)
        self._pending[key].extend(m.id for m in batch)
        return list(batch)

    def ack(self, stream: str, group: str, message_id: str) -> None:
        key = (stream, group)
        if key in self._pending and message_id in self._pending[key]:
            self._pending[key].remove(message_id)

    def pending(self, stream: str, group: str) -> list[StreamMessage]:
        key = (stream, group)
        ids = set(self._pending.get(key, []))
        return [m for m in self._streams.get(stream, []) if m.id in ids]

    def set_key(self, key: str, value: str) -> None:
        self._keys[key] = value

    def get_key(self, key: str) -> str | None:
        return self._keys.get(key)

    def delete_key(self, key: str) -> None:
        self._keys.pop(key, None)

    # --- 测试自省。生产代码不得使用 ---

    def all_messages(self, stream: str) -> list[StreamMessage]:
        """返回流中全部消息，绕过消费组语义。仅供测试断言使用。"""
        return list(self._streams.get(stream, []))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live/test_transport.py -v`
Expected: PASS —— 16 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/transport.py tests/live/test_transport.py
git commit -m "feat(live): Transport 抽象与内存实现"
```

---

## Task 3: Redis 传输与部署配置

**部署地址只允许出现在 gitignored 的本地配置**——公网 IP 固化进代码库是常见的隐患来源。

**Files:**
- Create: `src/qsys/live/redis_transport.py`
- Create: `src/qsys/live/config.py`
- Create: `config/deploy.example.toml`
- Modify: `.gitignore`
- Test: `tests/live/test_config.py`

**Interfaces:**
- Consumes: `Transport`（Task 2）
- Produces: `RedisTransport(client)` 实现 `Transport`，类方法 `from_config(cfg) -> RedisTransport`；`RedisConfig(host, port, password, tls, db)`、`StreamConfig(orders, control, fills, rejects, account, heartbeat)`、`DeployConfig(redis, streams, consumer_group, consumer_name)` 带 `load_deploy_config(path=None) -> DeployConfig`

- [ ] **Step 1: 写失败测试**

Create `tests/live/test_config.py`:

```python
import pytest

from qsys.live.config import DeployConfig, load_deploy_config

EXAMPLE = """
[redis]
host = "10.0.0.1"
port = 6380
password = "secret"
tls = true
db = 1

[streams]
orders = "cmd:orders"
control = "cmd:control"
fills = "evt:fills"
rejects = "evt:rejects"
account = "state:account"
heartbeat = "state:heartbeat"

[agent]
consumer_group = "core"
consumer_name = "core-1"
"""


def test_loads_all_sections(tmp_path):
    path = tmp_path / "deploy.toml"
    path.write_text(EXAMPLE)
    cfg = load_deploy_config(path)
    assert cfg.redis.host == "10.0.0.1"
    assert cfg.redis.port == 6380
    assert cfg.redis.tls is True
    assert cfg.streams.orders == "cmd:orders"
    assert cfg.consumer_group == "core"


def test_defaults_are_local_and_safe(tmp_path):
    path = tmp_path / "deploy.toml"
    path.write_text("[redis]\nhost = \"127.0.0.1\"\n")
    cfg = load_deploy_config(path)
    assert cfg.redis.port == 6379
    assert cfg.redis.tls is False
    assert cfg.streams.fills == "evt:fills"


def test_missing_file_raises_with_a_helpful_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="deploy.example.toml"):
        load_deploy_config(tmp_path / "nope.toml")


def test_repo_ships_an_example_config():
    from pathlib import Path

    example = Path("config/deploy.example.toml")
    assert example.exists()
    text = example.read_text()
    assert "127.0.0.1" in text, "example 必须只含占位地址"


def test_example_config_is_loadable():
    cfg = load_deploy_config("config/deploy.example.toml")
    assert isinstance(cfg, DeployConfig)


def test_local_config_is_gitignored():
    from pathlib import Path

    assert "config/deploy.local.toml" in Path(".gitignore").read_text()


def test_password_is_not_in_repr(tmp_path):
    """密码不得出现在日志或异常里。"""
    path = tmp_path / "deploy.toml"
    path.write_text(EXAMPLE)
    cfg = load_deploy_config(path)
    assert "secret" not in repr(cfg)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_config.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.live.config'`

- [ ] **Step 3: 创建配置文件**

Create `config/deploy.example.toml`:

```toml
# 复制为 config/deploy.local.toml 并填入真实地址。
# deploy.local.toml 已被 gitignore —— 部署地址绝不进入代码库。
#
# Redis 若跨公网通信，必须同时满足：
#   1. tls = true
#   2. password 为强密码
#   3. 安全组只放行对方公网 IP
# Redis 默认无认证且明文传输，裸露在公网上通常几小时内就会被扫到。

[redis]
host = "127.0.0.1"
port = 6379
password = ""
tls = false
db = 0

[streams]
orders = "cmd:orders"       # 核心 -> Windows：下单指令
control = "cmd:control"     # 核心 -> Windows：启停 / 撤单 / 查询
fills = "evt:fills"         # Windows -> 核心：成交回报
rejects = "evt:rejects"     # Windows -> 核心：拒单回报
account = "state:account"   # Windows -> 核心：持仓资金快照
heartbeat = "state:heartbeat"

[agent]
consumer_group = "core"
consumer_name = "core-1"
```

Append to `.gitignore`:

```
config/deploy.local.toml
```

- [ ] **Step 4: 实现 `src/qsys/live/config.py`**

```python
"""部署配置。

真实地址只允许出现在 gitignored 的 deploy.local.toml——
公网 IP 固化进代码库是常见的隐患来源：仓库共享或公开时会一起泄露，
且地址变更时改动分散。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path("config/deploy.local.toml")
EXAMPLE_PATH = Path("config/deploy.example.toml")


@dataclass(frozen=True, slots=True)
class RedisConfig:
    host: str = "127.0.0.1"
    port: int = 6379
    password: str = field(default="", repr=False)  # 不进 repr / 日志
    tls: bool = False
    db: int = 0


@dataclass(frozen=True, slots=True)
class StreamConfig:
    orders: str = "cmd:orders"
    control: str = "cmd:control"
    fills: str = "evt:fills"
    rejects: str = "evt:rejects"
    account: str = "state:account"
    heartbeat: str = "state:heartbeat"


@dataclass(frozen=True, slots=True)
class DeployConfig:
    redis: RedisConfig = field(default_factory=RedisConfig)
    streams: StreamConfig = field(default_factory=StreamConfig)
    consumer_group: str = "core"
    consumer_name: str = "core-1"


def load_deploy_config(path: str | Path | None = None) -> DeployConfig:
    target = Path(path) if path is not None else DEFAULT_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"{target} 不存在。请复制 {EXAMPLE_PATH} 为 {DEFAULT_PATH} 并填入真实地址。"
        )
    raw = tomllib.loads(target.read_text())
    agent = raw.get("agent", {})
    return DeployConfig(
        redis=RedisConfig(**raw.get("redis", {})),
        streams=StreamConfig(**raw.get("streams", {})),
        consumer_group=agent.get("consumer_group", "core"),
        consumer_name=agent.get("consumer_name", "core-1"),
    )
```

- [ ] **Step 5: 实现 `src/qsys/live/redis_transport.py`**

```python
"""Redis Stream 传输实现。

redis-py 在模块顶层导入 —— 但本模块只在真实部署时被导入，
单元测试一律用 MemoryTransport。
"""

from __future__ import annotations

from typing import Any

from qsys.live.config import DeployConfig
from qsys.live.transport import StreamMessage, Transport


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class RedisTransport(Transport):
    def __init__(self, client: Any) -> None:
        self._r = client

    @classmethod
    def from_config(cls, cfg: DeployConfig) -> RedisTransport:
        import redis

        client = redis.Redis(
            host=cfg.redis.host,
            port=cfg.redis.port,
            password=cfg.redis.password or None,
            db=cfg.redis.db,
            ssl=cfg.redis.tls,
            decode_responses=False,
            socket_keepalive=True,
        )
        return cls(client)

    def append(self, stream: str, payload: dict[str, str]) -> str:
        return _decode(self._r.xadd(stream, payload))

    def ensure_group(self, stream: str, group: str) -> None:
        try:
            self._r.xgroup_create(stream, group, id="$", mkstream=True)
        except Exception as exc:  # BUSYGROUP：组已存在，属正常
            if "BUSYGROUP" not in str(exc):
                raise

    def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[StreamMessage]:
        response = self._r.xreadgroup(group, consumer, {stream: ">"}, count=count)
        out: list[StreamMessage] = []
        for _, entries in response or []:
            for message_id, fields in entries:
                out.append(
                    StreamMessage(
                        _decode(message_id),
                        {_decode(k): _decode(v) for k, v in fields.items()},
                    )
                )
        return out

    def ack(self, stream: str, group: str, message_id: str) -> None:
        self._r.xack(stream, group, message_id)

    def pending(self, stream: str, group: str) -> list[StreamMessage]:
        response = self._r.xreadgroup(group, "recovery", {stream: "0"}, count=1000)
        out: list[StreamMessage] = []
        for _, entries in response or []:
            for message_id, fields in entries:
                out.append(
                    StreamMessage(
                        _decode(message_id),
                        {_decode(k): _decode(v) for k, v in fields.items()},
                    )
                )
        return out

    def set_key(self, key: str, value: str) -> None:
        self._r.set(key, value)

    def get_key(self, key: str) -> str | None:
        value = self._r.get(key)
        return None if value is None else _decode(value)

    def delete_key(self, key: str) -> None:
        self._r.delete(key)
```

- [ ] **Step 6: 添加 redis 依赖**

Add `"redis>=5.0"` to the `dependencies` list in `pyproject.toml`, then:

Run: `uv sync`
Expected: redis 安装成功

- [ ] **Step 7: 运行测试确认通过**

Run: `uv run pytest tests/live/test_config.py -v`
Expected: PASS —— 7 passed

- [ ] **Step 8: 提交**

```bash
git add src/qsys/live/config.py src/qsys/live/redis_transport.py \
        config/deploy.example.toml .gitignore pyproject.toml uv.lock \
        tests/live/test_config.py
git commit -m "feat(live): Redis Stream 传输与部署配置"
```

---

## Task 4: 幂等去重器

**网络重传永不导致重复下单。** 这是实盘系统最不能出的错。

**Files:**
- Create: `src/qsys/live/idempotency.py`
- Test: `tests/live/test_idempotency.py`

**Interfaces:**
- Consumes: `Transport`（Task 2）
- Produces: `IdempotencyGuard(transport, *, prefix="idem:")` 带 `seen(client_order_id) -> bool`、`remember(client_order_id, result) -> None`、`recall(client_order_id) -> str | None`、`process_once(client_order_id, handler) -> tuple[str, bool]`（返回 `(结果, 是否为重放)`）

- [ ] **Step 1: 写失败测试**

Create `tests/live/test_idempotency.py`:

```python
import pytest

from qsys.live.idempotency import IdempotencyGuard
from qsys.live.transport import MemoryTransport


@pytest.fixture
def guard() -> IdempotencyGuard:
    return IdempotencyGuard(MemoryTransport())


def test_unseen_id_is_not_seen(guard):
    assert guard.seen("c-1") is False


def test_remembered_id_is_seen(guard):
    guard.remember("c-1", "B123")
    assert guard.seen("c-1") is True


def test_recall_returns_the_stored_result(guard):
    guard.remember("c-1", "B123")
    assert guard.recall("c-1") == "B123"


def test_recall_of_unknown_id_is_none(guard):
    assert guard.recall("c-1") is None


def test_process_once_runs_the_handler_the_first_time(guard):
    calls = []

    def handler() -> str:
        calls.append(1)
        return "B123"

    result, replayed = guard.process_once("c-1", handler)
    assert result == "B123"
    assert replayed is False
    assert calls == [1]


def test_process_once_replays_instead_of_rerunning(guard):
    """网络重传必须回放旧结果，绝不重复下单。"""
    calls = []

    def handler() -> str:
        calls.append(1)
        return f"B{len(calls)}"

    guard.process_once("c-1", handler)
    result, replayed = guard.process_once("c-1", handler)

    assert result == "B1"
    assert replayed is True
    assert calls == [1], "handler 被重复执行了——会造成重复下单"


def test_different_ids_run_independently(guard):
    guard.process_once("c-1", lambda: "A")
    result, replayed = guard.process_once("c-2", lambda: "B")
    assert result == "B"
    assert replayed is False


def test_handler_failure_is_not_remembered(guard):
    """失败的指令必须允许重试，不能被当成已处理。"""

    def failing() -> str:
        raise RuntimeError("broker down")

    with pytest.raises(RuntimeError):
        guard.process_once("c-1", failing)
    assert guard.seen("c-1") is False


def test_prefix_isolates_namespaces():
    transport = MemoryTransport()
    a = IdempotencyGuard(transport, prefix="a:")
    b = IdempotencyGuard(transport, prefix="b:")
    a.remember("c-1", "A")
    assert b.seen("c-1") is False


def test_state_survives_a_new_guard_on_the_same_transport():
    """代理重启后仍然记得已处理的指令。"""
    transport = MemoryTransport()
    IdempotencyGuard(transport).remember("c-1", "B123")
    assert IdempotencyGuard(transport).recall("c-1") == "B123"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_idempotency.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.live.idempotency'`

- [ ] **Step 3: 实现 `src/qsys/live/idempotency.py`**

```python
"""幂等去重。

client_order_id 由核心侧生成，Windows 侧维护已处理集合。
重复指令直接回放旧结果——网络重传永不导致重复下单，
这是实盘系统最不能出的错。

失败的指令不记入已处理集合，允许重试。
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class IdempotencyGuard:
    def __init__(self, transport: Transport, *, prefix: str = "idem:") -> None:
        self._transport = transport
        self._prefix = prefix

    def _key(self, client_order_id: str) -> str:
        return f"{self._prefix}{client_order_id}"

    def seen(self, client_order_id: str) -> bool:
        return self._transport.get_key(self._key(client_order_id)) is not None

    def remember(self, client_order_id: str, result: str) -> None:
        self._transport.set_key(self._key(client_order_id), result)

    def recall(self, client_order_id: str) -> str | None:
        return self._transport.get_key(self._key(client_order_id))

    def process_once(
        self, client_order_id: str, handler: Callable[[], str]
    ) -> tuple[str, bool]:
        """执行一次并记住结果；已处理过则回放，返回 (结果, 是否为重放)。"""
        previous = self.recall(client_order_id)
        if previous is not None:
            log.info("idempotency.replayed", client_order_id=client_order_id)
            return previous, True

        result = handler()  # 失败时异常上抛，不记入已处理集合
        self.remember(client_order_id, result)
        return result, False
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live/test_idempotency.py -v`
Expected: PASS —— 10 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/idempotency.py tests/live/test_idempotency.py
git commit -m "feat(live): 幂等去重器"
```

---

## Task 5: 双路径 Kill Switch

**必须有一条不依赖 Web、不依赖核心进程的独立路径**——Web 挂了不能导致你停不下来。

**Files:**
- Create: `src/qsys/live/kill_switch.py`
- Create: `scripts/kill.py`
- Test: `tests/live/test_kill_switch.py`

**Interfaces:**
- Consumes: `Transport`（Task 2）
- Produces: `KillSwitch(transport, *, key="control:kill", local_path=None)` 带 `engage(reason)`、`release()`、`is_engaged() -> bool`、`reason() -> str`

- [ ] **Step 1: 写失败测试**

Create `tests/live/test_kill_switch.py`:

```python
import pytest

from qsys.live.kill_switch import KillSwitch
from qsys.live.transport import MemoryTransport


@pytest.fixture
def ks(tmp_path) -> KillSwitch:
    return KillSwitch(MemoryTransport(), local_path=tmp_path / "KILL")


def test_starts_disengaged(ks):
    assert ks.is_engaged() is False
    assert ks.reason() == ""


def test_engage_sets_the_flag(ks):
    ks.engage("manual stop")
    assert ks.is_engaged() is True
    assert "manual stop" in ks.reason()


def test_release_clears_the_flag(ks):
    ks.engage("x")
    ks.release()
    assert ks.is_engaged() is False


def test_local_file_alone_engages_the_switch(tmp_path):
    """不依赖 Redis 的独立路径：文件存在即停机。"""
    path = tmp_path / "KILL"
    ks = KillSwitch(MemoryTransport(), local_path=path)
    path.write_text("emergency")
    assert ks.is_engaged() is True
    assert "emergency" in ks.reason()


def test_redis_key_alone_engages_the_switch(tmp_path):
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=tmp_path / "KILL")
    transport.set_key("control:kill", "from web")
    assert ks.is_engaged() is True
    assert "from web" in ks.reason()


def test_release_clears_both_paths(tmp_path):
    path = tmp_path / "KILL"
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=path)
    ks.engage("x")
    assert path.exists()
    ks.release()
    assert not path.exists()
    assert transport.get_key("control:kill") is None


def test_engage_is_idempotent(ks):
    ks.engage("first")
    ks.engage("second")
    assert ks.is_engaged() is True


def test_release_when_not_engaged_is_not_an_error(ks):
    ks.release()


def test_works_without_a_local_path():
    """未配置本地路径时退化为纯 Redis 单路径，但仍可用。"""
    ks = KillSwitch(MemoryTransport(), local_path=None)
    ks.engage("x")
    assert ks.is_engaged() is True
    ks.release()
    assert ks.is_engaged() is False


def test_custom_key_is_honored(tmp_path):
    transport = MemoryTransport()
    ks = KillSwitch(transport, key="control:halt", local_path=tmp_path / "K")
    ks.engage("x")
    assert transport.get_key("control:halt") is not None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_kill_switch.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.live.kill_switch'`

- [ ] **Step 3: 实现 `src/qsys/live/kill_switch.py`**

```python
"""Kill switch —— 双路径。

Redis key 供 Web 与核心进程使用；本地文件是**不依赖任何服务**的独立路径。
Web 挂了、核心进程挂了、Redis 连不上——只要能碰到文件系统就能停机。

两条路径任一生效即视为停机（或的关系）：宁可误停，不可停不下来。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import structlog

from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class KillSwitch:
    def __init__(
        self,
        transport: Transport,
        *,
        key: str = "control:kill",
        local_path: str | Path | None = None,
    ) -> None:
        self._transport = transport
        self._key = key
        self._path = Path(local_path) if local_path is not None else None

    def engage(self, reason: str) -> None:
        stamp = f"{datetime.now().isoformat()} {reason}"
        self._transport.set_key(self._key, stamp)
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(stamp)
        log.warning("kill_switch.engaged", reason=reason)

    def release(self) -> None:
        self._transport.delete_key(self._key)
        if self._path is not None and self._path.exists():
            self._path.unlink()
        log.warning("kill_switch.released")

    def _local_reason(self) -> str | None:
        if self._path is None or not self._path.exists():
            return None
        return self._path.read_text().strip()

    def is_engaged(self) -> bool:
        return self._transport.get_key(self._key) is not None or self._local_reason() is not None

    def reason(self) -> str:
        parts = [
            self._transport.get_key(self._key),
            self._local_reason(),
        ]
        return " | ".join(p for p in parts if p)
```

- [ ] **Step 4: 实现 `scripts/kill.py`**

```python
"""独立 kill switch —— 不依赖 Web、不依赖核心进程。

即使 Web 服务挂了、核心进程崩了，只要能运行这个脚本就能停机。
连 Redis 都连不上时，它仍会写下本地停机标记。

用法：
  uv run python scripts/kill.py stop "原因"
  uv run python scripts/kill.py status
  uv run python scripts/kill.py resume
"""

from __future__ import annotations

import sys
from pathlib import Path

LOCAL_KILL_FILE = Path("var/KILL")


def _switch():
    from qsys.live.kill_switch import KillSwitch
    from qsys.live.transport import MemoryTransport

    try:
        from qsys.live.config import load_deploy_config
        from qsys.live.redis_transport import RedisTransport

        transport = RedisTransport.from_config(load_deploy_config())
        transport.get_key("control:kill")  # 探活
    except Exception as exc:  # Redis 不可达也必须能停机
        print(f"[warn] Redis 不可用（{exc}），仅使用本地停机标记", file=sys.stderr)
        transport = MemoryTransport()

    return KillSwitch(transport, local_path=LOCAL_KILL_FILE)


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in {"stop", "resume", "status"}:
        print(__doc__)
        return 2

    action = sys.argv[1]
    switch = _switch()

    if action == "stop":
        reason = sys.argv[2] if len(sys.argv) > 2 else "manual"
        switch.engage(reason)
        print(f"已停机: {reason}")
    elif action == "resume":
        switch.release()
        print("已恢复")
    else:
        engaged = switch.is_engaged()
        print(f"状态: {'已停机' if engaged else '运行中'}")
        if engaged:
            print(f"原因: {switch.reason()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/live/test_kill_switch.py -v`
Expected: PASS —— 10 passed

- [ ] **Step 6: 确认脚本可用**

Run: `uv run python scripts/kill.py status`
Expected: 打印 `Redis 不可用` 的警告（本机无 Redis），随后 `状态: 运行中`，退出码 0

- [ ] **Step 7: 提交**

```bash
git add src/qsys/live/kill_switch.py scripts/kill.py tests/live/test_kill_switch.py
git commit -m "feat(live): 双路径 kill switch 与独立停机脚本"
```

---

## Task 6: 风控规则集

风控闸门与约束系统职责不同：**闸门回答「能不能发这一单」（是/否），约束回答「能发多少」（数量）**。闸门管的是组合级与交易日级的限制。

**Files:**
- Create: `src/qsys/live/risk.py`
- Test: `tests/live/test_risk.py`

**Interfaces:**
- Consumes: `OrderIntent` / `Side`（1-2a）、`Portfolio`（1-2a）、`TradingCalendar`（1-1）、`KillSwitch`（Task 5）
- Produces: `RiskContext(portfolio, now, reference_price, day_start_value, turnover_today)`；`RiskVerdict(allowed, reason="")`；`RiskRule` (ABC: `name`, `check(intent, ctx) -> RiskVerdict`)；`KillSwitchRule(kill_switch)`、`MaxPositionWeightRule(max_weight)`、`MaxDailyTurnoverRule(max_ratio)`、`DailyLossCircuitBreaker(max_loss_ratio)`、`PriceDeviationRule(max_deviation, reference)`、`TradingHoursRule(calendar, open_time, close_time)`

- [ ] **Step 1: 写失败测试**

Create `tests/live/test_risk.py`:

```python
from datetime import date, datetime, time

import pytest

from qsys.core.calendar import AShareCalendar
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderIntent, Side
from qsys.core.types import Market, Symbol
from qsys.live.kill_switch import KillSwitch
from qsys.live.risk import (
    DailyLossCircuitBreaker,
    KillSwitchRule,
    MaxDailyTurnoverRule,
    MaxPositionWeightRule,
    PriceDeviationRule,
    RiskContext,
    TradingHoursRule,
)
from qsys.live.transport import MemoryTransport

CMB = Symbol("600036", Market.SH)
SESSIONS = [date(2024, 1, 3)]
CAL = AShareCalendar(SESSIONS)
T = datetime(2024, 1, 3, 9, 45)


def ctx(*, held: int = 0, cash: float = 100_000.0, price: float = 10.0,
        day_start: float = 100_000.0, turnover: float = 0.0,
        now: datetime = T) -> RiskContext:
    p = Portfolio(cash)
    if held:
        p.apply_fill(Fill("o", CMB, Side.BUY, held, price, 0.0, T))
        p.unfreeze()
    return RiskContext(portfolio=p, now=now, reference_price=price,
                       day_start_value=day_start, turnover_today=turnover)


BUY = OrderIntent(CMB, Side.BUY, 100)
SELL = OrderIntent(CMB, Side.SELL, 100)


# --- KillSwitchRule ---


def test_kill_switch_allows_when_disengaged(tmp_path):
    ks = KillSwitch(MemoryTransport(), local_path=tmp_path / "K")
    assert KillSwitchRule(ks).check(BUY, ctx()).allowed is True


def test_kill_switch_blocks_everything_when_engaged(tmp_path):
    ks = KillSwitch(MemoryTransport(), local_path=tmp_path / "K")
    ks.engage("stop")
    rule = KillSwitchRule(ks)
    assert rule.check(BUY, ctx()).allowed is False
    assert rule.check(SELL, ctx()).allowed is False


def test_kill_switch_reason_is_surfaced(tmp_path):
    ks = KillSwitch(MemoryTransport(), local_path=tmp_path / "K")
    ks.engage("circuit breaker")
    assert "circuit breaker" in KillSwitchRule(ks).check(BUY, ctx()).reason


# --- MaxPositionWeightRule ---


def test_position_weight_allows_within_limit():
    # 总值 100000，已持 1000 股 @10 = 10000，再买 100 股 -> 11000 = 11%
    r = MaxPositionWeightRule(0.20)
    assert r.check(BUY, ctx(held=1000)).allowed is True


def test_position_weight_blocks_beyond_limit():
    r = MaxPositionWeightRule(0.10)
    verdict = r.check(BUY, ctx(held=1000))
    assert verdict.allowed is False
    assert "weight" in verdict.reason


def test_position_weight_never_blocks_a_sell():
    """减仓永远放行——风控不能把人锁在仓位里。"""
    r = MaxPositionWeightRule(0.01)
    assert r.check(SELL, ctx(held=5000)).allowed is True


# --- MaxDailyTurnoverRule ---


def test_turnover_allows_within_limit():
    r = MaxDailyTurnoverRule(0.50)
    assert r.check(BUY, ctx(turnover=10_000.0)).allowed is True


def test_turnover_blocks_beyond_limit():
    r = MaxDailyTurnoverRule(0.10)
    verdict = r.check(BUY, ctx(turnover=9_500.0))
    assert verdict.allowed is False
    assert "turnover" in verdict.reason


# --- DailyLossCircuitBreaker ---


def test_circuit_breaker_allows_within_drawdown():
    r = DailyLossCircuitBreaker(0.05)
    assert r.check(BUY, ctx(cash=98_000.0, day_start=100_000.0)).allowed is True


def test_circuit_breaker_blocks_after_a_big_loss():
    r = DailyLossCircuitBreaker(0.02)
    verdict = r.check(BUY, ctx(cash=95_000.0, day_start=100_000.0))
    assert verdict.allowed is False
    assert "loss" in verdict.reason


def test_circuit_breaker_still_allows_selling():
    """熔断后仍可减仓——否则会被锁死在亏损仓位里。"""
    r = DailyLossCircuitBreaker(0.02)
    assert r.check(SELL, ctx(cash=95_000.0, day_start=100_000.0, held=100)).allowed is True


# --- PriceDeviationRule ---


def test_price_deviation_allows_normal_price():
    r = PriceDeviationRule(0.10)
    assert r.check(BUY, ctx(price=10.0), reference=10.2).allowed is True


def test_price_deviation_blocks_absurd_price():
    """防参数写错下出离谱价格。"""
    r = PriceDeviationRule(0.10)
    verdict = r.check(BUY, ctx(price=100.0), reference=10.0)
    assert verdict.allowed is False
    assert "deviation" in verdict.reason


def test_price_deviation_without_a_reference_allows():
    r = PriceDeviationRule(0.10)
    assert r.check(BUY, ctx(price=10.0), reference=None).allowed is True


# --- TradingHoursRule ---


def test_trading_hours_allows_during_session():
    r = TradingHoursRule(CAL)
    assert r.check(BUY, ctx(now=datetime(2024, 1, 3, 10, 0))).allowed is True


def test_trading_hours_blocks_before_open():
    r = TradingHoursRule(CAL)
    verdict = r.check(BUY, ctx(now=datetime(2024, 1, 3, 8, 0)))
    assert verdict.allowed is False
    assert "hours" in verdict.reason


def test_trading_hours_blocks_after_close():
    r = TradingHoursRule(CAL)
    assert r.check(BUY, ctx(now=datetime(2024, 1, 3, 16, 0))).allowed is False


def test_trading_hours_blocks_on_a_non_trading_day():
    r = TradingHoursRule(CAL)
    verdict = r.check(BUY, ctx(now=datetime(2024, 1, 6, 10, 0)))
    assert verdict.allowed is False
    assert "trading day" in verdict.reason


def test_trading_hours_allows_lunch_break_by_default():
    """午休期间挂单是常见做法（排队等下午开盘），默认不拦。"""
    r = TradingHoursRule(CAL)
    assert r.check(BUY, ctx(now=datetime(2024, 1, 3, 12, 0))).allowed is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_risk.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.live.risk'`

- [ ] **Step 3: 实现 `src/qsys/live/risk.py`（规则部分）**

```python
"""风控规则。

与约束系统职责不同：
  闸门（本模块）—— 能不能发这一单？是 / 否。管组合级与交易日级限制。
  约束（core）   —— 能发多少？管市场规则与数量夹取。

减仓类规则一律放行卖出——风控不能把人锁在仓位里，尤其是熔断之后。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time

from qsys.core.calendar import TradingCalendar
from qsys.core.portfolio import Portfolio
from qsys.core.trading import OrderIntent, Side
from qsys.live.kill_switch import KillSwitch


@dataclass(frozen=True, slots=True)
class RiskContext:
    portfolio: Portfolio
    now: datetime
    reference_price: float
    day_start_value: float
    turnover_today: float = 0.0


@dataclass(frozen=True, slots=True)
class RiskVerdict:
    allowed: bool
    reason: str = ""


class RiskRule(ABC):
    name: str

    @abstractmethod
    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict: ...


class KillSwitchRule(RiskRule):
    name = "kill_switch"

    def __init__(self, kill_switch: KillSwitch) -> None:
        self._ks = kill_switch

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        if self._ks.is_engaged():
            return RiskVerdict(False, f"kill switch engaged: {self._ks.reason()}")
        return RiskVerdict(True)


class MaxPositionWeightRule(RiskRule):
    name = "max_position_weight"

    def __init__(self, max_weight: float) -> None:
        if not 0 < max_weight <= 1:
            raise ValueError(f"max_weight must be in (0, 1]: {max_weight}")
        self._max = max_weight

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        if intent.side is Side.SELL:
            return RiskVerdict(True)  # 减仓永远放行
        held = ctx.portfolio.position(intent.symbol).total_qty
        prospective = (held + intent.qty) * ctx.reference_price
        total = ctx.portfolio.cash + sum(
            p.total_qty * ctx.reference_price for p in ctx.portfolio.positions.values()
        )
        if total <= 0:
            return RiskVerdict(False, "portfolio value is non-positive")
        weight = prospective / total
        if weight > self._max:
            return RiskVerdict(
                False, f"position weight {weight:.2%} exceeds limit {self._max:.2%}"
            )
        return RiskVerdict(True)


class MaxDailyTurnoverRule(RiskRule):
    name = "max_daily_turnover"

    def __init__(self, max_ratio: float) -> None:
        if max_ratio <= 0:
            raise ValueError(f"max_ratio must be > 0: {max_ratio}")
        self._max = max_ratio

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        prospective = ctx.turnover_today + intent.qty * ctx.reference_price
        if ctx.day_start_value <= 0:
            return RiskVerdict(True)
        ratio = prospective / ctx.day_start_value
        if ratio > self._max:
            return RiskVerdict(
                False, f"daily turnover {ratio:.2%} exceeds limit {self._max:.2%}"
            )
        return RiskVerdict(True)


class DailyLossCircuitBreaker(RiskRule):
    name = "daily_loss_breaker"

    def __init__(self, max_loss_ratio: float) -> None:
        if not 0 < max_loss_ratio < 1:
            raise ValueError(f"max_loss_ratio must be in (0, 1): {max_loss_ratio}")
        self._max = max_loss_ratio

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        if intent.side is Side.SELL:
            return RiskVerdict(True)  # 熔断后仍可减仓，否则会被锁死在亏损仓位里
        if ctx.day_start_value <= 0:
            return RiskVerdict(True)
        current = ctx.portfolio.cash + sum(
            p.total_qty * ctx.reference_price for p in ctx.portfolio.positions.values()
        )
        loss = 1.0 - current / ctx.day_start_value
        if loss > self._max:
            return RiskVerdict(
                False, f"daily loss {loss:.2%} exceeds limit {self._max:.2%}"
            )
        return RiskVerdict(True)


class PriceDeviationRule(RiskRule):
    """防参数写错下出离谱价格。"""

    name = "price_deviation"

    def __init__(self, max_deviation: float) -> None:
        if max_deviation <= 0:
            raise ValueError(f"max_deviation must be > 0: {max_deviation}")
        self._max = max_deviation

    def check(
        self, intent: OrderIntent, ctx: RiskContext, *, reference: float | None = None,
        **kwargs,
    ) -> RiskVerdict:
        if reference is None or reference <= 0:
            return RiskVerdict(True)
        deviation = abs(ctx.reference_price / reference - 1.0)
        if deviation > self._max:
            return RiskVerdict(
                False,
                f"price deviation {deviation:.2%} from {reference:.2f} "
                f"exceeds limit {self._max:.2%}",
            )
        return RiskVerdict(True)


class TradingHoursRule(RiskRule):
    name = "trading_hours"

    def __init__(
        self,
        calendar: TradingCalendar,
        open_time: time = time(9, 15),
        close_time: time = time(15, 5),
    ) -> None:
        self._calendar = calendar
        self._open = open_time
        self._close = close_time

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        day = ctx.now.date()
        if not self._calendar.is_trading_day(day):
            return RiskVerdict(False, f"{day} is not a trading day")
        clock = ctx.now.time()
        if not (self._open <= clock <= self._close):
            # 午休期间挂单是常见做法（排队等下午开盘），故不拦
            return RiskVerdict(False, f"{clock} outside trading hours")
        return RiskVerdict(True)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live/test_risk.py -v`
Expected: PASS —— 20 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/risk.py tests/live/test_risk.py
git commit -m "feat(live): 风控规则集"
```

---

## Task 7: 风控闸门

**Files:**
- Modify: `src/qsys/live/risk.py`（追加）
- Modify: `tests/live/test_risk.py`（追加）

**Interfaces:**
- Consumes: Task 6 的全部规则
- Produces: `RiskDecision(allowed, checks: tuple[tuple[str, RiskVerdict], ...])` 带 `blocking_rule: str`、`reason: str`、`as_log_fields() -> dict`；`RiskGate(rules)` 带 `check(intent, ctx, **kwargs) -> RiskDecision`、`names`；`default_ashare_gate(kill_switch, calendar, *, max_position_weight=0.20, max_daily_turnover=1.0, max_daily_loss=0.05, max_price_deviation=0.10) -> RiskGate`

- [ ] **Step 1: 追加失败测试**

Append to `tests/live/test_risk.py`:

```python


# --- RiskGate ---

from qsys.live.risk import RiskGate, default_ashare_gate


def gate(tmp_path, **kw):
    ks = KillSwitch(MemoryTransport(), local_path=tmp_path / "K")
    return default_ashare_gate(ks, CAL, **kw), ks


def test_gate_allows_a_clean_order(tmp_path):
    g, _ = gate(tmp_path)
    assert g.check(BUY, ctx()).allowed is True


def test_gate_stops_at_the_first_blocking_rule(tmp_path):
    g, ks = gate(tmp_path)
    ks.engage("halt")
    decision = g.check(BUY, ctx())
    assert decision.allowed is False
    assert decision.blocking_rule == "kill_switch"
    assert [name for name, _ in decision.checks] == ["kill_switch"]


def test_gate_records_every_rule_that_ran(tmp_path):
    g, _ = gate(tmp_path)
    decision = g.check(BUY, ctx())
    assert [name for name, _ in decision.checks] == list(g.names)


def test_gate_reason_names_the_blocking_rule(tmp_path):
    g, _ = gate(tmp_path, max_position_weight=0.01)
    decision = g.check(BUY, ctx(held=1000))
    assert decision.allowed is False
    assert decision.reason.startswith("max_position_weight")


def test_gate_log_fields_are_flat(tmp_path):
    g, _ = gate(tmp_path)
    fields = g.check(BUY, ctx()).as_log_fields()
    assert fields["allowed"] is True
    assert isinstance(fields["checks"], str)


def test_empty_gate_allows_everything():
    assert RiskGate([]).check(BUY, ctx()).allowed is True


def test_default_gate_rule_order_puts_kill_switch_first(tmp_path):
    """kill switch 必须最先检查——按下之后一切都该立即停。"""
    g, _ = gate(tmp_path)
    assert g.names[0] == "kill_switch"


def test_default_gate_includes_all_six_rules(tmp_path):
    g, _ = gate(tmp_path)
    assert set(g.names) == {
        "kill_switch", "trading_hours", "price_deviation",
        "daily_loss_breaker", "max_position_weight", "max_daily_turnover",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_risk.py -v`
Expected: FAIL —— `ImportError: cannot import name 'RiskGate'`

- [ ] **Step 3: 追加实现到 `src/qsys/live/risk.py`**

先在 import 区补上 `from collections.abc import Sequence`，再追加：

```python


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    checks: tuple[tuple[str, RiskVerdict], ...] = ()

    @property
    def blocking_rule(self) -> str:
        for name, verdict in self.checks:
            if not verdict.allowed:
                return name
        return ""

    @property
    def reason(self) -> str:
        for name, verdict in self.checks:
            if not verdict.allowed:
                return f"{name}: {verdict.reason}"
        return ""

    def as_log_fields(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "blocking_rule": self.blocking_rule,
            "reason": self.reason,
            "checks": ",".join(n for n, _ in self.checks),
        }


class RiskGate:
    """按序检查，任一条拒绝即立即停止。"""

    def __init__(self, rules: Sequence[RiskRule]) -> None:
        self._rules = tuple(rules)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(r.name for r in self._rules)

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskDecision:
        checks: list[tuple[str, RiskVerdict]] = []
        for rule in self._rules:
            verdict = rule.check(intent, ctx, **kwargs)
            checks.append((rule.name, verdict))
            if not verdict.allowed:
                return RiskDecision(False, tuple(checks))
        return RiskDecision(True, tuple(checks))


def default_ashare_gate(
    kill_switch: KillSwitch,
    calendar: TradingCalendar,
    *,
    max_position_weight: float = 0.20,
    max_daily_turnover: float = 1.0,
    max_daily_loss: float = 0.05,
    max_price_deviation: float = 0.10,
) -> RiskGate:
    """默认 A 股风控闸门。

    kill switch 必须最先检查——按下之后一切都该立即停。
    """
    return RiskGate(
        [
            KillSwitchRule(kill_switch),
            TradingHoursRule(calendar),
            PriceDeviationRule(max_price_deviation),
            DailyLossCircuitBreaker(max_daily_loss),
            MaxPositionWeightRule(max_position_weight),
            MaxDailyTurnoverRule(max_daily_turnover),
        ]
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live/test_risk.py -v`
Expected: PASS —— 28 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/risk.py tests/live/test_risk.py
git commit -m "feat(live): 风控闸门与 A 股默认规则组合"
```

---

## Task 8: LiveBroker

实现与 `SimBroker` 相同的 `Broker` 接口——回测与实盘的差异只可能来自这一个边界。

**Files:**
- Create: `src/qsys/live/live_broker.py`
- Test: `tests/live/test_live_broker.py`

**Interfaces:**
- Consumes: `Broker`（1-2b）、`Transport`（Task 2）、消息模型（Task 1）、`RiskGate` / `RiskContext`（Task 7）
- Produces: `LiveBroker(transport, streams, *, risk_gate, risk_context_factory, group="core", consumer="core-1")` 实现 `Broker`，另有 `account_snapshot() -> AccountSnapshot | None`、`blocked: tuple[Reject, ...]`

- [ ] **Step 1: 写失败测试**

Create `tests/live/test_live_broker.py`:

```python
from datetime import date, datetime

import pytest

from qsys.core.calendar import AShareCalendar
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.live.config import StreamConfig
from qsys.live.kill_switch import KillSwitch
from qsys.live.live_broker import LiveBroker
from qsys.live.messages import (
    AccountSnapshot,
    ControlCommand,
    FillEvent,
    OrderCommand,
    PositionSnapshot,
    RejectEvent,
)
from qsys.live.risk import RiskContext, default_ashare_gate
from qsys.live.transport import MemoryTransport

CMB = Symbol("600036", Market.SH)
SESSIONS = [date(2024, 1, 3)]
CAL = AShareCalendar(SESSIONS)
T = datetime(2024, 1, 3, 10, 0)
STREAMS = StreamConfig()


def order(side=Side.BUY, qty=100, oid="o1") -> Order:
    return Order(oid, CMB, side, qty, OrderType.MARKET, None, T)


def make(tmp_path, **kw):
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=tmp_path / "K")
    gate = default_ashare_gate(ks, CAL, **kw)

    def factory() -> RiskContext:
        return RiskContext(portfolio=Portfolio(1_000_000.0), now=T,
                           reference_price=10.0, day_start_value=1_000_000.0)

    broker = LiveBroker(transport, STREAMS, risk_gate=gate, risk_context_factory=factory)
    return broker, transport, ks


def test_submit_appends_an_order_command(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order())
    msgs = t.all_messages(STREAMS.orders)
    assert len(msgs) == 1
    cmd = OrderCommand.from_payload(msgs[0].payload)
    assert cmd.symbol == "600036.SH"
    assert cmd.qty == 100
    assert cmd.client_order_id == "o1"


def test_order_id_is_used_as_client_order_id(tmp_path):
    """client_order_id 由核心侧生成——幂等的基础。"""
    b, t, _ = make(tmp_path)
    b.submit(order(oid="abc-123"))
    cmd = OrderCommand.from_payload(t.all_messages(STREAMS.orders)[0].payload)
    assert cmd.client_order_id == "abc-123"


def test_risk_gate_blocks_the_order(tmp_path):
    b, t, ks = make(tmp_path)
    ks.engage("halt")
    b.submit(order())
    assert t.all_messages(STREAMS.orders) == []


def test_blocked_order_produces_a_reject(tmp_path):
    b, _, ks = make(tmp_path)
    ks.engage("halt")
    b.submit(order())
    rejects = b.poll_rejects()
    assert len(rejects) == 1
    assert "kill_switch" in rejects[0].reason


def test_pending_tracks_unfilled_orders(tmp_path):
    b, _, _ = make(tmp_path)
    b.submit(order())
    assert len(b.pending()) == 1


def test_poll_fills_reads_the_fill_stream(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order())
    t.append(STREAMS.fills, FillEvent(
        client_order_id="o1", symbol="600036.SH", side="buy", qty=100,
        price=10.5, fee=5.01, filled_at=T, broker_order_id="B1",
    ).to_payload())
    fills = b.poll_fills()
    assert len(fills) == 1
    assert fills[0].qty == 100
    assert fills[0].price == 10.5
    assert fills[0].order_id == "o1"


def test_fill_clears_the_pending_order(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order())
    t.append(STREAMS.fills, FillEvent(
        client_order_id="o1", symbol="600036.SH", side="buy", qty=100,
        price=10.5, fee=5.01, filled_at=T,
    ).to_payload())
    b.poll_fills()
    assert b.pending() == ()


def test_poll_fills_drains(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order())
    t.append(STREAMS.fills, FillEvent(
        client_order_id="o1", symbol="600036.SH", side="buy", qty=100,
        price=10.5, fee=0.0, filled_at=T,
    ).to_payload())
    assert len(b.poll_fills()) == 1
    assert b.poll_fills() == ()


def test_poll_rejects_reads_the_reject_stream(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order())
    t.append(STREAMS.rejects, RejectEvent(
        client_order_id="o1", symbol="600036.SH", reason="broker rejected", at=T,
    ).to_payload())
    rejects = b.poll_rejects()
    assert len(rejects) == 1
    assert rejects[0].reason == "broker rejected"


def test_on_market_data_is_a_no_op(tmp_path):
    """真实市场自己撮合——实盘 Broker 忽略行情推送。"""
    b, _, _ = make(tmp_path)
    b.on_market_data(None)
    assert b.poll_fills() == ()


def test_cancel_all_sends_a_control_command(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order())
    b.cancel_all()
    controls = t.all_messages(STREAMS.control)
    assert len(controls) == 1
    assert ControlCommand.from_payload(controls[0].payload).action == "cancel_all"
    assert b.pending() == ()


def test_account_snapshot_reads_the_latest_state(tmp_path):
    b, t, _ = make(tmp_path)
    t.set_key(STREAMS.account, AccountSnapshot(
        at=T, cash=98_765.43,
        positions={"600036.SH": PositionSnapshot(total_qty=500, available_qty=500,
                                                 avg_cost=27.63)},
    ).to_payload()["data"])
    snap = b.account_snapshot()
    assert snap is not None
    assert snap.cash == 98_765.43
    assert snap.positions["600036.SH"].total_qty == 500


def test_account_snapshot_is_none_when_absent(tmp_path):
    b, _, _ = make(tmp_path)
    assert b.account_snapshot() is None


def test_unknown_fill_is_ignored_not_fatal(tmp_path):
    """回报里出现未知 client_order_id 时不应崩溃——记录告警即可。"""
    b, t, _ = make(tmp_path)
    t.append(STREAMS.fills, FillEvent(
        client_order_id="ghost", symbol="600036.SH", side="buy", qty=1,
        price=1.0, fee=0.0, filled_at=T,
    ).to_payload())
    fills = b.poll_fills()
    assert len(fills) == 1  # 仍然交给上层，由对账发现异常
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_live_broker.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.live.live_broker'`

- [ ] **Step 3: 实现 `src/qsys/live/live_broker.py`**

```python
"""实盘 Broker。

实现与 SimBroker 相同的 Broker 接口——回测与实盘的差异只可能来自
这一个边界，因而可以逐笔归因。

风控闸门在发单前生效；被拦的订单产生 Reject 并记录，绝不静默丢弃。
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from qsys.backtest.broker import Broker
from qsys.core.events import MarketSnapshot, Reject
from qsys.core.trading import Fill, Order, OrderIntent, Side
from qsys.core.types import Symbol
from qsys.live.config import StreamConfig
from qsys.live.messages import (
    AccountSnapshot,
    ControlCommand,
    FillEvent,
    OrderCommand,
    RejectEvent,
)
from qsys.live.risk import RiskContext, RiskGate
from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class LiveBroker(Broker):
    def __init__(
        self,
        transport: Transport,
        streams: StreamConfig,
        *,
        risk_gate: RiskGate,
        risk_context_factory: Callable[[], RiskContext],
        group: str = "core",
        consumer: str = "core-1",
    ) -> None:
        self._t = transport
        self._s = streams
        self._gate = risk_gate
        self._risk_ctx = risk_context_factory
        self._group = group
        self._consumer = consumer
        self._pending: dict[str, Order] = {}
        self._rejects: list[Reject] = []
        for stream in (streams.fills, streams.rejects):
            self._t.ensure_group(stream, group)

    # --- Broker 接口 ---

    def submit(self, order: Order) -> None:
        intent = OrderIntent(order.symbol, order.side, order.qty, order.order_type,
                             order.limit_price)
        decision = self._gate.check(intent, self._risk_ctx())
        if not decision.allowed:
            log.warning("live_broker.blocked", client_order_id=order.order_id,
                        symbol=str(order.symbol), **decision.as_log_fields())
            self._rejects.append(
                Reject(order.order_id, order.symbol, order.side, order.qty,
                       decision.reason, order.created_at)
            )
            return

        self._t.append(
            self._s.orders,
            OrderCommand(
                client_order_id=order.order_id,
                symbol=str(order.symbol),
                side=order.side.value,
                qty=order.qty,
                order_type=order.order_type.value,
                limit_price=order.limit_price,
                created_at=order.created_at,
            ).to_payload(),
        )
        self._pending[order.order_id] = order
        log.info("live_broker.submitted", client_order_id=order.order_id,
                 symbol=str(order.symbol), side=order.side.value, qty=order.qty)

    def on_market_data(self, snapshot: MarketSnapshot | None) -> None:
        """实盘由真实市场撮合，忽略行情推送。"""

    def poll_fills(self) -> tuple[Fill, ...]:
        out: list[Fill] = []
        for msg in self._t.read_group(self._s.fills, self._group, self._consumer, count=100):
            event = FillEvent.from_payload(msg.payload)
            self._t.ack(self._s.fills, self._group, msg.id)
            self._pending.pop(event.client_order_id, None)
            out.append(
                Fill(
                    order_id=event.client_order_id,
                    symbol=Symbol.parse(event.symbol),
                    side=Side(event.side),
                    qty=event.qty,
                    price=event.price,
                    fee=event.fee,
                    filled_at=event.filled_at,
                )
            )
        return tuple(out)

    def poll_rejects(self) -> tuple[Reject, ...]:
        out = list(self._rejects)
        self._rejects.clear()
        for msg in self._t.read_group(self._s.rejects, self._group, self._consumer, count=100):
            event = RejectEvent.from_payload(msg.payload)
            self._t.ack(self._s.rejects, self._group, msg.id)
            order = self._pending.pop(event.client_order_id, None)
            out.append(
                Reject(
                    order_id=event.client_order_id,
                    symbol=Symbol.parse(event.symbol),
                    side=order.side if order else Side.BUY,
                    qty=order.qty if order else 0,
                    reason=event.reason,
                    at=event.at,
                )
            )
        return tuple(out)

    def pending(self) -> tuple[Order, ...]:
        return tuple(self._pending.values())

    def cancel_all(self) -> None:
        from datetime import datetime

        self._t.append(
            self._s.control,
            ControlCommand(action="cancel_all", at=datetime.now()).to_payload(),
        )
        self._pending.clear()
        log.warning("live_broker.cancel_all")

    # --- 实盘专有 ---

    def account_snapshot(self) -> AccountSnapshot | None:
        raw = self._t.get_key(self._s.account)
        if raw is None:
            return None
        return AccountSnapshot.model_validate_json(raw)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live/test_live_broker.py -v`
Expected: PASS —— 14 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/live_broker.py tests/live/test_live_broker.py
git commit -m "feat(live): LiveBroker"
```

---

## Task 9: ShadowBroker

**影子模式是独立的类，不是布尔开关。** 一个能静默决定真钱动不动的参数太危险——类型不同则不可能传错。

**Files:**
- Create: `src/qsys/live/shadow_broker.py`
- Test: `tests/live/test_shadow_broker.py`

**Interfaces:**
- Consumes: `Broker`（1-2b）、`Transport`（Task 2）、`RiskGate`（Task 7）
- Produces: `ShadowBroker(transport, streams, *, risk_gate, risk_context_factory, journal_stream="shadow:intents")` 实现 `Broker`，另有 `intents: tuple[Order, ...]`（本次会话记录的全部意图）

- [ ] **Step 1: 写失败测试**

Create `tests/live/test_shadow_broker.py`:

```python
from datetime import date, datetime

from qsys.core.calendar import AShareCalendar
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.live.config import StreamConfig
from qsys.live.kill_switch import KillSwitch
from qsys.live.messages import OrderCommand
from qsys.live.risk import RiskContext, default_ashare_gate
from qsys.live.shadow_broker import ShadowBroker
from qsys.live.transport import MemoryTransport

CMB = Symbol("600036", Market.SH)
CAL = AShareCalendar([date(2024, 1, 3)])
T = datetime(2024, 1, 3, 10, 0)
STREAMS = StreamConfig()


def order(oid="o1", qty=100) -> Order:
    return Order(oid, CMB, Side.BUY, qty, OrderType.MARKET, None, T)


def make(tmp_path):
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=tmp_path / "K")
    gate = default_ashare_gate(ks, CAL)

    def factory() -> RiskContext:
        return RiskContext(portfolio=Portfolio(1_000_000.0), now=T,
                           reference_price=10.0, day_start_value=1_000_000.0)

    return ShadowBroker(transport, STREAMS, risk_gate=gate,
                        risk_context_factory=factory), transport


def test_shadow_never_writes_to_the_order_stream(tmp_path):
    """影子模式最重要的性质：真钱一分不动。"""
    b, t = make(tmp_path)
    b.submit(order())
    t.ensure_group(STREAMS.orders, "agent")
    assert t.read_group(STREAMS.orders, "agent", "a1") == []


def test_shadow_records_the_intent(tmp_path):
    b, _ = make(tmp_path)
    b.submit(order())
    assert len(b.intents) == 1
    assert b.intents[0].order_id == "o1"


def test_shadow_journals_to_its_own_stream(tmp_path):
    b, t = make(tmp_path)
    b.submit(order())
    journaled = t.all_messages("shadow:intents")
    assert len(journaled) == 1
    assert OrderCommand.from_payload(journaled[0].payload).client_order_id == "o1"


def test_shadow_still_applies_the_risk_gate(tmp_path):
    """影子期也要验证风控是否会拦对东西。"""
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=tmp_path / "K")
    ks.engage("halt")
    gate = default_ashare_gate(ks, CAL)

    def factory() -> RiskContext:
        return RiskContext(portfolio=Portfolio(1_000_000.0), now=T,
                           reference_price=10.0, day_start_value=1_000_000.0)

    b = ShadowBroker(transport, STREAMS, risk_gate=gate, risk_context_factory=factory)
    b.submit(order())
    assert b.intents == ()
    assert len(b.poll_rejects()) == 1


def test_shadow_never_reports_fills(tmp_path):
    b, t = make(tmp_path)
    b.submit(order())
    assert b.poll_fills() == ()


def test_shadow_pending_reflects_recorded_intents(tmp_path):
    b, _ = make(tmp_path)
    b.submit(order("a"))
    b.submit(order("b"))
    assert len(b.pending()) == 2


def test_shadow_cancel_all_clears_pending(tmp_path):
    b, _ = make(tmp_path)
    b.submit(order())
    b.cancel_all()
    assert b.pending() == ()


def test_shadow_on_market_data_is_a_no_op(tmp_path):
    b, _ = make(tmp_path)
    b.on_market_data(None)
    assert b.poll_fills() == ()


def test_shadow_is_not_a_live_broker(tmp_path):
    """类型上就不可能把影子当实盘用。"""
    from qsys.live.live_broker import LiveBroker

    b, _ = make(tmp_path)
    assert not isinstance(b, LiveBroker)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_shadow_broker.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.live.shadow_broker'`

- [ ] **Step 3: 实现 `src/qsys/live/shadow_broker.py`**

```python
"""影子模式 Broker。

策略与风控全跑，**只记录意图，永不发单**——真钱一分不动。

刻意做成独立的类而非 LiveBroker(shadow=True)：一个能静默决定真钱
动不动的布尔参数太危险，传错一个 False 就从演习变成实盘。
类型不同则不可能传错。
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from qsys.backtest.broker import Broker
from qsys.core.events import MarketSnapshot, Reject
from qsys.core.trading import Fill, Order, OrderIntent
from qsys.live.config import StreamConfig
from qsys.live.messages import OrderCommand
from qsys.live.risk import RiskContext, RiskGate
from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class ShadowBroker(Broker):
    def __init__(
        self,
        transport: Transport,
        streams: StreamConfig,
        *,
        risk_gate: RiskGate,
        risk_context_factory: Callable[[], RiskContext],
        journal_stream: str = "shadow:intents",
    ) -> None:
        self._t = transport
        self._s = streams
        self._gate = risk_gate
        self._risk_ctx = risk_context_factory
        self._journal = journal_stream
        self._intents: list[Order] = []
        self._rejects: list[Reject] = []

    @property
    def intents(self) -> tuple[Order, ...]:
        return tuple(self._intents)

    def submit(self, order: Order) -> None:
        intent = OrderIntent(order.symbol, order.side, order.qty, order.order_type,
                             order.limit_price)
        decision = self._gate.check(intent, self._risk_ctx())
        if not decision.allowed:
            self._rejects.append(
                Reject(order.order_id, order.symbol, order.side, order.qty,
                       decision.reason, order.created_at)
            )
            return

        # 只写影子日志，绝不写 orders 流
        self._t.append(
            self._journal,
            OrderCommand(
                client_order_id=order.order_id,
                symbol=str(order.symbol),
                side=order.side.value,
                qty=order.qty,
                order_type=order.order_type.value,
                limit_price=order.limit_price,
                created_at=order.created_at,
            ).to_payload(),
        )
        self._intents.append(order)
        log.info("shadow.intent", client_order_id=order.order_id,
                 symbol=str(order.symbol), side=order.side.value, qty=order.qty)

    def on_market_data(self, snapshot: MarketSnapshot | None) -> None:
        """影子模式不撮合。"""

    def poll_fills(self) -> tuple[Fill, ...]:
        return ()

    def poll_rejects(self) -> tuple[Reject, ...]:
        drained = tuple(self._rejects)
        self._rejects.clear()
        return drained

    def pending(self) -> tuple[Order, ...]:
        return tuple(self._intents)

    def cancel_all(self) -> None:
        self._intents.clear()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live/test_shadow_broker.py -v`
Expected: PASS —— 9 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/shadow_broker.py tests/live/test_shadow_broker.py
git commit -m "feat(live): 影子模式 Broker（独立类型，非布尔开关）"
```

---

## Task 10: Windows 执行代理

**代理里不放任何策略逻辑**——只把标准化指令翻译成 `xtquant` 调用，把回报翻译回标准化事件。代码量应小到可一眼读完。

**Files:**
- Create: `src/qsys/agent/__init__.py`
- Create: `src/qsys/agent/adapter.py`
- Create: `src/qsys/agent/xtquant_adapter.py`
- Create: `src/qsys/agent/execution_agent.py`
- Create: `scripts/run_agent.py`
- Test: `tests/agent/test_execution_agent.py`

**Interfaces:**
- Consumes: `Transport`（Task 2）、消息模型（Task 1）、`IdempotencyGuard`（Task 4）
- Produces: `BrokerAdapter` (Protocol: `place_order(symbol, side, qty, order_type, limit_price) -> str`、`cancel_all() -> None`、`query_account() -> tuple[float, dict[str, PositionSnapshot]]`)；`FakeAdapter(...)`；`ExecutionAgent(transport, adapter, streams, *, group="agent", consumer="agent-1", guard=None)` 带 `run_once() -> int`、`publish_account()`、`beat()`

- [ ] **Step 1: 写失败测试**

Create `tests/agent/test_execution_agent.py`:

```python
from datetime import datetime

import pytest

from qsys.agent.adapter import FakeAdapter
from qsys.agent.execution_agent import ExecutionAgent
from qsys.live.config import StreamConfig
from qsys.live.messages import (
    AccountSnapshot,
    ControlCommand,
    FillEvent,
    OrderCommand,
    PositionSnapshot,
    RejectEvent,
)
from qsys.live.transport import MemoryTransport

STREAMS = StreamConfig()
T = datetime(2024, 1, 3, 10, 0)


def cmd(oid="c-1", qty=100) -> OrderCommand:
    return OrderCommand(client_order_id=oid, symbol="600036.SH", side="buy", qty=qty,
                        order_type="market", limit_price=None, created_at=T)


def make(**kw):
    transport = MemoryTransport()
    adapter = FakeAdapter(**kw)
    agent = ExecutionAgent(transport, adapter, STREAMS)
    return agent, transport, adapter


def drain(transport, stream, model):
    """读流中全部消息，绕过消费组的 $ 语义。"""
    return [model.from_payload(m.payload) for m in transport.all_messages(stream)]


def test_agent_places_the_order(tmp_path):
    agent, t, adapter = make()
    t.append(STREAMS.orders, cmd().to_payload())
    assert agent.run_once() == 1
    assert adapter.placed == [("600036.SH", "buy", 100, "market", None)]


def test_agent_publishes_a_fill(tmp_path):
    agent, t, _ = make()
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    fills = drain(t, STREAMS.fills, FillEvent)
    assert len(fills) == 1
    assert fills[0].client_order_id == "c-1"
    assert fills[0].qty == 100


def test_duplicate_command_does_not_place_twice():
    """网络重传永不导致重复下单。"""
    agent, t, adapter = make()
    payload = cmd().to_payload()
    t.append(STREAMS.orders, payload)
    t.append(STREAMS.orders, payload)
    agent.run_once()
    assert len(adapter.placed) == 1


def test_duplicate_command_replays_the_original_fill():
    agent, t, _ = make()
    payload = cmd().to_payload()
    t.append(STREAMS.orders, payload)
    agent.run_once()
    t.append(STREAMS.orders, payload)
    agent.run_once()
    fills = drain(t, STREAMS.fills, FillEvent)
    assert len(fills) == 1


def test_adapter_failure_produces_a_reject():
    agent, t, _ = make(fail_with="broker offline")
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    rejects = drain(t, STREAMS.rejects, RejectEvent)
    assert len(rejects) == 1
    assert "broker offline" in rejects[0].reason


def test_failed_command_can_be_retried():
    """失败的指令不记入已处理集合，允许重试。"""
    agent, t, adapter = make(fail_with="transient")
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    adapter.fail_with = None
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    assert len(adapter.placed) == 1


def test_control_cancel_all_is_forwarded():
    agent, t, adapter = make()
    t.append(STREAMS.control, ControlCommand(action="cancel_all", at=T).to_payload())
    agent.run_once()
    assert adapter.cancelled is True


def test_publish_account_writes_the_snapshot():
    agent, t, _ = make(cash=98_765.43, positions={"600036.SH": (500, 500, 27.63)})
    agent.publish_account()
    raw = t.get_key(STREAMS.account)
    assert raw is not None
    snap = AccountSnapshot.model_validate_json(raw)
    assert snap.cash == 98_765.43
    assert snap.positions["600036.SH"].available_qty == 500


def test_beat_writes_a_heartbeat():
    agent, t, _ = make()
    agent.beat()
    assert t.get_key(f"{STREAMS.heartbeat}:agent") is not None


def test_run_once_with_no_commands_returns_zero():
    agent, _, _ = make()
    assert agent.run_once() == 0


def test_malformed_command_is_rejected_not_fatal():
    """畸形消息不应让代理崩溃——记录并跳过。"""
    agent, t, adapter = make()
    t.append(STREAMS.orders, {"type": "OrderCommand", "data": "not json"})
    t.append(STREAMS.orders, cmd().to_payload())
    processed = agent.run_once()
    assert processed >= 1
    assert len(adapter.placed) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/agent/test_execution_agent.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.agent'`

- [ ] **Step 3: 实现 `src/qsys/agent/adapter.py`**

```bash
mkdir -p src/qsys/agent tests/agent
touch src/qsys/agent/__init__.py
```

```python
"""券商适配器协议与测试替身。

真实的 xtquant 适配器在 xtquant_adapter.py（仅 Windows 可导入）。
单元测试一律用 FakeAdapter——不得导入 xtquant。
"""

from __future__ import annotations

from typing import Protocol

from qsys.live.messages import PositionSnapshot


class BrokerAdapter(Protocol):
    def place_order(
        self, symbol: str, side: str, qty: int, order_type: str,
        limit_price: float | None,
    ) -> str:
        """下单，返回券商订单号。失败时抛异常。"""

    def cancel_all(self) -> None: ...

    def query_account(self) -> tuple[float, dict[str, PositionSnapshot]]:
        """返回 (可用现金, 持仓字典)。"""


class FakeAdapter:
    """测试替身：记录调用，可配置失败。"""

    def __init__(
        self,
        *,
        cash: float = 100_000.0,
        positions: dict[str, tuple[int, int, float]] | None = None,
        fail_with: str | None = None,
    ) -> None:
        self.cash = cash
        self._positions = positions or {}
        self.fail_with = fail_with
        self.placed: list[tuple[str, str, int, str, float | None]] = []
        self.cancelled = False
        self._seq = 0

    def place_order(
        self, symbol: str, side: str, qty: int, order_type: str,
        limit_price: float | None,
    ) -> str:
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        self.placed.append((symbol, side, qty, order_type, limit_price))
        self._seq += 1
        return f"B{self._seq}"

    def cancel_all(self) -> None:
        self.cancelled = True

    def query_account(self) -> tuple[float, dict[str, PositionSnapshot]]:
        return self.cash, {
            symbol: PositionSnapshot(total_qty=t, available_qty=a, avg_cost=c)
            for symbol, (t, a, c) in self._positions.items()
        }
```

- [ ] **Step 4: 实现 `src/qsys/agent/execution_agent.py`**

```python
"""Windows 执行代理。

**这里不放任何策略逻辑。** 只做三件事：
  1. 把标准化指令翻译成券商调用
  2. 把回报翻译回标准化事件
  3. 定期发布账户快照与心跳

代码量应小到可一眼读完——它跑在难调试的 Windows 上。
"""

from __future__ import annotations

from datetime import datetime

import structlog

from qsys.agent.adapter import BrokerAdapter
from qsys.live.config import StreamConfig
from qsys.live.idempotency import IdempotencyGuard
from qsys.live.messages import (
    AccountSnapshot,
    ControlCommand,
    FillEvent,
    Heartbeat,
    OrderCommand,
    RejectEvent,
)
from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class ExecutionAgent:
    def __init__(
        self,
        transport: Transport,
        adapter: BrokerAdapter,
        streams: StreamConfig,
        *,
        group: str = "agent",
        consumer: str = "agent-1",
        guard: IdempotencyGuard | None = None,
    ) -> None:
        self._t = transport
        self._adapter = adapter
        self._s = streams
        self._group = group
        self._consumer = consumer
        self._guard = guard or IdempotencyGuard(transport)
        for stream in (streams.orders, streams.control):
            self._t.ensure_group(stream, group)

    def run_once(self) -> int:
        """处理一批指令，返回处理条数。"""
        handled = 0
        handled += self._drain_orders()
        handled += self._drain_control()
        return handled

    def _drain_orders(self) -> int:
        handled = 0
        for msg in self._t.read_group(self._s.orders, self._group, self._consumer, count=50):
            handled += 1
            self._t.ack(self._s.orders, self._group, msg.id)
            try:
                command = OrderCommand.from_payload(msg.payload)
            except Exception as exc:  # 畸形消息不应让代理崩溃
                log.error("agent.malformed_order", error=str(exc), payload=msg.payload)
                continue
            self._execute(command)
        return handled

    def _execute(self, command: OrderCommand) -> None:
        def place() -> str:
            return self._adapter.place_order(
                command.symbol, command.side, command.qty,
                command.order_type, command.limit_price,
            )

        try:
            broker_order_id, replayed = self._guard.process_once(
                command.client_order_id, place
            )
        except Exception as exc:
            # 失败不记入已处理集合，允许重试
            log.error("agent.place_failed", client_order_id=command.client_order_id,
                      error=str(exc))
            self._t.append(
                self._s.rejects,
                RejectEvent(
                    client_order_id=command.client_order_id,
                    symbol=command.symbol,
                    reason=str(exc),
                    at=datetime.now(),
                ).to_payload(),
            )
            return

        if replayed:
            log.info("agent.duplicate_ignored", client_order_id=command.client_order_id)
            return

        self._t.append(
            self._s.fills,
            FillEvent(
                client_order_id=command.client_order_id,
                symbol=command.symbol,
                side=command.side,
                qty=command.qty,
                price=command.limit_price or 0.0,
                fee=0.0,
                filled_at=datetime.now(),
                broker_order_id=broker_order_id,
            ).to_payload(),
        )
        log.info("agent.placed", client_order_id=command.client_order_id,
                 broker_order_id=broker_order_id)

    def _drain_control(self) -> int:
        handled = 0
        for msg in self._t.read_group(self._s.control, self._group, self._consumer, count=50):
            handled += 1
            self._t.ack(self._s.control, self._group, msg.id)
            try:
                command = ControlCommand.from_payload(msg.payload)
            except Exception as exc:
                log.error("agent.malformed_control", error=str(exc))
                continue
            if command.action == "cancel_all":
                self._adapter.cancel_all()
                log.warning("agent.cancelled_all", reason=command.reason)
            elif command.action == "query":
                self.publish_account()
        return handled

    def publish_account(self) -> None:
        cash, positions = self._adapter.query_account()
        snapshot = AccountSnapshot(at=datetime.now(), cash=cash, positions=positions)
        self._t.set_key(self._s.account, snapshot.model_dump_json())

    def beat(self) -> None:
        self._t.set_key(
            f"{self._s.heartbeat}:agent",
            Heartbeat(source="agent", at=datetime.now()).model_dump_json(),
        )
```

**关于 `FillEvent` 的价格字段**：本任务的 `FakeAdapter` 立即"成交"，真实 `xtquant` 是异步回报。真实适配器应在 `place_order` 后**不**立即发 `FillEvent`，而由 xtquant 的回调推送真实成交价——这部分在 `xtquant_adapter.py` 中实现，本任务的同步路径仅供测试与影子期使用。这一差异必须在真实接入时处理，已记入完成标准。

- [ ] **Step 5: 实现 `src/qsys/agent/xtquant_adapter.py`**

```python
"""真实 xtquant 适配器。仅 Windows 可导入。

xtquant 的成交回报是**异步**的：place_order 只返回委托号，真实成交价
由回调推送。因此本适配器在收到回调时才发布 FillEvent——
与 FakeAdapter 的同步路径不同，这一点在接真实券商时必须验证。

依赖尽可能少：只用 xtquant 与标准库。
"""

from __future__ import annotations

from qsys.live.messages import PositionSnapshot

_SIDE_TO_XT = {"buy": 23, "sell": 24}  # xtquant 委托方向常量（接入时以官方文档为准）


class XtQuantAdapter:
    def __init__(self, account_id: str, mini_qmt_path: str) -> None:
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount

        self._trader = XtQuantTrader(mini_qmt_path, session_id=int(account_id[-6:]))
        self._account = StockAccount(account_id)
        self._trader.start()
        if self._trader.connect() != 0:
            raise RuntimeError("miniQMT 连接失败")
        self._trader.subscribe(self._account)

    def place_order(
        self, symbol: str, side: str, qty: int, order_type: str,
        limit_price: float | None,
    ) -> str:
        from xtquant import xtconstant

        price_type = (
            xtconstant.FIX_PRICE if order_type == "limit"
            else xtconstant.LATEST_PRICE
        )
        order_id = self._trader.order_stock(
            self._account, symbol, _SIDE_TO_XT[side], qty,
            price_type, limit_price or 0.0,
        )
        if order_id < 0:
            raise RuntimeError(f"下单被拒: order_id={order_id}")
        return str(order_id)

    def cancel_all(self) -> None:
        for order in self._trader.query_stock_orders(self._account, cancelable_only=True):
            self._trader.cancel_order_stock(self._account, order.order_id)

    def query_account(self) -> tuple[float, dict[str, PositionSnapshot]]:
        asset = self._trader.query_stock_asset(self._account)
        positions = {
            p.stock_code: PositionSnapshot(
                total_qty=p.volume, available_qty=p.can_use_volume,
                avg_cost=p.open_price,
            )
            for p in self._trader.query_stock_positions(self._account)
        }
        return float(asset.cash), positions
```

- [ ] **Step 6: 实现 `scripts/run_agent.py`**

```python
"""Windows 执行代理启动入口。

在云 Windows 上运行：
  uv run python scripts/run_agent.py --account <资金账号> --qmt <miniQMT路径>
"""

from __future__ import annotations

import argparse
import sys
import time

import structlog

from qsys.agent.execution_agent import ExecutionAgent
from qsys.live.config import load_deploy_config
from qsys.live.redis_transport import RedisTransport

log = structlog.get_logger("agent")

POLL_SECONDS = 1.0
ACCOUNT_PUBLISH_EVERY = 30


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows 执行代理")
    parser.add_argument("--account", required=True)
    parser.add_argument("--qmt", required=True, help="miniQMT userdata 路径")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    from qsys.agent.xtquant_adapter import XtQuantAdapter

    cfg = load_deploy_config(args.config)
    transport = RedisTransport.from_config(cfg)
    adapter = XtQuantAdapter(args.account, args.qmt)
    agent = ExecutionAgent(transport, adapter, cfg.streams,
                           consumer=cfg.consumer_name)

    log.info("agent.started", account=args.account)
    ticks = 0
    while True:
        try:
            agent.run_once()
            agent.beat()
            if ticks % ACCOUNT_PUBLISH_EVERY == 0:
                agent.publish_account()
        except Exception as exc:  # 单次循环失败不应终止代理
            log.error("agent.loop_error", error=str(exc))
        ticks += 1
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: 运行测试确认通过**

Run: `uv run pytest tests/agent/test_execution_agent.py -v`
Expected: PASS —— 11 passed

- [ ] **Step 8: 确认 xtquant 适配器不被单元测试导入**

Run: `uv run pytest tests/ -q -p no:randomly 2>&1 | grep -i xtquant || echo "未导入 xtquant"`
Expected: `未导入 xtquant`

- [ ] **Step 9: 提交**

```bash
git add src/qsys/agent scripts/run_agent.py tests/agent
git commit -m "feat(agent): Windows 执行代理与 xtquant 适配器"
```

---

## Task 11: 三层对账

**持仓不一致 = 立即停止下单。** 这是保守选择，会有误停的可能，但对不上账时继续交易更危险。

**Files:**
- Create: `src/qsys/live/reconcile.py`
- Test: `tests/live/test_reconcile.py`

**Interfaces:**
- Consumes: `Portfolio`（1-2a）、`AccountSnapshot`（Task 1）、`Fill`（1-2a）
- Produces: `PositionMismatch(symbol, local_qty, remote_qty)`；`PositionReconResult(is_consistent, cash_diff, mismatches)` 带 `summary()`；`reconcile_positions(local, remote, *, cash_tolerance=1.0) -> PositionReconResult`；`FillMismatch(client_order_id, kind, detail)`；`FillReconResult(is_consistent, mismatches)`；`reconcile_fills(local_fills, remote_fills) -> FillReconResult`；`SlippageSample(symbol, sim_price, live_price, deviation)`；`SlippageReport(samples, mean_deviation, max_deviation)` 带 `explained_by(model_rate) -> bool`；`compare_sim_vs_live(sim_fills, live_fills) -> SlippageReport`

- [ ] **Step 1: 写失败测试**

Create `tests/live/test_reconcile.py`:

```python
from datetime import datetime

import pytest

from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol
from qsys.live.messages import AccountSnapshot, PositionSnapshot
from qsys.live.reconcile import (
    compare_sim_vs_live,
    reconcile_fills,
    reconcile_positions,
)

CMB = Symbol("600036", Market.SH)
PAB = Symbol("000001", Market.SZ)
T = datetime(2024, 1, 3, 10, 0)


def local(cash: float, holdings: dict[Symbol, int]) -> Portfolio:
    p = Portfolio(cash)
    for symbol, qty in holdings.items():
        p.apply_fill(Fill("o", symbol, Side.BUY, qty, 10.0, 0.0, T))
    p.unfreeze()
    # 买入会扣现金，此处直接改回目标值以便精确控制测试
    p.cash = cash
    return p


def remote(cash: float, holdings: dict[str, int]) -> AccountSnapshot:
    return AccountSnapshot(
        at=T, cash=cash,
        positions={s: PositionSnapshot(total_qty=q, available_qty=q, avg_cost=10.0)
                   for s, q in holdings.items()},
    )


# --- 盘中对账：持仓 ---


def test_matching_positions_are_consistent():
    r = reconcile_positions(local(100_000.0, {CMB: 500}), remote(100_000.0, {"600036.SH": 500}))
    assert r.is_consistent is True
    assert r.mismatches == ()


def test_quantity_mismatch_is_flagged():
    r = reconcile_positions(local(100_000.0, {CMB: 500}), remote(100_000.0, {"600036.SH": 400}))
    assert r.is_consistent is False
    assert r.mismatches[0].local_qty == 500
    assert r.mismatches[0].remote_qty == 400


def test_position_missing_remotely_is_flagged():
    r = reconcile_positions(local(100_000.0, {CMB: 500}), remote(100_000.0, {}))
    assert r.is_consistent is False
    assert r.mismatches[0].remote_qty == 0


def test_position_missing_locally_is_flagged():
    r = reconcile_positions(local(100_000.0, {}), remote(100_000.0, {"600036.SH": 500}))
    assert r.is_consistent is False
    assert r.mismatches[0].local_qty == 0


def test_cash_within_tolerance_is_consistent():
    r = reconcile_positions(local(100_000.0, {}), remote(100_000.50, {}),
                            cash_tolerance=1.0)
    assert r.is_consistent is True


def test_cash_beyond_tolerance_is_flagged():
    r = reconcile_positions(local(100_000.0, {}), remote(99_000.0, {}),
                            cash_tolerance=1.0)
    assert r.is_consistent is False
    assert r.cash_diff == pytest.approx(1000.0)


def test_summary_lists_every_mismatch():
    r = reconcile_positions(
        local(100_000.0, {CMB: 500, PAB: 300}),
        remote(99_000.0, {"600036.SH": 400}),
    )
    text = r.summary()
    assert "600036.SH" in text and "000001.SZ" in text
    assert "1000" in text or "1,000" in text


def test_empty_both_sides_is_consistent():
    r = reconcile_positions(local(100_000.0, {}), remote(100_000.0, {}))
    assert r.is_consistent is True


# --- 盘后对账：成交 ---


def fill(oid: str, qty: int = 100, price: float = 10.0) -> Fill:
    return Fill(oid, CMB, Side.BUY, qty, price, 0.0, T)


def test_matching_fills_are_consistent():
    r = reconcile_fills([fill("a"), fill("b")], [fill("a"), fill("b")])
    assert r.is_consistent is True


def test_fill_missing_remotely_is_flagged():
    r = reconcile_fills([fill("a"), fill("b")], [fill("a")])
    assert r.is_consistent is False
    assert r.mismatches[0].kind == "missing_remote"
    assert r.mismatches[0].client_order_id == "b"


def test_fill_missing_locally_is_flagged():
    r = reconcile_fills([fill("a")], [fill("a"), fill("b")])
    assert r.mismatches[0].kind == "missing_local"


def test_quantity_difference_is_flagged():
    r = reconcile_fills([fill("a", qty=100)], [fill("a", qty=90)])
    assert r.mismatches[0].kind == "qty_differs"
    assert "100" in r.mismatches[0].detail and "90" in r.mismatches[0].detail


def test_price_difference_is_flagged():
    r = reconcile_fills([fill("a", price=10.0)], [fill("a", price=10.5)])
    assert r.mismatches[0].kind == "price_differs"


# --- 第三层：回测 vs 实盘 ---


def test_identical_fills_have_zero_deviation():
    report = compare_sim_vs_live([fill("a", price=10.0)], [fill("a", price=10.0)])
    assert report.mean_deviation == 0.0
    assert report.max_deviation == 0.0


def test_deviation_is_measured_per_order():
    report = compare_sim_vs_live([fill("a", price=10.0)], [fill("a", price=10.1)])
    assert report.samples[0].deviation == pytest.approx(0.01)


def test_unmatched_orders_are_skipped():
    report = compare_sim_vs_live([fill("a"), fill("b")], [fill("a")])
    assert len(report.samples) == 1


def test_deviation_within_model_is_explained():
    """差异能被滑点模型解释，即引擎与实盘一致。"""
    report = compare_sim_vs_live([fill("a", price=10.0)], [fill("a", price=10.01)])
    assert report.explained_by(0.002) is True


def test_deviation_beyond_model_is_unexplained():
    report = compare_sim_vs_live([fill("a", price=10.0)], [fill("a", price=11.0)])
    assert report.explained_by(0.002) is False


def test_empty_comparison_is_explained():
    report = compare_sim_vs_live([], [])
    assert report.explained_by(0.002) is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_reconcile.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.live.reconcile'`

- [ ] **Step 3: 实现 `src/qsys/live/reconcile.py`**

```python
"""三层对账 —— 整个可靠性设计的压舱石。

  盘中  持仓快照 vs 本地推演   不一致立即停止下单
  盘后  本地订单簿 vs 实际成交 逐笔比对
  回测  SimBroker vs 实盘成交  差异须能被滑点模型解释

第三层是这套架构独有的能力：它让回测随实盘运行越来越准，
而不是永远停在假设上。积累的差异数据是五期滑点校准的输入。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill
from qsys.core.types import Symbol
from qsys.live.messages import AccountSnapshot

log = structlog.get_logger(__name__)

_PRICE_TOLERANCE = 1e-6


# --- 盘中：持仓对账 ---


@dataclass(frozen=True, slots=True)
class PositionMismatch:
    symbol: str
    local_qty: int
    remote_qty: int


@dataclass(frozen=True, slots=True)
class PositionReconResult:
    is_consistent: bool
    cash_diff: float
    mismatches: tuple[PositionMismatch, ...]

    def summary(self) -> str:
        if self.is_consistent:
            return "reconcile: consistent"
        lines = [f"reconcile: INCONSISTENT (cash diff {self.cash_diff:,.2f})"]
        for m in self.mismatches:
            lines.append(f"  {m.symbol}: local={m.local_qty} remote={m.remote_qty}")
        return "\n".join(lines)


def reconcile_positions(
    local: Portfolio, remote: AccountSnapshot, *, cash_tolerance: float = 1.0
) -> PositionReconResult:
    local_qty = {str(s): p.total_qty for s, p in local.positions.items()}
    remote_qty = {s: p.total_qty for s, p in remote.positions.items()}

    mismatches = [
        PositionMismatch(symbol, local_qty.get(symbol, 0), remote_qty.get(symbol, 0))
        for symbol in sorted(set(local_qty) | set(remote_qty))
        if local_qty.get(symbol, 0) != remote_qty.get(symbol, 0)
    ]
    cash_diff = abs(local.cash - remote.cash)
    consistent = not mismatches and cash_diff <= cash_tolerance

    if not consistent:
        log.error("reconcile.inconsistent", cash_diff=cash_diff,
                  mismatches=len(mismatches))

    return PositionReconResult(consistent, cash_diff, tuple(mismatches))


# --- 盘后：成交对账 ---


@dataclass(frozen=True, slots=True)
class FillMismatch:
    client_order_id: str
    kind: str  # missing_remote / missing_local / qty_differs / price_differs
    detail: str


@dataclass(frozen=True, slots=True)
class FillReconResult:
    is_consistent: bool
    mismatches: tuple[FillMismatch, ...]


def reconcile_fills(
    local_fills: Sequence[Fill], remote_fills: Sequence[Fill]
) -> FillReconResult:
    local_by_id = {f.order_id: f for f in local_fills}
    remote_by_id = {f.order_id: f for f in remote_fills}
    mismatches: list[FillMismatch] = []

    for order_id in sorted(set(local_by_id) | set(remote_by_id)):
        ours = local_by_id.get(order_id)
        theirs = remote_by_id.get(order_id)
        if theirs is None:
            mismatches.append(FillMismatch(order_id, "missing_remote", "本地有成交，远端无"))
        elif ours is None:
            mismatches.append(FillMismatch(order_id, "missing_local", "远端有成交，本地无"))
        elif ours.qty != theirs.qty:
            mismatches.append(
                FillMismatch(order_id, "qty_differs", f"local={ours.qty} remote={theirs.qty}")
            )
        elif abs(ours.price - theirs.price) > _PRICE_TOLERANCE:
            mismatches.append(
                FillMismatch(order_id, "price_differs",
                             f"local={ours.price} remote={theirs.price}")
            )

    return FillReconResult(not mismatches, tuple(mismatches))


# --- 第三层：回测 vs 实盘 ---


@dataclass(frozen=True, slots=True)
class SlippageSample:
    symbol: str
    sim_price: float
    live_price: float
    deviation: float


@dataclass(frozen=True, slots=True)
class SlippageReport:
    samples: tuple[SlippageSample, ...]
    mean_deviation: float
    max_deviation: float

    def explained_by(self, model_rate: float) -> bool:
        """差异是否落在滑点模型的解释范围内。"""
        return self.max_deviation <= model_rate


def compare_sim_vs_live(
    sim_fills: Sequence[Fill], live_fills: Sequence[Fill]
) -> SlippageReport:
    live_by_id = {f.order_id: f for f in live_fills}
    samples: list[SlippageSample] = []

    for sim in sim_fills:
        live = live_by_id.get(sim.order_id)
        if live is None or sim.price <= 0:
            continue
        samples.append(
            SlippageSample(
                symbol=str(sim.symbol),
                sim_price=sim.price,
                live_price=live.price,
                deviation=abs(live.price / sim.price - 1.0),
            )
        )

    if not samples:
        return SlippageReport((), 0.0, 0.0)

    deviations = [s.deviation for s in samples]
    return SlippageReport(
        tuple(samples), sum(deviations) / len(deviations), max(deviations)
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live/test_reconcile.py -v`
Expected: PASS —— 20 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/reconcile.py tests/live/test_reconcile.py
git commit -m "feat(live): 三层对账"
```

---

## Task 12: 心跳与端到端集成

**Files:**
- Create: `src/qsys/live/heartbeat.py`
- Test: `tests/live/test_heartbeat.py`
- Test: `tests/test_live_link_e2e.py`

**Interfaces:**
- Consumes: `Transport`（Task 2）、`Heartbeat`（Task 1）、全部前序组件
- Produces: `HeartbeatMonitor(transport, *, prefix="state:heartbeat")` 带 `beat(source, now=None)`、`last_beat(source) -> datetime | None`、`is_alive(source, *, max_age_seconds=60, now=None) -> bool`、`dead_sources(sources, **kw) -> tuple[str, ...]`

- [ ] **Step 1: 写失败测试**

Create `tests/live/test_heartbeat.py`:

```python
from datetime import datetime, timedelta

from qsys.live.heartbeat import HeartbeatMonitor
from qsys.live.transport import MemoryTransport

NOW = datetime(2024, 1, 3, 10, 0)


def monitor() -> HeartbeatMonitor:
    return HeartbeatMonitor(MemoryTransport())


def test_no_beat_means_no_last_beat():
    assert monitor().last_beat("agent") is None


def test_beat_is_recorded():
    m = monitor()
    m.beat("agent", now=NOW)
    assert m.last_beat("agent") == NOW


def test_fresh_beat_is_alive():
    m = monitor()
    m.beat("agent", now=NOW)
    assert m.is_alive("agent", max_age_seconds=60, now=NOW + timedelta(seconds=30)) is True


def test_stale_beat_is_dead():
    m = monitor()
    m.beat("agent", now=NOW)
    assert m.is_alive("agent", max_age_seconds=60, now=NOW + timedelta(seconds=90)) is False


def test_never_beaten_source_is_dead():
    assert monitor().is_alive("agent", now=NOW) is False


def test_sources_are_independent():
    m = monitor()
    m.beat("agent", now=NOW)
    assert m.is_alive("agent", now=NOW) is True
    assert m.is_alive("core", now=NOW) is False


def test_dead_sources_lists_the_silent_ones():
    m = monitor()
    m.beat("agent", now=NOW)
    dead = m.dead_sources(["agent", "core"], max_age_seconds=60, now=NOW)
    assert dead == ("core",)


def test_dead_sources_is_empty_when_all_alive():
    m = monitor()
    m.beat("agent", now=NOW)
    m.beat("core", now=NOW)
    assert m.dead_sources(["agent", "core"], now=NOW) == ()


def test_later_beat_overwrites_the_earlier_one():
    m = monitor()
    m.beat("agent", now=NOW)
    later = NOW + timedelta(minutes=5)
    m.beat("agent", now=later)
    assert m.last_beat("agent") == later
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_heartbeat.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.live.heartbeat'`

- [ ] **Step 3: 实现 `src/qsys/live/heartbeat.py`**

```python
"""双向心跳。

核心侧与 Windows agent 互相心跳，任一侧失联告警。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import structlog

from qsys.live.messages import Heartbeat
from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class HeartbeatMonitor:
    def __init__(self, transport: Transport, *, prefix: str = "state:heartbeat") -> None:
        self._t = transport
        self._prefix = prefix

    def _key(self, source: str) -> str:
        return f"{self._prefix}:{source}"

    def beat(self, source: str, now: datetime | None = None) -> None:
        moment = now or datetime.now()
        self._t.set_key(
            self._key(source), Heartbeat(source=source, at=moment).model_dump_json()
        )

    def last_beat(self, source: str) -> datetime | None:
        raw = self._t.get_key(self._key(source))
        if raw is None:
            return None
        return Heartbeat.model_validate_json(raw).at

    def is_alive(
        self, source: str, *, max_age_seconds: float = 60.0,
        now: datetime | None = None,
    ) -> bool:
        last = self.last_beat(source)
        if last is None:
            return False
        age = ((now or datetime.now()) - last).total_seconds()
        return age <= max_age_seconds

    def dead_sources(
        self, sources: Sequence[str], *, max_age_seconds: float = 60.0,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        dead = tuple(
            s for s in sources
            if not self.is_alive(s, max_age_seconds=max_age_seconds, now=now)
        )
        if dead:
            log.error("heartbeat.dead_sources", sources=list(dead))
        return dead
```

- [ ] **Step 4: 写端到端集成测试**

Create `tests/test_live_link_e2e.py`:

```python
"""实盘链路端到端：假 Transport + 假券商，跑一个完整交易日。

验证核心侧与执行代理之间的完整往返，以及故障处理矩阵中的关键分支。
"""

from datetime import date, datetime

import pytest

from qsys.agent.adapter import FakeAdapter
from qsys.agent.execution_agent import ExecutionAgent
from qsys.core.calendar import AShareCalendar
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Order, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.live.config import StreamConfig
from qsys.live.heartbeat import HeartbeatMonitor
from qsys.live.kill_switch import KillSwitch
from qsys.live.live_broker import LiveBroker
from qsys.live.messages import AccountSnapshot
from qsys.live.reconcile import reconcile_positions
from qsys.live.risk import RiskContext, default_ashare_gate
from qsys.live.shadow_broker import ShadowBroker
from qsys.live.transport import MemoryTransport

CMB = Symbol("600036", Market.SH)
CAL = AShareCalendar([date(2024, 1, 3)])
T = datetime(2024, 1, 3, 10, 0)
STREAMS = StreamConfig()


def build(tmp_path, *, shadow: bool = False):
    transport = MemoryTransport()
    portfolio = Portfolio(1_000_000.0)
    ks = KillSwitch(transport, local_path=tmp_path / "KILL")
    gate = default_ashare_gate(ks, CAL)

    def factory() -> RiskContext:
        return RiskContext(portfolio=portfolio, now=T, reference_price=10.0,
                           day_start_value=1_000_000.0)

    cls = ShadowBroker if shadow else LiveBroker
    broker = cls(transport, STREAMS, risk_gate=gate, risk_context_factory=factory)
    agent = ExecutionAgent(transport, FakeAdapter(cash=1_000_000.0), STREAMS)
    return broker, agent, transport, portfolio, ks


def order(oid="o1", qty=100) -> Order:
    return Order(oid, CMB, Side.BUY, qty, OrderType.MARKET, None, T)


def test_full_round_trip_core_to_agent_to_core(tmp_path):
    broker, agent, _, portfolio, _ = build(tmp_path)

    broker.submit(order())
    assert len(broker.pending()) == 1

    agent.run_once()

    fills = broker.poll_fills()
    assert len(fills) == 1
    assert fills[0].order_id == "o1"
    assert broker.pending() == ()

    portfolio.apply_fill(fills[0])
    assert portfolio.position(CMB).total_qty == 100


def test_duplicate_delivery_does_not_double_fill(tmp_path):
    """网络重传：同一指令投递两次，只能成交一次。"""
    broker, agent, transport, _, _ = build(tmp_path)
    broker.submit(order())
    # 模拟重传：把 orders 流里的消息再追加一遍
    original = transport.all_messages(STREAMS.orders)[0]
    transport.append(STREAMS.orders, original.payload)

    agent.run_once()
    assert len(broker.poll_fills()) == 1


def test_kill_switch_stops_the_link(tmp_path):
    broker, agent, _, _, ks = build(tmp_path)
    ks.engage("emergency")
    broker.submit(order())
    agent.run_once()
    assert broker.poll_fills() == ()
    assert len(broker.poll_rejects()) == 1


def test_kill_switch_via_local_file_alone(tmp_path):
    """不依赖 Redis 的独立停机路径。"""
    broker, agent, _, _, _ = build(tmp_path)
    (tmp_path / "KILL").write_text("filesystem stop")
    broker.submit(order())
    agent.run_once()
    assert broker.poll_fills() == ()


def test_shadow_mode_never_reaches_the_agent(tmp_path):
    """影子模式：策略与风控全跑，真钱一分不动。"""
    broker, agent, _, _, _ = build(tmp_path, shadow=True)
    broker.submit(order())
    assert agent.run_once() == 0
    assert len(broker.intents) == 1


def test_account_snapshot_flows_back_for_reconciliation(tmp_path):
    broker, agent, _, portfolio, _ = build(tmp_path)
    broker.submit(order())
    agent.run_once()
    for f in broker.poll_fills():
        portfolio.apply_fill(f)

    agent.publish_account()
    snapshot = broker.account_snapshot()
    assert snapshot is not None

    result = reconcile_positions(portfolio, snapshot, cash_tolerance=1e9)
    # FakeAdapter 不跟踪持仓，故本地有仓、远端无仓 -> 必须被发现
    assert result.is_consistent is False
    assert result.mismatches[0].local_qty == 100


def test_position_mismatch_is_detected(tmp_path):
    broker, _, _, portfolio, _ = build(tmp_path)
    portfolio.apply_fill(Fill("x", CMB, Side.BUY, 500, 10.0, 0.0, T))
    remote = AccountSnapshot(at=T, cash=portfolio.cash, positions={})
    assert reconcile_positions(portfolio, remote).is_consistent is False


def test_heartbeat_detects_a_silent_agent(tmp_path):
    _, agent, transport, _, _ = build(tmp_path)
    monitor = HeartbeatMonitor(transport)
    agent.beat()
    assert monitor.is_alive("agent", max_age_seconds=60) is True

    from datetime import timedelta

    future = datetime.now() + timedelta(seconds=300)
    assert monitor.is_alive("agent", max_age_seconds=60, now=future) is False


def test_agent_failure_produces_a_reject_not_a_hang(tmp_path):
    transport = MemoryTransport()
    portfolio = Portfolio(1_000_000.0)
    ks = KillSwitch(transport, local_path=tmp_path / "KILL")
    gate = default_ashare_gate(ks, CAL)
    broker = LiveBroker(
        transport, STREAMS, risk_gate=gate,
        risk_context_factory=lambda: RiskContext(
            portfolio=portfolio, now=T, reference_price=10.0, day_start_value=1_000_000.0
        ),
    )
    agent = ExecutionAgent(transport, FakeAdapter(fail_with="broker offline"), STREAMS)

    broker.submit(order())
    agent.run_once()
    rejects = broker.poll_rejects()
    assert len(rejects) == 1
    assert "broker offline" in rejects[0].reason
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/live/test_heartbeat.py tests/test_live_link_e2e.py -v`
Expected: PASS —— 9 + 9 = 18 passed

- [ ] **Step 6: 运行全部测试**

Run: `uv run pytest -q`
Expected: PASS —— 计划 1-1（146）+ 1-2a（104）+ 1-2b（136）+ 本计划新增项全部通过

- [ ] **Step 7: 提交**

```bash
git add src/qsys/live/heartbeat.py tests/live/test_heartbeat.py tests/test_live_link_e2e.py
git commit -m "feat(live): 心跳监控与实盘链路端到端集成"
```

---

## 完成标准

1. `uv run pytest` 全绿，前三个计划（386 项）无回归
2. **单元测试不连接真实 Redis、不导入 xtquant** —— `grep -rn "import redis" tests/` 与 `grep -rn "xtquant" tests/` 均无结果
3. **网络重传不重复下单** —— `test_duplicate_delivery_does_not_double_fill` 通过
4. **kill switch 双路径** —— 仅本地文件即可停机（`test_kill_switch_via_local_file_alone`）；`scripts/kill.py status` 在 Redis 不可达时仍能运行
5. **影子模式真钱不动** —— `test_shadow_mode_never_reaches_the_agent` 通过；`ShadowBroker` 与 `LiveBroker` 是不同类型，不可能传错
6. **持仓不一致可被发现** —— `reconcile_positions` 在数量或现金超容差时返回 `is_consistent=False`
7. **部署地址不在仓库内** —— `config/deploy.local.toml` 已 gitignore；`grep -rn "110\.42\.205\|118\.25\.42" --include="*.py" --include="*.toml" .` 无结果
8. 风控闸门 `kill_switch` 排在首位，六条规则齐备
9. `scripts/run_agent.py --help` 可用

## 已知待办（真实接入时必须处理）

- **`ExecutionAgent` 当前在 `place_order` 成功后立即发布 `FillEvent`，价格取自限价单的 `limit_price`（市价单为 0）。** 真实 `xtquant` 的成交回报是**异步**的：`order_stock` 只返回委托号，真实成交价与数量由回调推送。接真实券商时必须改为「下单成功 → 等回调 → 发布带真实成交价的 FillEvent」，否则回测-实盘对账的第三层会拿到错误的实盘价格。本计划的同步路径仅供测试与影子期链路验证使用。
- `xtquant` 的委托方向常量与字段名以官方文档为准，`xtquant_adapter.py` 中的取值需在 Windows 上实测校正。

## 下一步

进入 **计划 1-4：监控台与日报** —— FastAPI + HTMX 只读监控台（持仓、净值、当日信号、成交、三层对账结果、数据质量报告）、每日运行报告、告警链路。之后一期即可进入影子模式一周的验收。
