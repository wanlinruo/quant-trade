import re

from qsys.web.svg import line_chart


def test_returns_an_svg_element():
    svg = line_chart([1.0, 2.0, 3.0])
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_dimensions_are_honored():
    svg = line_chart([1.0, 2.0], width=400, height=100)
    assert 'width="400"' in svg
    assert 'height="100"' in svg


def test_one_point_per_value():
    svg = line_chart([1.0, 2.0, 3.0, 4.0])
    points = re.search(r'points="([^"]+)"', svg).group(1)
    assert len(points.split()) == 4


def test_rising_series_goes_up_on_screen():
    """SVG 的 y 轴向下，上升序列的 y 必须递减。"""
    svg = line_chart([1.0, 2.0, 3.0])
    points = re.search(r'points="([^"]+)"', svg).group(1).split()
    ys = [float(p.split(",")[1]) for p in points]
    assert ys[0] > ys[-1]


def test_flat_series_is_a_horizontal_line():
    svg = line_chart([5.0, 5.0, 5.0])
    points = re.search(r'points="([^"]+)"', svg).group(1).split()
    ys = {round(float(p.split(",")[1]), 3) for p in points}
    assert len(ys) == 1


def test_empty_series_renders_a_placeholder():
    svg = line_chart([])
    assert "<svg" in svg
    assert "暂无数据" in svg


def test_single_point_does_not_crash():
    assert "<svg" in line_chart([42.0])


def test_stroke_colour_is_applied():
    assert 'stroke="#ff0000"' in line_chart([1.0, 2.0], stroke="#ff0000")


def test_points_stay_inside_the_padded_area():
    svg = line_chart([1.0, 5.0, 3.0], width=200, height=100, padding=10)
    points = re.search(r'points="([^"]+)"', svg).group(1).split()
    for p in points:
        x, y = (float(v) for v in p.split(","))
        assert 10 <= x <= 190
        assert 10 <= y <= 90


def test_no_external_references():
    """零外部依赖：除 SVG 命名空间外不得出现任何 http 引用。"""
    svg = line_chart([1.0, 2.0, 3.0]).replace(
        'xmlns="http://www.w3.org/2000/svg"', ""
    )
    assert "http://" not in svg
    assert "https://" not in svg
