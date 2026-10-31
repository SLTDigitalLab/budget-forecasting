"""Assemble a forecast report from a persisted completed run only."""

from __future__ import annotations

import re
import sys
from calendar import month_name
from datetime import datetime
from pathlib import Path
from typing import Any

UNAVAILABLE = "Unavailable"
MONTH_LABELS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
FULL_MONTH_NAMES = tuple(month_name[1:])
ALL_CATEGORIES_LABEL = "All Categories"
ALL_CATEGORIES_TOKENS = {"", "all", "all categories", "*", "__all__", ALL_CATEGORIES_LABEL.casefold()}
UNAVAILABLE_EXPORT_STATUSES = {
    "NO_DATA",
    "NO_POSITIVE_YEAR_MEAN",
    "ALL_MISSING",
    "NOT_EVALUABLE",
    "NO_VALID_WINNER",
    "UNRESOLVED_MISSING",
    "INVALID_DATA",
    "INSUFFICIENT_HISTORY",
    "FIT_FAILED",
}


class ReportError(Exception):
    pass


def is_iso_month(value: Any) -> bool:
    return bool(value) and bool(re.fullmatch(r"\d{4}-\d{2}", str(value)))


def iso_to_label(iso_month: str) -> str:
    text = str(iso_month or "")
    if not is_iso_month(text):
        return text
    year, month = text.split("-")
    index = int(month)
    if index < 1 or index > 12:
        return text
    return f"{MONTH_LABELS[index - 1]}-{year}"


def is_finite_amount(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number not in (float("inf"), float("-inf"))


def chronological_forecasts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(payload.get("monthly_forecasts") or [])
    return sorted(
        [row for row in rows if isinstance(row, dict) and row.get("month")],
        key=lambda row: str(row["month"]),
    )


def period_label(payload: dict[str, Any]) -> str:
    start = payload.get("requested_start_month")
    end = payload.get("requested_end_month")
    if not start or not end:
        return "—"
    return f"{iso_to_label(str(start))} to {iso_to_label(str(end))}"


def report_filename_stem(payload: dict[str, Any]) -> str:
    start = iso_to_label(str(payload.get("requested_start_month") or ""))
    end = iso_to_label(str(payload.get("requested_end_month") or ""))
    return f"SLT_Mobitel_Forecast_Report_{start}_to_{end}"


def _month_file_part(iso_month: str) -> str:
    text = str(iso_month or "")
    if not is_iso_month(text):
        return "Unknown"
    year, month = text.split("-")
    return f"{MONTH_LABELS[int(month) - 1]}_{year}"


def excel_filename_stem(payload: dict[str, Any]) -> str:
    start = _month_file_part(str(payload.get("requested_start_month") or ""))
    end = _month_file_part(str(payload.get("requested_end_month") or ""))
    return f"AI_Budget_Forecast_{start}_{end}"


def full_month_year(iso_month: str) -> str:
    text = str(iso_month or "")
    if not is_iso_month(text):
        return text
    year, month = text.split("-")
    index = int(month)
    if index < 1 or index > 12:
        return text
    return f"{FULL_MONTH_NAMES[index - 1]} {year}"


def excel_report_title(start_month: str, end_month: str) -> str:
    return f"AI-Based Budget Forecast ({full_month_year(start_month)} - {full_month_year(end_month)})"


def is_all_categories_label(category: Any) -> bool:
    return str(category or "").strip().casefold() in ALL_CATEGORIES_TOKENS


def export_month_isos(payload: dict[str, Any]) -> list[str]:
    months = [
        str(row["month"])
        for row in chronological_forecasts(payload)
        if is_iso_month(row.get("month"))
    ]
    if months:
        return months
    start = str(payload.get("requested_start_month") or "")
    end = str(payload.get("requested_end_month") or "")
    if is_iso_month(start) and is_iso_month(end) and start <= end:
        year, month = int(start[:4]), int(start[5:7])
        end_year, end_month = int(end[:4]), int(end[5:7])
        labels = []
        while (year, month) <= (end_year, end_month):
            labels.append(f"{year:04d}-{month:02d}")
            if month == 12:
                year += 1
                month = 1
            else:
                month += 1
        return labels
    return []


def month_column_headers(months: list[str]) -> list[str]:
    years = {str(month)[:4] for month in months if is_iso_month(month)}
    headers = []
    for month in months:
        if not is_iso_month(month):
            headers.append(str(month))
            continue
        label = MONTH_LABELS[int(month[5:7]) - 1]
        headers.append(label if len(years) <= 1 else f"{label} {month[:4]}")
    return headers


def _is_unavailable_code(item: dict[str, Any], monthly_values: list[Any]) -> bool:
    if item.get("available") is False:
        return True
    status = str(item.get("production_status") or item.get("source_status") or item.get("forecast_type") or "").strip()
    if status in UNAVAILABLE_EXPORT_STATUSES:
        return True
    if str(item.get("display_status") or "").strip().casefold() == "forecast unavailable":
        return True
    if any(is_finite_amount(value) for value in monthly_values):
        return False
    return True


def _monthly_map_from_account_rows(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    mapped: dict[str, dict[str, float]] = {}
    for row in payload.get("account_monthly_forecasts") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("budget_code") or "")
        month = str(row.get("month") or "")
        if not code or not is_iso_month(month) or not is_finite_amount(row.get("forecast_amount")):
            continue
        mapped.setdefault(code, {})[month] = float(row["forecast_amount"])
    return mapped


def _code_metadata(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for row in payload.get("account_monthly_forecasts") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("budget_code") or "")
        if not code:
            continue
        current = metadata.setdefault(code, {})
        if row.get("account_name") and not current.get("account_name"):
            current["account_name"] = str(row["account_name"])
        if row.get("category") and not current.get("category"):
            current["category"] = str(row["category"])
    for item in payload.get("budget_code_forecasts") or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("budget_code") or "")
        if not code:
            continue
        current = metadata.setdefault(code, {})
        current.update({key: item.get(key) for key in item})
        if item.get("account_name"):
            current["account_name"] = str(item["account_name"])
        if item.get("category"):
            current["category"] = str(item["category"])
    return metadata


def _resolve_category(item: dict[str, Any], report_category: str) -> str:
    category = str(item.get("category") or "").strip()
    if category and not is_all_categories_label(category):
        return category
    if report_category and not is_all_categories_label(report_category):
        return report_category
    return category if category and not is_all_categories_label(category) else ""


def _month_values_for_code(item: dict[str, Any], months: list[str], account_map: dict[str, float]) -> list[float | None]:
    forecast_map = item.get("forecast") if isinstance(item.get("forecast"), dict) else {}
    values: list[float | None] = []
    for month in months:
        raw = None
        if month in forecast_map:
            raw = forecast_map[month]
        elif month in account_map:
            raw = account_map[month]
        values.append(float(raw) if is_finite_amount(raw) else None)
    return values


def _optional_amount(value: Any) -> float | None:
    if is_finite_amount(value):
        return float(value)
    return None


def _reason_label(item: dict[str, Any]) -> str:
    code = str(item.get("reason_code") or item.get("reason") or "").strip()
    labels = {
        "BOOK_ENTRY_ACCOUNT": "Book Entry Account",
        "INSUFFICIENT_HISTORICAL_DATA": "Insufficient Historical Data",
        "NO_VALID_MODEL": "No Valid Model",
    }
    if code in labels:
        return labels[code]
    status = str(item.get("source_status") or item.get("production_status") or "").strip()
    if status == "NO_POSITIVE_YEAR_MEAN":
        return "Preprocessing policy decision required"
    if status in {"NO_DATA", "ALL_MISSING", "NOT_EVALUABLE", "UNRESOLVED_MISSING", "INSUFFICIENT_HISTORY"}:
        return "Insufficient Historical Data"
    if status == "NO_VALID_WINNER":
        return "No Valid Model"
    label = str(item.get("reason_label") or "").strip()
    if label:
        return label
    text = str(item.get("reason") or "").strip()
    return text or "Forecast unavailable"


def _exclusion_bucket(item: dict[str, Any]) -> str:
    stored = str(item.get("exclusion") or "").strip()
    if stored in {"book_entry", "insufficient", "other"}:
        return stored
    label = _reason_label(item)
    if label == "Book Entry Account":
        return "book_entry"
    if label == "Insufficient Historical Data":
        return "insufficient"
    return "other"


def _book_entry_codes() -> set[str]:
    engine = Path(__file__).resolve().parents[2] / "model_training_engine"
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    from preprocessing import BookEntryConfigurationError, book_entry_account_codes
    try:
        return book_entry_account_codes()
    except BookEntryConfigurationError as error:
        raise ReportError(str(error)) from error


def coverage_export(payload: dict[str, Any]) -> dict[str, Any]:
    """Read the forecast response coverage. Counts are not recalculated with a second rule."""
    blocked = _book_entry_codes()
    selected = [str(code) for code in payload.get("selected_accounts") or [] if str(code)]
    metadata = _code_metadata(payload)
    supplied = payload.get("unavailable_accounts")
    if not isinstance(supplied, list):
        detailed = payload.get("forecast_coverage") if isinstance(payload.get("forecast_coverage"), dict) else {}
        supplied = detailed.get("unavailable_accounts") if isinstance(detailed.get("unavailable_accounts"), list) else []
    rows = []
    seen: set[str] = set()
    for item in supplied:
        if not isinstance(item, dict):
            continue
        code = str(item.get("budget_code") or item.get("account_code") or "")
        if not code or code not in set(selected) or code in seen:
            continue
        seen.add(code)
        source = dict(metadata.get(code) or {})
        source.update(item)
        category = str(source.get("category") or "").strip()
        rows.append({
            "budget_code": code,
            "account_name": str(source.get("budget_name") or source.get("account_name") or ""),
            "category": category,
            "status": "UNAVAILABLE",
            "reason": _reason_label(source),
            "exclusion": _exclusion_bucket(source),
        })
    for code in selected:
        if code in seen:
            continue
        item = dict(metadata.get(code) or {})
        book_entry = code in blocked or str(item.get("reason") or "") == "BOOK_ENTRY_ACCOUNT" or str(item.get("reason_code") or "") == "BOOK_ENTRY_ACCOUNT"
        values = _month_values_for_code(item, export_month_isos(payload), {})
        if not book_entry and not _is_unavailable_code(item, values):
            continue
        if book_entry:
            item = {**item, "reason_code": "BOOK_ENTRY_ACCOUNT", "reason": "BOOK_ENTRY_ACCOUNT"}
        seen.add(code)
        rows.append({
            "budget_code": code,
            "account_name": str(item.get("budget_name") or item.get("account_name") or ""),
            "category": str(item.get("category") or "").strip(),
            "status": "UNAVAILABLE",
            "reason": _reason_label(item),
            "exclusion": _exclusion_bucket(item),
        })
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    detailed = payload.get("forecast_coverage") if isinstance(payload.get("forecast_coverage"), dict) else {}
    source = {**detailed, **coverage}
    has_split = any(key in source for key in ("book_entry_excluded", "insufficient_data", "other_unavailable", "forecasted_codes"))
    if has_split:
        total = int(source.get("total_selected_codes") or source.get("total_budget_codes") or source.get("requested_account_count") or len(selected))
        forecasted = int(source.get("forecasted_codes") if source.get("forecasted_codes") is not None else source.get("forecasted_budget_codes") or 0)
        book_entry = int(source.get("book_entry_excluded") or 0)
        insufficient = int(source.get("insufficient_data") or 0)
        other = int(source.get("other_unavailable") or 0)
    else:
        book_entry = sum(1 for row in rows if row["exclusion"] == "book_entry")
        insufficient = sum(1 for row in rows if row["exclusion"] == "insufficient")
        other = sum(1 for row in rows if row["exclusion"] == "other")
        total = len(selected)
        forecasted = max(0, total - book_entry - insufficient - other)
    return {
        "rows": [
            ["Total Budget Codes in Selection", total],
            ["Successfully Forecasted", forecasted],
            ["Book Entry Accounts Excluded", book_entry],
            ["Unavailable - Insufficient Historical Data", insufficient],
            ["Other Unavailable Accounts", other],
        ],
        "total": total,
        "forecasted": forecasted,
        "book_entry": book_entry,
        "insufficient": insufficient,
        "other": other,
        "unavailable_accounts": rows,
        "forecast_available": forecasted > 0,
    }


def build_excel_forecast_model(payload: dict[str, Any]) -> dict[str, Any]:
    months = export_month_isos(payload)
    selected = [str(code) for code in payload.get("selected_accounts") or [] if str(code)]
    metadata = _code_metadata(payload)
    account_maps = _monthly_map_from_account_rows(payload)
    report_category = str(payload.get("category") or "")
    blocked = _book_entry_codes()
    catalog: list[dict[str, Any]] = []
    for code in selected:
        item = dict(metadata.get(code) or {})
        if code in blocked or str(item.get("reason") or "") == "BOOK_ENTRY_ACCOUNT" or str(item.get("reason_code") or "") == "BOOK_ENTRY_ACCOUNT":
            continue
        values = _month_values_for_code(item, months, account_maps.get(code) or {})
        if _is_unavailable_code(item, values):
            continue
        zero_policy = (
            str(item.get("forecast_type") or "") == "ZERO_POLICY"
            or str(item.get("production_status") or "") == "ZERO_POLICY"
            or str(item.get("display_status") or "") == "Zero Policy"
        )
        finite = [float(value) for value in values if is_finite_amount(value)]
        catalog.append({
            "budget_code": code,
            "account_name": str(item.get("account_name") or ""),
            "category": _resolve_category(item, report_category),
            "months": values,
            "ytd": float(sum(finite)),
            "unavailable": False,
            "zero_policy": zero_policy,
            "source_months": dict(account_maps.get(code) or {}),
        })
    sections: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in catalog:
        category = row["category"]
        if category not in grouped:
            order.append(category)
            grouped[category] = []
        grouped[category].append(row)
    for category in order:
        rows = grouped[category]
        totals: list[float | None] = []
        for index in range(len(months)):
            amounts = [float(row["months"][index]) for row in rows if is_finite_amount(row["months"][index])]
            totals.append(float(sum(amounts)) if amounts else None)
        finite_totals = [float(value) for value in totals if is_finite_amount(value)]
        sections.append({
            "category": category,
            "rows": rows,
            "totals": totals,
            "total_ytd": float(sum(finite_totals)) if finite_totals else None,
        })
    grand_totals: list[float | None] = []
    for index in range(len(months)):
        amounts = [
            float(section["totals"][index])
            for section in sections
            if index < len(section.get("totals") or []) and is_finite_amount(section["totals"][index])
        ]
        grand_totals.append(float(sum(amounts)) if amounts else None)
    finite_grand = [float(value) for value in grand_totals if is_finite_amount(value)]
    start = str(payload.get("requested_start_month") or (months[0] if months else ""))
    end = str(payload.get("requested_end_month") or (months[-1] if months else ""))
    coverage = coverage_export(payload)
    return {
        "title": excel_report_title(start, end),
        "filename_stem": excel_filename_stem(payload),
        "months": months,
        "month_headers": month_column_headers(months),
        "sections": sections,
        "grand_totals": grand_totals,
        "grand_total_ytd": float(sum(finite_grand)) if finite_grand else None,
        "coverage_rows": coverage["rows"],
        "unavailable_accounts": coverage["unavailable_accounts"],
        "forecast_available": bool(sections),
        "no_forecast_message": "No forecast available for the selected category.",
        "no_unavailable_message": "No excluded or unavailable budget codes.",
    }


def format_generated_at(value: Any) -> str:
    if isinstance(value, datetime):
        stamp = value
    else:
        text = str(value or "").replace("Z", "+00:00")
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            return str(value or "—")
    return stamp.strftime("%d %b %Y")


def expected_bound(value: Any) -> float | str:
    if is_finite_amount(value):
        return float(value)
    return UNAVAILABLE


def month_over_month_change(current: Any, previous: Any) -> float | None:
    if not is_finite_amount(current) or not is_finite_amount(previous):
        return None
    previous_amount = float(previous)
    if previous_amount == 0:
        return None
    return ((float(current) - previous_amount) / previous_amount) * 100


def _mean(values: list[float]) -> float | None:
    finite = [value for value in values if is_finite_amount(value)]
    if not finite:
        return None
    return sum(finite) / len(finite)


def series_direction(values: list[float]) -> dict[str, Any]:
    finite = [float(value) for value in values if is_finite_amount(value)]
    if len(finite) < 2:
        return {"direction": "insufficient", "percent": None, "label": "Not enough months"}
    first = finite[0]
    last = finite[-1]
    if first == 0:
        if last == 0:
            return {"direction": "stable", "percent": 0.0, "label": "Stable"}
        return {
            "direction": "up" if last > 0 else "down",
            "percent": None,
            "label": "Rising" if last > 0 else "Falling",
        }
    percent = ((last - first) / abs(first)) * 100
    if abs(percent) < 0.5:
        return {"direction": "stable", "percent": percent, "label": "Stable"}
    if percent > 0:
        return {"direction": "up", "percent": percent, "label": "Rising"}
    return {"direction": "down", "percent": percent, "label": "Falling"}


def merge_historical_actuals(*groups: Any) -> list[dict[str, Any]]:
    by_month: dict[str, float] = {}
    for rows in groups:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            month = str(row.get("month") or "")
            amount = row.get("actual_amount")
            if not is_iso_month(month) or not is_finite_amount(amount) or month in by_month:
                continue
            by_month[month] = float(amount)
    return [{"month": month, "actual_amount": by_month[month]} for month in sorted(by_month)]


def historical_periods_for_forecast_month(
    forecast_month: str,
    actuals_by_month: dict[str, float],
    history_end: str | None,
    history_start: str | None = None,
) -> list[dict[str, Any]]:
    if not is_iso_month(forecast_month):
        return []
    target = forecast_month[5:7]
    periods = []
    for month, actual in actuals_by_month.items():
        if not is_iso_month(month) or month[5:7] != target:
            continue
        if month >= forecast_month:
            continue
        if history_start and month < history_start:
            continue
        if history_end and month > history_end:
            continue
        if not is_finite_amount(actual):
            continue
        periods.append({"month": month, "actual": float(actual)})
    periods.sort(key=lambda row: row["month"])
    return periods


def average_from_periods(periods: list[dict[str, Any]]) -> float | None:
    if not periods:
        return None
    total = sum(float(row["actual"]) for row in periods)
    if not is_finite_amount(total):
        return None
    return total / len(periods)


def contributing_forecast_codes(payload: dict[str, Any]) -> list[str]:
    """Codes with at least one finite forecast amount (zeros/negatives kept)."""
    codes: set[str] = set()
    for row in payload.get("account_monthly_forecasts") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("budget_code") or "").strip()
        if code and is_finite_amount(row.get("forecast_amount")):
            codes.add(code)
    return sorted(codes)


def account_forecasts_by_month(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    by_month: dict[str, dict[str, float]] = {}
    for row in payload.get("account_monthly_forecasts") or []:
        if not isinstance(row, dict):
            continue
        month = str(row.get("month") or "")
        code = str(row.get("budget_code") or "").strip()
        if not is_iso_month(month) or not code or not is_finite_amount(row.get("forecast_amount")):
            continue
        by_month.setdefault(month, {})[code] = float(row["forecast_amount"])
    return by_month


def load_confirmed_code_actuals(budget_codes: list[str]) -> dict[str, dict[str, float]]:
    """Confirmed DB actuals keyed code → month → finite amount. Missing stays absent."""
    try:
        from app.config import AMOUNT_CONVERSION_FACTOR
        from app.services.dataset_repository import list_historical_actuals_for_codes
    except Exception:
        return {}
    try:
        rows = list_historical_actuals_for_codes(budget_codes)
    except Exception:
        return {}
    factor = float(AMOUNT_CONVERSION_FACTOR)
    by_code: dict[str, dict[str, float]] = {}
    for row in rows:
        code = str(row.get("budget_code") or "").strip()
        month = str(row.get("month") or "").strip()
        if not code or not is_iso_month(month) or not is_finite_amount(row.get("amount")):
            continue
        by_code.setdefault(code, {})[month] = float(row["amount"]) * factor
    return by_code


def forecast_distribution_by_code(payload: dict[str, Any]) -> list[dict[str, Any]]:
    selected = [str(code) for code in payload.get("selected_accounts") or [] if str(code)]
    allowed = set(selected)
    totals: dict[str, dict[str, Any]] = {}
    for row in payload.get("account_monthly_forecasts") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("budget_code") or "")
        if not code or (allowed and code not in allowed) or not is_finite_amount(row.get("forecast_amount")):
            continue
        current = totals.get(code) or {"budget_code": code, "account_name": "", "forecast_amount": 0.0}
        current["forecast_amount"] += float(row["forecast_amount"])
        if row.get("account_name"):
            current["account_name"] = str(row["account_name"])
        totals[code] = current
    items = sorted(
        totals.values(),
        key=lambda row: (-float(row["forecast_amount"]), str(row["budget_code"])),
    )
    total = sum(float(row["forecast_amount"]) for row in items)
    ranked = []
    for index, row in enumerate(items, start=1):
        ranked.append({
            **row,
            "percent": 0.0 if total == 0 else float(row["forecast_amount"]) / total,
            "total": total,
            "rank": index,
        })
    return ranked


def _growth_sentence(percent: float | None) -> str:
    if not is_finite_amount(percent):
        return ""
    value = float(percent)
    if abs(value) < 0.5:
        return "Predicted monthly budget is in line with the historical monthly average."
    share = f"{abs(value):.1f}%"
    if value > 0:
        return f"Predicted monthly budget is {share} above the historical monthly average."
    return f"Predicted monthly budget is {share} below the historical monthly average."


def _trend_sentence(trend: dict[str, Any] | None) -> str:
    label = (trend or {}).get("label")
    if label == "Rising":
        return "Predicted monthly amounts are rising across the selected period."
    if label == "Falling":
        return "Predicted monthly amounts are falling across the selected period."
    if label == "Stable":
        return "Predicted monthly amounts are stable across the selected period."
    return ""


def build_budget_trend(payload: dict[str, Any]) -> dict[str, Any]:
    actuals = merge_historical_actuals(payload.get("historical_actuals"))
    forecasts = chronological_forecasts(payload)
    actual_months = {row["month"] for row in actuals}
    historical_values = [float(row["actual_amount"]) for row in actuals]
    forecast_values = [
        float(row["forecast_amount"])
        for row in forecasts
        if is_iso_month(row.get("month"))
        and str(row["month"]) not in actual_months
        and is_finite_amount(row.get("forecast_amount"))
    ]
    historical_average = _mean(historical_values)
    if is_finite_amount(payload.get("monthly_average")):
        forecast_average = float(payload["monthly_average"])
    else:
        forecast_average = _mean(forecast_values)
    difference = (
        None
        if historical_average is None or forecast_average is None
        else forecast_average - historical_average
    )
    difference_percent = (
        None
        if difference is None or historical_average in (None, 0)
        else (difference / historical_average) * 100
    )
    return {
        "historical_average": historical_average,
        "forecast_average": forecast_average,
        "difference": difference,
        "difference_percent": difference_percent,
        "forecast_trend": series_direction(forecast_values),
    }


def build_forecast_summary(payload: dict[str, Any]) -> dict[str, Any]:
    months = [row for row in chronological_forecasts(payload) if is_finite_amount(row.get("forecast_amount"))]
    trend = build_budget_trend(payload)
    codes = forecast_distribution_by_code(payload)
    peak = min(months, key=lambda row: (-float(row["forecast_amount"]), str(row["month"])), default=None)
    trough = min(months, key=lambda row: (float(row["forecast_amount"]), str(row["month"])), default=None)
    expected_range_months = sum(
        1
        for row in months
        if is_finite_amount(row.get("lower_bound")) and is_finite_amount(row.get("upper_bound"))
    )
    insights: list[str] = []
    if peak and peak.get("month"):
        insights.append(f"The highest predicted month is {iso_to_label(str(peak['month']))}.")
    growth = _growth_sentence(trend["difference_percent"])
    if growth:
        insights.append(growth)
    direction = _trend_sentence(trend["forecast_trend"])
    if direction:
        insights.append(direction)
    if codes:
        share = f"{float(codes[0]['percent']) * 100:.1f}%"
        name = f" {codes[0]['account_name']}" if codes[0].get("account_name") else ""
        insights.append(f"{codes[0]['budget_code']}{name} accounts for {share} of the predicted total.")
    if expected_range_months > 0 and expected_range_months < len(months):
        insights.append(
            f"An expected range is available for {expected_range_months} of {len(months)} predicted months."
        )
    elif expected_range_months == len(months) and months:
        insights.append("An expected range is available for every predicted month.")
    return {
        "period": period_label(payload),
        "month_count": len(months),
        "overall_total": _optional_amount(payload.get("overall_total")),
        "monthly_average": _optional_amount(payload.get("monthly_average")),
        "minimum_monthly_forecast": _optional_amount(payload.get("minimum_monthly_forecast")),
        "maximum_monthly_forecast": _optional_amount(payload.get("maximum_monthly_forecast")),
        "peak_month": str(peak["month"]) if peak else "",
        "trough_month": str(trough["month"]) if trough else "",
        "historical_average": trend["historical_average"],
        "difference": trend["difference"],
        "difference_percent": trend["difference_percent"],
        "forecast_trend": trend["forecast_trend"],
        "top_code": codes[0] if codes else None,
        "code_count": len(codes),
        "expected_range_months": expected_range_months,
        "insights": insights,
    }


def build_forecast_comparison(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Same-calendar-month comparison on matched contributing Budget Codes.

    Prefer confirmed code-level actuals for codes that contribute finite forecast
    amounts. Fall back to sparse forecast-attached aggregates only when confirmed
    code actuals are unavailable.
    """
    history_end = str(payload.get("history_end") or payload.get("historical_end") or "") or None
    history_start = str(payload.get("comparison_history_start") or "") or None
    codes = contributing_forecast_codes(payload)
    by_code = load_confirmed_code_actuals(codes) if codes else {}
    forecasts_by_month = account_forecasts_by_month(payload)

    if by_code:
        rows: list[dict[str, Any]] = []
        for row in chronological_forecasts(payload):
            month = str(row.get("month") or "")
            if not is_iso_month(month) or not is_finite_amount(row.get("forecast_amount")):
                continue
            forecast_amount = float(row["forecast_amount"])
            code_forecasts = forecasts_by_month.get(month) or {}
            matched_forecast = 0.0
            matched_historical = 0.0
            matched_code_count = 0
            period_sums: dict[str, dict[str, float | int]] = {}
            for code, code_forecast in code_forecasts.items():
                periods = historical_periods_for_forecast_month(
                    month,
                    by_code.get(code) or {},
                    history_end,
                    history_start,
                )
                baseline = average_from_periods(periods)
                if baseline is None:
                    continue
                matched_forecast += float(code_forecast)
                matched_historical += float(baseline)
                matched_code_count += 1
                for period in periods:
                    bucket = period_sums.setdefault(period["month"], {"actual": 0.0, "code_count": 0})
                    bucket["actual"] = float(bucket["actual"]) + float(period["actual"])
                    bucket["code_count"] = int(bucket["code_count"]) + 1
            has_match = matched_code_count > 0
            matched_forecast_amount = matched_forecast if has_match else None
            historical_average = matched_historical if has_match else None
            difference = None if not has_match else matched_forecast - matched_historical
            difference_percent = (
                None
                if not has_match or matched_historical == 0 or difference is None
                else (difference / matched_historical) * 100
            )
            periods_used = [
                {
                    "month": period_month,
                    "actual": float(info["actual"]),
                    "code_count": int(info["code_count"]),
                }
                for period_month, info in sorted(period_sums.items())
            ]
            rows.append({
                "month": month,
                "forecast_amount": forecast_amount,
                "matched_forecast_amount": matched_forecast_amount,
                "historical_average": historical_average,
                "difference": difference,
                "difference_percent": difference_percent,
                "observation_count": len(periods_used),
                "matched_code_count": matched_code_count,
                "historical_periods_used": periods_used,
                "comparison_scope": "code_matched",
            })
        return rows

    # Fallback: sparse forecast-attached category aggregates (legacy).
    actuals = merge_historical_actuals(payload.get("historical_actuals"))
    actuals_by_month = {row["month"]: float(row["actual_amount"]) for row in actuals}
    rows = []
    for row in chronological_forecasts(payload):
        month = str(row.get("month") or "")
        if not is_iso_month(month) or not is_finite_amount(row.get("forecast_amount")):
            continue
        periods = historical_periods_for_forecast_month(month, actuals_by_month, history_end, history_start)
        historical_average = average_from_periods(periods)
        forecast_amount = float(row["forecast_amount"])
        matched_forecast_amount = None if historical_average is None else forecast_amount
        difference = None if historical_average is None else forecast_amount - historical_average
        difference_percent = (
            None
            if historical_average in (None, 0) or difference is None
            else (difference / historical_average) * 100
        )
        rows.append({
            "month": month,
            "forecast_amount": forecast_amount,
            "matched_forecast_amount": matched_forecast_amount,
            "historical_average": historical_average,
            "difference": difference,
            "difference_percent": difference_percent,
            "observation_count": len(periods),
            "matched_code_count": 0,
            "historical_periods_used": periods,
            "comparison_scope": "aggregate",
        })
    return rows


def monthly_report_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    previous = None
    for row in chronological_forecasts(payload):
        amount = row.get("forecast_amount")
        rows.append({
            "month": str(row["month"]),
            "forecast_amount": float(amount) if is_finite_amount(amount) else None,
            "lower_bound": expected_bound(row.get("lower_bound")),
            "upper_bound": expected_bound(row.get("upper_bound")),
            "change_percent": month_over_month_change(amount, previous),
        })
        previous = amount if is_finite_amount(amount) else None
    return rows


def require_completed_forecast(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        raise ReportError("No completed forecast selected.")
    if payload.get("book_entry_recalculation_blocked"):
        raise ReportError(
            "This saved forecast includes Book Entry amounts that cannot be separated from the combined totals. "
            "The export was not created."
        )
    selected = [str(code) for code in payload.get("selected_accounts") or [] if str(code)]
    if not selected:
        raise ReportError("Only a completed forecast can be exported.")
    months = chronological_forecasts(payload)
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    coverage_only = bool(payload.get("coverage_only")) or (
        not months and int(coverage.get("forecasted_codes") or 0) == 0 and bool(payload.get("unavailable_accounts"))
    )
    if not months and not coverage_only:
        raise ReportError("Only a completed forecast can be exported.")
    if payload.get("overall_total") is None and not coverage_only:
        raise ReportError("Only a completed forecast can be exported.")
    return payload


def build_forecast_report(payload: dict[str, Any] | None) -> dict[str, Any]:
    stored = require_completed_forecast(payload)
    summary = build_forecast_summary(stored)
    codes = forecast_distribution_by_code(stored)
    monthly = monthly_report_rows(stored)
    comparison = build_forecast_comparison(stored)
    selected = [str(code) for code in stored.get("selected_accounts") or [] if str(code)]
    excel_model = build_excel_forecast_model(stored)
    return {
        "title": "AI Budget Forecast Report",
        "filename_stem": report_filename_stem(stored),
        "header": {
            "period": summary["period"],
            "generated_date": format_generated_at(stored.get("generated_at")),
            "category": str(stored.get("category") or ""),
            "forecast_month_count": int(stored.get("forecast_month_count") or summary["month_count"]),
            "selected_account_count": int(stored.get("selected_account_count") or len(selected)),
        },
        "summary": summary,
        "insights": list(summary["insights"]),
        "monthly_forecasts": monthly,
        "budget_codes": codes,
        "selected_accounts": selected,
        "comparison": comparison,
        "account_monthly_forecasts": list(stored.get("account_monthly_forecasts") or []),
        "account_yearly_forecasts": list(stored.get("account_yearly_forecasts") or []),
        "combined_yearly_forecasts": list(stored.get("combined_yearly_forecasts") or []),
        "overall_total": _optional_amount(stored.get("overall_total")),
        "monthly_average": _optional_amount(stored.get("monthly_average")),
        "amount_unit": str(stored.get("amount_unit") or "LKR_MILLIONS"),
        "chart_months": chronological_forecasts(stored),
        "excel_forecast": excel_model,
        "excel_filename_stem": excel_filename_stem(stored),
        "coverage_rows": excel_model["coverage_rows"],
        "unavailable_accounts": excel_model["unavailable_accounts"],
        "forecast_available": excel_model["forecast_available"],
        "no_forecast_message": excel_model["no_forecast_message"],
        "no_unavailable_message": excel_model["no_unavailable_message"],
    }
