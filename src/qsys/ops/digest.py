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
from html import escape

OK_MARK = "正常"
BAD_MARK = "有异常"
# 一条明细占一行，长的截断。错误文本常带换行（对账不一致会列出每个标的），
# 原样铺开会把日报撑成几十行，扫不动就没人读了。
VALUE_LIMIT = 140


def one_line(text: str, limit: int = VALUE_LIMIT) -> str:
    flat = " / ".join(part.strip() for part in str(text).splitlines() if part.strip())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


@dataclass(frozen=True, slots=True)
class Line:
    label: str
    value: str
    problem: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", one_line(self.value))


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    lines: list[Line] = field(default_factory=list)
    note: str = ""

    @property
    def problems(self) -> list[Line]:
        return [line for line in self.lines if line.problem]


@dataclass(frozen=True, slots=True)
class Digest:
    day: date
    sections: list[Section] = field(default_factory=list)

    @property
    def problems(self) -> list[tuple[str, Line]]:
        return [(s.title, line) for s in self.sections for line in s.problems]

    @property
    def healthy(self) -> bool:
        return not self.problems

    def subject(self) -> str:
        mark = OK_MARK if self.healthy else f"{BAD_MARK} {len(self.problems)} 项"
        return f"[qsys] {self.day} 日报 · {mark}"

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
                flag = "!! " if line.problem else "   "
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
                    f"<li>{escape(title)}｜{escape(line.label)}："
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
                f"<td style=\"padding:3px 0;{'color:#d33;font-weight:600' if line.problem else ''}\">"
                f"{escape(line.value)}</td></tr>"
                for line in section.lines
            )
            parts.append(f"<table style=\"border-collapse:collapse\">{rows}</table>")
        parts.append("</div>")
        return "".join(parts)


def _strategy_section(runs: list[dict]) -> Section:
    """当日各引擎的策略运行。没有记录本身就是问题——那说明策略没跑。"""
    if not runs:
        return Section("策略", [Line("运行记录", "当日无任何记录", problem=True)])

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
        lines.append(Line(f"{engine}·出错{suffix}", error, problem=True))
    lines.append(Line("当日委托", f"{orders} 笔，成交 {fills} 笔，拒单 {rejects} 笔",
                      problem=rejects > 0))
    return Section("策略", lines)


def _account_section(accounts: list[dict]) -> Section:
    return Section("账户", [
        Line(str(a.get("name", "")),
             f"总资产 {float(a.get('total_value', 0.0)):,.2f}"
             f"（现金 {float(a.get('cash', 0.0)):,.2f}，持仓 {a.get('positions', 0)} 只）")
        for a in accounts
    ])


def _service_section(checks: list[dict], missing: list[str]) -> Section:
    """服务质检结论 + 当天没按时跑的任务。"""
    lines: list[Line] = []
    bad = [c for c in checks if c.get("status") == "failed"]
    warn = [c for c in checks if c.get("status") == "warning"]
    if checks:
        lines.append(Line(
            "质检", f"{len(checks)} 项，失败 {len(bad)}，告警 {len(warn)}",
            problem=bool(bad),
        ))
    else:
        lines.append(Line("质检", "当日没有质检记录", problem=True))
    for item in bad + warn:
        lines.append(Line(
            str(item.get("name", "")), str(item.get("detail", ""))[:200],
            problem=item.get("status") == "failed",
        ))
    for name in missing:
        lines.append(Line(name, "当日没有运行记录", problem=True))
    return Section("服务与数据", lines)


def build_digest(
    day: date,
    *,
    strategy_runs: list[dict] | None = None,
    accounts: list[dict] | None = None,
    checks: list[dict] | None = None,
    missing_services: list[str] | None = None,
) -> Digest:
    return Digest(day, [
        _strategy_section(strategy_runs or []),
        _account_section(accounts or []),
        _service_section(checks or [], missing_services or []),
    ])
