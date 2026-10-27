# coding: utf-8
"""#538: decode observed native in-memory packets, without loading any data.

This is the UTC+8 A-share daily contract observed on JiangHai 2.1.33.0.
The caller supplies raw unadjusted columns/time, native session strings and the
seven-field dividend dict. Native source completeness and units remain separate
quality checks; nonempty metadata is not proof of complete event coverage.
"""

from datetime import datetime as _NativeDateTime


_NATIVE_DAY_MS = 86400000
_NATIVE_OFFSET_MS = 28800000
_NATIVE_CLOSE_MS = 54000000
_NATIVE_EPOCH = _NativeDateTime(1970, 1, 1)


def _native_midnight(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("native day timetags must be integer milliseconds")
    if (value + _NATIVE_OFFSET_MS) % _NATIVE_DAY_MS:
        raise ValueError("native day timetag is not UTC+8 midnight")
    return value


def _native_session_tag(label):
    if not isinstance(label, str) or len(label) != 8 or not label.isdigit():
        raise ValueError("native sessions must be YYYYMMDD strings")
    day = _NativeDateTime.strptime(label, "%Y%m%d")
    if day.strftime("%Y%m%d") != label:
        raise ValueError("invalid native session date")
    return (day - _NATIVE_EPOCH).days * _NATIVE_DAY_MS - _NATIVE_OFFSET_MS


def _native_event_increments(payload):
    if not isinstance(payload, (list, tuple)) or len(payload) != 7:
        raise ValueError("native dividend event must have seven explicit fields")
    values = tuple(_adjustment_value(v) for v in payload[:5])
    flag = payload[5]
    if flag is not None and (
            isinstance(flag, bool) or not isinstance(flag, int) or flag not in (0, 1)):
        raise ValueError("unknown native reform flag")
    price = _adjustment_factor(payload[6])
    share = (None if any(v is None for v in values[1:4]) else
             _adjustment_factor(1.0 + (values[1] + values[2]) + values[3]))
    return price, share


def native_forward_adjusted_columns(columns, day_timetags, trading_dates,
                                    dividend_events, *, as_of, reference_symbol_count):
    """Map observed native packets to canonical forward-adjustment arithmetic.

    Calendar and visible bars must agree exactly. Events are multiplied in date
    order, starting at an actually supplied event rather than an invented factor
    of one. Earlier rows, empty events and missing increments remain unknown.
    The result is nullable; History still rejects incomplete input.
    """
    if isinstance(as_of, bool) or not isinstance(as_of, int):
        raise ValueError("native as_of must be integer epoch milliseconds")
    _adjustment_group_count(reference_symbol_count)
    if set(History.COLUMNS) - set(columns):
        raise ValueError("missing canonical history columns")
    raw = {name: tuple(columns[name]) for name in History.COLUMNS}
    days = tuple(_native_midnight(t) for t in day_timetags)
    if any(len(values) != len(days) for values in raw.values()):
        raise ValueError("native bar columns and day timetags must align")
    if any(a >= b for a, b in zip(days, days[1:])):
        raise ValueError("native bars must be unique and strictly increasing")
    sessions = tuple(_native_session_tag(d) for d in trading_dates)
    if any(a >= b for a, b in zip(sessions, sessions[1:])):
        raise ValueError("native calendar must be unique and strictly increasing")
    visible_days = tuple(d for d in days if d + _NATIVE_CLOSE_MS <= as_of)
    expected_days = tuple(d for d in sessions if d + _NATIVE_CLOSE_MS <= as_of)
    if visible_days != expected_days:
        raise ValueError("visible native bars do not match the supplied session window")
    if not isinstance(dividend_events, dict):
        raise ValueError("native events must be an explicit dict")
    keys = sorted(_native_midnight(k) for k in dividend_events)
    count = len(visible_days)
    if not count:
        return {name: [] for name in History.COLUMNS}
    sessions_set = set(sessions)
    price, share = [], []
    cumulative_price, cumulative_share = None, None
    seeded, pointer = False, 0
    for day in visible_days:
        while pointer < len(keys) and keys[pointer] <= day:
            event_day = keys[pointer]
            if visible_days[0] <= event_day and event_day not in sessions_set:
                raise ValueError("native dividend event is outside the supplied sessions")
            p, s = _native_event_increments(dividend_events[event_day])
            if not seeded:
                cumulative_price, cumulative_share = p, s
                seeded = True
            else:
                cumulative_price = (
                    None if cumulative_price is None or p is None else
                    _adjustment_factor(cumulative_price * p)
                )
                cumulative_share = (
                    None if cumulative_share is None or s is None else
                    _adjustment_factor(cumulative_share * s)
                )
            pointer += 1
        price.append(cumulative_price)
        share.append(cumulative_share)
    return forward_adjusted_columns(
        {name: values[:count] for name, values in raw.items()},
        [d + _NATIVE_CLOSE_MS for d in visible_days], as_of=as_of,
        cum_price_factors=price, cum_share_factors=share,
        reference_symbol_count=reference_symbol_count,
    )
