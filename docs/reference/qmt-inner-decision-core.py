# coding: utf-8
"""#533 GENERATED calculation specimen; NOT an executable QMT strategy.
No market/account access, scheduling, state recovery or order submission.
ORDER_SUBMITTED is the original allocation result label, not a broker event.
Regenerate locally with docs/reference/build-qmt-inner-core.py.
"""

from collections import namedtuple as _namedtuple

SOURCE_SHA256 = {'params.py': 'f68097be4b43a146fe17d9200b70c3a8fa4c3062384e3782023e5d4c52d3eff9', 'gate.py': '63974cdaba1ad59c1883ed5906f8f28888b9c9eec0a40781fa7675bab198b1a0', 'allocation.py': 'c779cb2c5a8744aef9ec7538c1b378ab128d36be06f7b20301596484677d1812', 'exits.py': '3d6eb8e4ca787acf36b68225ca4054d38c40841ec12759d27be5e60333b08b13'}

NEW_STOCK_DAYS = 50
EXCLUDED_PREFIXES = ('4', '8')
EXCLUDED_PREFIX_2 = '68'
CHINEXT_PREFIXES = ('300', '301')
LOW_HISTORY_DAYS = 60
LOW_RELATIVE_POSITION_MAX = 0.5
LOW_MIN_MONEY = 100000000.0
LOW_AUCTION_RATIO_MIN = 0.955
LOW_AUCTION_RATIO_MAX = 0.97
LOW_MAIN_AUCTION_VOL_RATIO_MIN = 0.02
HIGH_AVG_INC_MIN = 0.07
HIGH_MIN_MONEY = 550000000.0
HIGH_MAX_MONEY = 2000000000.0
HIGH_MIN_MARKET_CAP = 70.0
HIGH_MAX_CIRCULATING_CAP = 520.0
HIGH_AUCTION_VOL_RATIO_MIN = 0.03
HIGH_AUCTION_RATIO_MIN = 1.0
HIGH_AUCTION_RATIO_MAX = 1.06
LEFT_PRESSURE_HISTORY_DAYS = 101
LEFT_PRESSURE_DEFAULT_ZYTS = 100
LEFT_PRESSURE_PADDING = 5
LEFT_PRESSURE_VOLUME_FACTOR = 0.9
S6_LOW_TREND_WEAK_THRESHOLD = -0.009053065956339234
S6_LOW_HEAT_LOW_THRESHOLD = 0.0069591652776029595
S6_LOW_STOCK_RET10_HIGH = 0.09255705602513398
S6_LOW_DISTANCE60_HIGH = -0.19610064664276514
S6_HIGH_MAIN_BREADTH_HIGH = 0.0042554739215828
S6_HC_TOUCHED_NOT_LOW = 103.33333333333333
S6_HC_ADVANCE_NOT_LOW = 0.5104943476458254
BRANCH_WEIGHTS = {'HIGH_MAIN': 1.15, 'LOW_MAIN': 1.15, 'HIGH_CHINEXT': 0.85, 'LOW_CHINEXT': 1.0}
Classification = _namedtuple('Classification', ['state', 'known', 'reason'])

def three_or(a, b):
    if a is True or b is True:
        return True
    if a is False and b is False:
        return False
    return None

def three_and(a, b):
    if a is False or b is False:
        return False
    if a is True and b is True:
        return True
    return None

def _num(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number

def classify(tag, env, stock_factors, *, thresholds=None):
    if tag == 'LOW_MAIN':
        return _classify_low_main(env, stock_factors, thresholds or {})
    if tag == 'HIGH_MAIN':
        return _classify_high_main(env, thresholds or {})
    if tag == 'HIGH_CHINEXT':
        return _classify_high_chinext(env, thresholds or {})
    return Classification('UNCHANGED', True, 'SAMPLE_INSUFFICIENT_KEEP_BASELINE')

def _classify_low_main(env, stock_factors, thresholds):
    slope = _num(env.get('index_ma20_slope_5d'))
    heat = _num(env.get('prior_limitup_nextday_return_mean'))
    ret10 = _num(stock_factors.get('stock_return_10d'))
    dist60 = _num(stock_factors.get('distance_to_60d_high'))
    market_bad = None if slope is None or heat is None else bool(slope > thresholds.get('low_trend_weak', S6_LOW_TREND_WEAK_THRESHOLD) and heat > thresholds.get('low_heat_low', S6_LOW_HEAT_LOW_THRESHOLD))
    stock_bad = None if ret10 is None or dist60 is None else bool(ret10 >= S6_LOW_STOCK_RET10_HIGH and dist60 >= S6_LOW_DISTANCE60_HIGH)
    risk = three_or(market_bad, stock_bad)
    if risk is True:
        reasons = []
        if market_bad is True:
            reasons.append('MARKET_BAD_NOT_WEAK_HIGH_HEAT')
        if stock_bad is True:
            reasons.append('STOCK_OVERHEAT_RET10_HIGH_NEAR_60D_HIGH')
        return Classification('RISK', True, '|'.join(reasons))
    if risk is False:
        return Classification('CLEAN', True, 'LOW_MAIN_CLEAN')
    return Classification('UNKNOWN_ALLOW', False, 'LOW_MAIN_DATA_UNKNOWN_ALLOW')

def _classify_high_main(env, thresholds):
    breadth = _num(env.get('market_median_return_1d'))
    if breadth is None:
        return Classification('UNKNOWN_ALLOW', False, 'BREADTH_UNKNOWN_ALLOW')
    if breadth > thresholds.get('high_main_breadth_high', S6_HIGH_MAIN_BREADTH_HIGH):
        return Classification('FAVORABLE', True, 'BREADTH_STRONG')
    return Classification('OTHER', True, 'BREADTH_OTHER')

def _classify_high_chinext(env, thresholds):
    touched = _num(env.get('touched_limit_up_count'))
    advance = _num(env.get('market_advance_ratio'))
    a = None if touched is None else bool(touched > S6_HC_TOUCHED_NOT_LOW)
    b = None if advance is None else bool(advance > thresholds.get('high_chinext_advance_not_low', S6_HC_ADVANCE_NOT_LOW))
    favorable = three_and(a, b)
    if favorable is True:
        return Classification('FAVORABLE', True, 'ACTIVITY_AND_BREADTH_OK')
    if favorable is False:
        return Classification('OTHER', True, 'ACTIVITY_OR_BREADTH_LOW')
    return Classification('UNKNOWN_ALLOW', False, 'HC_ENV_UNKNOWN_ALLOW')

def gate_allow(tag, classification):
    if classification.state == 'UNKNOWN_ALLOW':
        return True
    if tag == 'LOW_MAIN':
        return classification.state == 'CLEAN'
    if tag in ('HIGH_MAIN', 'HIGH_CHINEXT'):
        return classification.state == 'FAVORABLE'
    return True
LOT = 100
PRICE_BUFFER = 0.002
RESERVE_COMMISSION_RATE = 0.0003
RESERVE_MIN_COMMISSION = 5.0
RESERVE_TRANSFER_RATE = 1e-05
Budget = _namedtuple('Budget', ['symbol', 'tag', 'weight', 'budget', 'qty', 'status', 'protect_price'])
Budget.__new__.__defaults__ = (None,)

def fee_reserve(budget):
    if budget <= 0:
        return 0.0
    commission = max(RESERVE_MIN_COMMISSION, budget * RESERVE_COMMISSION_RATE)
    return commission + budget * RESERVE_TRANSFER_RATE

def protect_price(open_price, limit_up, buffer=PRICE_BUFFER):
    if buffer < 0:
        raise ValueError(f'price buffer must be >= 0: {buffer}')
    protected = open_price * (1.0 + buffer)
    if limit_up is not None and limit_up > 0:
        return min(protected, limit_up)
    return protected

def allocate(candidates, cash, opens, limit_ups=None, max_budget=None, price_buffer=PRICE_BUFFER, sizing_buffer=None):
    if sizing_buffer is None:
        sizing_buffer = price_buffer
    if not candidates:
        return []
    weights = [(s, tag, float(BRANCH_WEIGHTS.get(tag, 1.0))) for s, tag in candidates]
    weights = [(s, tag, w if w > 0 else 1.0) for s, tag, w in weights]
    total = sum((w for _, _, w in weights)) or float(len(weights))
    limits = limit_ups or {}
    allocatable = max(0.0, cash)
    out = []
    for symbol, tag, weight in weights:
        budget = allocatable * weight / total
        if max_budget is not None:
            budget = min(budget, max(0.0, max_budget))
        if allocatable <= 0:
            out.append(Budget(symbol, tag, weight, 0.0, 0, 'NO_AVAILABLE_CASH'))
            continue
        price = opens.get(symbol)
        if price is None or price <= 0:
            out.append(Budget(symbol, tag, weight, budget, 0, 'INVALID_OPEN_PRICE'))
            continue
        protected = protect_price(price, limits.get(symbol), price_buffer)
        if protected <= 0:
            out.append(Budget(symbol, tag, weight, budget, 0, 'INVALID_OPEN_PRICE'))
            continue
        sizing = protect_price(price, limits.get(symbol), sizing_buffer)
        principal = max(0.0, budget - fee_reserve(budget))
        qty = int(principal / sizing / LOT) * LOT
        if qty < LOT:
            out.append(Budget(symbol, tag, weight, budget, 0, 'BELOW_BOARD_LOT', protected))
            continue
        out.append(Budget(symbol, tag, weight, budget, qty, 'ORDER_SUBMITTED', protected))
    return out
ExitDecision = _namedtuple('ExitDecision', ['should_exit', 'reason', 'ret'])
ExitDecision.__new__.__defaults__ = (None, None)
_HOLD = ExitDecision(False)

def dynamic_ma5(recent_closes, last_price):
    closes = list(recent_closes)[-4:]
    if not closes:
        return None
    return (sum(closes) / len(closes) * 4 + last_price) / 5

def _return_pct(last_price, entry_price):
    return 100.0 * (last_price / entry_price - 1.0)

def _is_limit_up(last_price, limit_up):
    return limit_up is not None and last_price >= limit_up

def legacy_exit(last_price, limit_up, entry_price, ma5):
    if not entry_price or entry_price <= 0:
        return _HOLD
    if not _is_limit_up(last_price, limit_up) and last_price > entry_price:
        return ExitDecision(True, 'TAKE_PROFIT', _return_pct(last_price, entry_price))
    if ma5 is not None and last_price < ma5:
        return ExitDecision(True, 'MA5_STOP', _return_pct(last_price, entry_price))
    return _HOLD

def low_1125(last_price, limit_up, entry_price):
    if not entry_price or entry_price <= 0:
        return _HOLD
    ret = _return_pct(last_price, entry_price)
    if _is_limit_up(last_price, limit_up):
        return ExitDecision(False, None, ret)
    if ret > 0:
        return ExitDecision(True, 'LW_1125_PROFIT_NON_LIMIT', ret)
    return ExitDecision(False, None, ret)

def low_1425(last_price, limit_up, entry_price):
    if not entry_price or entry_price <= 0:
        return _HOLD
    ret = _return_pct(last_price, entry_price)
    if _is_limit_up(last_price, limit_up):
        return ExitDecision(False, None, ret)
    return ExitDecision(True, 'LW_1425_NON_LIMIT_EXIT', ret)
