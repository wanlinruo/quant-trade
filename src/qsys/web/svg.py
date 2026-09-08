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
