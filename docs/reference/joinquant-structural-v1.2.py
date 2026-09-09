# -*- coding: utf-8 -*-
# =============================================================================
# Structural Strategy V1.2 — FROZEN FINAL
# =============================================================================
# Frozen structure:
#   HIGH_MAIN
#   HIGH_CHINEXT
#   LOW_MAIN
#   LOW_CHINEXT
#
# Disabled:
#   WEAK_MAIN
#   WEAK_CHINEXT
#
# Exit lifecycle:
#   HIGH:
#     11:25 / 14:50
#       profit & non-limit-up -> TAKE_PROFIT
#       below dynamic MA5     -> MA5_STOP
#       otherwise             -> HOLD
#
#   LOW:
#     11:25
#       limit-up                  -> HOLD
#       non-limit-up & profitable -> SELL
#       non-limit-up & non-profit -> WAIT TO 14:25
#
#     14:25
#       limit-up     -> HOLD OVERNIGHT
#       non-limit-up -> UNCONDITIONAL SELL
#
# Allocation:
#   Existing S8 stability-aware relative weights.
#   No fixed single-name cap.
#
# This file is a frozen production/research-validation candidate.
# Obsolete S4/S5/S6/S7/S8 shadow exporters, ablation engines, and Cap70
# experiment functions have been removed when they are not required by the
# live Entry / Gate / Exit / allocation path.
#
# Final validation target:
#   2023-01-01 ~ 2026-09-01
#   initial capital: 100000
# =============================================================================

from jqdata import *
from jqfactor import *
import pandas as pd
from datetime import datetime, timedelta, date
import requests
import json
import time

# V4核心实验：在V3基础上恢复原策略两处固定1.1公式；其余第一阶段纠错保持不变。

# ---------------------------- 初始化 ---------------------------------

# ==============================================================================
# S7 FINAL EXECUTABLE STRATEGY V1
# ------------------------------------------------------------------------------
# Purpose:
#   Promote the four S6-V3 gates that passed cost-aware ablation into the REAL
#   candidate pipeline. This is no longer a shadow portfolio.
#
# Frozen executable gates (NO tuning in S7):
#   LOW_MAIN       : reject RISK; allow CLEAN / UNKNOWN_ALLOW
#   HIGH_MAIN      : allow FAVORABLE; reject OTHER; allow UNKNOWN_ALLOW
#   WEAK_MAIN      : allow FAVORABLE; reject QUIET; allow UNKNOWN_ALLOW
#   HIGH_CHINEXT   : allow FAVORABLE; reject OTHER; allow UNKNOWN_ALLOW
#   LOW_CHINEXT / WEAK_CHINEXT: unchanged (insufficient sample)
#
# S4-V2 entry/exit/risk lifecycle remains unchanged after the S7 gate.
# S6 shadow accounts are disabled in this executable version.
# ==============================================================================

# =============================================================================
# S8 FINAL STABILITY-AWARE ALLOCATION V1
# -----------------------------------------------------------------------------
# Research boundary:
#   - DO NOT change any S7 gate / entry / exit / risk rule.
#   - Only change relative capital allocation among candidates that already passed S7.
#   - Core branches: HIGH_MAIN / LOW_MAIN -> 1.15
#   - Tactical branches: WEAK_MAIN / HIGH_CHINEXT -> 0.85
#   - Insufficient-sample branches: LOW_CHINEXT / WEAK_CHINEXT -> 1.00
#   - Weights are normalized across today's allowed candidates; if only one candidate
#     exists it can still receive the full available budget.
# These are frozen structural weights selected from a broad stable plateau, not a grid optimum.
# =============================================================================
S8_BRANCH_WEIGHTS = {
    'HIGH_MAIN': 1.15,
    'LOW_MAIN': 1.15,
    'WEAK_MAIN': 0.85,
    'HIGH_CHINEXT': 0.85,
    'LOW_CHINEXT': 1.00,
    'WEAK_CHINEXT': 1.00,
}

def initialize(context):
    set_option('use_real_price', True)
    log.set_level('system', 'error')
    set_option('avoid_future_data', True)

    g.n_days_limit_up_list = []
    g.trade_log = []
    g.daily_records = []
    g.prev_total_value = None
    g.weekly_summaries = []
    g.monthly_summaries = []
    g.yearly_summaries = []

    # 真实成交关联
    g.pending_order_meta = {}
    g.logged_trade_ids = set()

    # ===== Stage 2 生命周期骨架 =====
    # 当日信号标签：stock -> HIGH_MAIN / HIGH_CHINEXT / ...
    g.signal_tags = {}

    # 当前股票的“最后一次买入策略标签”。
    # S2-V2 为保持基线行为，跨分支重复买入时仍允许覆盖。
    # 这正是 S2-V2 要解决的问题。
    g.position_tags = {}

    # 仅用于结构诊断；S2-V2 保持旧的“首次入场日”语义。
    g.position_entry_dates = {}

    # 记录跨分支持仓碰撞，但本版不阻止交易。
    g.collision_events = []

    # ===== S2-V2 PositionLot影子账本 =====
    g.position_lots = {}          # stock -> [lot, lot, ...]
    g.closed_lots = []            # 已完成/部分完成的lot级卖出记录
    g.lot_seq = 0
    g.lot_reconcile_errors = []   # 真实仓位与影子lot数量不一致
    g.lot_collision_events = []   # 同stock不同策略开放lot共存
    g.stage2_version = 'STRUCTURAL_BASELINE_V2.0'

    # ===== Stage 4 / S4-V2 =====
    g.stage4_version = 'S4_V2_REAL_RULE_VALIDATION'
    g.s4v2_entry_filter_events = []
    g.s4v2_risk_events = []
    g.s4v2_export_dir = 'strategy_s4_v2_real'
    g.s4v2_export_prefix = 'strategy_s4_v2_real__'
    g.s4v2_export_mode = None

    # 企业微信默认关闭
    g.webhook_url = 'YOUR_WECHAT_WEBHOOK_URL'
    g.enable_wechat = False

    # ===== S5 V4 Tail Risk Collector / Regime Shadow =====
    # 纯研究旁路：不改变 Entry / Exit / Risk / 仓位。
    # 下一次长回测一次性采集 LOW_MAIN 个股入场因子，并验证已冻结的 Regime 候选。
    init_regime_analysis()

    # ===== S7 Final Executable Gates =====
    # S6-V3 已完成成本感知消融；本版不再运行任何Shadow账户。
    g.s7_version = 'S7_FINAL_EXECUTABLE_V1'
    g.s7_gate_decisions = []
    g.s7_export_dir = 'strategy_s7_final_executable_v1'
    g.s7_export_prefix = 'strategy_s7_final_executable_v1__'
    g.s7_export_mode = None

    # ===== S8 Final Stability-Aware Allocation =====
    g.s8_version = 'S8_FINAL_STABILITY_AWARE_ALLOCATION_FROZEN'
    g.s8_branch_weights = dict(S8_BRANCH_WEIGHTS)
    g.s8_allocation_decisions = []
    g.s8_export_dir = 'strategy_s8_final_stability_aware_v1_2_file_first'
    g.s8_export_prefix = 'strategy_s8_final_stability_aware_v1_2__'
    g.s8_export_mode = None

    # ===== FINAL / OOS minimal research output =====
    g.final_version = 'STRUCTURAL_STRATEGY_V1_2_FROZEN_FINAL'
    g.final_export_dir = 'strategy_structural_v1_2_frozen_final'
    g.final_export_prefix = 'strategy_structural_v1_2_frozen_final__'
    g.final_export_mode = None
    g.final_daily_nav = []
    try:
        g.final_initial_value = float(context.portfolio.total_value)
    except Exception:
        g.final_initial_value = 100000.0

    # V1.1 LOW/WEAK lifecycle research events
    g.low_weak_lifecycle_events = []

def after_code_changed(context):
    g.n_days_limit_up_list = []
    unschedule_all()

    # Entry
    run_daily(buy, '09:26')

    # 11:25:
    #   HIGH -> legacy TP / dynamic MA5
    #   LOW/WEAK -> non-limit-up & profitable sells; otherwise hold.
    run_daily(sell_1125, time='11:25', reference_security='000300.XSHG')

    # 14:25:
    #   LOW/WEAK only -> non-limit-up unconditional exit; limit-up holds.
    run_daily(sell_low_weak_1425, time='14:25', reference_security='000300.XSHG')

    # 14:50:
    #   HIGH only -> legacy TP / dynamic MA5.
    run_daily(sell_high_1450, time='14:50', reference_security='000300.XSHG')


def before_trading_start(context):
    # S5 V4：记录当天入场可知的上一交易日市场原始因子。
    # 已将全市场生态计算改为批量快速路径，避免 12 日全市场逐股循环。
    record_market_regime(context)


def send_wechat_msg(msg):
    """发送 Markdown 消息到企业微信，若开关关闭则跳过"""
    if not g.enable_wechat:
        return
    try:
        headers = {'Content-Type': 'application/json'}
        data = {
            "msgtype": "markdown",
            "markdown": {
                "content": msg
            }
        }
        requests.post(g.webhook_url, headers=headers, data=json.dumps(data), timeout=3)
    except Exception as e:
        print(f"Webhook 发送失败: {e}")

# ---------------------------- 独立开盘汇总函数 ----------------------------
def print_open_summary(context, qualified, sbgk_stocks, sbdk_stocks, rzq_stocks, buy_details, insufficient):
    """打印开盘前汇总信息（资产、选股、分类、买入详情、资金不足）并发送企微"""
    date_str = context.current_dt.strftime("%Y-%m-%d")
    # 构建消息内容
    lines = []
    lines.append(f"## 📊 **首板高开-低开-弱转强混合策略** 开盘汇总 ({date_str})")
    lines.append(f"**总资产**: {context.portfolio.total_value:.2f}  **可用现金**: {context.portfolio.available_cash:.2f}")
    lines.append(f"**今日选股**: {qualified if qualified else '空'}")
    if qualified:
        lines.append(f"**计划买入数量**: {len(qualified)} 只")
        lines.append(f"**首板高开**: {sbgk_stocks}")
        lines.append(f"**首板低开**: {sbdk_stocks}")
        lines.append(f"**弱转强**: {rzq_stocks}")
        if buy_details:
            lines.append("**买入详情**:")
            for s, price, amt in buy_details:
                lines.append(f"  • {s} {get_security_info(s).display_name} 价格:{price:.2f} 买入金额:{amt:.2f}")
        if insufficient:
            lines.append(f"**资金不足（不足一手）**: {insufficient}")
    msg = "\n".join(lines)
    # 打印控制台
    print(f'\n========== 开盘前汇总 ({date_str}) ==========')
    print(f'总资产: {context.portfolio.total_value:.2f}  可用现金: {context.portfolio.available_cash:.2f}')
    print(f'今日选股: {qualified if qualified else "空"}')
    if qualified:
        print(f'计划买入数量: {len(qualified)} 只')
        print('首板高开：' + str(sbgk_stocks))
        print('首板低开：' + str(sbdk_stocks))
        print('弱转强：' + str(rzq_stocks))
        if buy_details:
            print('买入详情:')
            for s, price, amt in buy_details:
                print(f'  {s} {get_security_info(s).display_name} 价格:{price:.2f} 买入金额:{amt:.2f}')
        if insufficient:
            print(f'资金不足（不足一手）未能买入: {insufficient}')
    print('\n============================================')
    # 发送企微
    send_wechat_msg(msg)

# ---------------------------- 第一阶段纠错辅助函数 ----------------------------
def get_first_auction_record(stock, start, end, fields):
    """V4归因实验：恢复原策略竞价取值方式。

    get_call_auction 返回非空时，直接取返回结果的第1条记录，
    不按 time 重排，也不改成最后一条。
    其余第一阶段纠错保持不变。
    """
    auction = get_call_auction(stock, start_date=start, end_date=end, fields=fields)
    if auction is None or auction.empty:
        return None
    return auction.iloc[0]

def register_order_meta(order_obj, operation, stock, avg_cost=None,
                        strategy_tag=None, exit_reason=None, entry_stock_factors=None):
    """登记下单信息，但不把委托当作真实成交。

    S2-V2 新增 strategy_tag / exit_reason，仅用于生命周期路由和归因；
    不改变订单本身。
    """
    if order_obj is None:
        return False
    order_id = getattr(order_obj, 'order_id', None)
    if order_id is None:
        return False
    g.pending_order_meta[order_id] = {
        'operation': operation,
        'stock': stock,
        'avg_cost': avg_cost,
        'strategy_tag': strategy_tag,
        'exit_reason': exit_reason,
        # S5 V4：仅研究旁路使用。BUY成交后复制进 PositionLot，
        # 后续 SELL 归因可以恢复买入当时的个股特征。
        'entry_stock_factors': entry_stock_factors,
    }
    return True


def _next_lot_id(stock):
    g.lot_seq += 1
    return '%s-%06d' % (stock, g.lot_seq)


def _open_lots(stock):
    return [x for x in g.position_lots.get(stock, []) if x.get('remaining_qty', 0) > 0]


def _shadow_buy_lot(stock, strategy_tag, trade_time, price, volume, trade_id, order_id, entry_stock_factors=None):
    """每笔真实BUY成交建立一个独立虚拟lot。只记账，不影响真实订单。"""
    if volume <= 0:
        return

    existing_tags = set(
        x.get('strategy_tag') for x in _open_lots(stock)
        if x.get('strategy_tag')
    )

    lot = {
        'lot_id': _next_lot_id(stock),
        'stock': stock,
        'strategy_tag': strategy_tag or 'UNKNOWN',
        'entry_time': trade_time,
        'entry_date': trade_time.date(),
        'entry_price': float(price),
        'initial_qty': float(volume),
        'remaining_qty': float(volume),
        'buy_trade_id': trade_id,
        'buy_order_id': order_id,
        # S5 V4：保存 BUY 当时已经可知的个股因子快照。
        # 该字段不参与任何交易决策。
        'entry_stock_factors': dict(entry_stock_factors or {}),
    }
    g.position_lots.setdefault(stock, []).append(lot)

    if existing_tags and lot['strategy_tag'] not in existing_tags:
        event = {
            'date': trade_time,
            'stock': stock,
            'existing_tags': sorted(existing_tags),
            'new_tag': lot['strategy_tag'],
            'new_lot_id': lot['lot_id'],
            'price': float(price),
            'qty': float(volume),
        }
        g.lot_collision_events.append(event)
        print('[S2-LOT-COLLISION] %s stock=%s existing=%s new=%s lot=%s qty=%s price=%.3f'
              % (trade_time, stock, sorted(existing_tags),
                 lot['strategy_tag'], lot['lot_id'], volume, price))


def _shadow_sell_lots(stock, trade_time, price, volume, trade_id, order_id):
    """把真实SELL成交回写到PositionLot。

    S2-V3优先按照卖单生成时的lot_exit_plan分配；
    若缺少计划，才退回FIFO作为容错。
    """
    remaining_sell = float(volume)
    if remaining_sell <= 0:
        return

    meta = g.pending_order_meta.get(order_id, {})
    plan = meta.get('lot_exit_plan') or []

    # lot_id -> lot
    lot_map = {
        lot.get('lot_id'): lot
        for lot in g.position_lots.get(stock, [])
    }

    allocations = []

    if plan:
        for p in plan:
            if remaining_sell <= 0:
                break

            lot = lot_map.get(p.get('lot_id'))
            if lot is None:
                continue

            available = float(lot.get('remaining_qty', 0) or 0)
            planned = float(p.get('qty', 0) or 0)
            qty = min(available, planned, remaining_sell)
            if qty <= 0:
                continue

            allocations.append((lot, qty, p.get('reason')))
            remaining_sell -= qty

    # 容错：若真实成交量超过计划分配，则按FIFO补齐
    if remaining_sell > 1e-8:
        lots = get_open_lots(stock)
        lots.sort(key=lambda x: (x['entry_time'], x['lot_id']))
        used_ids = {x[0].get('lot_id') for x in allocations}

        for lot in lots:
            if remaining_sell <= 0:
                break
            if lot.get('lot_id') in used_ids:
                continue

            qty = min(float(lot.get('remaining_qty', 0) or 0), remaining_sell)
            if qty <= 0:
                continue

            allocations.append((lot, qty, 'FALLBACK_FIFO'))
            remaining_sell -= qty

    for lot, qty, reason in allocations:
        entry_price = float(lot.get('entry_price', 0) or 0)
        realized_pnl = (float(price) - entry_price) * qty
        ret = (float(price) / entry_price - 1) * 100 if entry_price else None
        holding_days = _trade_holding_days(lot['entry_date'], trade_time.date())

        g.closed_lots.append({
            'lot_id': lot['lot_id'],
            'stock': stock,
            'strategy_tag': lot.get('strategy_tag'),
            'entry_time': lot.get('entry_time'),
            'entry_price': entry_price,
            'exit_time': trade_time,
            'exit_price': float(price),
            'qty': qty,
            'return_pct': ret,
            'realized_pnl_gross': realized_pnl,
            'holding_trade_days': holding_days,
            'exit_reason': reason,
            'buy_trade_id': lot.get('buy_trade_id'),
            'sell_trade_id': trade_id,
            'sell_order_id': order_id,
        })

        lot['remaining_qty'] = max(float(lot.get('remaining_qty', 0) or 0) - qty, 0)

    if remaining_sell > 1e-8:
        print('[S2-V3-LOT-WARN] SELL仍有未匹配数量 stock=%s unmatched_qty=%s'
              % (stock, remaining_sell))

def reconcile_shadow_lots(context, phase=''):
    """校验真实持仓数量与影子lot剩余数量。

    只诊断，不修正、不影响交易。
    """
    stocks = set(g.position_lots.keys()) | set(context.portfolio.positions.keys())
    for stock in stocks:
        shadow_qty = sum(float(x.get('remaining_qty', 0)) for x in _open_lots(stock))
        pos = context.portfolio.positions.get(stock)
        real_qty = float(pos.total_amount) if pos is not None else 0.0

        if abs(shadow_qty - real_qty) > 1e-8:
            event = {
                'date': context.current_dt,
                'phase': phase,
                'stock': stock,
                'real_qty': real_qty,
                'shadow_qty': shadow_qty,
                'diff': shadow_qty - real_qty,
            }
            g.lot_reconcile_errors.append(event)
            print('[S2-LOT-RECONCILE-ERROR] %s phase=%s stock=%s real=%s shadow=%s diff=%s'
                  % (context.current_dt, phase, stock, real_qty,
                     shadow_qty, shadow_qty - real_qty))


def sync_filled_trades(context):
    """用聚宽真实成交回报同步 trade_log。

    S2-V2：
    - BUY 成交后登记策略标签；
    - 若已有持仓标签且新标签不同，记录 collision_event；
    - 为保证与冻结基线等价，本版仍允许新标签覆盖旧标签；
    - 不做虚拟子仓拆分，留给 S2-V2。
    """
    trades = get_trades()
    if not trades:
        return

    for trade_id, trade in trades.items():
        if trade_id in g.logged_trade_ids:
            continue

        order_id = getattr(trade, 'order_id', None)
        meta = g.pending_order_meta.get(order_id, {})
        operation = meta.get('operation')

        if operation not in ('BUY', 'SELL'):
            continue

        stock = getattr(trade, 'security', None) or meta.get('stock')
        price = float(getattr(trade, 'price', 0) or 0)
        volume = float(getattr(trade, 'amount', 0) or 0)
        trade_time = getattr(trade, 'time', context.current_dt)
        amount = price * volume
        strategy_tag = meta.get('strategy_tag')
        exit_reason = meta.get('exit_reason')

        ret = None
        if operation == 'SELL':
            avg_cost = getattr(trade, 'avg_cost', None)
            if avg_cost is None or avg_cost == 0:
                avg_cost = meta.get('avg_cost')
            if avg_cost:
                ret = 100 * (price / avg_cost - 1)

        if operation == 'BUY':
            _shadow_buy_lot(
                stock, strategy_tag, trade_time, price, volume,
                trade_id, order_id,
                entry_stock_factors=meta.get('entry_stock_factors')
            )

            old_tag = g.position_tags.get(stock)
            if old_tag and strategy_tag and old_tag != strategy_tag:
                g.collision_events.append({
                    'date': trade_time,
                    'stock': stock,
                    'old_tag': old_tag,
                    'new_tag': strategy_tag,
                    'price': price,
                    'volume': volume,
                })
                print('[S2-COLLISION] %s %s -> %s stock=%s price=%.3f volume=%s'
                      % (trade_time, old_tag, strategy_tag, stock, price, volume))

            # 兼容字段：仍记录最后一次标签，但S2-V3真实退出不再依赖它
            if strategy_tag:
                g.position_tags[stock] = strategy_tag

            if stock not in g.position_entry_dates:
                g.position_entry_dates[stock] = trade_time.date()

        if operation == 'SELL':
            # S5 V4：只归因本次 SELL 新产生的 closed_lot 事件。
            # 不再按 lot_id 去重，因为同一个 lot 可能发生部分卖出，
            # 每次真实已实现收益都应该保留。
            before_closed_count = len(g.closed_lots) if hasattr(g, 'closed_lots') else 0

            _shadow_sell_lots(stock, trade_time, price, volume,
                              trade_id, order_id)

            if hasattr(g, 'closed_lots'):
                new_closed_lots = g.closed_lots[before_closed_count:]
                for closed_lot in new_closed_lots:
                    record_trade_regime(closed_lot, context)

        g.trade_log.append({
            'date': trade_time,
            'stock': stock,
            'operation': operation,
            'price': price,
            'volume': volume,
            'amount': amount,
            'return': ret,
            'order_id': order_id,
            'trade_id': trade_id,
            'strategy_tag': strategy_tag,
            'exit_reason': exit_reason,
        })
        g.logged_trade_ids.add(trade_id)

    # 只清理已经完全没有真实持仓的生命周期标签
    live_positions = set(context.portfolio.positions.keys())
    for stock in list(g.position_tags.keys()):
        if stock not in live_positions:
            g.position_tags.pop(stock, None)
            g.position_entry_dates.pop(stock, None)


def classify_board(stock):
    """显式板块制度标签。"""
    return 'CHINEXT' if stock.startswith(('300', '301')) else 'MAIN'


def make_strategy_tag(strategy_name, stock):
    return '%s_%s' % (strategy_name, classify_board(stock))


def strategy_name_from_tag(tag):
    if not tag:
        return 'UNKNOWN'
    return str(tag).split('_', 1)[0]


def _trade_holding_days(entry_date, current_date):
    try:
        days = get_trade_days(start_date=entry_date, end_date=current_date)
        return max(len(days) - 1, 0)
    except Exception:
        return max((current_date - entry_date).days, 0)


def get_open_lots(stock):
    return [
        lot for lot in g.position_lots.get(stock, [])
        if float(lot.get('remaining_qty', 0) or 0) > 0
    ]


def get_sellable_lots(context, stock):
    """A股T+1：只允许卖出入场日早于当前交易日的lot。"""
    today = context.current_dt.date()
    return [
        lot for lot in get_open_lots(stock)
        if lot.get('entry_date') is not None and lot['entry_date'] < today
    ]


def _ma5_now(stock):
    current_data = get_current_data()
    close_data = attribute_history(stock, 4, '1d', ['close'])
    if close_data is None or len(close_data) == 0:
        return None
    m4 = close_data['close'].mean()
    return (m4 * 4 + current_data[stock].last_price) / 5


def legacy_lot_exit_decision(context, stock, lot):
    """冻结基线退出语义，改为以lot自己的entry_price判断盈亏。"""
    current_data = get_current_data()
    last_price = current_data[stock].last_price
    high_limit = current_data[stock].high_limit
    entry_price = float(lot.get('entry_price', 0) or 0)

    if entry_price <= 0:
        return False, None, None

    # 原止盈：非涨停且当前盈利
    if last_price < high_limit and last_price > entry_price:
        ret = 100 * (last_price / entry_price - 1)
        return True, 'TAKE_PROFIT', ret

    # 原MA5
    ma5 = _ma5_now(stock)
    if ma5 is not None and last_price < ma5:
        ret = 100 * (last_price / entry_price - 1)
        return True, 'MA5_STOP', ret

    return False, None, None














def _is_limit_up_now(current_data, stock):
    """Robust current limit-up check."""
    try:
        last_price = float(current_data[stock].last_price)
        high_limit = float(current_data[stock].high_limit)
        if last_price <= 0 or high_limit <= 0:
            return False
        return last_price >= high_limit * (1.0 - 1e-7)
    except Exception:
        return False


def _lot_return_pct(current_data, stock, lot):
    try:
        entry_price = float(lot.get('entry_price', 0) or 0)
        last_price = float(current_data[stock].last_price)
        if entry_price <= 0 or last_price <= 0:
            return None
        return 100.0 * (last_price / entry_price - 1.0)
    except Exception:
        return None


def _record_low_weak_lifecycle_event(context, stock, lot, checkpoint,
                                     is_limit_up, ret, action, reason):
    try:
        g.low_weak_lifecycle_events.append({
            'date': context.current_dt,
            'stock': stock,
            'strategy_tag': lot.get('strategy_tag'),
            'lot_id': lot.get('lot_id'),
            'entry_date': lot.get('entry_date'),
            'holding_trade_days': _trade_holding_days(
                lot.get('entry_date'), context.current_dt.date()
            ) if lot.get('entry_date') is not None else None,
            'checkpoint': checkpoint,
            'last_price': get_current_data()[stock].last_price,
            'high_limit': get_current_data()[stock].high_limit,
            'is_limit_up': bool(is_limit_up),
            'return_pct_now': ret,
            'action': action,
            'reason': reason,
        })
    except Exception:
        pass


def low_weak_1125_decision(context, stock, lot):
    """
    LOW/WEAK 11:25:
      limit-up -> hold
      non-limit-up & profit > 0 -> sell
      non-limit-up & return <= 0 -> wait 14:25
    """
    current_data = get_current_data()
    ret = _lot_return_pct(current_data, stock, lot)
    if ret is None:
        return False, None, None

    is_limit_up = _is_limit_up_now(current_data, stock)

    if is_limit_up:
        _record_low_weak_lifecycle_event(
            context, stock, lot, '11:25', True, ret,
            'HOLD', 'LW_1125_LIMIT_UP_HOLD'
        )
        return False, None, ret

    if ret > 0:
        _record_low_weak_lifecycle_event(
            context, stock, lot, '11:25', False, ret,
            'SELL', 'LW_1125_PROFIT_NON_LIMIT'
        )
        return True, 'LW_1125_PROFIT_NON_LIMIT', ret

    _record_low_weak_lifecycle_event(
        context, stock, lot, '11:25', False, ret,
        'HOLD_TO_1425', 'LW_1125_NONPROFIT_WAIT_1425'
    )
    return False, None, ret


def low_weak_1425_decision(context, stock, lot):
    """
    LOW/WEAK 14:25:
      limit-up -> hold overnight
      non-limit-up -> exit regardless of P/L
    """
    current_data = get_current_data()
    ret = _lot_return_pct(current_data, stock, lot)
    if ret is None:
        return False, None, None

    is_limit_up = _is_limit_up_now(current_data, stock)

    if is_limit_up:
        _record_low_weak_lifecycle_event(
            context, stock, lot, '14:25', True, ret,
            'HOLD_OVERNIGHT', 'LW_1425_LIMIT_UP_HOLD'
        )
        return False, None, ret

    _record_low_weak_lifecycle_event(
        context, stock, lot, '14:25', False, ret,
        'SELL', 'LW_1425_NON_LIMIT_EXIT'
    )
    return True, 'LW_1425_NON_LIMIT_EXIT', ret


def _submit_checkpoint_exits(context, checkpoint):
    """
    checkpoint:
      '1125'      -> HIGH legacy + LOW/WEAK morning lifecycle
      'LW_1425'   -> LOW/WEAK only
      'HIGH_1450' -> HIGH only
    """
    sync_filled_trades(context)
    current_data = get_current_data()

    for stock in list(context.portfolio.positions):
        position = context.portfolio.positions[stock]
        closeable = float(position.closeable_amount or 0)
        if closeable <= 0:
            continue

        sellable_lots = get_sellable_lots(context, stock)
        if not sellable_lots:
            continue

        exit_lots = []
        requested_qty = 0.0

        for lot in sellable_lots:
            strategy = strategy_name_from_tag(lot.get('strategy_tag'))

            should_exit = False
            reason = None
            ret = None

            if checkpoint == '1125':
                if strategy in ('LOW', 'WEAK'):
                    should_exit, reason, ret = low_weak_1125_decision(
                        context, stock, lot
                    )
                else:
                    should_exit, reason, ret = legacy_lot_exit_decision(
                        context, stock, lot
                    )

            elif checkpoint == 'LW_1425':
                if strategy not in ('LOW', 'WEAK'):
                    continue
                should_exit, reason, ret = low_weak_1425_decision(
                    context, stock, lot
                )

            elif checkpoint == 'HIGH_1450':
                if strategy != 'HIGH':
                    continue
                should_exit, reason, ret = legacy_lot_exit_decision(
                    context, stock, lot
                )

            if not should_exit:
                continue

            qty = float(lot.get('remaining_qty', 0) or 0)
            if qty <= 0:
                continue

            exit_lots.append({
                'lot': lot,
                'reason': reason,
                'ret': ret,
                'qty': qty,
            })
            requested_qty += qty

        if requested_qty <= 0:
            continue

        sell_qty = min(requested_qty, closeable)
        if sell_qty <= 0:
            continue

        target_amount = max(float(position.total_amount) - sell_qty, 0)
        order_obj = order_target(stock, int(target_amount))

        if order_obj is not None:
            order_id = getattr(order_obj, 'order_id', None)
            if order_id is not None:
                summary = []
                remaining = sell_qty

                for x in exit_lots:
                    if remaining <= 0:
                        break
                    q = min(x['qty'], remaining)
                    summary.append({
                        'lot_id': x['lot']['lot_id'],
                        'strategy_tag': x['lot'].get('strategy_tag'),
                        'qty': q,
                        'reason': x['reason'],
                        'ret': x['ret'],
                    })
                    remaining -= q

                g.pending_order_meta[order_id] = {
                    'operation': 'SELL',
                    'stock': stock,
                    'avg_cost': position.avg_cost,
                    'strategy_tag': 'MULTI_LOT',
                    'exit_reason': 'LOT_ROUTED_V1_1',
                    'lot_exit_plan': summary,
                }

                print('[V1.1-EXIT] %s checkpoint=%s stock=%s sell_qty=%s target=%s lots=%s'
                      % (context.current_dt, checkpoint, stock, sell_qty,
                         target_amount, summary))


def sell_1125(context):
    _submit_checkpoint_exits(context, '1125')


def sell_low_weak_1425(context):
    _submit_checkpoint_exits(context, 'LW_1425')


def sell_high_1450(context):
    _submit_checkpoint_exits(context, 'HIGH_1450')



# ============================ Stage 4 / S4-V2 REAL RULES ============================

def _s4v2_safe_float(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def s4v2_low_main_auction_ratio(stock, context, start, end):
    """LOW_MAIN Stage4 Entry因子：竞价量 / 昨日成交量。

    与Stage3/S4-V1口径保持一致：
    - 昨日成交量：attribute_history(..., 1, '1d', volume)
    - 竞价量：原策略语义 get_call_auction 返回第1条
    """
    try:
        h = attribute_history(
            stock, 1, '1d',
            fields=['volume'],
            skip_paused=True
        )
        if h is None or len(h) == 0:
            return None

        prev_volume = _s4v2_safe_float(h['volume'].iloc[-1])
        if not prev_volume or prev_volume <= 0:
            return None

        auc = get_first_auction_record(
            stock, start, end,
            ['time', 'volume', 'current']
        )
        if auc is None:
            return None

        av = _s4v2_safe_float(auc['volume'])
        if av is None:
            return None

        return av / prev_volume
    except Exception as e:
        print('[S4-V2-AUCTION-WARN] stock=%s error=%s' % (stock, e))
        return None
















def buy(context):
    """S7 Final Entry Dispatcher：S4候选 -> S7真实Gate -> 真实资金分配/下单。"""
    current_data = get_current_data()
    raw_qualified, raw_sbgk, raw_sbdk, raw_rzq = get_stock_list(context)

    # V1.2 NO_WEAK:
    # Remove the entire WEAK family before S7 Gate and before allocation.
    # This is a pure structural ablation; no thresholds are changed.
    def _v12_keep_non_weak(stock):
        return g.signal_tags.get(stock) not in ('WEAK_MAIN', 'WEAK_CHINEXT')

    raw_qualified = [s for s in raw_qualified if _v12_keep_non_weak(s)]
    raw_rzq = [s for s in raw_rzq if _v12_keep_non_weak(s)]

    # S7：这是正式Gate，不再是Shadow。UNKNOWN_ALLOW采用保守兼容策略继续放行。
    qualified, sbgk_stocks, sbdk_stocks, rzq_stocks = s7_apply_real_gates(
        context, raw_qualified, raw_sbgk, raw_sbdk, raw_rzq
    )

    buy_details = []
    insufficient = []

    if qualified:
        # S8：只改变通过 S7 Gate 后的同日相对资金分配。
        # 权重会在“今日允许候选”内部归一化，因此单只候选仍可使用全部可用预算。
        available_cash = float(context.portfolio.available_cash or 0.0)
        allocatable_cash = max(0.0, available_cash)
        weighted = []
        total_weight = 0.0
        for stock in qualified:
            tag = g.signal_tags.get(stock, 'UNKNOWN')
            weight = float(getattr(g, 's8_branch_weights', S8_BRANCH_WEIGHTS).get(tag, 1.0))
            if weight <= 0 or pd.isna(weight):
                weight = 1.0
            weighted.append((stock, tag, weight))
            total_weight += weight

        if total_weight <= 0:
            total_weight = float(len(weighted)) if weighted else 1.0

        for stock, tag, weight in weighted:
            budget = allocatable_cash * weight / total_weight
            open_price = current_data[stock].day_open

            allocation_row = {
                'date': context.current_dt,
                'stock': stock,
                'strategy_tag': tag,
                'branch_weight': weight,
                'total_day_weight': total_weight,
                'available_cash_before_batch': available_cash,
                'target_budget': budget,
                'open_price': open_price,
                'order_submitted': False,
                'status': None,
            }

            if allocatable_cash <= 0:
                insufficient.append(stock)
                allocation_row['target_budget'] = 0.0
                allocation_row['status'] = 'NO_AVAILABLE_CASH'
                g.s8_allocation_decisions.append(allocation_row)
                continue

            if open_price is None or pd.isna(open_price) or open_price <= 0:
                insufficient.append(stock)
                allocation_row['status'] = 'INVALID_OPEN_PRICE'
                g.s8_allocation_decisions.append(allocation_row)
                continue

            if budget / open_price >= 100:
                order_obj = order_value(stock, budget, MarketOrderStyle(open_price))
                entry_stock_factors = getattr(g, 's5_signal_entry_factors', {}).get(stock)
                if register_order_meta(
                        order_obj, 'BUY', stock, strategy_tag=tag,
                        entry_stock_factors=entry_stock_factors):
                    buy_details.append((stock, open_price, budget))
                    allocation_row['order_submitted'] = True
                    allocation_row['status'] = 'ORDER_SUBMITTED'
                else:
                    insufficient.append(stock)
                    allocation_row['status'] = 'ORDER_META_FAILED'
            else:
                insufficient.append(stock)
                allocation_row['status'] = 'BELOW_BOARD_LOT'

            g.s8_allocation_decisions.append(allocation_row)
            print('[S8-ALLOC] %s stock=%s tag=%s weight=%.2f total_weight=%.2f cash=%.2f budget=%.2f status=%s' % (
                context.current_dt, stock, tag, weight, total_weight,
                available_cash, budget, allocation_row.get('status')
            ))

    # FINAL版不再输出逐次 S8 checkpoint；只在收盘统一落盘最小研究数据。

    print_open_summary(
        context, qualified, sbgk_stocks, sbdk_stocks,
        rzq_stocks, buy_details, insufficient
    )


# ---------------------------- Stage2 lot级退出正式生效 ----------------------------
# V2.0.1 修复：删除后置同名 legacy exit/risk 定义，避免覆盖 lot-level 接口。

def after_trading_end(context):
    # 第一阶段纠错：先按真实成交回报更新交易日志
    sync_filled_trades(context)

    current_date = context.current_dt.date()
    total_value = context.portfolio.total_value

    if g.prev_total_value is not None and g.prev_total_value != 0:
        daily_return = (total_value - g.prev_total_value) / g.prev_total_value * 100
    else:
        daily_return = 0.0
    g.prev_total_value = total_value

    positions = list(context.portfolio.positions.keys())
    today_trades = [t for t in g.trade_log if t['date'].date() == current_date]

    g.daily_records.append({
        'date': current_date,
        'total_value': total_value,
        'daily_return': daily_return,
        'positions': positions,
        'trades': today_trades
    })

    # 控制台打印
    print(f'\n========== 每日收盘 {current_date} ==========')
    print(f'总资产: {total_value:.2f}  当日收益率: {daily_return:.2f}%')
    print(f'持仓: {positions if positions else "空仓"}')
    print(f'今日交易次数: {len(today_trades)}')
    print('\n============================================')
    # 发送企微
    close_msg = f"## 📈 **首板高开-低开-弱转强混合策略** 收盘总结 ({current_date})\n**总资产**: {total_value:.2f}\n**当日收益率**: {daily_return:.2f}%\n**持仓**: {positions if positions else '空仓'}\n**今日交易次数**: {len(today_trades)}"
    send_wechat_msg(close_msg)

    # S2-V2：收盘前同步最新真实成交并核对影子lot数量
    sync_filled_trades(context)
    reconcile_shadow_lots(context, phase='AFTER_CLOSE')

    # FINAL/OOS：仅保留最小必要研究数据，不再落盘历史 S4/S5/S7/S8 冗余研究文件。
    final_export_research(context)

    if g.lot_collision_events:
        print('[S2-LOT-SUMMARY] 累计跨分支lot碰撞次数: %d'
              % len(g.lot_collision_events))
    if g.lot_reconcile_errors:
        print('[S2-LOT-SUMMARY] 累计lot数量核对异常: %d'
              % len(g.lot_reconcile_errors))

    # S2-V2 结构诊断：仅打印累计跨分支碰撞次数，不改变持仓
    if g.collision_events:
        print('[S2-STRUCTURE] 累计跨分支持仓碰撞次数: %d' % len(g.collision_events))




# ---------------------------- 选股核心函数（修正版：低开/高开互斥）----------------------------
def entry_low(stock, context, start, end):
    """LOW 独立 Entry 接口；S2-V2 完全调用冻结基线条件。"""
    return check_low_open(stock, context, start, end)


def entry_high(stock, context, start, end):
    """HIGH 独立 Entry 接口；S2-V2 完全调用冻结基线条件。"""
    return check_high_open(stock, context, start, end)


def entry_weak(stock, context, start, end):
    """WEAK 独立 Entry 接口；S2-V2 完全调用冻结基线条件。"""
    return check_weak_to_strong(stock, context, start, end)








def get_stock_list(context):
    """HIGH/LOW/WEAK独立Entry路由；保持LOW先于HIGH的原互斥顺序。"""
    target_list, target_list2 = prepare_stock_list(context)

    date_now = context.current_dt.strftime("%Y-%m-%d")
    start = date_now + ' 09:15:00'
    end = date_now + ' 09:26:00'

    sbgk_stocks = []
    sbdk_stocks = []
    rzq_stocks = []
    g.signal_tags = {}
    # S5 V4：仅保存当天最终进入真实下单候选的个股研究因子。
    g.s5_signal_entry_factors = {}

    for s in target_list:
        if entry_low(s, context, start, end):
            tag = make_strategy_tag('LOW', s)

            # Stage4 V2：只过滤 LOW_MAIN；LOW_CHINEXT 完全保持V2.0
            if tag == 'LOW_MAIN':
                auction_ratio = s4v2_low_main_auction_ratio(s, context, start, end)
                passed = (auction_ratio is not None and auction_ratio >= 0.020)

                g.s4v2_entry_filter_events.append({
                    'date': context.current_dt,
                    'stock': s,
                    'strategy_tag': tag,
                    'auction_vol_ratio': auction_ratio,
                    'threshold': 0.020,
                    'passed': passed,
                })

                # 原LOW已经命中时，即使Stage4附加过滤失败，也不回落去检查HIGH。
                # 两者开盘区间本身互斥；显式continue用于固定生命周期语义。
                if not passed:
                    print('[S4-V2-ENTRY-FILTER] %s %s LOW_MAIN rejected auction_ratio=%s'
                          % (context.current_dt, s, auction_ratio))
                    continue

                # S5 V4：只在 LOW_MAIN 已经通过原有 S4 Entry 之后采集个股因子。
                # 因此不会让研究层改变候选资格，也避免对大量未入选股票做昂贵查询。
                try:
                    g.s5_signal_entry_factors[s] = _s5_collect_low_main_entry_factors(
                        s, context, start, end, auction_vol_ratio=auction_ratio
                    )
                except Exception as e:
                    print('[S5-V4-LOW-FACTOR-WARN] stock=%s error=%s' % (s, e))

            sbdk_stocks.append(s)
            g.signal_tags[s] = tag
            continue

        if entry_high(s, context, start, end):
            sbgk_stocks.append(s)
            g.signal_tags[s] = make_strategy_tag('HIGH', s)

    for s in target_list2:
        if entry_weak(s, context, start, end):
            rzq_stocks.append(s)
            g.signal_tags[s] = make_strategy_tag('WEAK', s)

    qualified = sbgk_stocks + sbdk_stocks + rzq_stocks
    return qualified, sbgk_stocks, sbdk_stocks, rzq_stocks

def check_low_open(stock, context, start, end):
    history_data = attribute_history(stock, 60, '1d', fields=['close', 'high', 'low', 'money'], skip_paused=True)
    if len(history_data) < 60:
        return False
    close = history_data['close'].iloc[-1]
    high = history_data['high'].max()
    low = history_data['low'].min()
    if high <= low:
        return False
    rp = (close - low) / (high - low)
    money = history_data['money'].iloc[-1]
    if rp > 0.5 or money < 1e8:
        return False

    # V4归因实验：恢复原策略 auction['current'][0] 的语义
    auction = get_first_auction_record(stock, start, end, ['time', 'current'])
    if auction is None:
        return False
    current_ratio = float(auction['current']) / close
    return 0.955 <= current_ratio <= 0.97

def check_high_open(stock, context, start, end):
    """Stage2制度显式化后的HIGH。

    不再用固定1.1反推涨停日前收盘：
        avg_inc = 昨日VWAP / 涨停日前真实收盘 - 1
    阈值仍保持原7%，本阶段不做参数优化。
    """
    prev_days = attribute_history(
        stock, 2, '1d',
        fields=['close', 'volume', 'money'],
        skip_paused=True
    )
    if len(prev_days) < 2:
        return False

    pre_prev_close = prev_days['close'].iloc[-2]
    prev_close = prev_days['close'].iloc[-1]
    prev_volume = prev_days['volume'].iloc[-1]
    prev_money = prev_days['money'].iloc[-1]

    if pre_prev_close <= 0 or prev_close <= 0 or prev_volume <= 0:
        return False

    vwap = prev_money / prev_volume
    avg_inc = vwap / pre_prev_close - 1

    if avg_inc < 0.07 or prev_money < 5.5e8 or prev_money > 20e8:
        return False

    turn = get_valuation(
        stock,
        start_date=context.previous_date,
        end_date=context.previous_date,
        fields=['turnover_ratio', 'market_cap', 'circulating_market_cap']
    )
    if turn.empty or turn['market_cap'].iloc[0] < 70 or turn['circulating_market_cap'].iloc[0] > 520:
        return False

    auction = get_first_auction_record(stock, start, end, ['time', 'volume', 'current'])
    if auction is None:
        return False

    auction_volume = float(auction['volume']) if pd.notna(auction['volume']) else 0
    if auction_volume / prev_volume < 0.03:
        return False

    curr_ratio = float(auction['current']) / prev_close
    if curr_ratio <= 1 or curr_ratio >= 1.06:
        return False

    return check_left_pressure(stock)

def check_weak_to_strong(stock, context, start, end):
    """Stage2制度显式化后的WEAK。

    竞价强弱直接使用真实昨收：
        curr_ratio = auction / prev_close
    不再使用 high_limit / 1.1。
    其余阈值保持不变。
    """
    prev = attribute_history(
        stock, 4, '1d',
        fields=['open', 'close', 'volume', 'money'],
        skip_paused=True
    )
    if len(prev) < 4:
        return False

    first_close = prev['close'].iloc[0]
    prev_open = prev['open'].iloc[-1]
    prev_close = prev['close'].iloc[-1]
    prev_volume = prev['volume'].iloc[-1]
    prev_money = prev['money'].iloc[-1]

    if first_close <= 0 or prev_open <= 0 or prev_close <= 0 or prev_volume <= 0:
        return False

    inc = (prev_close - first_close) / first_close
    if inc > 0.28:
        return False

    oc_ratio = (prev_close - prev_open) / prev_open
    if oc_ratio < -0.05:
        return False

    avg_inc = prev_money / prev_volume / prev_close - 1
    if avg_inc < -0.04 or prev_money < 3e8 or prev_money > 19e8:
        return False

    turn = get_valuation(
        stock,
        start_date=context.previous_date,
        end_date=context.previous_date,
        fields=['turnover_ratio', 'market_cap', 'circulating_market_cap']
    )
    if turn.empty or turn['market_cap'].iloc[0] < 70 or turn['circulating_market_cap'].iloc[0] > 520:
        return False

    auction = get_first_auction_record(stock, start, end, ['time', 'volume', 'current'])
    if auction is None:
        return False

    auction_volume = float(auction['volume']) if pd.notna(auction['volume']) else 0
    if auction_volume / prev_volume < 0.03:
        return False

    curr_ratio = float(auction['current']) / prev_close
    if curr_ratio <= 0.98 or curr_ratio >= 1.09:
        return False

    return check_left_pressure(stock)

def check_left_pressure(stock):
    hst = attribute_history(stock, 101, '1d', fields=['high', 'volume'], skip_paused=True)
    prev_high = hst['high'].iloc[-1]
    zyts_0 = 100
    for i in range(3, len(hst)):        # 从倒数第3天开始查找（原版行为）
        if hst['high'].iloc[-i] >= prev_high:
            zyts_0 = i - 1
            break
    zyts = zyts_0 + 5
    vol_data = hst['volume'][-zyts:]    # 不检查越界，与原版一致
    if len(vol_data) < 2 or vol_data[-1] <= max(vol_data[:-1]) * 0.9:
        return False
    return True

# ---------------------------- 股票池准备（原样）----------------------------
def prepare_stock_list(context):
    today = context.current_dt.date()
    yesterday = context.previous_date
    initial = set_stockpool(context)
    initial = filter_kcbj_stock(initial)
    initial = filter_st_paused_stock(initial, today)
    initial = filter_new_stock(initial, today)

    if not g.n_days_limit_up_list:
        days = get_trade_days(end_date=yesterday, count=3)[:-1]
        for day in days:
            g.n_days_limit_up_list.append(get_hl_stock(initial, day, 1))

    hl_list = get_hl_stock(initial, yesterday, 1)
    g.n_days_limit_up_list.append(hl_list)
    hl1_set = set(g.n_days_limit_up_list[-2])
    hl_list = [s for s in hl_list if s not in hl1_set]

    hl_list2 = get_ever_hl_stock2(initial, yesterday)
    hl_list2 = [s for s in hl_list2 if s not in hl1_set]

    g.n_days_limit_up_list.pop(0)
    return hl_list, hl_list2

def set_stockpool(context):
    return get_all_securities('stock', context.previous_date).index.tolist()

def get_hl_stock(stock_list, date1, days=1):
    if not stock_list:
        return []
    h_s = get_price(stock_list, end_date=date1, frequency='daily',
                    fields=['close', 'high_limit', 'paused'],
                    count=days, panel=False, fill_paused=False, skip_paused=False
                    ).query('close==high_limit and paused==0').groupby('code').size()
    return h_s.index.tolist()

def get_ever_hl_stock2(stock_list, date1):
    if not stock_list:
        return []
    h_s = get_price(stock_list, end_date=date1, frequency='daily',
                    fields=['close', 'high', 'high_limit', 'paused'],
                    count=1, panel=False, fill_paused=False, skip_paused=False
                    ).query('close!=high_limit and high==high_limit and paused==0').groupby('code').size()
    return h_s.index.tolist()

def filter_new_stock(initial_list, date, days=50):
    return [s for s in initial_list if get_security_info(s).start_date < date - timedelta(days=days)]

def filter_st_paused_stock(initial_list, date):
    cur = get_current_data()
    return [s for s in initial_list if not (cur[s].is_st or cur[s].paused or '退' in cur[s].name)]

def filter_kcbj_stock(initial_list):
    return [s for s in initial_list if s[0] != '4' and s[0] != '8' and s[:2] != '68']


# =============================================================================
# ==============================================================================
# S7 Final - REAL Gate Integration
# ==============================================================================



def s7_apply_real_gates(context, qualified, sbgk_stocks, sbdk_stocks, rzq_stocks):
    """Apply frozen S6 gates to REAL candidates. No threshold optimization here."""
    allowed = []
    allowed_set = set()
    today = context.current_dt

    for stock in qualified:
        tag = g.signal_tags.get(stock, 'UNKNOWN')
        try:
            cls = _s6_classify(stock, tag, context)
            if tag == 'WEAK_MAIN':
                # OOS conclusion: remove the hard WEAK_MAIN market Gate.
                # Keep classification only for diagnostics.
                allow = True
                cls = dict(cls)
                cls['reason'] = 'WEAK_MAIN_GATE_REMOVED_OOS_FAIL | shadow=' + str(cls.get('reason'))
                cls['state'] = 'GATE_REMOVED_ALLOW'
            else:
                allow = bool(_s6_gate_allow(tag, cls))
        except Exception as e:
            # Missing/exception policy: preserve baseline rather than reject an unclassifiable trade.
            cls = {'state': 'UNKNOWN_ALLOW', 'known': False, 'reason': 'S7_CLASSIFY_EXCEPTION_ALLOW:%s' % e}
            allow = True

        row = {
            'date': today,
            'stock': stock,
            'strategy_tag': tag,
            's7_state': cls.get('state'),
            'classification_known': bool(cls.get('known')),
            'reason': cls.get('reason'),
            'real_allow': allow,
        }
        g.s7_gate_decisions.append(row)

        if allow:
            allowed.append(stock)
            allowed_set.add(stock)
        else:
            print('[S7-GATE-REJECT] %s stock=%s tag=%s state=%s reason=%s' % (
                today, stock, tag, cls.get('state'), cls.get('reason')))

    raw_n = len(qualified)
    kept_n = len(allowed)
    if raw_n or kept_n:
        print('[S7-GATE] %s raw=%d allow=%d reject=%d' % (
            today, raw_n, kept_n, raw_n - kept_n))

    # Preserve original display/branch routing lists, restricted to actually executable candidates.
    return (
        allowed,
        [x for x in sbgk_stocks if x in allowed_set],
        [x for x in sbdk_stocks if x in allowed_set],
        [x for x in rzq_stocks if x in allowed_set],
    )







# ==============================================================================
# FINAL / OOS Minimal Research Output
# ==============================================================================

def _final_export_path(name):
    """Research环境优先子目录；不支持时降级为根目录前缀。"""
    if getattr(g, 'final_export_mode', None) is None:
        try:
            write_file(g.final_export_dir + '/.init',
                       'STRUCTURAL_OPTIMIZED_STRATEGY_V1_0_FINAL_OOS',
                       append=False)
            g.final_export_mode = 'folder'
        except Exception:
            g.final_export_mode = 'prefix'
    if g.final_export_mode == 'folder':
        return g.final_export_dir + '/' + name
    return g.final_export_prefix + name


def _final_closed_lots_df():
    """仅保留稳健性/OOS分析必需的逐笔字段。"""
    cols = [
        'lot_id', 'stock', 'strategy_tag',
        'entry_time', 'exit_time',
        'entry_price', 'exit_price', 'qty',
        'return_pct', 'realized_pnl_gross',
        'holding_trade_days', 'exit_reason'
    ]
    rows = []
    for x in getattr(g, 'closed_lots', []):
        row = {k: x.get(k) for k in cols}
        try:
            row['entry_notional'] = float(x.get('entry_price', 0.0) or 0.0) * float(x.get('qty', 0.0) or 0.0)
        except Exception:
            row['entry_notional'] = None
        rows.append(row)
    out_cols = cols + ['entry_notional']
    return pd.DataFrame(rows, columns=out_cols)


def _final_gate_decisions_df():
    cols = [
        'date', 'stock', 'strategy_tag', 's7_state',
        'classification_known', 'reason', 'real_allow'
    ]
    rows = []
    for x in getattr(g, 's7_gate_decisions', []):
        rows.append({k: x.get(k) for k in cols})
    return pd.DataFrame(rows, columns=cols)


def _final_gate_summary_df():
    """不用 named aggregation，兼容聚宽旧 pandas。"""
    buckets = {}
    for x in getattr(g, 's7_gate_decisions', []):
        key = (
            str(x.get('strategy_tag', 'UNKNOWN')),
            str(x.get('s7_state', 'UNKNOWN')),
            bool(x.get('real_allow', False))
        )
        buckets[key] = buckets.get(key, 0) + 1

    rows = []
    for (tag, state, allow), count in sorted(buckets.items()):
        rows.append({
            'strategy_tag': tag,
            's7_state': state,
            'real_allow': allow,
            'count': count,
        })
    return pd.DataFrame(rows, columns=['strategy_tag', 's7_state', 'real_allow', 'count'])


def _final_branch_summary_df():
    """逐分支最小交易统计；更细时间拆分由 daily_nav / closed_lots 离线完成。"""
    df = _final_closed_lots_df()
    cols = [
        'strategy_tag', 'closed_lots', 'wins', 'losses', 'win_rate',
        'avg_return_pct', 'median_return_pct', 'gross_realized_pnl',
        'avg_holding_trade_days'
    ]
    if len(df) == 0:
        return pd.DataFrame(columns=cols)

    rows = []
    tags = sorted(set(str(x) for x in df['strategy_tag'].dropna().tolist()))
    for tag in tags:
        sub = df[df['strategy_tag'].astype(str) == tag]
        rets = pd.to_numeric(sub['return_pct'], errors='coerce').dropna()
        pnls = pd.to_numeric(sub['realized_pnl_gross'], errors='coerce').dropna()
        holds = pd.to_numeric(sub['holding_trade_days'], errors='coerce').dropna()

        wins = int((rets > 0).sum()) if len(rets) else 0
        losses = int((rets <= 0).sum()) if len(rets) else 0
        rows.append({
            'strategy_tag': tag,
            'closed_lots': int(len(sub)),
            'wins': wins,
            'losses': losses,
            'win_rate': float(wins) / len(rets) if len(rets) else None,
            'avg_return_pct': float(rets.mean()) if len(rets) else None,
            'median_return_pct': float(rets.median()) if len(rets) else None,
            'gross_realized_pnl': float(pnls.sum()) if len(pnls) else 0.0,
            'avg_holding_trade_days': float(holds.mean()) if len(holds) else None,
        })
    return pd.DataFrame(rows, columns=cols)


def _final_config_df():
    rows = [
        {'category': 'VERSION', 'key': 'strategy',
         'value': 'STRUCTURAL_STRATEGY_V1_2_FROZEN_FINAL'},

        {'category': 'LIFECYCLE', 'key': 'HIGH_1125',
         'value': 'profit_non_limit_sell_else_dynamic_ma5'},
        {'category': 'LIFECYCLE', 'key': 'HIGH_1450',
         'value': 'profit_non_limit_sell_else_dynamic_ma5'},

        {'category': 'LIFECYCLE', 'key': 'LOW_WEAK_1125',
         'value': 'limit_up_hold; non_limit_profit_sell; non_limit_nonprofit_wait_1425'},
        {'category': 'LIFECYCLE', 'key': 'LOW_WEAK_1425',
         'value': 'limit_up_hold_overnight; non_limit_sell_unconditional'},

        {'category': 'REMOVED_RULE', 'key': 'LOW_MAIN_T1_1120_RETURN_PCT_LE_-2',
         'value': 'REMOVED_V1_1'},
        {'category': 'REMOVED_RULE', 'key': 'WEAK_MAIN_T1_1445_RETURN_PCT_LE_0',
         'value': 'REMOVED_V1_1'},
        {'category': 'REMOVED_RULE', 'key': 'LOW_WEAK_DYNAMIC_MA5',
         'value': 'REMOVED_V1_1'},
        {'category': 'REMOVED_GATE', 'key': 'WEAK_MAIN_MARKET_GATE',
         'value': 'REMOVED_OOS_FAIL'},

        {'category': 'BRANCH_DISABLED', 'key': 'WEAK_MAIN',
         'value': 'DISABLED_V1_2_NO_WEAK'},
        {'category': 'BRANCH_DISABLED', 'key': 'WEAK_CHINEXT',
         'value': 'DISABLED_V1_2_NO_WEAK'},

        {'category': 'S7_GATE', 'key': 'LOW_TREND_WEAK_THRESHOLD',
         'value': S6_LOW_TREND_WEAK_THRESHOLD},
        {'category': 'S7_GATE', 'key': 'LOW_HEAT_LOW_THRESHOLD',
         'value': S6_LOW_HEAT_LOW_THRESHOLD},
        {'category': 'S7_GATE', 'key': 'LOW_STOCK_RET10_HIGH',
         'value': S6_LOW_STOCK_RET10_HIGH},
        {'category': 'S7_GATE', 'key': 'LOW_DISTANCE60_HIGH',
         'value': S6_LOW_DISTANCE60_HIGH},
        {'category': 'S7_GATE', 'key': 'HIGH_MAIN_BREADTH_HIGH',
         'value': S6_HIGH_MAIN_BREADTH_HIGH},
        {'category': 'S7_GATE', 'key': 'HIGH_CHINEXT_TOUCHED_NOT_LOW',
         'value': S6_HC_TOUCHED_NOT_LOW},
        {'category': 'S7_GATE', 'key': 'HIGH_CHINEXT_ADVANCE_NOT_LOW',
         'value': S6_HC_ADVANCE_NOT_LOW},
    ]

    for tag, weight in S8_BRANCH_WEIGHTS.items():
        rows.append({'category': 'S8_WEIGHT', 'key': tag, 'value': weight})

    return pd.DataFrame(rows, columns=['category', 'key', 'value'])


def _lifecycle_events_df():
    cols = [
        'date', 'stock', 'strategy_tag', 'lot_id', 'entry_date',
        'holding_trade_days', 'checkpoint',
        'last_price', 'high_limit', 'is_limit_up',
        'return_pct_now', 'action', 'reason'
    ]
    rows = getattr(g, 'low_weak_lifecycle_events', [])
    return pd.DataFrame(rows, columns=cols)


def _lifecycle_summary_df():
    df = _lifecycle_events_df()
    cols = [
        'strategy_tag', 'checkpoint', 'action', 'reason',
        'count', 'avg_return_pct_now', 'median_return_pct_now'
    ]
    if len(df) == 0:
        return pd.DataFrame(columns=cols)

    buckets = {}
    for _, r in df.iterrows():
        key = (
            str(r.get('strategy_tag')),
            str(r.get('checkpoint')),
            str(r.get('action')),
            str(r.get('reason')),
        )
        buckets.setdefault(key, []).append(r.get('return_pct_now'))

    rows = []
    for key, vals in sorted(buckets.items()):
        clean = []
        for v in vals:
            try:
                fv = float(v)
                if not pd.isna(fv):
                    clean.append(fv)
            except Exception:
                pass
        rows.append({
            'strategy_tag': key[0],
            'checkpoint': key[1],
            'action': key[2],
            'reason': key[3],
            'count': len(vals),
            'avg_return_pct_now': sum(clean) / len(clean) if clean else None,
            'median_return_pct_now': float(pd.Series(clean).median()) if clean else None,
        })

    return pd.DataFrame(rows, columns=cols)


def final_export_research(context):
    """
    每日收盘覆盖落盘。
    只保留能够回答 OOS/过拟合问题的最小数据集。
    """
    try:
        total_value = float(context.portfolio.total_value or 0.0)
        available_cash = float(context.portfolio.available_cash or 0.0)

        # 使用首日净值作为 daily_nav 基准；避免依赖控制台日志。
        if getattr(g, 'final_daily_nav', None) is None:
            g.final_daily_nav = []

        if g.final_daily_nav:
            prev = float(g.final_daily_nav[-1].get('total_value', total_value) or total_value)
            daily_ret = total_value / prev - 1.0 if prev else 0.0
        else:
            initial = float(getattr(g, 'final_initial_value', total_value) or total_value)
            daily_ret = total_value / initial - 1.0 if initial else 0.0

        nav_row = {
            'date': context.current_dt.date(),
            'total_value': total_value,
            'available_cash': available_cash,
            'position_count': len(context.portfolio.positions),
            'daily_return': daily_ret,
        }

        # 热更新/重复回调时，同一日期覆盖而非重复追加。
        if g.final_daily_nav and g.final_daily_nav[-1].get('date') == nav_row['date']:
            g.final_daily_nav[-1] = nav_row
        else:
            g.final_daily_nav.append(nav_row)

        daily_nav = pd.DataFrame(
            g.final_daily_nav,
            columns=['date', 'total_value', 'available_cash', 'position_count', 'daily_return']
        )
        closed = _final_closed_lots_df()
        gate_decisions = _final_gate_decisions_df()
        gate_summary = _final_gate_summary_df()
        branch_summary = _final_branch_summary_df()
        config = _final_config_df()
        lifecycle_events = _lifecycle_events_df()
        lifecycle_summary = _lifecycle_summary_df()

        initial_value = float(getattr(g, 'final_initial_value', 0.0) or 0.0)
        cumulative_return = total_value / initial_value - 1.0 if initial_value else None

        status = pd.DataFrame([{
            'date': context.current_dt,
            'version': getattr(g, 'final_version', 'STRUCTURAL_STRATEGY_V1_2_FROZEN_FINAL'),
            'initial_value': initial_value,
            'total_value': total_value,
            'cumulative_return': cumulative_return,
            'available_cash': available_cash,
            'position_count': len(context.portfolio.positions),
            'closed_lots': len(closed),
            'gate_decisions': len(gate_decisions),
            'gate_allowed': int(gate_decisions['real_allow'].astype(bool).sum()) if len(gate_decisions) else 0,
            'gate_rejected': int((~gate_decisions['real_allow'].astype(bool)).sum()) if len(gate_decisions) else 0,
            'lifecycle_events': len(lifecycle_events),
            'lw_1125_profit_sells': int((lifecycle_events['reason'] == 'LW_1125_PROFIT_NON_LIMIT').sum()) if len(lifecycle_events) else 0,
            'lw_1425_non_limit_exits': int((lifecycle_events['reason'] == 'LW_1425_NON_LIMIT_EXIT').sum()) if len(lifecycle_events) else 0,
            'lw_limit_up_holds': int(lifecycle_events['is_limit_up'].astype(bool).sum()) if len(lifecycle_events) else 0,
            'weak_branch_disabled': True,
            'closed_weak_lots': int(
                closed['strategy_tag'].isin(['WEAK_MAIN', 'WEAK_CHINEXT']).sum()
            ) if len(closed) else 0,
        }])

        readme = (
            'Structural Strategy V1.2 - FROZEN FINAL\n'
            'WEAK_MAIN and WEAK_CHINEXT are completely disabled before Gate/allocation.\n'
            'HIGH lifecycle remains unchanged from V1.1.\n'
            'LOW lifecycle remains unchanged from V1.1.\n'
            'No threshold tuning and no other structural changes are introduced.\n\n'
            'Recommended first test: 2025-01-01 ~ 2026-09-01.\n'
        )

        out = _final_export_path
        write_file(out('README.txt'), readme, append=False)
        write_file(out('frozen_config.csv'), config.to_csv(index=False), append=False)
        write_file(out('daily_nav.csv'), daily_nav.to_csv(index=False), append=False)
        write_file(out('closed_lots.csv'), closed.to_csv(index=False), append=False)
        write_file(out('branch_summary.csv'), branch_summary.to_csv(index=False), append=False)
        write_file(out('gate_decisions.csv'), gate_decisions.to_csv(index=False), append=False)
        write_file(out('gate_summary.csv'), gate_summary.to_csv(index=False), append=False)
        write_file(out('low_weak_lifecycle_events.csv'), lifecycle_events.to_csv(index=False), append=False)
        write_file(out('lifecycle_summary.csv'), lifecycle_summary.to_csv(index=False), append=False)
        write_file(out('run_status.csv'), status.to_csv(index=False), append=False)

        health = pd.DataFrame([{
            'date': context.current_dt,
            'ok': True,
            'message': 'final_checkpoint_ok',
            'daily_nav_rows': len(daily_nav),
            'closed_lots': len(closed),
            'gate_decisions': len(gate_decisions),
            'lifecycle_events': len(lifecycle_events),
        }])
        write_file(out('export_health.csv'), health.to_csv(index=False), append=False)
        return True

    except Exception as e:
        try:
            health = pd.DataFrame([{
                'date': context.current_dt,
                'ok': False,
                'message': repr(e),
            }])
            write_file(_final_export_path('export_health.csv'),
                       health.to_csv(index=False), append=False)
        except Exception:
            pass
        return False


# ==============================================================================
# S8 Final - Stability-Aware Allocation Research Output
# ==============================================================================











# S6 V3 - Cost-Aware Rule Ablation Shadow Engine
# =============================================================================
# 定位：纯研究旁路，不参与任何真实 Entry / Exit / Risk / Position Sizing。
#
# 本版不再挖新因子、不再优化阈值，只回答两个问题：
# 1) S5/S6 已冻结的四个 Gate，在计入保守交易成本后是否仍有账户级价值；
# 2) 逐个拿掉某个 Gate 后，最终 NAV / MDD / 2026 前推表现损失多少。
#
# 同时运行 6 个影子组合：
#   BASELINE_ALL            : 不启用任何 S6 Gate，完整候选集基准影子账户；
#   FULL_STRICT             : 四个主要 Gate 全部启用；
#   FULL_MINUS_LOW          : 仅拿掉 LOW_MAIN Gate；
#   FULL_MINUS_HIGH_MAIN    : 仅拿掉 HIGH_MAIN Gate（等价于上一版 HYBRID 的核心结构）；
#   FULL_MINUS_WEAK         : 仅拿掉 WEAK_MAIN Gate；
#   FULL_MINUS_HIGH_CHINEXT : 仅拿掉 HIGH_CHINEXT Gate。
#
# 成本模型（可配置、非参数优化）：
#   - 双边佣金 0.03%，单笔最低 5 元；
#   - 卖出印花税 0.05%；
#   - 双边过户费 0.001%；
#   - 双边滑点 5 bps。
# 这是研究用的保守默认值；不同券商/成交条件可不同。
# =============================================================================

S6_EXPORT_DIR = 'strategy_s6_rule_ablation_v3'
S6_EXPORT_PREFIX = 'strategy_s6_rule_ablation_v3__'

# ---- Frozen S5 candidate thresholds; DO NOT optimize in S6-V3 ----
S6_LOW_TREND_WEAK_THRESHOLD = -0.009053065956339234
S6_LOW_HEAT_LOW_THRESHOLD = 0.0069591652776029595
S6_LOW_STOCK_RET10_HIGH = 0.09255705602513398
S6_LOW_DISTANCE60_HIGH = -0.19610064664276514
S6_HIGH_MAIN_BREADTH_HIGH = 0.0042554739215828
S6_WEAK_LIMIT_DOWN_HIGH = 12.0
S6_WEAK_TOUCHED_HIGH = 113.0
S6_HC_TOUCHED_NOT_LOW = 103.33333333333333
S6_HC_ADVANCE_NOT_LOW = 0.5104943476458254

# ---- Conservative transaction-cost assumptions ----
S6_COMMISSION_RATE = 0.0003
S6_MIN_COMMISSION = 5.0
S6_STAMP_TAX_SELL_RATE = 0.0005
S6_TRANSFER_FEE_RATE = 0.00001
S6_SLIPPAGE_BPS_BUY = 5.0
S6_SLIPPAGE_BPS_SELL = 5.0

S6_PORTFOLIO_MODES = (
    'BASELINE_ALL',
    'FULL_STRICT',
    'FULL_MINUS_LOW',
    'FULL_MINUS_HIGH_MAIN',
    'FULL_MINUS_WEAK',
    'FULL_MINUS_HIGH_CHINEXT',
)




def _s6_num(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None




def _s6_today_env(context):
    return getattr(g, 'regime_daily', {}).get(context.current_dt.date(), {}) or {}


def _s6_three_or(a, b):
    """Three-valued OR: True dominates; both False => False; otherwise unknown(None)."""
    if a is True or b is True:
        return True
    if a is False and b is False:
        return False
    return None


def _s6_three_and(a, b):
    """Three-valued AND: False dominates; both True => True; otherwise unknown(None)."""
    if a is False or b is False:
        return False
    if a is True and b is True:
        return True
    return None


def _s6_classify(stock, tag, context):
    """Frozen structural classification. Never changes the real candidate list."""
    env = _s6_today_env(context)
    reasons = []
    state = 'UNCHANGED'
    known = True

    if tag == 'LOW_MAIN':
        slope = _s6_num(env.get('index_ma20_slope_5d'))
        heat = _s6_num(env.get('prior_limitup_nextday_return_mean'))
        sf = getattr(g, 's5_signal_entry_factors', {}).get(stock, {}) or {}
        ret10 = _s6_num(sf.get('stock_return_10d'))
        dist60 = _s6_num(sf.get('distance_to_60d_high'))

        market_bad = None if slope is None or heat is None else bool(
            slope > S6_LOW_TREND_WEAK_THRESHOLD and heat > S6_LOW_HEAT_LOW_THRESHOLD)
        stock_bad = None if ret10 is None or dist60 is None else bool(
            ret10 >= S6_LOW_STOCK_RET10_HIGH and dist60 >= S6_LOW_DISTANCE60_HIGH)
        risk = _s6_three_or(market_bad, stock_bad)
        known = risk is not None
        if risk is True:
            state = 'RISK'
            if market_bad is True:
                reasons.append('MARKET_BAD_NOT_WEAK_HIGH_HEAT')
            if stock_bad is True:
                reasons.append('STOCK_OVERHEAT_RET10_HIGH_NEAR_60D_HIGH')
        elif risk is False:
            state = 'CLEAN'
            reasons.append('LOW_MAIN_CLEAN')
        else:
            state = 'UNKNOWN_ALLOW'
            reasons.append('LOW_MAIN_DATA_UNKNOWN_ALLOW')

    elif tag == 'HIGH_MAIN':
        breadth = _s6_num(env.get('market_median_return_1d'))
        known = breadth is not None
        if not known:
            state = 'UNKNOWN_ALLOW'
            reasons.append('BREADTH_UNKNOWN_ALLOW')
        else:
            favorable = breadth > S6_HIGH_MAIN_BREADTH_HIGH
            state = 'FAVORABLE' if favorable else 'OTHER'
            reasons.append('BREADTH_STRONG' if favorable else 'BREADTH_OTHER')

    elif tag == 'WEAK_MAIN':
        down = _s6_num(env.get('limit_down_count'))
        touched = _s6_num(env.get('touched_limit_up_count'))
        a = None if down is None else bool(down > S6_WEAK_LIMIT_DOWN_HIGH)
        b = None if touched is None else bool(touched > S6_WEAK_TOUCHED_HIGH)
        favorable = _s6_three_or(a, b)
        known = favorable is not None
        if favorable is True:
            state = 'FAVORABLE'
            if a is True:
                reasons.append('STRESS_HIGH')
            if b is True:
                reasons.append('ACTIVITY_HIGH')
        elif favorable is False:
            state = 'QUIET'
            reasons.append('QUIET_NON_EXTREME')
        else:
            state = 'UNKNOWN_ALLOW'
            reasons.append('WEAK_ENV_UNKNOWN_ALLOW')

    elif tag == 'HIGH_CHINEXT':
        touched = _s6_num(env.get('touched_limit_up_count'))
        advance = _s6_num(env.get('market_advance_ratio'))
        a = None if touched is None else bool(touched > S6_HC_TOUCHED_NOT_LOW)
        b = None if advance is None else bool(advance > S6_HC_ADVANCE_NOT_LOW)
        favorable = _s6_three_and(a, b)
        known = favorable is not None
        if favorable is True:
            state = 'FAVORABLE'
            reasons.append('ACTIVITY_AND_BREADTH_OK')
        elif favorable is False:
            state = 'OTHER'
            reasons.append('ACTIVITY_OR_BREADTH_LOW')
        else:
            state = 'UNKNOWN_ALLOW'
            reasons.append('HC_ENV_UNKNOWN_ALLOW')

    else:
        state = 'UNCHANGED'
        known = True
        reasons.append('SAMPLE_INSUFFICIENT_KEEP_BASELINE')

    return {'state': state, 'known': bool(known), 'reason': '|'.join(reasons)}


def _s6_gate_allow(tag, cls):
    if cls.get('state') == 'UNKNOWN_ALLOW':
        return True
    if tag == 'LOW_MAIN':
        return cls.get('state') == 'CLEAN'
    if tag == 'HIGH_MAIN':
        return cls.get('state') == 'FAVORABLE'
    if tag == 'WEAK_MAIN':
        # V1.1: WEAK_MAIN hard Gate removed after OOS failure.
        return True
    if tag == 'HIGH_CHINEXT':
        return cls.get('state') == 'FAVORABLE'
    return True






















































# =============================================================================
# =============================================================================
# S5 V4 - Tail Risk Collector + Frozen Regime Shadow
# =============================================================================
# 定位：纯研究旁路，不参与任何 Entry / Exit / Risk / Position Sizing。
#
# 为什么做这一版：
# 1) S5 V4 已经确认 LOW_MAIN 的市场环境不是简单“牛熊/冷热”，而存在
#    Trend × Heat 的交互结构；
# 2) 已确认“Not Weak Trend + High Heat”是宽阈值平台下都较差的环境候选；
# 3) 同时仍存在 Weak Trend + High Heat 中少数极端个股尾部失败，说明
#    市场 Regime 不能替代个股 Tail Risk 研究；
# 4) 上一轮 20 个月回测耗时超过两小时，因此本版优先减少研究层行情调用。
#
# 本版一次性完成：
# A. 保留 V3 全部市场原始因子；
# B. 将全市场生态采集由“12日全市场 + Python逐股”优化为：
#       3日全市场批量统计 + 仅对 T-1 涨停股补取10日历史；
# C. 仅对已通过 S4 Entry 的 LOW_MAIN 采集个股入场原始因子；
# D. 记录冻结 Regime Candidate 的 SHADOW 标签，仅做旁路验证；
# E. 输出 LOW_MAIN Tail Failure / 资本贡献 / Shadow Regime 报告。
# =============================================================================

S5_FACTOR_DIR = 'strategy_s5_tail_risk_v4'
S5_FACTOR_PREFIX = 'strategy_s5_tail_risk_v4__'

# -----------------------------------------------------------------------------
# 冻结候选阈值（来源：上一轮 2025 TRAIN 的 1/3 分位，仅用于影子标签）
# -----------------------------------------------------------------------------
# 注意：这些数值绝不进入 get_stock_list 的通过/拒绝判断。
# 下一轮全量回测的作用是验证候选结构、采集新因子，而不是把它们偷偷变成规则。
S5_LOW_TREND_WEAK_THRESHOLD = -0.009053065956339234
S5_LOW_HEAT_LOW_THRESHOLD = 0.0069591652776029595

S5_ENTRY_FACTOR_COLUMNS = [
    'index_return_5d',
    'index_return_20d',
    'index_return_60d',
    'index_price_ma20_gap',
    'index_price_ma60_gap',
    'index_ma20_ma60_gap',
    'index_ma20_slope_5d',
    'index_volatility_20d',
    'limit_up_count',
    'limit_down_count',
    'limit_up_down_ratio',
    'touched_limit_up_count',
    'broken_board_count',
    'broken_board_rate',
    'first_board_count',
    'max_consecutive_limit_up_10d',
    'prior_limitup_nextday_return_mean',
    'prior_limitup_nextday_return_median',
    'market_advance_ratio',
    'market_median_return_1d',
    'market_total_money_billion',
]

# LOW_MAIN 个股级入场变量。全部必须在下单前已经可知。
S5_LOW_STOCK_FACTOR_COLUMNS = [
    'relative_position_60',
    'auction_open_return',
    'auction_vol_ratio',
    'prev_money',
    'market_cap',
    'circulating_market_cap',
    'turnover_ratio',
    'stock_return_5d',
    'stock_return_10d',
    'stock_return_20d',
    'distance_to_20d_high',
    'distance_to_60d_high',
    'prev_day_amplitude',
    'prev_day_close_position',
    'prev_day_open_close_return',
    'prev_day_gap_return',
    'prev_volume_ratio_5d',
    'prev_money_ratio_5d',
]


def _s5_prepare_export_path():
    """复用 S4 write_file 落盘模式；目录失败自动退化为根目录前缀。"""
    if getattr(g, 's5_factor_export_mode', None) is None:
        try:
            write_file(S5_FACTOR_DIR + '/.init', 'S5_TAIL_RISK_COLLECTOR_V4', append=False)
            g.s5_factor_export_mode = 'folder'
            print('[S5-V4] Research输出: %s/' % S5_FACTOR_DIR)
        except Exception as e:
            g.s5_factor_export_mode = 'prefix'
            print('[S5-V4] 子目录不可用，降级Research根目录前缀: %s' % e)

    if g.s5_factor_export_mode == 'folder':
        return lambda name: S5_FACTOR_DIR + '/' + name
    return lambda name: S5_FACTOR_PREFIX + name


def init_regime_analysis():
    _s5_prepare_export_path()
    if not hasattr(g, 'regime_daily'):
        g.regime_daily = {}
    if not hasattr(g, 'regime_trade_records'):
        g.regime_trade_records = []
    if not hasattr(g, 's5_signal_entry_factors'):
        g.s5_signal_entry_factors = {}
    if not hasattr(g, 's5_perf_records'):
        g.s5_perf_records = []


def _s5_safe_float(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def _s5_pct(a, b):
    a = _s5_safe_float(a)
    b = _s5_safe_float(b)
    if a is None or b is None or b == 0:
        return None
    return a / b - 1.0


def _s5_index_raw_factors(context):
    result = {
        'index_close': None,
        'index_ma20': None,
        'index_ma60': None,
        'index_return_5d': None,
        'index_return_20d': None,
        'index_return_60d': None,
        'index_price_ma20_gap': None,
        'index_price_ma60_gap': None,
        'index_ma20_ma60_gap': None,
        'index_ma20_slope_5d': None,
        'index_volatility_20d': None,
    }
    try:
        h = attribute_history('000300.XSHG', 80, '1d', fields=['close'], skip_paused=True)
        if h is None or len(h) < 61:
            return result
        close = pd.to_numeric(h['close'], errors='coerce').dropna()
        if len(close) < 61:
            return result

        last = float(close.iloc[-1])
        ma20 = float(close.iloc[-20:].mean())
        ma60 = float(close.iloc[-60:].mean())
        result['index_close'] = last
        result['index_ma20'] = ma20
        result['index_ma60'] = ma60
        result['index_return_5d'] = _s5_pct(last, close.iloc[-6])
        result['index_return_20d'] = _s5_pct(last, close.iloc[-21])
        result['index_return_60d'] = _s5_pct(last, close.iloc[-61])
        result['index_price_ma20_gap'] = _s5_pct(last, ma20)
        result['index_price_ma60_gap'] = _s5_pct(last, ma60)
        result['index_ma20_ma60_gap'] = _s5_pct(ma20, ma60)
        if len(close) >= 25:
            ma20_5d_ago = float(close.iloc[-25:-5].mean())
            result['index_ma20_slope_5d'] = _s5_pct(ma20, ma20_5d_ago)
        daily_ret = close.pct_change().dropna()
        if len(daily_ret) >= 20:
            result['index_volatility_20d'] = float(
                daily_ret.iloc[-20:].std(ddof=1) * (252 ** 0.5)
            )
    except Exception as e:
        print('[S5-V4-INDEX-WARN] %s' % e)
    return result


def _s5_relevant_universe(context):
    stocks = get_all_securities('stock', context.previous_date).index.tolist()
    return filter_kcbj_stock(stocks)


def _s5_market_raw_factors(context):
    """快速全市场生态采集。

    性能优化核心：
    - V3: 全市场一次取12日，再 Python groupby 对每只股票循环；
    - V4: 全市场只取3日，所有广度/涨跌停/首板/溢价均向量化；
          只有当前 T-1 涨停股（通常几十只）再补取10日计算最高连板。
    """
    result = {
        'limit_up_count': None,
        'limit_down_count': None,
        'limit_up_down_ratio': None,
        'touched_limit_up_count': None,
        'broken_board_count': None,
        'broken_board_rate': None,
        'first_board_count': None,
        'max_consecutive_limit_up_10d': None,
        'prior_limitup_nextday_return_mean': None,
        'prior_limitup_nextday_return_median': None,
        'market_up_count': None,
        'market_down_count': None,
        'market_flat_count': None,
        'market_advance_ratio': None,
        'market_median_return_1d': None,
        'market_total_money_billion': None,
        'market_valid_stock_count': None,
    }

    try:
        stocks = _s5_relevant_universe(context)
        if not stocks:
            return result

        px = get_price(
            stocks,
            end_date=context.previous_date,
            frequency='daily',
            fields=['close', 'high', 'high_limit', 'low_limit', 'paused', 'money'],
            count=3,
            panel=False,
            fill_paused=False,
            skip_paused=False
        )
        if px is None or len(px) == 0:
            return result

        px = px.copy()
        sort_cols = ['code', 'time'] if 'time' in px.columns else ['code']
        px = px.sort_values(sort_cols)
        for c in ['close', 'high', 'high_limit', 'low_limit', 'paused', 'money']:
            if c in px.columns:
                px[c] = pd.to_numeric(px[c], errors='coerce')

        # 只保留未停牌且核心价格字段有效的数据。
        valid = px[(px['paused'].fillna(1) == 0)].dropna(
            subset=['close', 'high', 'high_limit', 'low_limit']
        ).copy()
        if len(valid) == 0:
            return result

        # 最新(T-1)与次新(T-2)行；不再逐股票 Python 循环。
        valid['_rank_rev'] = valid.groupby('code').cumcount(ascending=False)
        t1 = valid[valid['_rank_rev'] == 0].copy()
        t2 = valid[valid['_rank_rev'] == 1].copy()

        is_lu = t1['close'] >= t1['high_limit'] * 0.9999
        is_ld = t1['close'] <= t1['low_limit'] * 1.0001
        touched = t1['high'] >= t1['high_limit'] * 0.9999
        broken = touched & (~is_lu)

        lu_codes = set(t1.loc[is_lu, 'code'].astype(str))
        result['limit_up_count'] = int(is_lu.sum())
        result['limit_down_count'] = int(is_ld.sum())
        result['limit_up_down_ratio'] = float(is_lu.sum()) / max(int(is_ld.sum()), 1)
        result['touched_limit_up_count'] = int(touched.sum())
        result['broken_board_count'] = int(broken.sum())
        result['broken_board_rate'] = (
            float(broken.sum()) / int(touched.sum()) if int(touched.sum()) > 0 else 0.0
        )
        result['market_valid_stock_count'] = int(len(t1))
        result['market_total_money_billion'] = float(
            pd.to_numeric(t1['money'], errors='coerce').fillna(0).sum() / 1e9
        )

        # 对齐 T-1 / T-2，一次性计算广度、昨日涨停溢价和首板。
        a = t1[['code', 'close', 'high_limit']].rename(
            columns={'close': 'close_t1', 'high_limit': 'high_limit_t1'}
        )
        b = t2[['code', 'close', 'high_limit']].rename(
            columns={'close': 'close_t2', 'high_limit': 'high_limit_t2'}
        )
        pair = a.merge(b, on='code', how='inner')
        pair = pair[(pair['close_t2'] > 0) & (pair['high_limit_t2'] > 0)]

        if len(pair):
            pair['ret_t1'] = pair['close_t1'] / pair['close_t2'] - 1.0
            r = pd.to_numeric(pair['ret_t1'], errors='coerce').dropna()
            if len(r):
                up = int((r > 0).sum())
                down = int((r < 0).sum())
                flat = int((r == 0).sum())
                result['market_up_count'] = up
                result['market_down_count'] = down
                result['market_flat_count'] = flat
                result['market_advance_ratio'] = float(up) / max(up + down, 1)
                result['market_median_return_1d'] = float(r.median())

            pair['was_lu_t2'] = pair['close_t2'] >= pair['high_limit_t2'] * 0.9999
            t2_lu_codes = set(pair.loc[pair['was_lu_t2'], 'code'].astype(str))
            result['first_board_count'] = int(len(lu_codes - t2_lu_codes))

            premium = pd.to_numeric(
                pair.loc[pair['was_lu_t2'], 'ret_t1'], errors='coerce'
            ).dropna()
            if len(premium):
                result['prior_limitup_nextday_return_mean'] = float(premium.mean())
                result['prior_limitup_nextday_return_median'] = float(premium.median())
        else:
            result['first_board_count'] = int(len(lu_codes))

        # 最高连板只对 T-1 仍封板的少量股票补10日历史。
        max_height = 0
        if lu_codes:
            lu_list = sorted(lu_codes)
            hist = get_price(
                lu_list,
                end_date=context.previous_date,
                frequency='daily',
                fields=['close', 'high_limit', 'paused'],
                count=10,
                panel=False,
                fill_paused=False,
                skip_paused=False
            )
            if hist is not None and len(hist):
                hist = hist.copy()
                if 'time' in hist.columns:
                    hist = hist.sort_values(['code', 'time'])
                for c in ['close', 'high_limit', 'paused']:
                    hist[c] = pd.to_numeric(hist[c], errors='coerce')
                hist = hist[(hist['paused'].fillna(1) == 0)].dropna(
                    subset=['close', 'high_limit']
                )
                # 这里只有几十只当前涨停股，循环成本很低。
                for _, sub in hist.groupby('code', sort=False):
                    height = 0
                    for row in sub.iloc[::-1].itertuples(index=False):
                        close_v = _s5_safe_float(getattr(row, 'close', None))
                        hl_v = _s5_safe_float(getattr(row, 'high_limit', None))
                        if close_v is not None and hl_v and close_v >= hl_v * 0.9999:
                            height += 1
                        else:
                            break
                    max_height = max(max_height, height)
        result['max_consecutive_limit_up_10d'] = int(max_height)

    except Exception as e:
        print('[S5-V4-MARKET-WARN] %s' % e)
    return result


def calculate_market_regime(context):
    data = {
        'signal_date': context.current_dt.date(),
        'source_market_date': context.previous_date,
    }
    t0 = time.time()
    data.update(_s5_index_raw_factors(context))
    t1 = time.time()
    data.update(_s5_market_raw_factors(context))
    t2 = time.time()
    if hasattr(g, 's5_perf_records'):
        g.s5_perf_records.append({
            'trade_date': context.current_dt.date(),
            'index_factor_seconds': t1 - t0,
            'market_factor_seconds': t2 - t1,
            'total_factor_seconds': t2 - t0,
        })
    return data


def record_market_regime(context):
    init_regime_analysis()
    day = context.current_dt.date()
    if day not in g.regime_daily:
        g.regime_daily[day] = calculate_market_regime(context)


def _s5_low_main_shadow_fields(context):
    """读取当天已经落盘的市场环境，生成冻结候选影子标签。"""
    env = getattr(g, 'regime_daily', {}).get(context.current_dt.date(), {}) or {}
    slope = _s5_safe_float(env.get('index_ma20_slope_5d'))
    heat = _s5_safe_float(env.get('prior_limitup_nextday_return_mean'))

    trend_weak = None if slope is None else bool(slope <= S5_LOW_TREND_WEAK_THRESHOLD)
    heat_low = None if heat is None else bool(heat <= S5_LOW_HEAT_LOW_THRESHOLD)

    cell = 'UNKNOWN'
    bad = None
    if trend_weak is not None and heat_low is not None:
        cell = ('WEAK_TREND' if trend_weak else 'NOT_WEAK_TREND') + '__' + (
            'LOW_HEAT' if heat_low else 'HIGH_HEAT'
        )
        bad = bool((not trend_weak) and (not heat_low))

    return {
        'shadow_trend_threshold': S5_LOW_TREND_WEAK_THRESHOLD,
        'shadow_heat_threshold': S5_LOW_HEAT_LOW_THRESHOLD,
        'shadow_trend_weak': trend_weak,
        'shadow_heat_low': heat_low,
        'shadow_regime_cell': cell,
        'shadow_bad_regime_candidate': bad,
    }


def _s5_collect_low_main_entry_factors(stock, context, start, end, auction_vol_ratio=None):
    """仅对已通过 S4 LOW_MAIN Entry 的股票采集个股入场因子。

    所有字段均只使用 T-1 及更早日线、T日竞价或 T-1 valuation，
    不读取 T 日收盘后信息。
    """
    result = {k: None for k in S5_LOW_STOCK_FACTOR_COLUMNS}
    result.update(_s5_low_main_shadow_fields(context))
    result['factor_capture_time'] = context.current_dt

    h = attribute_history(
        stock, 65, '1d',
        fields=['open', 'close', 'high', 'low', 'volume', 'money'],
        skip_paused=True
    )
    if h is not None and len(h):
        h = h.copy()
        for c in ['open', 'close', 'high', 'low', 'volume', 'money']:
            h[c] = pd.to_numeric(h[c], errors='coerce')
        h = h.dropna(subset=['close', 'high', 'low'])

        if len(h) >= 2:
            prev = h.iloc[-1]
            prev2 = h.iloc[-2]
            prev_close = _s5_safe_float(prev['close'])
            prev2_close = _s5_safe_float(prev2['close'])
            prev_high = _s5_safe_float(prev['high'])
            prev_low = _s5_safe_float(prev['low'])
            prev_open = _s5_safe_float(prev['open'])

            result['prev_money'] = _s5_safe_float(prev['money'])

            if prev_close is not None and prev_high is not None and prev_low is not None:
                denom = prev_high - prev_low
                if denom > 0:
                    result['prev_day_close_position'] = (prev_close - prev_low) / denom
                if prev2_close and prev2_close > 0:
                    result['prev_day_amplitude'] = (prev_high - prev_low) / prev2_close

            if prev_open and prev_open > 0 and prev_close is not None:
                result['prev_day_open_close_return'] = prev_close / prev_open - 1.0
            if prev2_close and prev2_close > 0 and prev_open is not None:
                result['prev_day_gap_return'] = prev_open / prev2_close - 1.0

            close = h['close'].dropna()
            high = h['high'].dropna()
            low = h['low'].dropna()
            if len(close) >= 6:
                result['stock_return_5d'] = _s5_pct(close.iloc[-1], close.iloc[-6])
            if len(close) >= 11:
                result['stock_return_10d'] = _s5_pct(close.iloc[-1], close.iloc[-11])
            if len(close) >= 21:
                result['stock_return_20d'] = _s5_pct(close.iloc[-1], close.iloc[-21])

            if len(high) >= 20 and prev_close is not None:
                result['distance_to_20d_high'] = _s5_pct(prev_close, high.iloc[-20:].max())
            if len(high) >= 60 and prev_close is not None:
                result['distance_to_60d_high'] = _s5_pct(prev_close, high.iloc[-60:].max())

            if len(high) >= 60 and len(low) >= 60 and prev_close is not None:
                h60 = float(high.iloc[-60:].max())
                l60 = float(low.iloc[-60:].min())
                if h60 > l60:
                    result['relative_position_60'] = (prev_close - l60) / (h60 - l60)

            vol = pd.to_numeric(h['volume'], errors='coerce')
            money = pd.to_numeric(h['money'], errors='coerce')
            if len(vol) >= 6:
                base = vol.iloc[-6:-1].dropna().mean()
                if base and base > 0:
                    result['prev_volume_ratio_5d'] = _s5_safe_float(vol.iloc[-1]) / base
            if len(money) >= 6:
                base = money.iloc[-6:-1].dropna().mean()
                if base and base > 0:
                    result['prev_money_ratio_5d'] = _s5_safe_float(money.iloc[-1]) / base

            auc = get_first_auction_record(stock, start, end, ['time', 'volume', 'current'])
            if auc is not None and prev_close and prev_close > 0:
                auction_price = _s5_safe_float(auc['current'])
                if auction_price is not None:
                    result['auction_open_return'] = auction_price / prev_close - 1.0

    result['auction_vol_ratio'] = _s5_safe_float(auction_vol_ratio)

    try:
        val = get_valuation(
            stock,
            start_date=context.previous_date,
            end_date=context.previous_date,
            fields=['turnover_ratio', 'market_cap', 'circulating_market_cap']
        )
        if val is not None and len(val):
            result['turnover_ratio'] = _s5_safe_float(val['turnover_ratio'].iloc[0])
            result['market_cap'] = _s5_safe_float(val['market_cap'].iloc[0])
            result['circulating_market_cap'] = _s5_safe_float(val['circulating_market_cap'].iloc[0])
    except Exception as e:
        print('[S5-V4-VALUATION-WARN] stock=%s error=%s' % (stock, e))

    return result


def _s5_prefixed_env(env, prefix):
    if not env:
        return {}
    return {prefix + k: v for k, v in env.items()}


def _s5_outcome_class(closed_lot):
    reason = str(closed_lot.get('exit_reason') or '')
    ret = _s5_safe_float(closed_lot.get('return_pct'))
    if reason == 'TAKE_PROFIT':
        return 'TAKE_PROFIT_SUCCESS'
    if reason == 'S4_LOW_T1_1120_RET_LE_-2':
        return 'T1_FAILURE'
    if reason == 'MA5_STOP':
        return 'MA5_FAILURE'
    if ret is not None:
        return 'OTHER_WIN' if ret > 0 else 'OTHER_LOSS'
    return 'UNKNOWN'


def record_trade_regime(closed_lot, context):
    init_regime_analysis()

    entry_date = closed_lot.get('entry_time')
    if hasattr(entry_date, 'date'):
        entry_date = entry_date.date()
    if entry_date is None:
        entry_date = closed_lot.get('entry_date')

    exit_date = closed_lot.get('exit_time')
    if hasattr(exit_date, 'date'):
        exit_date = exit_date.date()
    if exit_date is None:
        exit_date = context.current_dt.date()

    entry_env = g.regime_daily.get(entry_date)
    exit_env = g.regime_daily.get(exit_date)

    qty = _s5_safe_float(closed_lot.get('qty'))
    entry_price = _s5_safe_float(closed_lot.get('entry_price'))
    pnl = _s5_safe_float(closed_lot.get('realized_pnl_gross'))
    entry_capital = entry_price * qty if entry_price is not None and qty is not None else None
    pnl_on_capital = pnl / entry_capital if pnl is not None and entry_capital and entry_capital > 0 else None

    row = {
        'lot_id': closed_lot.get('lot_id'),
        'stock': closed_lot.get('stock'),
        'strategy_tag': closed_lot.get('strategy_tag'),
        'entry_time': closed_lot.get('entry_time'),
        'entry_price': closed_lot.get('entry_price'),
        'entry_capital': entry_capital,
        'exit_time': closed_lot.get('exit_time'),
        'exit_price': closed_lot.get('exit_price'),
        'qty': closed_lot.get('qty'),
        'holding_trade_days': closed_lot.get('holding_trade_days'),
        'exit_reason': closed_lot.get('exit_reason'),
        'outcome_class': _s5_outcome_class(closed_lot),
        'return_pct': closed_lot.get('return_pct'),
        'realized_pnl_gross': closed_lot.get('realized_pnl_gross'),
        'pnl_on_entry_capital': pnl_on_capital,
        'buy_trade_id': closed_lot.get('buy_trade_id'),
        'sell_trade_id': closed_lot.get('sell_trade_id'),
        'sell_order_id': closed_lot.get('sell_order_id'),
    }
    row.update(_s5_prefixed_env(entry_env, 'entry_'))
    row.update(_s5_prefixed_env(exit_env, 'exit_'))

    # closed_lots 保持 S2/S4 原有结构，不额外塞嵌套dict；
    # 通过 lot_id 回到仍保存在 position_lots 中的原始 lot 读取 BUY 因子快照。
    stock_factors = closed_lot.get('entry_stock_factors') or {}
    if not stock_factors:
        lot_id = closed_lot.get('lot_id')
        stock_code = closed_lot.get('stock')
        for original_lot in getattr(g, 'position_lots', {}).get(stock_code, []):
            if original_lot.get('lot_id') == lot_id:
                stock_factors = original_lot.get('entry_stock_factors') or {}
                break
    for k, v in stock_factors.items():
        row['entry_stock_' + str(k)] = v

    g.regime_trade_records.append(row)












