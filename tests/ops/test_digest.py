"""每日汇报：标题就要能看出今天正不正常。

日报如果要读完才知道有没有事，很快就没人读了——所以结论前置是这个模块的主要约束。
"""

from datetime import date

from qsys.ops.alerts import send_email
from qsys.ops.digest import build_digest

DAY = date(2026, 9, 28)


def _clean():
    return build_digest(
        DAY,
        strategy_runs=[
            {"engine": "影子盘", "phase": "auction", "orders": 0, "rejects": 0,
             "summary": "候选 36 只 → 入档 1 只 → 闸门通过 0 只"},
        ],
        accounts=[{"name": "模拟-09:25入场", "total_value": 100_000.0,
                   "cash": 100_000.0, "positions": 0}],
        checks=[{"name": "tushare-sync｜日线到货", "status": "succeeded", "detail": ""}],
    )


def test_a_clean_day_says_so_in_the_subject():
    digest = _clean()

    assert digest.healthy is True
    assert "正常" in digest.subject() and "2026-09-28" in digest.subject()


def test_a_failed_check_reaches_the_subject_line():
    digest = build_digest(DAY, checks=[
        {"name": "shadow-core｜当日策略事件已执行", "status": "failed",
         "detail": "4 / 7 个事件未正常执行"},
    ])

    assert digest.healthy is False
    assert "有异常" in digest.subject()


def test_problems_are_repeated_at_the_top_of_the_body():
    """结论前置：不用读到第三节才发现服务挂了。"""
    digest = build_digest(DAY, checks=[
        {"name": "质检项", "status": "failed", "detail": "炸了"},
    ])

    text = digest.as_text()
    assert text.index("需要关注") < text.index("【策略】")


def test_no_strategy_record_is_itself_a_problem():
    """当天一条策略记录都没有，说明策略没跑——这比任何明细都要紧。"""
    digest = build_digest(DAY, strategy_runs=[])

    assert digest.healthy is False
    assert any("无任何记录" in line.value for _, line in digest.problems)


def test_a_strategy_error_is_surfaced():
    digest = build_digest(DAY, strategy_runs=[
        {"engine": "影子盘", "phase": "session_start", "orders": 0, "rejects": 0,
         "error": "reconcile: INCONSISTENT"},
    ])

    assert digest.healthy is False
    assert any("INCONSISTENT" in line.value for _, line in digest.problems)


def test_a_service_that_never_ran_is_named():
    digest = build_digest(DAY, missing_services=["tushare-sync"])

    assert any("tushare-sync" in line.label for _, line in digest.problems)


def test_rejects_are_flagged_but_plain_orders_are_not():
    ok_checks = [{"name": "a", "status": "succeeded", "detail": ""}]
    busy = build_digest(DAY, checks=ok_checks, strategy_runs=[
        {"engine": "实盘", "phase": "auction", "orders": 2, "rejects": 1,
         "summary": "买入 2 只"},
    ])
    fine = build_digest(DAY, checks=ok_checks, strategy_runs=[
        {"engine": "实盘", "phase": "auction", "orders": 2, "rejects": 0,
         "summary": "买入 2 只"},
    ])

    assert busy.healthy is False
    assert fine.healthy is True


def test_the_check_counts_come_out_right():
    digest = build_digest(DAY, checks=[
        {"name": "a", "status": "succeeded", "detail": ""},
        {"name": "b", "status": "warning", "detail": "慢"},
        {"name": "c", "status": "failed", "detail": "断了"},
    ])

    body = digest.as_text()
    assert "3 项，失败 1，告警 1" in body


def test_html_escapes_whatever_came_out_of_the_database():
    """明细里带尖括号也不能把邮件排版打烂。"""
    digest = build_digest(DAY, checks=[
        {"name": "x", "status": "failed", "detail": "<script>alert(1)</script>"},
    ])

    html = digest.as_html()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_the_email_carries_both_plain_text_and_html():
    """纯文本保底，HTML 好看；只发 HTML 的话纯文本客户端会看到一片空白。"""
    class Config:
        from_address = "a@example.com"
        recipients = ("b@example.com",)

    captured = {}
    send_email(Config(), "主题", "纯文本", "<b>富文本</b>",
               sender=lambda message: captured.update(m=message))

    message = captured["m"]
    assert message["Subject"] == "主题"
    assert message["To"] == "b@example.com"
    kinds = {part.get_content_type() for part in message.walk()}
    assert "text/plain" in kinds and "text/html" in kinds


# ---- 排版：日报要能一眼扫完（2026-09-29 拿真实数据跑出来才发现）----

MULTILINE = ("reconcile: INCONSISTENT (cash diff 49,998.69)\n"
             "  131801.SZ: local=0 remote=1000\n"
             "  131990.SZ: local=0 remote=1000")


def test_a_multiline_error_is_flattened_to_one_line():
    """对账错误会逐个列出标的，原样铺开能把日报撑成几十行。"""
    digest = build_digest(DAY, strategy_runs=[
        {"engine": "影子盘", "phase": "bar", "error": MULTILINE},
    ])

    value = digest.problems[0][1].value
    assert "\n" not in value
    assert "131801.SZ" in value and "131990.SZ" in value


def test_the_same_failure_across_events_is_said_once():
    """一次对账失败会让 checkpoint/bar/session_end 全报同一句，说四遍没有意义。"""
    digest = build_digest(DAY, checks=[{"name": "a", "status": "succeeded"}],
                          strategy_runs=[
        {"engine": "影子盘", "phase": phase, "error": MULTILINE}
        for phase in ("checkpoint", "checkpoint", "bar", "session_end")
    ])

    errors = [line for _, line in digest.problems]
    assert len(errors) == 1
    assert "4 个事件" in errors[0].label


def test_different_engines_are_still_reported_separately():
    digest = build_digest(DAY, checks=[{"name": "a", "status": "succeeded"}],
                          strategy_runs=[
        {"engine": "影子盘", "phase": "bar", "error": "甲"},
        {"engine": "实盘", "phase": "bar", "error": "甲"},
    ])

    assert len(digest.problems) == 2


def test_a_very_long_detail_is_truncated():
    digest = build_digest(DAY, strategy_runs=[
        {"engine": "影子盘", "phase": "bar", "error": "很长" * 200},
    ])

    assert len(digest.problems[0][1].value) <= 140
