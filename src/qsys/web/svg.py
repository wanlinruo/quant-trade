"""服务端渲染的内联 SVG 折线图。

零外部依赖：图表在服务端算好坐标直接输出 SVG，浏览器不下载任何 JS 库。
监控页面最需要的性质是「出事的时候一定打得开」。
"""

from __future__ import annotations

from collections.abc import Sequence


def line_chart(
    values: Sequence[float],
    *,
    width: int = 760,
    height: int = 200,
    padding: int = 24,
    stroke: str = "#2563eb",
) -> str:
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">'
    )
    if not values:
        return (
            f"{head}"
            f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" '
            f'fill="#94a3b8" font-size="14">暂无数据</text>'
            "</svg>"
        )

    inner_w = max(1, width - 2 * padding)
    inner_h = max(1, height - 2 * padding)
    lo, hi = min(values), max(values)
    span = hi - lo

    def x_of(i: int) -> float:
        if len(values) == 1:
            return padding + inner_w / 2
        return padding + inner_w * i / (len(values) - 1)

    def y_of(v: float) -> float:
        if span == 0:
            return padding + inner_h / 2  # 平线居中
        return padding + inner_h * (1.0 - (v - lo) / span)

    points = " ".join(f"{x_of(i):.2f},{y_of(v):.2f}" for i, v in enumerate(values))
    baseline = padding + inner_h

    return (
        f"{head}"
        f'<polyline fill="none" stroke="{stroke}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round" points="{points}" />'
        f'<line x1="{padding}" y1="{baseline}" x2="{width - padding}" y2="{baseline}" '
        f'stroke="#e2e8f0" stroke-width="1" />'
        "</svg>"
    )


def multi_line_chart(
    series: Sequence[tuple[str, Sequence[float], str]], *, width: int = 760,
    height: int = 240, padding: int = 28,
) -> str:
    """共享坐标轴的多序列曲线，供策略/基准/超额报告使用。"""
    nonempty = [(name, list(values), color) for name, values, color in series if values]
    if not nonempty:
        return line_chart([], width=width, height=height, padding=padding)
    all_values = [v for _, values, _ in nonempty for v in values]
    lo, hi = min(all_values), max(all_values)
    span, inner_w, inner_h = hi - lo, width - 2 * padding, height - 2 * padding

    def points(values):
        return " ".join(
            f"{padding + inner_w * i / max(len(values) - 1, 1):.2f},"
            f"{padding + inner_h * (0.5 if span == 0 else 1 - (v - lo) / span):.2f}"
            for i, v in enumerate(values)
        )
    lines = "".join(
        f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{points(values)}" />'
        for _, values, color in nonempty
    )
    legend = "".join(
        f'<text x="{padding + i * 130}" y="16" fill="{color}" font-size="11">{name}</text>'
        for i, (name, _, color) in enumerate(nonempty)
    )
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img">{legend}{lines}</svg>')
