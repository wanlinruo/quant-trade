"""每日整体情况汇报（运维视角）。

和 ``qsys.ops.daily_report`` 的分工：那个是单个引擎的交易日报（权益、成交、拒单），
给策略看；这个是全局的一天怎么样，给管理者看——策略跑了没、账户什么状态、
服务和数据有没有问题。

**纯函数**：只接已经取好的数据，不碰数据库、不发网络请求。加载与投递在
``scripts/send_daily_digest.py``，这样汇报内容本身测得动。

写法上有一条自律：**先说结论，再说明细**。标题就要能看出今天正不正常，
点开只是为了知道哪里不对——日报如果要读完才知道有没有事，很快就没人读了。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from html import escape

OK_MARK = "正常"
BAD_MARK = "有异常"
CLOSED_MARK = "休市"
# 与 qsys.ops.service_checks 的每晚质检同口径，别在两处各定一套
CERT_WARN_DAYS, CERT_BAD_DAYS = 21, 10
MEMORY_WARN_PCT = 90
# 一条明细占一行，长的截断。错误文本常带换行（对账不一致会列出每个标的），
# 原样铺开会把日报撑成几十行，扫不动就没人读了。
VALUE_LIMIT = 140


def one_line(text: str, limit: int = VALUE_LIMIT) -> str:
    flat = " / ".join(part.strip() for part in str(text).splitlines() if part.strip())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class Level(StrEnum):
    """一行的严重度。

    只分「有事 / 没事」是不够的：2026-09-30 指标字典有 2 条阻断，日报却因为
    它们只是 warning 而在标题上写了「正常」——信号到了，被降噪降掉了。
    告警也算异常，只是排在失败后面。
    """

    OK = "ok"
    WARN = "warn"
    BAD = "bad"


@dataclass(frozen=True, slots=True)
class Line:
    label: str
    value: str
    level: Level = Level.OK

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", one_line(self.value))

    @property
    def problem(self) -> bool:
        return self.level is not Level.OK


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    lines: list[Line] = field(default_factory=list)
    note: str = ""

    @property
    def problems(self) -> list[Line]:
        return [line for line in self.lines if line.problem]

    @property
    def worst(self) -> Level:
        levels = [line.level for line in self.lines]
        return (Level.BAD if Level.BAD in levels
                else Level.WARN if Level.WARN in levels else Level.OK)


@dataclass(frozen=True, slots=True)
class Digest:
    day: date
    sections: list[Section] = field(default_factory=list)
    trading: bool = True

    @property
    def problems(self) -> list[tuple[str, Line]]:
        """失败在前，告警在后——扫标题之后第一眼看到的应该是最要紧的。"""
        return sorted(
            ((s.title, line) for s in self.sections for line in s.problems),
            key=lambda pair: pair[1].level is not Level.BAD,
        )

    @property
    def healthy(self) -> bool:
        return not self.problems

    def tally(self) -> tuple[int, int]:
        levels = [line.level for _, line in self.problems]
        return levels.count(Level.BAD), levels.count(Level.WARN)

    def subject(self) -> str:
        closed = "" if self.trading else f" · {CLOSED_MARK}"
        if self.healthy:
            return f"[qsys] {self.day} 日报{closed} · {OK_MARK}"
        bad, warn = self.tally()
        kinds = "，".join(p for p in (f"失败 {bad}" if bad else "",
                                      f"告警 {warn}" if warn else "") if p)
        return (f"[qsys] {self.day} 日报{closed} · "
                f"{BAD_MARK} {len(self.problems)} 项（{kinds}）")

    def as_text(self) -> str:
        out = [self.subject(), ""]
        if self.problems:
            out.append("需要关注：")
            out += [f"  · {title}｜{line.label}：{line.value}"
                    for title, line in self.problems]
            out.append("")
        for section in self.sections:
            out.append(f"【{section.title}】")
            if section.note:
                out.append(f"  {section.note}")
            for line in section.lines:
                flag = {Level.BAD: "!! ", Level.WARN: " ! "}.get(line.level, "   ")
                out.append(f"{flag}{line.label}：{line.value}")
            out.append("")
        return "\n".join(out).rstrip() + "\n"

    def as_html(self) -> str:
        head = escape(self.subject())
        parts = [
            "<div style=\"font-family:-apple-system,SimSun,sans-serif;"
            "font-size:14px;line-height:1.7;color:#222\">",
            f"<h2 style=\"margin:0 0 12px\">{head}</h2>",
        ]
        if self.problems:
            parts.append(
                "<div style=\"background:#fff4f4;border-left:3px solid #d33;"
                "padding:8px 12px;margin:0 0 16px\"><b>需要关注</b><ul style=\"margin:6px 0\">"
                + "".join(
                    f"<li><b>{'失败' if line.level is Level.BAD else '告警'}</b>"
                    f"｜{escape(title)}｜{escape(line.label)}："
                    f"{escape(line.value)}</li>"
                    for title, line in self.problems
                )
                + "</ul></div>"
            )
        for section in self.sections:
            parts.append(f"<h3 style=\"margin:16px 0 6px\">{escape(section.title)}</h3>")
            if section.note:
                parts.append(
                    f"<p style=\"margin:0 0 6px;color:#666\">{escape(section.note)}</p>"
                )
            rows = "".join(
                "<tr>"
                f"<td style=\"padding:3px 12px 3px 0;color:#666;white-space:nowrap\">"
                f"{escape(line.label)}</td>"
                f"<td style=\"padding:3px 0;{_INK.get(line.level, '')}\">"
                f"{escape(line.value)}</td></tr>"
                for line in section.lines
            )
            parts.append(f"<table style=\"border-collapse:collapse\">{rows}</table>")
        parts.append("</div>")
        return "".join(parts)


_INK = {Level.BAD: "color:#d33;font-weight:600", Level.WARN: "color:#b26a00"}


def _strategy_section(runs: list[dict]) -> Section:
    """当日各引擎的策略运行。没有记录本身就是问题——那说明策略没跑。"""
    if not runs:
        return Section("策略", [Line("运行记录", "当日无任何记录", Level.BAD)])

    lines: list[Line] = []
    orders = fills = rejects = 0
    # 同一个故障常在多个事件上重复（一次对账失败会让 checkpoint/bar/session_end
    # 全部报同一句）。按「引擎 + 错误」合并计数，只说一次。
    errors: dict[tuple[str, str], int] = {}
    for run in runs:
        engine = str(run.get("engine", "?"))
        phase = str(run.get("phase", ""))
        orders += int(run.get("orders") or 0)
        rejects += int(run.get("rejects") or 0)
        fills += int(run.get("fills") or 0)
        error = one_line(run.get("error") or "")
        if error:
            errors[(engine, error)] = errors.get((engine, error), 0) + 1
        elif phase == "auction":
            lines.append(Line(f"{engine}·竞价", str(run.get("summary") or "")))
    for (engine, error), count in errors.items():
        suffix = f"（{count} 个事件）" if count > 1 else ""
        lines.append(Line(f"{engine}·出错{suffix}", error, Level.BAD))
    lines.append(Line("当日委托", f"{orders} 笔，成交 {fills} 笔，拒单 {rejects} 笔",
                      Level.BAD if rejects else Level.OK))
    return Section("策略", lines)


def _account_section(accounts: list[dict]) -> Section:
    return Section("账户", [
        Line(str(a.get("name", "")),
             f"总资产 {float(a.get('total_value', 0.0)):,.2f}"
             f"（现金 {float(a.get('cash', 0.0)):,.2f}，持仓 {a.get('positions', 0)} 只）")
        for a in accounts
    ])


def _service_section(checks: list[dict], missing: list[str], *,
                     trading: bool = True) -> Section:
    """服务质检结论 + 当天没按时跑的任务。"""
    lines: list[Line] = []
    bad = [c for c in checks if c.get("status") == "failed"]
    warn = [c for c in checks if c.get("status") == "warning"]
    if not trading and not checks:
        # 休市日本来就不跑质检，缺记录不是故障
        return Section("服务与数据", [Line("质检", "休市日不安排质检")])
    if checks:
        lines.append(Line(
            "质检", f"{len(checks)} 项，失败 {len(bad)}，告警 {len(warn)}",
            Level.BAD if bad else Level.WARN if warn else Level.OK,
        ))
    else:
        lines.append(Line("质检", "当日没有质检记录", Level.BAD))
    for item in bad + warn:
        lines.append(Line(
            str(item.get("name", "")), str(item.get("detail", ""))[:200],
            Level.BAD if item.get("status") == "failed" else Level.WARN,
        ))
    for name in missing:
        lines.append(Line(name, "当日没有运行记录", Level.BAD))
    return Section("服务与数据", lines)


def _quality_section(quality: dict, *, trading: bool) -> Section:
    """指标字典当晚的结论。

    单独成段，不再只在服务质检里占一行：2026-09-30 两条阻断就是这么被读过去的。
    空字典＝查过但没有结果；`None` 由 `build_digest` 处理成整段不出现。
    """
    if not quality:
        if not trading:
            return Section("数据质量", [Line("验证", "休市日不安排验证")])
        return Section("数据质量", [Line("验证", "当日没有验证结果", Level.BAD)])

    blocking = list(quality.get("blocking") or ())
    lines = [Line(
        "验证",
        f"{quality.get('total', 0)} 项，阻断 {len(blocking)}，"
        f"警告 {quality.get('warnings', 0)}"
        + (f"（{quality['at']} 完成）" if quality.get("at") else ""),
        Level.BAD if blocking else Level.OK,
    )]
    lines += [Line(str(item.get("metric", "")), str(item.get("detail", "")), Level.BAD)
              for item in blocking]
    known = quality.get("known_warnings")
    return Section("数据质量", lines,
                   note=f"长期已知问题 {known} 项，已单独登记" if known else "")


def _infra_section(infra: dict) -> Section:
    """节点还撑不撑得住。休市日这段就是日报的全部意义所在。

    空字典＝采集失败；`None` 由 `build_digest` 处理成整段不出现。
    """
    if not infra:
        return Section("节点", [Line("观测", "本次没有采集到节点状态", Level.WARN)])

    lines: list[Line] = []
    disk = infra.get("disk")
    if disk:
        lines.append(Line(
            "磁盘",
            f"剩余 {disk['free_gib']:.1f} GiB / 共 {disk['total_gib']:.1f} GiB"
            f"（已用 {disk['used_pct']}%）",
            {"fail": Level.BAD, "warn": Level.WARN}.get(disk.get("level"), Level.OK),
        ))
    else:
        lines.append(Line("磁盘", "问不出来", Level.WARN))

    ram = infra.get("memory")
    if ram:
        lines.append(Line("内存", f"可用 {ram['available_gib']:.1f} GiB / 共 "
                                  f"{ram['total_gib']:.1f} GiB（已用 {ram['used_pct']}%）",
                          Level.WARN if ram["used_pct"] >= MEMORY_WARN_PCT else Level.OK))

    days = infra.get("cert_days")
    if days is not None:
        lines.append(Line("站点证书", f"剩余 {days} 天",
                          Level.BAD if days < CERT_BAD_DAYS else
                          Level.WARN if days < CERT_WARN_DAYS else Level.OK))

    failed = infra.get("failed_units")
    if failed is None:
        lines.append(Line("失败单元", "问不出来", Level.WARN))
    elif failed:
        lines.append(Line("失败单元", "、".join(failed), Level.BAD))
    else:
        lines.append(Line("失败单元", "无"))

    for unit in infra.get("units") or ():
        running = unit.get("state") == "active"
        lines.append(Line(str(unit.get("name", "")),
                          f"{unit.get('state', '?')}"
                          + (f"，自 {unit['since']}" if unit.get("since") else ""),
                          Level.OK if running else Level.BAD))
    return Section("节点", lines)


def _notification_section(deliveries: list[dict]) -> Section:
    """通知链路自己今天好不好。

    「没收到告警」有两种可能：今天没事，或者告警发不出去。这两种在收件人那边
    长得一模一样，所以每天明确报一次推了几条、失败几条。
    """
    if not deliveries:
        return Section("通知", [Line("今日推送", "0 条（当日无告警）")])
    failed = [d for d in deliveries if d.get("failed")]
    by_severity = {}
    for item in deliveries:
        key = str(item.get("severity", "?"))
        by_severity[key] = by_severity.get(key, 0) + 1
    breakdown = "，".join(f"{k} {v}" for k, v in sorted(by_severity.items()))
    lines = [Line("今日推送", f"{len(deliveries)} 条（{breakdown}）")]
    if failed:
        channels = sorted({name for d in failed for name in d.get("failed", ())})
        lines.append(Line("投递失败", f"{len(failed)} 条，通道：{'、'.join(channels)}",
                          Level.BAD))
    return Section("通知", lines)


def _readiness_section(readiness: dict) -> Section:
    """离实盘还差什么——只用查得到的事实，不回显手写的计划表。

    计划表会过期，而过期的「一切就绪」比没有这一段更糟。这里报的四项每天都能
    重新算出来：跑在什么模式、急停在不在、两条链连续多少个交易日零错误、
    券商代理还有没有心跳。
    """
    lines: list[Line] = []
    mode = readiness.get("mode", "?")
    orders = readiness.get("orders_enabled")
    lines.append(Line("运行模式", f"{mode}，下单{'已开启' if orders else '未开启'}"))

    kill = readiness.get("kill_switch")
    lines.append(Line("急停开关", kill if kill else "未触发",
                      Level.BAD if kill else Level.OK))

    for name, days in (readiness.get("clean_streak") or {}).items():
        lines.append(Line(f"{name}·连续零错误", f"{days} 个交易日"))

    beat = readiness.get("agent_beat") or {}
    state = beat.get("state")
    if state == "fresh":
        lines.append(Line("券商代理心跳", f"{beat.get('age_s', 0.0):.1f} 秒前"))
    elif state == "absent":
        lines.append(Line("券商代理心跳", "无心跳", Level.WARN))
    else:
        # 「问不出来」不能写成「无心跳」——一次时区 bug 就让它天天报代理离线
        lines.append(Line("券商代理心跳", "问不出来", Level.WARN))
    return Section("实盘就绪", lines)


def build_digest(
    day: date,
    *,
    trading: bool = True,
    strategy_runs: list[dict] | None = None,
    accounts: list[dict] | None = None,
    checks: list[dict] | None = None,
    missing_services: list[str] | None = None,
    quality: dict | None = None,
    infra: dict | None = None,
    deliveries: list[dict] | None = None,
    readiness: dict | None = None,
) -> Digest:
    """休市日照发，但不拿交易日的尺子去量它。

    `quality` / `infra` 省略即该段不出现；给空字典表示「查过，没查到」。


    以前休市日干脆不发（定时器 Mon..Fri 触发，脚本取「最近已收盘交易日」，
    于是国庆七天会把 09-30 那封原样重发五遍）。管理者要的是**每天**一封：
    机器还在跑，磁盘还在涨，证书还在倒计时。但策略没跑、质检没排，
    这些在休市日不是异常，不能算进结论里。
    """
    if trading:
        sections = [_strategy_section(strategy_runs or []),
                    _account_section(accounts or [])]
    else:
        sections = [Section("策略与账户", [Line("休市", "今日非交易日，无策略与账户活动")])]
    if quality is not None:
        sections.append(_quality_section(quality, trading=trading))
    sections.append(_service_section(checks or [], missing_services or [],
                                     trading=trading))
    if infra is not None:
        sections.append(_infra_section(infra))
    if deliveries is not None:
        sections.append(_notification_section(deliveries))
    if readiness is not None:
        sections.append(_readiness_section(readiness))
    return Digest(day, sections, trading)
