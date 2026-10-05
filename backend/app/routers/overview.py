"""Read-only Overview endpoints for category historical actuals."""

from __future__ import annotations

import calendar
import re
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import (
    AMOUNT_CONVERSION_FACTOR,
    BACKEND_DIR,
    DATASET_STORAGE_DIR,
    HISTORICAL_MASTER_DATASET_PATH,
    MANAGED_MASTER_FILENAME,
    RESPONSE_AMOUNT_UNIT,
)
from app.db import DatabaseError, list_forecast_records
from app.schemas.forecast import ForecastRecordSummary
from app.services.model_loader import get_model_bundle

ENGINE_DIR = BACKEND_DIR / "model_training_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from preprocessing import (  # noqa: E402
    _load_original_excel,
    assign_categories,
    detect_monthly_columns,
)

DEFAULT_CATEGORY_ID = "international_settlement"
DEFAULT_CATEGORY_NAME = "International Settlement"
RECURRING_COVERAGE = 0.75

router = APIRouter(prefix="/api/overview", tags=["overview"])

_workbook: dict | None = None
_categories: list[dict] | None = None
_historical_cache: dict[str, dict] = {}
_analysis_cache: dict[str, dict] = {}


def invalidate_overview_cache() -> None:
    global _workbook, _categories, _historical_cache, _analysis_cache
    _workbook = None
    _categories = None
    _historical_cache = {}
    _analysis_cache = {}


class OverviewDataError(Exception):
    pass


class OverviewNotFoundError(OverviewDataError):
    pass


class OverviewCategory(BaseModel):
    id: str
    name: str


class OverviewCategoriesResponse(BaseModel):
    categories: list[OverviewCategory]
    default_category: str


class MonthlyActualItem(BaseModel):
    month: str
    actual_amount: Optional[float] = None


class BudgetCodeActualItem(BaseModel):
    budget_code: str
    account_name: str
    actual_amount: float


class OverviewHistoricalResponse(BaseModel):
    category: OverviewCategory
    history_start: str
    history_end: str
    latest_complete_year: Optional[int] = None
    latest_year: int
    latest_year_is_partial: bool
    latest_actual_month: str
    amount_unit: str
    conversion_factor: float
    latest_year_monthly_actuals: list[MonthlyActualItem]
    latest_month_budget_codes: list[BudgetCodeActualItem]
    latest_complete_year_budget_codes: list[BudgetCodeActualItem] = []
    historical_data_partial: bool = False
    historical_missing_budget_codes: list[str] = []
    historical_missing_budget_code_count: int = 0


class OverviewForecastActivityResponse(BaseModel):
    latest_forecast_summary: Optional[ForecastRecordSummary] = None
    recent_forecasting_activities: list[ForecastRecordSummary]


class YearlyActualItem(BaseModel):
    year: int
    actual_amount: float
    months_included: int
    year_status: str
    growth_percent: Optional[float] = None


class SeasonalActualItem(BaseModel):
    calendar_month: int
    month_label: str
    average_amount: Optional[float] = None
    observation_count: int


class BudgetCodeHistoryItem(BaseModel):
    budget_code: str
    account_name: str
    actual_amount: float
    months_present: int
    history_month_count: int
    recurring: bool


class OverviewAnalysisResponse(BaseModel):
    category: OverviewCategory
    history_start: str
    history_end: str
    latest_complete_year: Optional[int] = None
    latest_year: int
    latest_year_is_partial: bool
    latest_actual_month: str
    amount_unit: str
    conversion_factor: float
    overall_total: float
    monthly_average: Optional[float] = None
    latest_year_growth_percent: Optional[float] = None
    complete_month_count: int
    budget_code_count: int
    recurring_count: int
    monthly_actuals: list[MonthlyActualItem]
    yearly_actuals: list[YearlyActualItem]
    seasonal_profile: list[SeasonalActualItem]
    budget_codes: list[BudgetCodeHistoryItem]
    historical_data_partial: bool = False
    historical_missing_budget_codes: list[str] = []
    historical_missing_budget_code_count: int = 0


def _is_international_settlement(name: str) -> bool:
    text = str(name or "").casefold()
    return "int" in text and "settlement" in text


def _category_slug(name: str) -> str:
    text = str(name or "").strip()
    if _is_international_settlement(text):
        return DEFAULT_CATEGORY_ID
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_") or "category"


def _display_category_name(name: str) -> str:
    text = str(name or "").strip()
    return DEFAULT_CATEGORY_NAME if _is_international_settlement(text) else text


def _iso_month(timestamp: pd.Timestamp) -> str:
    value = pd.Timestamp(timestamp).replace(day=1)
    return f"{int(value.year):04d}-{int(value.month):02d}"


def _period_scope(month_dates) -> dict:
    dates = sorted({pd.Timestamp(item).replace(day=1) for item in month_dates})
    if not dates:
        raise OverviewDataError("No historical months are available.")
    by_year: dict[int, list[pd.Timestamp]] = {}
    for timestamp in dates:
        by_year.setdefault(int(timestamp.year), []).append(timestamp)
    complete = [
        year
        for year, months in by_year.items()
        if {int(item.month) for item in months} == set(range(1, 13))
    ]
    if complete:
        trend_year = max(complete)
        latest_complete_year = trend_year
        is_partial = False
    else:
        trend_year = max(by_year)
        latest_complete_year = None
        is_partial = True
    history_start = _iso_month(dates[0])
    history_end = _iso_month(dates[-1])
    if history_start > history_end:
        raise OverviewDataError("History start is after history end.")
    return {
        "history_start": history_start,
        "history_end": history_end,
        "latest_complete_year": latest_complete_year,
        "latest_year": trend_year,
        "latest_year_is_partial": is_partial,
        "latest_actual_month": history_end,
        "year_months": [pd.Timestamp(year=trend_year, month=month, day=1) for month in range(1, 13)],
    }


def _complete_history_mask(monthly_values: pd.DataFrame) -> pd.Series:
    numeric = monthly_values.apply(pd.to_numeric, errors="coerce")
    if numeric.empty:
        return pd.Series(dtype=bool)
    return numeric.notna().all(axis=1) & np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1)


def _uses_production_accounts(category: dict) -> bool:
    return category.get("id") == DEFAULT_CATEGORY_ID or _is_international_settlement(
        category.get("source_name") or category.get("name") or ""
    )


def _resolve_original_actuals_path() -> Path:
    dataset_dir = BACKEND_DIR / "model_training_engine" / "dataset"
    candidates = [
        DATASET_STORAGE_DIR / MANAGED_MASTER_FILENAME,
        Path(HISTORICAL_MASTER_DATASET_PATH),
        dataset_dir / "historical_actuals_master.xlsx",
        dataset_dir / "original_actual_data.xlsx",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return Path(HISTORICAL_MASTER_DATASET_PATH)


def _record_category(bundle: dict, code: str) -> str:
    records = bundle.get("account_records") or bundle.get("account_models") or {}
    entry = records.get(code) or records.get(str(code)) or {}
    if isinstance(entry, dict):
        return str(entry.get("category") or "").strip()
    return ""


def _category_matches_record(category: dict, record_category: str) -> bool:
    source = str(category.get("source_name") or category.get("name") or "")
    left = source.strip()
    right = str(record_category or "").strip()
    if not left or not right:
        return False
    if left.casefold() == right.casefold():
        return True
    return _is_international_settlement(left) and _is_international_settlement(right)


def _production_selected_accounts(category: dict) -> list[str] | None:
    """Optional forecast-artifact codes for metadata only. Never required to render Overview."""
    bundle = get_model_bundle()
    if not isinstance(bundle, dict):
        return None
    codes = [str(code) for code in bundle.get("selected_accounts") or []]
    if not codes or len(set(codes)) != len(codes):
        return None
    filtered = []
    saw_record_category = False
    for code in codes:
        record_category = _record_category(bundle, code)
        if record_category:
            saw_record_category = True
            if _category_matches_record(category, record_category):
                filtered.append(code)
        elif _uses_production_accounts(category):
            filtered.append(code)
    if saw_record_category:
        return filtered
    if _uses_production_accounts(category):
        return filtered
    return None


def _select_category_codes(category: dict, all_numeric: pd.DataFrame) -> tuple[list[str], list[str]]:
    historical = [str(code) for code in all_numeric.index.tolist()]
    if not historical:
        raise OverviewDataError("The selected category has no historical actuals.")
    production = _production_selected_accounts(category) or []
    available = set(historical)
    missing = [code for code in production if code not in available]
    return historical, missing


def _workbook_from_historical_rows(rows: list[dict], identity: dict | None = None) -> dict | None:
    identity = identity or {}
    codes_meta = [item for item in (identity.get("budget_codes") or []) if item.get("budget_code")]
    if not rows and not codes_meta:
        return None
    from app.services.dataset_parser import actuals_header_for_month
    from app.services.dataset_workflow import inclusive_months

    months = inclusive_months(
        identity.get("earliest_month"),
        identity.get("latest_month"),
        [str(item["month"]) for item in rows if item.get("month")],
    )
    codes: dict[str, dict] = {}
    for item in rows:
        code = str(item["budget_code"])
        bucket = codes.setdefault(
            code,
            {
                "ACT CODE": code,
                "ACT NAME": item.get("description") or code,
                "category": item.get("category") or "",
            },
        )
        header = actuals_header_for_month(str(item["month"]))
        bucket[header] = item.get("amount")
        if item.get("description"):
            bucket["ACT NAME"] = item["description"]
        if item.get("category"):
            bucket["category"] = item["category"]
    for item in codes_meta:
        code = str(item["budget_code"])
        bucket = codes.setdefault(
            code,
            {
                "ACT CODE": code,
                "ACT NAME": item.get("description") or code,
                "category": item.get("category") or "",
            },
        )
        if item.get("description"):
            bucket["ACT NAME"] = item["description"]
        if item.get("category"):
            bucket["category"] = item["category"]
    if not codes or not months:
        return None
    frame = pd.DataFrame(list(codes.values()))
    for month in months:
        header = actuals_header_for_month(month)
        if header not in frame.columns:
            frame[header] = None
    assigned = assign_categories(frame, "ACT CODE", "ACT NAME")
    if "category" in frame.columns:
        assigned["category"] = frame["category"].where(frame["category"].astype(str).str.strip() != "", assigned["category"])
        assigned["is_category_row"] = False
    from app.services.dataset_parser import detect_upload_month_columns

    monthly_columns, month_dates, _errors = detect_upload_month_columns(assigned.columns.tolist())
    if not monthly_columns:
        return None
    return {
        "df": assigned,
        "account_code_col": "ACT CODE",
        "account_name_col": "ACT NAME",
        "assigned": assigned,
        "monthly_columns": list(monthly_columns),
        "month_dates": [pd.Timestamp(item).replace(day=1) for item in month_dates],
        "sheet_name": "historical_actuals",
        "header_row_idx": 0,
    }


def _load_workbook() -> dict:
    global _workbook
    if _workbook is not None:
        return _workbook
    try:
        from app.services.dataset_repository import list_historical_actuals, load_master_identity

        rows = list_historical_actuals()
        try:
            identity = load_master_identity()
        except Exception:
            identity = {"budget_codes": [], "earliest_month": None, "latest_month": None}
        stored = _workbook_from_historical_rows(rows, identity)
        if stored is not None:
            _workbook = stored
            return _workbook
    except Exception:
        pass
    actuals_path = _resolve_original_actuals_path()
    if not actuals_path.exists():
        raise OverviewDataError(f"Historical Actuals workbook was not found: {actuals_path}")
    loaded = _load_original_excel(str(actuals_path))
    assigned = assign_categories(loaded["df"], loaded["account_code_col"], loaded["account_name_col"])
    monthly_columns, month_dates = detect_monthly_columns(assigned.columns.tolist())
    _workbook = {
        **loaded,
        "assigned": assigned,
        "monthly_columns": list(monthly_columns),
        "month_dates": [pd.Timestamp(item).replace(day=1) for item in month_dates],
    }
    return _workbook


def _list_categories() -> dict:
    global _categories
    if _categories is None:
        assigned = _load_workbook()["assigned"]
        names = []
        seen = set()
        for raw in assigned.loc[~assigned["is_category_row"], "category"].dropna():
            name = str(raw).strip()
            ident = _category_slug(name)
            if not name or ident in seen:
                continue
            seen.add(ident)
            names.append({"id": ident, "name": _display_category_name(name), "source_name": name})
        if not names:
            raise OverviewDataError("No categories were found in the historical Actuals workbook.")
        _categories = names
    default = next((item["id"] for item in _categories if item["id"] == DEFAULT_CATEGORY_ID), _categories[0]["id"])
    return {
        "categories": [{"id": item["id"], "name": item["name"]} for item in _categories],
        "default_category": default,
    }


def _resolve_category(requested: str | None) -> dict:
    catalog = _list_categories()
    if not requested:
        ident = catalog["default_category"]
    else:
        ident = _category_slug(requested)
        known = {item["id"] for item in catalog["categories"]}
        if ident not in known:
            match = next(
                (
                    item
                    for item in catalog["categories"]
                    if item["name"].casefold() == requested.strip().casefold() or item["id"] == requested.strip()
                ),
                None,
            )
            if match is None:
                raise OverviewNotFoundError(f"Unknown category: {requested}")
            ident = match["id"]
    return next(item for item in _categories if item["id"] == ident)


def _prepare_category_actuals(category: dict) -> dict:
    workbook = _load_workbook()
    account_code_col = workbook["account_code_col"]
    account_name_col = workbook["account_name_col"]
    monthly_columns = list(workbook["monthly_columns"])
    month_dates = [pd.Timestamp(item).replace(day=1) for item in workbook["month_dates"]]
    if not month_dates:
        raise OverviewDataError("No historical months are available.")

    scope = _period_scope(month_dates)
    date_to_column = {date: column for date, column in zip(month_dates, monthly_columns)}
    year_columns = [date_to_column[date] for date in scope["year_months"] if date in date_to_column]
    if not year_columns:
        raise OverviewDataError("The selected category has no historical actuals for the latest year.")
    latest_iso = scope["latest_actual_month"]
    latest_column = date_to_column.get(pd.Timestamp(f"{latest_iso}-01"))
    if latest_column is None:
        raise OverviewDataError("Latest actual month is missing from the historical data.")

    assigned = workbook["assigned"]
    accounts = assigned[
        (~assigned["is_category_row"])
        & assigned["numeric_account_code"].notna()
        & assigned[account_code_col].notna()
        & assigned["category"].notna()
        & (assigned["category"].astype(str).str.strip().str.casefold() == category["source_name"].strip().casefold())
    ].copy()
    if accounts.empty:
        raise OverviewDataError(f"No account rows were found for category {category['source_name']!r}.")

    history_values = accounts[monthly_columns].apply(pd.to_numeric, errors="coerce")
    history_values.index = accounts[account_code_col].astype(str)
    if history_values.index.has_duplicates:
        raise OverviewDataError(
            "Duplicate account codes exist in the selected category. "
            f"Codes: {sorted(history_values.index[history_values.index.duplicated()].astype(str).unique().tolist())}"
        )
    selected_codes, missing_production = _select_category_codes(category, history_values)
    if not selected_codes:
        raise OverviewDataError("The selected category has no historical actuals.")
    selected_all = history_values.loc[selected_codes]
    selected = selected_all[[column for column in year_columns if column in selected_all.columns]]
    names = {
        str(code): str(name or "")
        for code, name in zip(accounts[account_code_col].astype(str), accounts[account_name_col].fillna("").astype(str))
    }
    usable = accounts.loc[accounts[account_code_col].astype(str).isin(selected_codes)].copy()

    return {
        "category": category,
        "scope": scope,
        "date_to_column": date_to_column,
        "month_dates": month_dates,
        "selected_codes": selected_codes,
        "names": names,
        "selected_year": selected,
        "selected_all": selected_all,
        "usable": usable,
        "latest_iso": latest_iso,
        "latest_column": latest_column,
        "account_code_col": account_code_col,
        "historical_missing_budget_codes": list(missing_production),
    }


def _build_historical(category: dict) -> dict:
    prepared = _prepare_category_actuals(category)
    scope = prepared["scope"]
    selected = prepared["selected_year"]
    date_to_column = prepared["date_to_column"]
    selected_codes = prepared["selected_codes"]
    names = prepared["names"]
    latest_iso = prepared["latest_iso"]
    latest_column = prepared["latest_column"]
    selected_all = prepared["selected_all"]

    monthly_actuals = []
    for date in scope["year_months"]:
        iso = _iso_month(date)
        column = date_to_column.get(date)
        if column is None or column not in selected.columns:
            monthly_actuals.append({"month": iso, "actual_amount": None})
            continue
        column_values = pd.to_numeric(selected[column], errors="coerce")
        finite = column_values[np.isfinite(column_values.to_numpy(dtype=float))]
        if finite.empty:
            monthly_actuals.append({"month": iso, "actual_amount": None})
            continue
        total = float(finite.sum())
        if not np.isfinite(total):
            raise OverviewDataError("Monthly actuals contain a non-finite total.")
        monthly_actuals.append({"month": iso, "actual_amount": float(total * AMOUNT_CONVERSION_FACTOR)})

    if latest_column not in selected_all.columns:
        raise OverviewDataError("Latest actual month is missing from the historical data.")
    latest_amounts = pd.to_numeric(selected_all[latest_column], errors="coerce")

    budget_codes = []
    for code in selected_codes:
        amount = pd.to_numeric(latest_amounts.loc[code], errors="coerce")
        if pd.isna(amount) or not np.isfinite(float(amount)):
            continue
        budget_codes.append({
            "budget_code": code,
            "account_name": names.get(code) or "",
            "actual_amount": float(amount * AMOUNT_CONVERSION_FACTOR),
        })
    donut_total = float(sum(item["actual_amount"] for item in budget_codes))
    if not budget_codes or not np.isfinite(donut_total):
        raise OverviewDataError("The selected category has no historical actuals for the latest month.")

    return {
        "category": {"id": category["id"], "name": category["name"]},
        "history_start": scope["history_start"],
        "history_end": scope["history_end"],
        "latest_complete_year": scope["latest_complete_year"],
        "latest_year": int(scope["latest_year"]),
        "latest_year_is_partial": bool(scope["latest_year_is_partial"]),
        "latest_actual_month": latest_iso,
        "amount_unit": RESPONSE_AMOUNT_UNIT,
        "conversion_factor": float(AMOUNT_CONVERSION_FACTOR),
        "latest_year_monthly_actuals": monthly_actuals,
        "latest_month_budget_codes": budget_codes,
        "latest_complete_year_budget_codes": _complete_year_budget_codes(prepared, scope),
        **_historical_coverage_fields(prepared),
    }


def _complete_year_budget_codes(prepared: dict, scope: dict) -> list[dict]:
    """Sum each Budget Code across the latest complete actual year only."""
    if scope.get("latest_year_is_partial") or not scope.get("latest_complete_year"):
        return []
    selected = prepared["selected_year"]
    date_to_column = prepared["date_to_column"]
    names = prepared["names"]
    rows = []
    for code in prepared["selected_codes"]:
        total = 0.0
        found = False
        for date in scope["year_months"]:
            column = date_to_column.get(date)
            if column is None or column not in selected.columns or code not in selected.index:
                continue
            value = pd.to_numeric(selected.loc[code, column], errors="coerce")
            if pd.isna(value) or not np.isfinite(float(value)):
                continue
            total += float(value) * AMOUNT_CONVERSION_FACTOR
            found = True
        if not found or not np.isfinite(total):
            continue
        rows.append({
            "budget_code": str(code),
            "account_name": names.get(str(code)) or names.get(code) or "",
            "actual_amount": float(total),
        })
    rows.sort(key=lambda item: (-item["actual_amount"], item["budget_code"]))
    return rows


def _historical_coverage_fields(prepared: dict) -> dict:
    missing = [str(code) for code in (prepared.get("historical_missing_budget_codes") or [])]
    return {
        "historical_data_partial": bool(missing),
        "historical_missing_budget_codes": missing,
        "historical_missing_budget_code_count": len(missing),
    }


def _like_for_like_growth(current: dict[int, float], previous: dict[int, float] | None) -> float | None:
    if not previous:
        return None
    shared = sorted(set(current) & set(previous))
    if not shared:
        return None
    current_total = float(sum(current[month] for month in shared))
    previous_total = float(sum(previous[month] for month in shared))
    if previous_total == 0 or not np.isfinite(current_total) or not np.isfinite(previous_total):
        return None
    return float((current_total - previous_total) / previous_total * 100)


def _yearly_actuals(month_totals: list[tuple[str, float]]) -> list[dict]:
    by_year: dict[int, dict[int, float]] = {}
    for iso, amount in month_totals:
        year = int(iso[:4])
        month = int(iso[5:7])
        by_year.setdefault(year, {})[month] = float(amount)
    rows = []
    for year in sorted(by_year):
        months_map = by_year[year]
        months_included = len(months_map)
        rows.append({
            "year": year,
            "actual_amount": float(sum(months_map.values())),
            "months_included": months_included,
            "year_status": "FULL_YEAR" if months_included == 12 else "PARTIAL_YEAR",
            "growth_percent": _like_for_like_growth(months_map, by_year.get(year - 1)),
        })
    return rows


def _seasonal_profile(month_totals: list[tuple[str, float]]) -> list[dict]:
    buckets: dict[int, list[float]] = {month: [] for month in range(1, 13)}
    for iso, amount in month_totals:
        buckets[int(iso[5:7])].append(float(amount))
    return [
        {
            "calendar_month": month,
            "month_label": calendar.month_abbr[month],
            "average_amount": float(sum(values) / len(values)) if values else None,
            "observation_count": len(values),
        }
        for month, values in buckets.items()
    ]


def _combined_month_total(selected_all: pd.DataFrame, column: str) -> float | None:
    if column not in selected_all.columns:
        return None
    series = pd.to_numeric(selected_all[column], errors="coerce")
    finite = series[np.isfinite(series.to_numpy(dtype=float))]
    if finite.empty:
        return None
    total = float(finite.sum() * AMOUNT_CONVERSION_FACTOR)
    if not np.isfinite(total):
        raise OverviewDataError("Monthly actuals contain a non-finite total.")
    return total


def _analysis_from_prepared(prepared: dict) -> dict:
    category = prepared["category"]
    scope = prepared["scope"]
    date_to_column = prepared["date_to_column"]
    month_dates = prepared["month_dates"]
    selected_codes = prepared["selected_codes"]
    names = prepared["names"]
    selected_all = prepared["selected_all"]
    history_month_count = len(month_dates)

    monthly_actuals = []
    complete_totals: list[tuple[str, float]] = []
    for date in month_dates:
        iso = _iso_month(date)
        column = date_to_column.get(date)
        total = _combined_month_total(selected_all, column) if column else None
        monthly_actuals.append({"month": iso, "actual_amount": total})
        if total is not None:
            complete_totals.append((iso, total))

    complete_month_count = len(complete_totals)
    overall_total = float(sum(amount for _, amount in complete_totals))
    if not np.isfinite(overall_total):
        raise OverviewDataError("Historical actuals contain a non-finite total.")
    monthly_average = float(overall_total / complete_month_count) if complete_month_count else None
    yearly_actuals = _yearly_actuals(complete_totals)
    latest_year_growth_percent = yearly_actuals[-1]["growth_percent"] if yearly_actuals else None

    budget_codes = []
    for code in selected_codes:
        months_present = 0
        actual_amount = 0.0
        for date in month_dates:
            column = date_to_column.get(date)
            if column is None or column not in selected_all.columns:
                continue
            value = pd.to_numeric(selected_all.loc[code, column], errors="coerce")
            if pd.isna(value) or not np.isfinite(float(value)):
                continue
            months_present += 1
            actual_amount += float(value) * AMOUNT_CONVERSION_FACTOR
        if not np.isfinite(actual_amount):
            raise OverviewDataError(f"Budget Code {code} has a non-finite actual amount.")
        coverage = months_present / history_month_count if history_month_count else 0.0
        budget_codes.append({
            "budget_code": code,
            "account_name": names.get(code) or "",
            "actual_amount": float(actual_amount),
            "months_present": months_present,
            "history_month_count": history_month_count,
            "recurring": coverage >= RECURRING_COVERAGE,
        })
    budget_codes.sort(key=lambda item: (-item["actual_amount"], item["budget_code"]))

    return {
        "category": {"id": category["id"], "name": category["name"]},
        "history_start": scope["history_start"],
        "history_end": scope["history_end"],
        "latest_complete_year": scope["latest_complete_year"],
        "latest_year": int(scope["latest_year"]),
        "latest_year_is_partial": bool(scope["latest_year_is_partial"]),
        "latest_actual_month": scope["latest_actual_month"],
        "amount_unit": RESPONSE_AMOUNT_UNIT,
        "conversion_factor": float(AMOUNT_CONVERSION_FACTOR),
        "overall_total": overall_total,
        "monthly_average": monthly_average,
        "latest_year_growth_percent": latest_year_growth_percent,
        "complete_month_count": complete_month_count,
        "budget_code_count": len(budget_codes),
        "recurring_count": sum(1 for item in budget_codes if item["recurring"]),
        "monthly_actuals": monthly_actuals,
        "yearly_actuals": yearly_actuals,
        "seasonal_profile": _seasonal_profile(complete_totals),
        "budget_codes": budget_codes,
        **_historical_coverage_fields(prepared),
    }


def _build_analysis(category: dict) -> dict:
    return _analysis_from_prepared(_prepare_category_actuals(category))


def _get_historical(category: str | None) -> dict:
    resolved = _resolve_category(category)
    cached = _historical_cache.get(resolved["id"])
    if cached is not None:
        return cached
    payload = _build_historical(resolved)
    _historical_cache[resolved["id"]] = payload
    return payload


def _get_analysis(category: str | None) -> dict:
    resolved = _resolve_category(category)
    cached = _analysis_cache.get(resolved["id"])
    if cached is not None:
        return cached
    payload = _build_analysis(resolved)
    _analysis_cache[resolved["id"]] = payload
    return payload


@router.get("/categories", response_model=OverviewCategoriesResponse)
def overview_categories() -> OverviewCategoriesResponse:
    try:
        return OverviewCategoriesResponse(**_list_categories())
    except OverviewDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/historical", response_model=OverviewHistoricalResponse)
def overview_historical(
    category: str | None = Query(default=None, description="Category id or name"),
) -> OverviewHistoricalResponse:
    try:
        return OverviewHistoricalResponse(**_get_historical(category))
    except OverviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except OverviewDataError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/analysis", response_model=OverviewAnalysisResponse)
def overview_analysis(
    category: str | None = Query(default=None, description="Category id or name"),
) -> OverviewAnalysisResponse:
    try:
        return OverviewAnalysisResponse(**_get_analysis(category))
    except OverviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except OverviewDataError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/forecast-activity", response_model=OverviewForecastActivityResponse)
def overview_forecast_activity() -> OverviewForecastActivityResponse:
    try:
        records = [ForecastRecordSummary(**row) for row in list_forecast_records(limit=8)]
    except DatabaseError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return OverviewForecastActivityResponse(
        latest_forecast_summary=records[0] if records else None,
        recent_forecasting_activities=records,
    )
