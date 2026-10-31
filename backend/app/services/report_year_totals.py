"""Year-level forecast totals for PDF charts and monthly detail subtotals.

Presentation helpers only — sums selected monthly forecast amounts at full
precision. Does not change stored forecasts or Excel models.
"""

from __future__ import annotations

from typing import Any

from app.services.report_builder import MONTH_LABELS, is_finite_amount, is_iso_month


def _parse_iso(iso: Any) -> tuple[int, int, str] | None:
    text = str(iso or "")
    if not is_iso_month(text):
        return None
    year = int(text[:4])
    month = int(text[5:7])
    return year, month, text


def _month_name(month: int) -> str:
    if 1 <= month <= 12:
        return MONTH_LABELS[month - 1]
    return str(month)


def is_full_forecast_calendar_year(*, start_month: int, end_month: int, month_count: int) -> bool:
    return int(start_month) == 1 and int(end_month) == 12 and int(month_count) == 12


def forecast_year_chart_title(*, year: int, start_month: int, end_month: int, month_count: int) -> str:
    if is_full_forecast_calendar_year(start_month=start_month, end_month=end_month, month_count=month_count):
        return f"YTD {int(year)}"
    return f"Forecast Period Total {int(year)}"


def forecast_year_range_label(*, year: int, start_month: int, end_month: int) -> str:
    y = int(year)
    start = int(start_month)
    end = int(end_month)
    if start == end:
        return f"{_month_name(start)} {y}"
    return f"{_month_name(start)} {y} – {_month_name(end)} {y}"


def sum_available_amounts(values: list[Any]) -> float | None:
    finite = [float(value) for value in values if is_finite_amount(value)]
    if not finite:
        return None
    return float(sum(finite))


def summarize_selected_forecast_years(
    monthly_rows: list[dict[str, Any]] | None,
    *,
    month_key: str = "month",
    amount_key: str = "forecast_amount",
) -> list[dict[str, Any]]:
    """Group selected forecast months by calendar year and sum each year separately."""
    by_year: dict[int, list[tuple[str, int, Any]]] = {}
    for row in monthly_rows or []:
        if not isinstance(row, dict):
            continue
        parsed = _parse_iso(row.get(month_key))
        if parsed is None:
            continue
        year, month, iso = parsed
        by_year.setdefault(year, []).append((iso, month, row.get(amount_key)))

    summaries: list[dict[str, Any]] = []
    for year in sorted(by_year):
        group = sorted(by_year[year], key=lambda item: item[0])
        amounts = [amount for _, _, amount in group]
        available = [amount for amount in amounts if is_finite_amount(amount)]
        if not available and not group:
            continue
        start_iso, start_month, _ = group[0]
        end_iso, end_month, _ = group[-1]
        month_count = len(group)
        available_count = len(available)
        total = sum_available_amounts(amounts)
        summary = {
            "year": year,
            "start_month": start_month,
            "end_month": end_month,
            "start_iso": start_iso,
            "end_iso": end_iso,
            "month_count": month_count,
            "available_count": available_count,
            "has_gaps": available_count < month_count,
            "total": total,
            "title": forecast_year_chart_title(
                year=year,
                start_month=start_month,
                end_month=end_month,
                month_count=month_count,
            ),
            "range_label": forecast_year_range_label(
                year=year,
                start_month=start_month,
                end_month=end_month,
            ),
            "is_full_calendar": is_full_forecast_calendar_year(
                start_month=start_month,
                end_month=end_month,
                month_count=month_count,
            ),
        }
        if summary["has_gaps"]:
            summary["coverage_label"] = f"{available_count} of {month_count} months with values"
        else:
            summary["coverage_label"] = None
        summaries.append(summary)
    return summaries


def shared_zero_inclusive_domain(values: list[Any], *, padding_ratio: float = 0.12) -> tuple[float, float]:
    finite = [float(value) for value in values if is_finite_amount(value)]
    if not finite:
        return -1.0, 1.0
    low = min(finite + [0.0])
    high = max(finite + [0.0])
    span = high - low
    padding = max(abs(high) * padding_ratio, 1.0) if span < 1e-9 else span * padding_ratio
    return low - padding, high + padding
