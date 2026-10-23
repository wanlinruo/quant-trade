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
