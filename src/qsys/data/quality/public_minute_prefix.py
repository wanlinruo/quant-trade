"""#583: replay public first-positive observations; never decide production health."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import re


class EvidenceRejected(ValueError):
    """Incomplete or contradictory evidence, expressed as a stable code."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise EvidenceRejected(code)


def number(value) -> Decimal:
    require(not isinstance(value, bool) and value is not None, "missing_or_boolean_number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise EvidenceRejected("invalid_number") from exc
    require(result.is_finite(), "nonfinite_number")
    return result


def code_for(symbol: str) -> str:
    require(isinstance(symbol, str) and bool(re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", symbol)),
            "invalid_symbol")
    digits, market = symbol.split(".")
    return market.lower() + digits


@dataclass(frozen=True)
class Point:
    at: datetime
    volume: Decimal
    amount: Decimal | None


def tencent_points(payload: bytes, symbol: str, day: date) -> list[Point]:
    """Read raw cumulative fields, before the chart client's fill/conversion code."""
    code = code_for(symbol)
    value = json.loads(payload)
    require(isinstance(value, dict), "tencent_invalid_container")
    require(type(value.get("code")) is int and value["code"] == 0, "tencent_request_failed")
    dates = value["data"][code]["data"]
    require(isinstance(dates, list), "tencent_invalid_days")
    require(all(isinstance(row, dict) for row in dates), "tencent_invalid_day_record")
    selected = [row for row in dates if row.get("date") == day.strftime("%Y%m%d")]
    require(len(selected) == 1, "tencent_target_day_missing_or_duplicate")
    rows = selected[0]["data"]
    require(isinstance(rows, list) and 0 < len(rows) <= 2000, "tencent_rows_missing_or_excessive")
    width = 3 if symbol.endswith(".BJ") else 4  # Actual native BJ response omits amount.
    result = []
    for row in rows:
        require(isinstance(row, str), "tencent_invalid_row")
        fields = row.split(" ")
        require(len(fields) == width, "tencent_unverified_row_width")
        require(bool(re.fullmatch(r"[0-9]{4}", fields[0])), "tencent_invalid_time")
        at = datetime.strptime(day.isoformat() + " " + fields[0], "%Y-%m-%d %H%M")
        require(number(fields[1]) > 0, "tencent_nonpositive_price")
        volume = number(fields[2])
        amount = number(fields[3]) if width == 4 else None
        require(volume >= 0 and (amount is None or amount >= 0), "tencent_negative_value")
        if result:
            require(at > result[-1].at, "tencent_time_not_unique_or_ordered")
            require(volume >= result[-1].volume, "tencent_cumulative_volume_decreased")
        result.append(Point(at, volume, amount))
    return result


def sina_points(payload: bytes, day: date) -> list[Point]:
    """Strict JSONP decoding; never execute vendor JavaScript or infer a missing row."""
    text = payload.decode("utf-8")
    match = re.fullmatch(
        r"\s*(?:/\*[^*]*\*/\s*)?var _data=\((\[.*\]|null)\);\s*", text, re.S,
    )
    require(match is not None, "sina_unverified_wrapper")
    rows = json.loads(match.group(1))
    require(isinstance(rows, list) and 0 < len(rows) <= 300, "sina_rows_missing_or_excessive")
    result = []
    for row in rows:
        at = datetime.strptime(row["day"], "%Y-%m-%d %H:%M:%S")
        volume, amount = number(row["volume"]), number(row["amount"])
        require(volume >= 0 and amount >= 0, "sina_negative_value")
        require(volume != 0 or amount == 0, "sina_zero_volume_positive_amount")
        require(not result or at > result[-1].at, "sina_time_not_unique_or_ordered")
        result.append(Point(at, volume, amount))
    require(result[0].at.date() < day, "sina_prefix_window_not_proven")
    selected = [point for point in result if point.at.date() == day]
    require(bool(selected), "sina_target_day_missing")
    return selected


def assess_prefix(symbol: str, day: date, warehouse_rows: list[dict],
                  tencent: list[Point], sina: list[Point]) -> dict:
    """Compare zero-prefix shape and first-positive labels, with no unit conversion.

    Agreement concerns observations from different publishers, not their underlying
    feed independence, complete OHLCV equality, or a production pass/exception.
    """
    result = {"symbol": symbol, "day": day.isoformat(), "supported": False, "errors": []}
    try:
        require(bool(warehouse_rows), "warehouse_rows_missing")
        times = [datetime.fromisoformat(row["dt"]) for row in warehouse_rows]
        require(all(row["symbol"] == symbol and at.date() == day
                    for row, at in zip(warehouse_rows, times)), "warehouse_identity_mismatch")
        require(times == sorted(set(times)), "warehouse_time_not_unique_or_ordered")
        require(times[0] == datetime(day.year, day.month, day.day, 9, 30),
                "warehouse_prefix_start_not_proven")
        volumes = [number(row["volume"]) for row in warehouse_rows]
        require(all(volume >= 0 for volume in volumes), "warehouse_negative_volume")
        positive = [i for i, volume in enumerate(volumes) if volume > 0]
        require(bool(positive), "warehouse_positive_observation_missing")
        first = positive[0]
        require(first > 0, "warehouse_zero_prefix_missing")
        prefix = warehouse_rows[:first]
        require(all(times[i] == times[0] + timedelta(minutes=i) for i in range(first + 1)),
                "warehouse_prefix_grid_gap")
        price = None
        for row in prefix:
            require(number(row["volume"]) == 0 and number(row["amount"]) == 0,
                    "warehouse_prefix_volume_or_amount_not_zero")
            prices = [number(row[name]) for name in ("open", "high", "low", "close")]
            require(prices[0] > 0 and len(set(prices)) == 1, "warehouse_zero_prefix_price_not_flat")
            require(price is None or prices[0] == price, "warehouse_zero_prefix_price_changed")
            price = prices[0]
        first_at = times[first]
        result.update(leading_rows=first, warehouse_first_positive=first_at.isoformat())
        for provider, points in (("tencent", tencent), ("sina", sina)):
            require(bool(points) and all(point.at.date() == day for point in points),
                    provider + "_identity_or_rows_missing")
            require(all(point.volume >= 0 for point in points), provider + "_negative_volume")
            positive = [point for point in points if point.volume > 0]
            require(bool(positive), provider + "_positive_observation_missing")
            require(positive[0].at == first_at, provider + "_first_positive_mismatch")
            result[provider + "_first_positive"] = positive[0].at.isoformat()
        by_time = {point.at: point for point in tencent}
        for at in times[:first]:
            require(at in by_time, "tencent_zero_prefix_observation_missing")
            point = by_time[at]
            require(point.volume == 0 and (point.amount is None or point.amount == 0),
                    "tencent_prefix_not_zero")
        result["supported"] = True
    except (EvidenceRejected, KeyError, ValueError, TypeError) as exc:
        result["errors"] = [str(exc) if isinstance(exc, EvidenceRejected) else type(exc).__name__]
    return result
