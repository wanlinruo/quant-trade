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


# ---- 告警也算异常（2026-09-30 指标字典 2 条阻断，标题却写「正常」）----

def test_a_warning_alone_still_breaks_the_normal_verdict():
    """质检只报 warning 时，旧写法在标题上写「正常」——信号到了却被降噪掉。"""
    digest = build_digest(DAY, checks=[
        {"name": "catalog-verify｜阻断级规则全部通过", "status": "warning",
         "detail": "2 条规则未通过"},
    ], strategy_runs=[{"engine": "影子盘", "phase": "auction", "summary": "无"}])

    assert digest.healthy is False
    # 质检汇总行本身也降成告警，所以是 2 项
    assert "有异常 2 项（告警 2）" in digest.subject()
    assert "失败" not in digest.subject()


def test_the_subject_separates_failures_from_warnings():
    digest = build_digest(DAY, checks=[
        {"name": "a", "status": "failed", "detail": "断了"},
        {"name": "b", "status": "warning", "detail": "慢"},
    ], strategy_runs=[{"engine": "影子盘", "phase": "auction", "summary": "无"}])

    # 质检汇总行本身也计一项失败，所以是 3 项
    assert digest.tally() == (2, 1)
    assert "失败 2，告警 1" in digest.subject()


def test_failures_are_listed_before_warnings():
    digest = build_digest(DAY, checks=[
        {"name": "先出现的告警", "status": "warning", "detail": "慢"},
        {"name": "后出现的失败", "status": "failed", "detail": "断了"},
    ], strategy_runs=[{"engine": "影子盘", "phase": "auction", "summary": "无"}])

    labels = [line.label for _, line in digest.problems]
    assert labels.index("后出现的失败") < labels.index("先出现的告警")


def test_the_two_levels_are_told_apart_in_the_body():
    digest = build_digest(DAY, checks=[
        {"name": "甲", "status": "failed", "detail": "断了"},
        {"name": "乙", "status": "warning", "detail": "慢"},
    ])

    text = digest.as_text()
    assert "!! 甲" in text and " ! 乙" in text
    html = digest.as_html()
    assert "<b>失败</b>" in html and "<b>告警</b>" in html


# ---- 休市日也要发，但别拿交易日的尺子量它（2026-10-01 国庆期间发现）----

HOLIDAY = date(2026, 10, 1)


def test_a_closed_day_is_still_reported_and_says_so():
    digest = build_digest(HOLIDAY, trading=False)

    assert "休市" in digest.subject()
    assert digest.healthy is True


def test_a_closed_day_does_not_count_the_missing_strategy_run():
    """休市日策略本来就不跑，报「当日无任何记录」是假警报。"""
    digest = build_digest(HOLIDAY, trading=False, strategy_runs=[])

    assert digest.problems == []


def test_a_closed_day_does_not_expect_the_nightly_checks():
    digest = build_digest(HOLIDAY, trading=False, checks=[])

    assert digest.healthy is True
    assert "休市日不安排质检" in digest.as_text()


def test_a_real_problem_on_a_closed_day_still_gets_through():
    """休市不等于不看——磁盘满了、证书过期了照样要报。"""
    digest = build_digest(HOLIDAY, trading=False, checks=[
        {"name": "service-checks｜磁盘剩余容量", "status": "failed", "detail": "剩余 3 GiB"},
    ])

    assert digest.healthy is False
    assert "休市" in digest.subject() and "失败" in digest.subject()


# ---- 数据质量单独成段（2026-09-30 两条阻断只在服务质检里占一行，被读过去了）----

def test_blocking_metrics_are_named_one_by_one():
    digest = build_digest(DAY, quality={
        "at": "19:41", "total": 570, "warnings": 28,
        "blocking": [{"metric": "live_recon.is_consistent", "detail": "到货或采集失败 4 项"}],
    })

    assert digest.healthy is False
    labels = [line.label for _, line in digest.problems]
    assert "live_recon.is_consistent" in labels
    assert "570 项，阻断 1，警告 28（19:41 完成）" in digest.as_text()


def test_a_clean_verification_is_not_a_problem():
    digest = build_digest(DAY, quality={"total": 570, "warnings": 28, "blocking": []},
                          strategy_runs=[{"engine": "影子盘", "phase": "auction",
                                          "summary": "无"}],
                          checks=[{"name": "a", "status": "succeeded"}])

    assert digest.healthy is True


def test_a_trading_day_without_any_verification_is_a_problem():
    digest = build_digest(DAY, quality={})

    assert any(line.value == "当日没有验证结果" for _, line in digest.problems)


def test_a_closed_day_is_not_expected_to_verify():
    digest = build_digest(HOLIDAY, trading=False, quality={})

    assert digest.healthy is True


def test_the_quality_section_is_absent_when_not_asked_for():
    assert "数据质量" not in build_digest(DAY).as_text()


# ---- 节点段：休市日这段就是日报的全部意义 ----

def _infra(**over):
    base = {"disk": {"free_gib": 30.6, "total_gib": 98.2, "used_pct": 64, "level": "ok"},
            "memory": {"available_gib": 9.2, "total_gib": 15.6, "used_pct": 41},
            "cert_days": 86, "failed_units": (),
            "units": [{"name": "影子盘核心", "state": "active", "since": "09-30 11:09"}]}
    return {**base, **over}


def test_a_healthy_node_reports_without_complaining():
    digest = build_digest(HOLIDAY, trading=False, infra=_infra())

    assert digest.healthy is True
    assert "剩余 30.6 GiB" in digest.as_text()


def test_a_failed_unit_is_a_problem():
    digest = build_digest(HOLIDAY, trading=False,
                          infra=_infra(failed_units=("fwupd-refresh.service",)))

    assert any("fwupd-refresh.service" in line.value for _, line in digest.problems)


def test_an_unanswerable_systemd_warns_rather_than_passing_silently():
    """问不出来不等于没事——把未知当健康是监控里最贵的错。"""
    digest = build_digest(HOLIDAY, trading=False, infra=_infra(failed_units=None))

    assert any(line.value == "问不出来" for _, line in digest.problems)


def test_a_disk_about_to_fill_escalates():
    low = build_digest(HOLIDAY, trading=False, infra=_infra(
        disk={"free_gib": 6.0, "total_gib": 98.2, "used_pct": 94, "level": "fail"}))
    warn = build_digest(HOLIDAY, trading=False, infra=_infra(
        disk={"free_gib": 15.0, "total_gib": 98.2, "used_pct": 85, "level": "warn"}))

    assert low.tally() == (1, 0)
    assert warn.tally() == (0, 1)


def test_an_expiring_certificate_escalates_before_it_bites():
    soon = build_digest(HOLIDAY, trading=False, infra=_infra(cert_days=9))
    warn = build_digest(HOLIDAY, trading=False, infra=_infra(cert_days=20))
    fine = build_digest(HOLIDAY, trading=False, infra=_infra(cert_days=21))

    assert soon.tally() == (1, 0)
    assert warn.tally() == (0, 1)
    assert fine.healthy is True


def test_a_stopped_service_is_a_problem():
    digest = build_digest(HOLIDAY, trading=False, infra=_infra(
        units=[{"name": "影子盘核心", "state": "inactive"}]))

    assert any("影子盘核心" == line.label for _, line in digest.problems)


def test_infra_collection_failing_is_itself_worth_saying():
    digest = build_digest(HOLIDAY, trading=False, infra={})

    assert any("没有采集到节点状态" in line.value for _, line in digest.problems)


# ---- 通知自检：「今天没收到告警」不该和「告警发不出去」长得一样 ----

def test_a_quiet_day_says_zero_rather_than_saying_nothing():
    digest = build_digest(HOLIDAY, trading=False, deliveries=[])

    assert "0 条（当日无告警）" in digest.as_text()
    assert digest.healthy is True


def test_the_push_count_is_broken_down_by_severity():
    digest = build_digest(HOLIDAY, trading=False, deliveries=[
        {"severity": "INFO", "failed": []},
        {"severity": "WARNING", "failed": []},
        {"severity": "WARNING", "failed": []},
    ])

    assert "3 条（INFO 1，WARNING 2）" in digest.as_text()
    assert digest.healthy is True


def test_an_undelivered_alert_is_a_failure():
    """机器人 key 被撤、群解散、出口被墙，全都静默——只有留账能发现。"""
    digest = build_digest(HOLIDAY, trading=False, deliveries=[
        {"severity": "CRITICAL", "failed": ["WebhookAlertSink"]},
    ])

    assert digest.tally() == (1, 0)
    assert any("WebhookAlertSink" in line.value for _, line in digest.problems)


# ---- 实盘就绪：只报查得到的事实，计划表会过期 ----

def _ready(**over):
    base = {"mode": "shadow", "orders_enabled": False, "kill_switch": None,
            "clean_streak": {"影子盘": 1, "模拟盘": 5}, "agent_beat_age_s": 0.4}
    return {**base, **over}


def test_readiness_states_the_mode_and_the_streaks():
    text = build_digest(HOLIDAY, trading=False, readiness=_ready()).as_text()

    assert "shadow，下单未开启" in text
    assert "影子盘·连续零错误：1 个交易日" in text
    assert "模拟盘·连续零错误：5 个交易日" in text


def test_an_engaged_kill_switch_is_a_failure():
    digest = build_digest(HOLIDAY, trading=False,
                          readiness=_ready(kill_switch="2026-10-01 手工停机"))

    assert digest.tally() == (1, 0)


def test_a_missing_agent_heartbeat_is_a_warning_not_a_failure():
    """休市日代理本来就可能没开，报失败会天天狼来了。"""
    digest = build_digest(HOLIDAY, trading=False, readiness=_ready(agent_beat_age_s=None))

    assert digest.tally() == (0, 1)
