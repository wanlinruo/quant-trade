"""#646：全站按 A 股惯例涨红跌绿；错误/异常提示保持红色，不得借用跌色。"""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "src/qsys/web/templates"


def _css() -> str:
    return (TEMPLATES / "base.html").read_text()


def test_rise_is_red_and_fall_is_green():
    css = _css()
    assert re.search(r"--rise:\s*#dc2626", css) and re.search(r"--fall:\s*#16a34a", css)
    assert re.search(r"\.up\s*\{\s*color:\s*var\(--rise\)", css)
    assert re.search(r"\.down\s*\{\s*color:\s*var\(--fall\)", css)


def test_alert_text_stays_red_for_errors():
    assert re.search(r"\.alert-text\s*\{\s*color:\s*var\(--bad\)", _css())


def _emits_down(attr: str) -> bool:
    """'down' 作为输出类名（而非状态字典的键或比较值）。"""
    return re.search(r"(?<!==\s)'down'(?!\s*:)", attr) is not None


def test_down_class_is_only_used_for_price_direction():
    """class 属性里出现 'down' 时，同一属性必须是 'up' … 'down' 的涨跌三元式，不能当错误红用。"""
    offenders = []
    for path in TEMPLATES.glob("*.html"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for attr in re.findall(r'class="([^"]*)"', line):
                if _emits_down(attr) and "'up'" not in attr:
                    offenders.append(f"{path.name}:{lineno}")
    assert offenders == []


def test_guard_catches_an_error_style_down_class(tmp_path):
    sample = """<p class="{{ 'down' if broken else 'muted' }}">x</p>"""
    assert any(_emits_down(a) and "'up'" not in a for a in re.findall(r'class="([^"]*)"', sample))
    assert not _emits_down("{{ {'down':'bad'}.get(s) }}")
    assert not _emits_down("{{ 'error' if s == 'down' else 'muted' }}")
