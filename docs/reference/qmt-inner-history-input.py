# coding: utf-8
"""#533: in-memory calculation input; no QMT field mapping or data injection.

The future native adapter must first prove dates, adjustment and units.
The container copies already validated canonical columns. The forward adjustment
helper below operates on explicit aligned canonical cumulative factors and close
times, NOT native QMT ex-event payloads. Neither loads, fills, sorts or converts
units; neither establishes native calendar/factor coverage or data visibility.
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


def _adjustment_value(value):
    if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or value != value or value in (float("inf"), -float("inf"))):
        raise ValueError("adjustment values must be finite numbers or explicit None")
    return value


def _adjustment_factor(value):
    value = _adjustment_value(value)
    if value is not None and value <= 0:
        raise ValueError("cumulative factors must be positive or explicit None")
    return value


def _adjustment_group_count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("reference symbol count must be an explicit nonnegative integer")
    return value


def forward_adjusted_columns(columns, bar_close_times, *, as_of,
                             cum_price_factors, cum_share_factors,
                             reference_symbol_count):
    """Canonical in-memory counterpart of HistoricalFeed FORWARD (#538).

    Close times and as_of are explicit comparable integer timestamps in the same
    unit/time basis, supplied after calendar/visibility validation. The final
    visible input bar is the anchor. Factors must align one-for-one with bars;
    missing factors propagate None, including a missing anchor. The nullable
    output cannot enter History until complete. reference_symbol_count describes
    the actual visible cohort of the reference feed call, not a strategy choice.
    No calendar, coverage or cohort cardinality is inferred.
    """
    if isinstance(as_of, bool) or not isinstance(as_of, int):
        raise ValueError("as_of must be an explicit integer timestamp")
    groups = _adjustment_group_count(reference_symbol_count)
    if set(History.COLUMNS) - set(columns):
        raise ValueError("missing canonical history columns")
    copied = {name: tuple(columns[name]) for name in History.COLUMNS}
    times = tuple(bar_close_times)
    price = tuple(cum_price_factors)
    share = tuple(cum_share_factors)
    lengths = {len(values) for values in copied.values()}
    lengths.update((len(times), len(price), len(share)))
    if len(lengths) != 1:
        raise ValueError("bars, close times and cumulative factors must align")
    if any(isinstance(t, bool) or not isinstance(t, int) for t in times):
        raise ValueError("close times must be explicit integer timestamps")
    if any(left >= right for left, right in zip(times, times[1:])):
        raise ValueError("close times must be unique and strictly increasing")
    count = sum(t <= as_of for t in times)
    result = {name: [_adjustment_value(v) for v in values[:count]]
              for name, values in copied.items()}
    if not count:
        return result
    if not groups:
        raise ValueError("visible bars require a positive reference symbol count")
    price = tuple(_adjustment_factor(v) for v in price[:count])
    share = tuple(_adjustment_factor(v) for v in share[:count])
    price_anchor, share_anchor = price[-1], share[-1]
    for index in range(count):
        if price[index] is None or price_anchor is None:
            price_ratio = None
        else:
            # Locked Polars: a single-group anchor broadcasts as a scalar.
            # For multiple rows it multiplies by a reciprocal; vector anchors
            # and one-row scalar/scalar operations use direct division.
            price_ratio = _adjustment_value(
                price[index] * (1.0 / price_anchor) if groups == 1 and count > 1
                else price[index] / price_anchor
            )
        share_ratio = (None if share[index] is None or share_anchor is None
                       else _adjustment_value(share_anchor / share[index]))
        if price_ratio == 0 or share_ratio == 0:
            raise ValueError("cumulative factor ratio underflow")
        for name in ("open", "high", "low", "close"):
            value = result[name][index]
            result[name][index] = (
                None if value is None or price_ratio is None
                else _adjustment_value(value * price_ratio)
            )
        value = result["volume"][index]
        if value is None or share_ratio is None:
            result["volume"][index] = None
        else:
            # Same float multiplication, half-to-even rounding and Int64 range
            # as the locked Polars feed. Cash never enters this share ratio.
            rounded = round(_adjustment_value(value * share_ratio))
            if not -(2 ** 63) <= rounded < 2 ** 63:
                raise ValueError("adjusted volume exceeds Int64 range")
            result["volume"][index] = rounded
    return result
