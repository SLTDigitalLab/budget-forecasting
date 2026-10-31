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
    ACTUALS_DISPLAY_LABEL,
    _load_original_excel,
    assign_categories,
    category_order_from_headings,
    detect_monthly_columns,
    normalize_historical_actuals,
    sort_categories_by_order,
)

DEFAULT_CATEGORY_ID = "international_settlement"
DEFAULT_CATEGORY_NAME = "International Settlement"
ALL_CATEGORIES_ID = "all"
ALL_CATEGORIES_NAME = "All Categories"
ALL_CATEGORIES_TOKENS = {
    "all",
    "all categories",
    "all_categories",
    "*",
    "__all__",
}
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
    # Null when the code is in the category but has no finite numeric month in scope.
    actual_amount: Optional[float] = None


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
    historical_display_year: Optional[int] = None
    historical_display_year_partial: bool = False
    historical_display_budget_codes: list[BudgetCodeActualItem] = []
    # Membership vs display-year amount coverage (not rewritten into stored forecasts).
    category_budget_code_count: int = 0
    historical_display_amount_code_count: int = 0
    coverage_scope: str = "master"
    historical_data_partial: bool = False
    historical_missing_budget_codes: list[str] = []
    historical_missing_budget_code_count: int = 0
    latest_available_month: Optional[str] = None
    latest_available_status: Optional[str] = None
    latest_available_submitted: int = 0
    latest_available_applicable: int = 0
    latest_available_declared_missing: int = 0
    latest_available_numeric: int = 0
    historical_observations_exist: bool = False
    actuals_display_label: str = ACTUALS_DISPLAY_LABEL


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
    display_history_start: Optional[str] = None
    display_history_end: Optional[str] = None
    history_range: str = "latest2"
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
    latest_available_month: Optional[str] = None
    latest_available_status: Optional[str] = None
    latest_available_submitted: int = 0
    latest_available_applicable: int = 0
    latest_available_declared_missing: int = 0
    latest_available_numeric: int = 0
    actuals_display_label: str = ACTUALS_DISPLAY_LABEL


def _is_international_settlement(name: str) -> bool:
    text = str(name or "").casefold()
    return "int" in text and "settlement" in text


def _category_slug(name: str) -> str:
    text = str(name or "").strip()
    if _is_all_categories_label(text):
        return ALL_CATEGORIES_ID
    if _is_international_settlement(text):
        return DEFAULT_CATEGORY_ID
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_") or "category"


def _is_all_categories_label(value: object) -> bool:
    return str(value or "").strip().casefold() in ALL_CATEGORIES_TOKENS


def _all_categories_record() -> dict:
    return {
        "id": ALL_CATEGORIES_ID,
        "name": ALL_CATEGORIES_NAME,
        "source_name": ALL_CATEGORIES_NAME,
    }


def _display_category_name(name: str) -> str:
    text = str(name or "").strip()
    if _is_all_categories_label(text):
        return ALL_CATEGORIES_NAME
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
    assigned = assign_categories(
        loaded["df"],
        loaded["account_code_col"],
        loaded["account_name_col"],
        category_heading_excel_rows=loaded.get("category_heading_excel_rows"),
    )
    monthly_columns, month_dates = detect_monthly_columns(assigned.columns.tolist())
    _workbook = {
        **loaded,
        "assigned": assigned,
        "monthly_columns": list(monthly_columns),
        "month_dates": [pd.Timestamp(item).replace(day=1) for item in month_dates],
        "category_order": list(loaded.get("category_order") or []),
    }
    return _workbook


def _master_category_catalog() -> tuple[list[str], dict[str, str]]:
    """Return (category_order, code->category) from the current master when available."""
    try:
        from app.services.dataset_master import load_master_state

        state = load_master_state()
        order = [str(item).strip() for item in (state.get("category_order") or []) if str(item or "").strip()]
        mapping = {
            str(item.get("budget_code")): str(item.get("category") or "").strip()
            for item in (state.get("budget_codes") or [])
            if item.get("budget_code") and str(item.get("category") or "").strip()
        }
        if not order:
            order = category_order_from_headings(list(mapping.values()))
        return order, mapping
    except Exception:
        return [], {}


def _list_categories() -> dict:
    global _categories
    if _categories is None:
        workbook = _load_workbook()
        assigned = workbook["assigned"]
        seen = set()
        source_names: list[str] = []
        for raw in assigned.loc[~assigned["is_category_row"], "category"].dropna():
            name = str(raw).strip()
            if not name:
                continue
            ident = _category_slug(name)
            if ident in seen:
                continue
            seen.add(ident)
            source_names.append(name)
        master_order, _mapping = _master_category_catalog()
        workbook_order = list(workbook.get("category_order") or [])
        ordered_sources = sort_categories_by_order(
            source_names,
            master_order or workbook_order or category_order_from_headings(source_names),
        )
        # Include master-only categories (no current numeric rows) in workbook/master order.
        for name in master_order or []:
            ident = _category_slug(name)
            if ident in seen:
                continue
            seen.add(ident)
            ordered_sources.append(name)
        ordered_sources = sort_categories_by_order(
            ordered_sources,
            master_order or workbook_order or ordered_sources,
        )
        names = [
            {"id": _category_slug(name), "name": _display_category_name(name), "source_name": name}
            for name in ordered_sources
        ]
        if not names:
            raise OverviewDataError("No categories were found in the historical Actuals workbook.")
        _categories = names
    default = next((item["id"] for item in _categories if item["id"] == DEFAULT_CATEGORY_ID), _categories[0]["id"])
    return {
        "categories": [{"id": item["id"], "name": item["name"]} for item in _categories],
        "default_category": default,
    }


def _resolve_category(requested: str | None) -> dict:
    if _is_all_categories_label(requested):
        return _all_categories_record()
    catalog = _list_categories()
    if not requested:
        ident = catalog["default_category"]
    else:
        ident = _category_slug(requested)
        if ident == ALL_CATEGORIES_ID:
            return _all_categories_record()
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
    account_mask = (
        (~assigned["is_category_row"])
        & assigned["numeric_account_code"].notna()
        & assigned[account_code_col].notna()
        & assigned["category"].notna()
    )
    if category.get("id") == ALL_CATEGORIES_ID or _is_all_categories_label(category.get("source_name")):
        # Confirmed master: every numeric account row across all source categories.
        accounts = assigned[account_mask].copy()
    else:
        accounts = assigned[
            account_mask
            & (
                assigned["category"].astype(str).str.strip().str.casefold()
                == category["source_name"].strip().casefold()
            )
        ].copy()
    if accounts.empty:
        raise OverviewDataError(f"No account rows were found for category {category['source_name']!r}.")

    history_values = accounts[monthly_columns].apply(pd.to_numeric, errors="coerce")
    history_values = pd.DataFrame(
        {
            column: normalize_historical_actuals(history_values[column].to_numpy(dtype=float))
            for column in history_values.columns
        },
        index=accounts[account_code_col].astype(str),
    )
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
        "actuals_display_label": ACTUALS_DISPLAY_LABEL,
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


def _shift_iso_month(iso: str, delta: int) -> str:
    year = int(iso[:4])
    month = int(iso[5:7])
    index = year * 12 + (month - 1) + delta
    return f"{index // 12:04d}-{(index % 12) + 1:02d}"


def _complete_calendar_years_from_dates(month_dates) -> list[int]:
    """Years with confirmed Jan–Dec month records present (amounts may be null)."""
    by_year: dict[int, set[int]] = {}
    for item in month_dates or []:
        timestamp = pd.Timestamp(item).replace(day=1)
        by_year.setdefault(int(timestamp.year), set()).add(int(timestamp.month))
    return sorted(
        year
        for year, months in by_year.items()
        if months == set(range(1, 13))
    )


def _display_history_window(
    history_start: str,
    history_end: str,
    month_dates=None,
    *,
    history_range: str = "latest2",
) -> tuple[str, str]:
    """Historical display window from confirmed month records.

    latest2: latest two complete calendar years (example: data through Sep 2026
    → Jan 2024–Dec 2025). all: full available history including partial years.
    """
    mode = str(history_range or "latest2").strip().lower()
    if mode in {"all", "all_history", "full"}:
        return str(history_start or history_end), str(history_end or history_start)
    complete_years = _complete_calendar_years_from_dates(month_dates)
    if not complete_years:
        return "", ""
    selected = complete_years[-2:]
    start = f"{selected[0]:04d}-01"
    end = f"{selected[-1]:04d}-12"
    available_start = str(history_start or start)
    available_end = str(history_end or end)
    if available_start and start < available_start:
        start = available_start
    if available_end and end > available_end:
        end = available_end
    if start > end:
        return "", ""
    return start, end


def _iso_in_window(iso: str, start: str, end: str) -> bool:
    return bool(iso) and start <= iso <= end


def _yearly_actuals(
    month_totals: list[tuple[str, float]],
    *,
    display_years: set[int] | None = None,
) -> list[dict]:
    by_year: dict[int, dict[int, float]] = {}
    for iso, amount in month_totals:
        year = int(iso[:4])
        month = int(iso[5:7])
        by_year.setdefault(year, {})[month] = float(amount)
    years = sorted(display_years) if display_years is not None else sorted(by_year)
    rows = []
    for year in years:
        months_map = by_year.get(year) or {}
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


def _analysis_from_prepared(prepared: dict, *, history_range: str = "latest2") -> dict:
    category = prepared["category"]
    scope = prepared["scope"]
    date_to_column = prepared["date_to_column"]
    month_dates = prepared["month_dates"]
    selected_codes = prepared["selected_codes"]
    names = prepared["names"]
    selected_all = prepared["selected_all"]
    mode = str(history_range or "latest2").strip().lower()
    if mode in {"last24", "recent", "rolling24"}:
        mode = "latest2"

    monthly_actuals = []
    complete_totals: list[tuple[str, float]] = []
    for date in month_dates:
        iso = _iso_month(date)
        column = date_to_column.get(date)
        total = _combined_month_total(selected_all, column) if column else None
        monthly_actuals.append({"month": iso, "actual_amount": total})
        if total is not None:
            complete_totals.append((iso, total))

    display_start, display_end = _display_history_window(
        scope["history_start"],
        scope["history_end"],
        month_dates,
        history_range=mode,
    )
    complete_years = _complete_calendar_years_from_dates(month_dates)
    range_is_all = mode in {"all", "all_history", "full"}
    selected_years = None if range_is_all else set(complete_years[-2:])
    empty_latest2 = selected_years is not None and not selected_years

    def _in_aggregate_scope(iso: str) -> bool:
        if empty_latest2 or not display_start or not display_end:
            return False
        if not _iso_in_window(iso, display_start, display_end):
            return False
        if selected_years is None:
            return True
        return int(iso[:4]) in selected_years

    display_dates = [
        date for date in month_dates
        if _in_aggregate_scope(_iso_month(date))
    ]
    display_complete = [
        (iso, amount) for iso, amount in complete_totals
        if _in_aggregate_scope(iso)
    ]
    display_years = {int(iso[:4]) for iso, _ in display_complete}
    baseline_year = (
        int(display_start[:4]) - 1
        if display_start and len(display_start) >= 7 and not empty_latest2
        else None
    )
    yearly_source = list(display_complete)
    if baseline_year is not None:
        yearly_source.extend(
            (iso, amount) for iso, amount in complete_totals
            if int(iso[:4]) == baseline_year
        )

    complete_month_count = len(display_complete)
    overall_total = float(sum(amount for _, amount in display_complete))
    if not np.isfinite(overall_total):
        raise OverviewDataError("Historical actuals contain a non-finite total.")
    monthly_average = float(overall_total / complete_month_count) if complete_month_count else None
    yearly_actuals = _yearly_actuals(yearly_source, display_years=display_years) if display_years else []
    latest_year_growth_percent = yearly_actuals[-1]["growth_percent"] if yearly_actuals else None

    history_month_count = len(display_dates)
    budget_codes = []
    for code in selected_codes:
        months_present = 0
        actual_amount = 0.0
        for date in display_dates:
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
        "display_history_start": None if empty_latest2 else (display_start or None),
        "display_history_end": None if empty_latest2 else (display_end or None),
        "history_range": "all" if range_is_all else "latest2",
        "latest_complete_year": scope["latest_complete_year"],
        "latest_year": int(scope["latest_year"]),
        "latest_year_is_partial": bool(scope["latest_year_is_partial"]),
        "latest_actual_month": scope["latest_actual_month"],
        "amount_unit": RESPONSE_AMOUNT_UNIT,
        "conversion_factor": float(AMOUNT_CONVERSION_FACTOR),
        "actuals_display_label": ACTUALS_DISPLAY_LABEL,
        "overall_total": overall_total,
        "monthly_average": monthly_average,
        "latest_year_growth_percent": latest_year_growth_percent,
        "complete_month_count": complete_month_count,
        "budget_code_count": len(budget_codes),
        "recurring_count": sum(1 for item in budget_codes if item["recurring"]),
        # Full stored monthly series retained for Forecast/Comparison tabs and client windows.
        "monthly_actuals": monthly_actuals,
        "yearly_actuals": yearly_actuals,
        "seasonal_profile": _seasonal_profile(display_complete),
        "budget_codes": budget_codes,
        **_historical_coverage_fields(prepared),
    }


def _build_analysis(category: dict, *, history_range: str = "latest2") -> dict:
    return _analysis_from_prepared(_prepare_category_actuals(category), history_range=history_range)


def _category_year_actuals(prepared: dict, year: int) -> tuple[list[dict], list[dict]]:
    """Numeric totals for one calendar year. Declared-missing months stay null."""
    selected = prepared["selected_year"]
    selected_all = prepared["selected_all"]
    date_to_column = prepared["date_to_column"]
    names = prepared["names"]
    selected_codes = prepared["selected_codes"]
    monthly_actuals = []
    code_totals: dict[str, float] = {}
    code_found: set[str] = set()
    for month in range(1, 13):
        date = pd.Timestamp(year=year, month=month, day=1)
        iso = _iso_month(date)
        column = date_to_column.get(date)
        frame = selected if column in getattr(selected, "columns", []) else selected_all
        if column is None or column not in frame.columns:
            monthly_actuals.append({"month": iso, "actual_amount": None})
            continue
        column_values = pd.to_numeric(frame[column], errors="coerce")
        finite = column_values[np.isfinite(column_values.to_numpy(dtype=float))]
        if finite.empty:
            monthly_actuals.append({"month": iso, "actual_amount": None})
        else:
            monthly_actuals.append({"month": iso, "actual_amount": float(finite.sum() * AMOUNT_CONVERSION_FACTOR)})
        for code in selected_codes:
            if column is None or column not in frame.columns or code not in frame.index:
                continue
            value = pd.to_numeric(frame.loc[code, column], errors="coerce")
            if pd.isna(value) or not np.isfinite(float(value)):
                continue
            code_totals[code] = code_totals.get(code, 0.0) + float(value) * AMOUNT_CONVERSION_FACTOR
            code_found.add(code)
    rows = []
    for code in selected_codes:
        name = names.get(str(code)) or names.get(code) or ""
        if code in code_found and np.isfinite(code_totals.get(code, float("nan"))):
            rows.append({
                "budget_code": str(code),
                "account_name": name,
                "actual_amount": float(code_totals[code]),
            })
        else:
            # Keep category membership visible: no finite month in this year is not a silent drop.
            rows.append({
                "budget_code": str(code),
                "account_name": name,
                "actual_amount": None,
            })
    rows.sort(
        key=lambda item: (
            item["actual_amount"] is None,
            -(item["actual_amount"] if item["actual_amount"] is not None else 0.0),
            item["budget_code"],
        )
    )
    return monthly_actuals, rows


def _years_with_numeric_actuals(prepared: dict) -> list[int]:
    frame = prepared.get("selected_all")
    if frame is None or getattr(frame, "empty", True):
        return []
    found = set()
    for date in prepared.get("month_dates") or []:
        column = (prepared.get("date_to_column") or {}).get(date)
        if column is None or column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        if np.isfinite(values.to_numpy(dtype=float)).any():
            found.add(int(pd.Timestamp(date).year))
    return sorted(found)


def _historical_display_year(complete_year, prepared: dict) -> tuple[int | None, bool]:
    """Charts use the global Latest Year only. A later partial year is not substituted."""
    del prepared
    if complete_year is None:
        return None, False
    return int(complete_year), False


def _overlay_submission_coverage(payload: dict, category: dict | None = None) -> dict:
    """Attach master submission coverage without hiding category numeric history."""
    from app.services.dataset_master import load_master_state
    from app.services.submission_state import coverage_fields, coverage_report

    report = coverage_report(load_master_state())
    fields = coverage_fields(report)
    complete_year = fields.get("latest_complete_year")
    payload.update(fields)
    payload["latest_complete_year"] = complete_year
    payload["coverage_scope"] = "master"
    if category is None or "latest_year_monthly_actuals" not in payload:
        payload["historical_display_year"] = complete_year
        payload["historical_display_year_partial"] = False
        payload["latest_year_is_partial"] = complete_year is None
        return payload
    prepared = _prepare_category_actuals(category)
    display_year, partial = _historical_display_year(complete_year, prepared)
    payload["historical_observations_exist"] = bool(_years_with_numeric_actuals(prepared))
    payload["historical_display_year"] = display_year
    payload["historical_display_year_partial"] = partial
    payload["latest_year_is_partial"] = complete_year is None
    selected_count = len(prepared.get("selected_codes") or [])
    payload["category_budget_code_count"] = int(selected_count)
    if display_year is None:
        payload["latest_year_monthly_actuals"] = []
        payload["latest_complete_year_budget_codes"] = []
        payload["historical_display_budget_codes"] = []
        payload["historical_display_amount_code_count"] = 0
        return payload
    monthly_actuals, budget_codes = _category_year_actuals(prepared, int(display_year))
    payload["latest_year"] = int(display_year)
    payload["latest_year_monthly_actuals"] = monthly_actuals
    payload["historical_display_budget_codes"] = budget_codes
    payload["latest_complete_year_budget_codes"] = [] if partial else budget_codes
    payload["historical_display_amount_code_count"] = sum(
        1 for row in budget_codes if row.get("actual_amount") is not None
    )
    return payload


def _get_historical(category: str | None) -> dict:
    resolved = _resolve_category(category)
    cached = _historical_cache.get(resolved["id"])
    if cached is not None:
        return cached
    payload = _overlay_submission_coverage(_build_historical(resolved), resolved)
    _historical_cache[resolved["id"]] = payload
    return payload


def _normalize_history_range(history_range: str | None) -> str:
    mode = str(history_range or "latest2").strip().lower()
    if mode in {"all", "all_history", "full"}:
        return "all"
    return "latest2"


def _get_analysis(category: str | None, history_range: str | None = "latest2") -> dict:
    resolved = _resolve_category(category)
    mode = _normalize_history_range(history_range)
    cache_key = f"{resolved['id']}:{mode}"
    cached = _analysis_cache.get(cache_key)
    if cached is not None:
        return cached
    payload = _overlay_submission_coverage(_build_analysis(resolved, history_range=mode), None)
    _analysis_cache[cache_key] = payload
    return payload


class CodeActualsRequest(BaseModel):
    budget_codes: list[str] = []


class CodeMonthActualItem(BaseModel):
    budget_code: str
    month: str
    # Null preserves explicit missing declarations; absent records are omitted entirely.
    actual_amount: Optional[float] = None


class CodeActualsResponse(BaseModel):
    amount_unit: str
    conversion_factor: float
    history_start: Optional[str] = None
    history_end: Optional[str] = None
    budget_code_count: int = 0
    actuals: list[CodeMonthActualItem]


def _code_actuals_payload(budget_codes: list[str]) -> dict:
    from app.services.dataset_repository import list_historical_actuals_for_codes

    requested = sorted({str(code).strip() for code in budget_codes if str(code or "").strip()})
    try:
        rows = list_historical_actuals_for_codes(requested)
    except DatabaseError as error:
        raise OverviewDataError(str(error)) from error

    factor = float(AMOUNT_CONVERSION_FACTOR)
    actuals: list[dict] = []
    months: list[str] = []
    codes_seen: set[str] = set()
    for row in rows:
        code = str(row.get("budget_code") or "").strip()
        month = str(row.get("month") or "").strip()
        if not code or not re.fullmatch(r"\d{4}-\d{2}", month):
            continue
        raw = row.get("amount")
        amount: float | None
        if raw is None or raw == "":
            # Explicit missing declaration — keep null; never invent a zero.
            amount = None
        else:
            try:
                number = float(raw) * factor
            except (TypeError, ValueError):
                # Non-numeric stored value: skip; do not coerce to zero.
                continue
            if number != number or number in (float("inf"), float("-inf")):
                continue
            amount = float(number)
        codes_seen.add(code)
        months.append(month)
        actuals.append({
            "budget_code": code,
            "month": month,
            "actual_amount": amount,
        })
    months_sorted = sorted(set(months))
    return {
        "amount_unit": RESPONSE_AMOUNT_UNIT,
        "conversion_factor": factor,
        "history_start": months_sorted[0] if months_sorted else None,
        "history_end": months_sorted[-1] if months_sorted else None,
        "budget_code_count": len(codes_seen),
        "actuals": actuals,
    }


@router.get("/categories", response_model=OverviewCategoriesResponse)
def overview_categories() -> OverviewCategoriesResponse:
    try:
        return OverviewCategoriesResponse(**_list_categories())
    except OverviewDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/code-actuals", response_model=CodeActualsResponse)
def overview_code_actuals(body: CodeActualsRequest) -> CodeActualsResponse:
    """Confirmed historical actuals for a specific Budget Code population."""
    try:
        return CodeActualsResponse(**_code_actuals_payload(body.budget_codes or []))
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
    history_range: str | None = Query(
        default="latest2",
        description="Historical display range: latest2 (two complete calendar years) or all",
    ),
) -> OverviewAnalysisResponse:
    try:
        return OverviewAnalysisResponse(**_get_analysis(category, history_range=history_range))
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
