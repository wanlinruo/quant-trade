# coding: utf-8
"""#533 GENERATED calculation specimen; NOT an executable QMT strategy.
No market/account access, scheduling, state recovery or order submission.
ORDER_SUBMITTED is the original allocation result label, not a broker event.
Regenerate locally with docs/reference/build-qmt-inner-core.py.
"""

from collections import namedtuple as _namedtuple

SOURCE_SHA256 = {'params.py': 'f68097be4b43a146fe17d9200b70c3a8fa4c3062384e3782023e5d4c52d3eff9', 'gate.py': '63974cdaba1ad59c1883ed5906f8f28888b9c9eec0a40781fa7675bab198b1a0', 'allocation.py': 'c779cb2c5a8744aef9ec7538c1b378ab128d36be06f7b20301596484677d1812', 'exits.py': '3d6eb8e4ca787acf36b68225ca4054d38c40841ec12759d27be5e60333b08b13', 'entry.py': '63a2805f70e4c579709391c9eb011f0fb4205255e63b8139fb6f5965ea332506', 'selection.py': '412c1cfb18cb9b1864b9b7c3467d9c7830de01456411f1e57d6b337f90c9f1fe', 'qmt-inner-history-input.py': '38573f91bce5ae669c895096289deeace979df820e48e9211598e2f568982f45'}

# coding: utf-8
"""#533: in-memory calculation input; no QMT field mapping or data injection.

The future native adapter must first prove dates, adjustment and units.
This container copies already validated canonical columns without converting,
filling, sorting or loading any data. It is also used by offline fixtures.
"""


class _HistoryColumn(tuple):
    def to_list(self):
        return list(self)

    def max(self):
        return max(self) if self else None

    def min(self):
        return min(self) if self else None


class History:
    COLUMNS = ("open", "high", "low", "close", "volume", "amount")

    def __init__(self, columns):
        missing = set(self.COLUMNS) - set(columns)
        if missing:
            raise ValueError("missing canonical history columns")
        copied = {name: _HistoryColumn(columns[name]) for name in self.COLUMNS}
        lengths = {len(values) for values in copied.values()}
        if len(lengths) != 1:
            raise ValueError("unequal canonical history column lengths")
        for values in copied.values():
            for value in values:
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or value != value or value in (float("inf"), -float("inf"))):
                    raise ValueError("history values must be explicit finite numbers")
        self._columns = copied
        self.height = next(iter(lengths))

    def __getitem__(self, name):
        return self._columns[name]

    def tail(self, count):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("tail requires a nonnegative integer")
        return History({name: values[-count:] if count else ()
                        for name, values in self._columns.items()})

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
LOT = 100

def diagnose_low_open(history, auction_price):
    enough_history = history.height >= LOW_HISTORY_DAYS
    out = {'low_history_ok': enough_history, 'low_auction_price_ok': auction_price is not None, 'low_relative_position': None, 'low_relative_position_ok': False, 'low_prev_amount': None, 'low_amount_ok': False, 'low_open_ratio': None, 'low_open_band_ok': False}
    if auction_price is None or not enough_history:
        return {**out, 'eligible': False}
    window = history.tail(LOW_HISTORY_DAYS)
    close = window['close'].to_list()[-1]
    high = window['high'].max()
    low = window['low'].min()
    money = window['amount'].to_list()[-1]
    out['low_prev_amount'] = money
    out['low_amount_ok'] = money >= LOW_MIN_MONEY
    if high > low:
        relative_position = (close - low) / (high - low)
        out['low_relative_position'] = relative_position
        out['low_relative_position_ok'] = relative_position <= LOW_RELATIVE_POSITION_MAX
    if close > 0:
        ratio = auction_price / close
        out['low_open_ratio'] = ratio
        out['low_open_band_ok'] = LOW_AUCTION_RATIO_MIN <= ratio <= LOW_AUCTION_RATIO_MAX
    out['eligible'] = all((bool(out[key]) for key in ('low_history_ok', 'low_auction_price_ok', 'low_relative_position_ok', 'low_amount_ok', 'low_open_band_ok')))
    return out

def check_low_open(history, auction_price):
    return bool(diagnose_low_open(history, auction_price)['eligible'])

def check_left_pressure(history):
    highs = history['high'].to_list()
    volumes = history['volume'].to_list()
    if not highs:
        return False
    prev_high = highs[-1]
    zyts_0 = LEFT_PRESSURE_DEFAULT_ZYTS
    for i in range(3, len(highs)):
        if highs[-i] >= prev_high:
            zyts_0 = i - 1
            break
    window = volumes[-(zyts_0 + LEFT_PRESSURE_PADDING):]
    if len(window) < 2:
        return False
    return window[-1] > max(window[:-1]) * LEFT_PRESSURE_VOLUME_FACTOR

def check_high_open(history, auction_price, auction_volume, valuation, *, require_auction_volume=True):
    return bool(diagnose_high_open(history, auction_price, auction_volume, valuation, require_auction_volume=require_auction_volume)['eligible'])

def diagnose_high_open(history, auction_price, auction_volume, valuation, *, require_auction_volume=True):
    out = {'high_history_ok': history.height >= 2, 'high_auction_price_ok': auction_price is not None, 'high_auction_volume_present': auction_volume is not None, 'high_auction_volume_required': require_auction_volume, 'high_avg_inc': None, 'high_avg_inc_ok': False, 'high_prev_amount': None, 'high_amount_ok': False, 'high_market_cap': None, 'high_market_cap_ok': False, 'high_circulating_market_cap': None, 'high_circulating_cap_ok': False, 'high_auction_volume_ratio': None, 'high_auction_volume_ok': None, 'high_auction_volume_rule_ok': not require_auction_volume, 'high_open_ratio': None, 'high_open_band_ok': False, 'high_left_pressure_ok': False}
    if auction_price is None or history.height < 2:
        return {**out, 'eligible': False}
    closes = history['close'].to_list()
    volumes = history['volume'].to_list()
    moneys = history['amount'].to_list()
    pre_prev_close, prev_close = (closes[-2], closes[-1])
    prev_volume, prev_money = (volumes[-1], moneys[-1])
    out['high_prev_amount'] = prev_money
    out['high_amount_ok'] = HIGH_MIN_MONEY <= prev_money <= HIGH_MAX_MONEY
    if pre_prev_close > 0 and prev_close > 0 and (prev_volume > 0):
        vwap = prev_money / (prev_volume * LOT)
        avg_inc = vwap / pre_prev_close - 1
        out['high_avg_inc'] = avg_inc
        out['high_avg_inc_ok'] = avg_inc >= HIGH_AVG_INC_MIN
        ratio = auction_price / prev_close
        out['high_open_ratio'] = ratio
        out['high_open_band_ok'] = HIGH_AUCTION_RATIO_MIN < ratio < HIGH_AUCTION_RATIO_MAX
        if auction_volume is not None:
            volume_ratio = auction_volume / prev_volume
            out['high_auction_volume_ratio'] = volume_ratio
            out['high_auction_volume_ok'] = volume_ratio >= HIGH_AUCTION_VOL_RATIO_MIN
            out['high_auction_volume_rule_ok'] = out['high_auction_volume_ok']
    if valuation is not None:
        market_cap = valuation['market_cap']
        circulating = valuation['circulating_market_cap']
        out['high_market_cap'] = market_cap
        out['high_market_cap_ok'] = market_cap >= HIGH_MIN_MARKET_CAP
        out['high_circulating_market_cap'] = circulating
        out['high_circulating_cap_ok'] = circulating <= HIGH_MAX_CIRCULATING_CAP
    out['high_left_pressure_ok'] = check_left_pressure(history)
    out['eligible'] = all((bool(out[key]) for key in ('high_history_ok', 'high_auction_price_ok', 'high_avg_inc_ok', 'high_amount_ok', 'high_market_cap_ok', 'high_circulating_cap_ok', 'high_auction_volume_rule_ok', 'high_open_band_ok', 'high_left_pressure_ok')))
    return out

def _stock_factors(bars):
    closes = bars['close'].to_list()
    highs = bars['high'].to_list()
    factors = {'stock_return_10d': None, 'distance_to_60d_high': None}
    if len(closes) >= 11 and closes[-11]:
        factors['stock_return_10d'] = closes[-1] / closes[-11] - 1.0
    if len(highs) >= 60:
        peak = max(highs[-60:])
        if peak:
            factors['distance_to_60d_high'] = closes[-1] / peak - 1.0
    return factors
