"""告警受管键清单（#662 计划 03；设计文档 §8.2）。

每个键登记：来源、类别（incident = 需要处理、会恢复；notice = 通知）、允许的级别、标题模板、说明。
标题模板用 ``str.format``；多数键的标题由生产者按现有措辞给出（模板 ``{title}``），键本身才是稳定标识。
对象（subject）用服务的稳定 ID（与 #592 一致）、组件名或检查 ID。新增告警必须先在这里登记。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from qsys.ops.alert_events import Severity

I, W, C = Severity.INFO, Severity.WARNING, Severity.CRITICAL


@dataclass(frozen=True, slots=True)
class AlertKey:
    id: str
    source: str
    kind: Literal["incident", "notice"]
    severities: frozenset[Severity]
    title: str
    description: str


def _k(id: str, source: str, kind: str, severities: set[Severity], title: str, description: str) -> AlertKey:
    return AlertKey(id, source, kind, frozenset(severities), title, description)  # type: ignore[arg-type]


_ALL = (
    # ---- 实盘 / 影子核心 ----
    _k("live.reconcile.drift", "live-core", "incident", {W}, "影子盘与券商账户不一致（不停机）",
       "影子模式下账户与券商快照不一致，只告警不停机"),
    _k("live.reconcile.failed", "live-core", "incident", {C}, "实盘账户对账失败",
       "实盘账户与券商快照不一致：落急停并停止"),
    _k("live.heartbeat.timeout", "live-core", "incident", {C}, "实盘心跳超时",
       "盘前检查发现执行端或行情心跳超时：落急停、撤本系统委托；subject 为超时的心跳源"),
    _k("live.order.rejected", "live-core", "notice", {W}, "实盘订单被拒", "券商拒单明细"),
    _k("live.reject.fuse", "live-core", "incident", {C}, "实盘连续拒单熔断", "连续拒单达到阈值：落急停、撤单"),
    _k("live.core.fuse", "live-core", "incident", {C}, "实盘核心连续故障熔断", "tick 连续异常熔断落闸（#535）"),
    _k("live.fuse.cancel_resent", "live-core", "notice", {C}, "熔断撤单已补发",
       "熔断时撤单投递失败，Redis 恢复后已重新投递 cancel_all"),
    _k("shadow.tick.failing", "shadow-core", "incident", {C, I}, "{title}",
       "影子核心 tick 连续失败超过阈值；恢复时为 resolved"),
    # ---- 策略运行通知（模拟 / 影子 / 实盘引擎）----
    _k("strategy.run.error", "strategy", "notice", {C}, "{title}", "策略运行出错；subject 为引擎名"),
    _k("strategy.run.rejects", "strategy", "notice", {W}, "{title}", "策略运行有拒单"),
    _k("strategy.run.report", "strategy", "notice", {I}, "{title}", "竞价决策、买卖与收盘汇总"),
    # ---- 健康观察：交易链路、进程、调度 ----
    _k("observer.chain.down", "health-observer", "incident", {C, I}, "{title}",
       "交易链路失败；subject 为服务稳定 ID"),
    _k("observer.chain.state_unreadable", "health-observer", "incident", {C, I}, "{title}", "交易状态库读取失败"),
    _k("observer.chain.degraded", "health-observer", "incident", {W, I}, "{title}", "策略事件延迟"),
    _k("observer.process.down", "health-observer", "incident", {C, I}, "{title}", "服务进程停止"),
    _k("observer.schedule.inactive", "health-observer", "incident", {C, I}, "{title}", "定时任务调度未启用"),
    _k("observer.schedule.failed", "health-observer", "incident", {C, I}, "{title}", "定时任务最近执行失败"),
    # ---- 基础设施巡检（infra_watch.Finding.key 映射）----
    _k("infra.disk.data", "health-observer", "incident", {W, C, I}, "{title}", "数据盘余量；subject 为空"),
    _k("infra.disk.system", "health-observer", "incident", {W, C, I}, "{title}", "系统盘余量"),
    _k("infra.cert.expiry", "health-observer", "incident", {W, C, I}, "{title}", "站点证书到期"),
    _k("infra.unit.failed", "health-observer", "incident", {C, I}, "{title}", "systemd 单元失败；subject 为单元名"),
    _k("infra.kill.engaged", "health-observer", "incident", {C, I}, "{title}", "急停开关已触发"),
    _k("infra.gateway_kill.engaged", "health-observer", "incident", {C, I}, "{title}",
       "Windows 网关本地闸单侧已落（#677）"),
    _k("infra.order_book.empty", "health-observer", "incident", {C, I}, "{title}", "行情全推无盘口（#651）"),
    _k("infra.redis.unreachable", "health-observer", "incident", {C, I}, "{title}", "Redis 不可达"),
    # ---- 演练与质检 ----
    _k("drill.reboot.passed", "reboot-drill", "notice", {I}, "月度重启演练通过（{day}）", "月度重启演练通过"),
    _k("drill.reboot.failed", "reboot-drill", "notice", {C}, "月度重启演练未通过（{day}）", "月度重启演练未通过"),
    _k("checks.service.passed", "service-checks", "notice", {I}, "服务质检通过（{day}）", "每晚服务质检全部正常"),
    _k("checks.service.problems", "service-checks", "notice", {W, C}, "{title}",
       "每晚服务质检有失败（critical）或告警（warning）"),
)

KEYS: dict[str, AlertKey] = {k.id: k for k in _ALL}


def lookup(key: str) -> AlertKey | None:
    return KEYS.get(key)
