"""Predict-only forecasts from the cached all-budget artifact."""

from __future__ import annotations

from calendar import month_abbr
from datetime import datetime, timezone
import re
import sys

import numpy as np
import pandas as pd

from app.config import (
    AMOUNT_CONVERSION_FACTOR,
    BACKEND_DIR,
    MODEL_AMOUNT_UNIT,
    PER_BUDGET_CODE_ALGORITHM,
    RESPONSE_AMOUNT_UNIT,
)

MAX_FORECAST_MONTHS = 60
_ISO_MONTH = re.compile(r"^\d{4}-\d{2}$")
_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
UNAVAILABLE_BEYOND_CALIBRATED_HORIZON = "unavailable_beyond_calibrated_horizon"
STATEFUL_ALGORITHMS = {"XGBoost", "Prophet", "ARIMAX", "SARIMAX"}
UNAVAILABLE_STATUSES = {
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
ALL_CATEGORIES_TOKENS = {"", "all", "all categories", "*", "__all__"}
ALL_CATEGORIES_LABEL = "All Categories"
UNAVAILABLE_DISPLAY_STATUS = "Forecast Unavailable"
UNAVAILABLE_REASON = "Insufficient historical data for reliable model evaluation."
SAFE_UNAVAILABLE_REASON = "Forecast unavailable for this budget code."
BOOK_ENTRY_REASON = "BOOK_ENTRY_ACCOUNT"
BOOK_ENTRY_REASON_LABEL = "Book Entry Account"
INSUFFICIENT_REASON = "INSUFFICIENT_HISTORICAL_DATA"
INSUFFICIENT_REASON_LABEL = "Insufficient Historical Data"
NO_MODEL_REASON = "NO_VALID_MODEL"
NO_MODEL_REASON_LABEL = "No Valid Model"
NO_FORECASTABLE_MESSAGE = "No forecastable budget codes available in this category."
INSUFFICIENT_STATUSES = {
    "NO_DATA",
    "ALL_MISSING",
    "NOT_EVALUABLE",
    "UNRESOLVED_MISSING",
    "INSUFFICIENT_HISTORY",
}
ZERO_POLICY_DISPLAY_STATUS = "Zero Policy"
AVAILABLE_DISPLAY_STATUS = "Available"
_MONTH_LOOKUP = {month_abbr[number]: number for number in range(1, 13)}
ENGINE_DIR = BACKEND_DIR / "model_training_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from evaluation import ARIMA_FAMILY, inspect_arima_stability  # noqa: E402
from preprocessing import (  # noqa: E402
    BookEntryConfigurationError,
    book_entry_account_codes,
    effective_historical_actual,
)
from model_training import forecast_from_fitted  # noqa: E402


class ForecastingError(Exception):
    """Raised when a loaded model cannot produce a valid forecast."""


class InvalidForecastWindowError(ForecastingError):
    """Raised when the requested forecast window is invalid."""


class UnsupportedAccountError(ForecastingError):
    """Raised when a requested Budget Code is not in the selected accounts."""


def parse_month(value: str) -> tuple[int, int]:
    timestamp = parse_month_timestamp(value)
    return int(timestamp.year), int(timestamp.month)


def parse_month_timestamp(value: str) -> pd.Timestamp:
    text = str(value or "").strip()
    if not text:
        raise InvalidForecastWindowError("A valid month value is required.")
    try:
        if len(text) == 7 and text[4] == "-" and text[:4].isdigit() and text[5:].isdigit():
            year = int(text[:4])
            month = int(text[5:])
            if month < 1 or month > 12:
                raise ValueError
            return pd.Timestamp(year=year, month=month, day=1)
        parsed = pd.Timestamp(text)
        if pd.isna(parsed):
            raise ValueError
        return parsed.replace(day=1)
    except (ValueError, TypeError, KeyError):
        pass
    try:
        if "-" not in text:
            raise ValueError
        left, right = text.split("-", 1)
        if left[:3].title() in _MONTH_LOOKUP:
            month = _MONTH_LOOKUP[left[:3].title()]
            year = int(right)
            return pd.Timestamp(year=year, month=month, day=1)
        if right[:3].title() in _MONTH_LOOKUP and left.isdigit():
            month = _MONTH_LOOKUP[right[:3].title()]
            year = int(left)
            return pd.Timestamp(year=year, month=month, day=1)
    except (KeyError, ValueError, TypeError) as error:
        raise InvalidForecastWindowError("A valid month value is required.") from error
    raise InvalidForecastWindowError("A valid month value is required.")


def to_iso_month(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def timestamp_to_iso(timestamp: pd.Timestamp) -> str:
    return to_iso_month(int(timestamp.year), int(timestamp.month))


def first_forecast_month(historical_end) -> pd.Timestamp:
    history_end = parse_month_timestamp(str(historical_end))
    return history_end + pd.offsets.MonthBegin(1)


def month_phrase(timestamp: pd.Timestamp) -> str:
    return f"{_MONTH_NAMES[int(timestamp.month) - 1]} {int(timestamp.year)}"


def published_forecast_bounds(bundle: dict) -> dict[str, str]:
    """Forecast window from the published bundle's historical end. Not the live master."""
    raw = bundle.get("historical_end")
    if raw is None or str(raw).strip() == "":
        raw = bundle.get("history_end")
    if raw is None or str(raw).strip() == "":
        raise InvalidForecastWindowError(
            "The published model bundle does not include a historical end month. "
            "Save Models after retraining before generating a forecast."
        )
    try:
        history_end = parse_month_timestamp(str(raw))
    except InvalidForecastWindowError as error:
        raise InvalidForecastWindowError(
            "The published model bundle historical end month is invalid. "
            "Save Models after retraining before generating a forecast."
        ) from error
    origin = history_end + pd.offsets.MonthBegin(1)
    horizon_end = pd.Timestamp(year=int(history_end.year) + 3, month=12, day=1)
    if origin > horizon_end:
        raise InvalidForecastWindowError(
            "The published model bundle historical end leaves no valid forecast month."
        )
    return {
        "history_end": timestamp_to_iso(history_end),
        "min_forecast_month": timestamp_to_iso(origin),
        "max_forecast_end": timestamp_to_iso(horizon_end),
    }


def remaining_months_from_history_end(history_end: str | None) -> dict[str, str | bool]:
    """
    Remaining months follow the published model's historical cutoff through December
    of that same year. A newer uploaded dataset is not a cutoff.
    """
    if not history_end or not str(history_end).strip():
        return {
            "available": False,
            "reason": "missing",
            "start_month": "",
            "end_month": "",
            "history_end": "",
            "message": (
                "Remaining months are unavailable until the published model provides a historical cutoff."
            ),
        }
    try:
        cutoff = parse_month_timestamp(str(history_end))
    except InvalidForecastWindowError:
        return {
            "available": False,
            "reason": "invalid",
            "start_month": "",
            "end_month": "",
            "history_end": "",
            "message": "Remaining months are unavailable because the published historical cutoff is invalid.",
        }
    if int(cutoff.month) == 12:
        return {
            "available": False,
            "reason": "december",
            "start_month": "",
            "end_month": "",
            "history_end": timestamp_to_iso(cutoff),
            "message": (
                f"There are no remaining months after {month_phrase(cutoff)}. "
                "Use Future financial years instead."
            ),
        }
    start = cutoff + pd.offsets.MonthBegin(1)
    if int(start.year) != int(cutoff.year):
        return {
            "available": False,
            "reason": "december",
            "start_month": "",
            "end_month": "",
            "history_end": timestamp_to_iso(cutoff),
            "message": (
                f"There are no remaining months after {month_phrase(cutoff)}. "
                "Use Future financial years instead."
            ),
        }
    end = pd.Timestamp(year=int(cutoff.year), month=12, day=1)
    return {
        "available": True,
        "reason": "",
        "start_month": timestamp_to_iso(start),
        "end_month": timestamp_to_iso(end),
        "history_end": timestamp_to_iso(cutoff),
        "message": "",
    }


def validate_remaining_year_request(
    start_month: str | None,
    end_month: str | None,
    *,
    bundle: dict | None = None,
    history_end: str | None = None,
    min_forecast_month: str | None = None,
    max_forecast_end: str | None = None,
) -> dict[str, str]:
    """Reject Remaining months requests that do not match the published model cutoff."""
    if bundle is not None:
        bounds = published_forecast_bounds(bundle)
        history_end = bounds["history_end"]
        min_forecast_month = bounds["min_forecast_month"]
        max_forecast_end = bounds["max_forecast_end"]
    expected = remaining_months_from_history_end(history_end)
    if not expected["available"]:
        raise InvalidForecastWindowError(str(expected["message"]))
    if start_month != expected["start_month"] or end_month != expected["end_month"]:
        raise InvalidForecastWindowError(
            "Remaining months must be "
            f"{month_phrase(parse_month_timestamp(str(expected['start_month'])))} through "
            f"{month_phrase(parse_month_timestamp(str(expected['end_month'])))} "
            f"based on the published model historical cutoff "
            f"({month_phrase(parse_month_timestamp(str(expected['history_end'])))})."
        )
    if min_forecast_month and max_forecast_end and (
        str(expected["start_month"]) < str(min_forecast_month)
        or str(expected["end_month"]) > str(max_forecast_end)
    ):
        raise InvalidForecastWindowError(
            "Remaining months "
            f"{month_phrase(parse_month_timestamp(str(expected['start_month'])))} through "
            f"{month_phrase(parse_month_timestamp(str(expected['end_month'])))} "
            "are not available from the published model. "
            f"Choose a period from {month_phrase(parse_month_timestamp(str(min_forecast_month)))} "
            f"through {month_phrase(parse_month_timestamp(str(max_forecast_end)))}."
        )
    return {
        "start_month": str(expected["start_month"]),
        "end_month": str(expected["end_month"]),
        "history_end": str(expected["history_end"]),
    }


def _allowed_forecast_message(origin: pd.Timestamp, horizon_end: pd.Timestamp) -> str:
    return f"Forecast dates must be from {month_phrase(origin)} through {month_phrase(horizon_end)}."


def add_months(year: int, month: int, count: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + count
    return index // 12, (index % 12) + 1


def month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def iso_range(start: tuple[int, int], count: int) -> list[str]:
    year, month = start
    labels = []
    for _ in range(count):
        labels.append(to_iso_month(year, month))
        year, month = add_months(year, month, 1)
    return labels


def month_range(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    if end < start:
        raise InvalidForecastWindowError("End month cannot be before start month.")
    return list(pd.date_range(start, end, freq="MS"))


def convert_amount(model_value: float) -> float:
    """Convert the stored model unit to the API unit exactly once."""
    return float(model_value) * float(AMOUNT_CONVERSION_FACTOR)


def _finite_floats(raw, expected: int) -> list[float]:
    values = np.asarray(raw, dtype=float).reshape(-1)
    if values.size != expected:
        raise ForecastingError(
            f"The model returned {values.size} predictions, expected {expected}."
        )
    if not np.isfinite(values).all():
        raise ForecastingError("The model produced a non-finite forecast value.")
    return [convert_amount(float(value)) for value in values]


def _account_name_map(bundle: dict) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in bundle.get("eligible_accounts") or []:
        if isinstance(row, dict):
            code = str(row.get("Account Code") or "")
            if code:
                names[code] = str(row.get("Account Name") or "")
    for source_key in ("account_models", "account_records"):
        for code, entry in (bundle.get(source_key) or {}).items():
            if isinstance(entry, dict) and entry.get("account_name"):
                names[str(code)] = str(entry["account_name"])
    return names


def selected_account_codes(bundle: dict) -> list[str]:
    codes = [str(code) for code in bundle.get("selected_accounts") or []]
    if not codes or len(set(codes)) != len(codes):
        raise ForecastingError("The artifact must contain unique selected accounts.")
    return codes


def is_all_categories(category: str | None) -> bool:
    if category is None:
        return True
    return str(category).strip().casefold() in ALL_CATEGORIES_TOKENS


def _account_record(bundle: dict, code: str) -> dict:
    records = bundle.get("account_records") or bundle.get("account_models") or {}
    models = bundle.get("models") or {}
    entry = records.get(code) or records.get(str(code)) or models.get(code) or models.get(str(code)) or {}
    return entry if isinstance(entry, dict) else {}


def account_category(bundle: dict, code: str) -> str:
    record = _account_record(bundle, code)
    return str(record.get("category") or "").strip()


def _categories_match(left: str, right: str) -> bool:
    a = str(left or "").strip().casefold()
    b = str(right or "").strip().casefold()
    if not a or not b:
        return False
    if a == b:
        return True
    return "int" in a and "settlement" in a and "int" in b and "settlement" in b


def production_status_of(entry: dict) -> str:
    return str((entry or {}).get("production_status") or "").strip()


def is_zero_policy_account(entry: dict) -> bool:
    status = production_status_of(entry)
    if status == "ZERO_POLICY":
        return True
    algorithm = str((entry or {}).get("algorithm") or (entry or {}).get("model_name") or "").strip()
    return algorithm == "ZERO_POLICY"


def is_book_entry_account(code: str) -> bool:
    return str(code) in book_entry_account_codes()


def is_unavailable_account(entry: dict) -> bool:
    return production_status_of(entry) in UNAVAILABLE_STATUSES


def _coverage_reason(status: str) -> tuple[str, str, str]:
    if status in INSUFFICIENT_STATUSES:
        return INSUFFICIENT_REASON, INSUFFICIENT_REASON_LABEL, "insufficient"
    if status == "NO_VALID_WINNER":
        return NO_MODEL_REASON, NO_MODEL_REASON_LABEL, "other"
    return "OTHER", SAFE_UNAVAILABLE_REASON, "other"


def _unavailable_record(code, account_name, category_name, status, reason, reason_code, reason_label, bucket):
    return {
        "account_code": code,
        "budget_code": code,
        "budget_name": account_name,
        "account_name": account_name,
        "category": category_name,
        "source_status": status or "UNAVAILABLE",
        "display_status": "UNAVAILABLE" if reason_code == BOOK_ENTRY_REASON else UNAVAILABLE_DISPLAY_STATUS,
        "status": "UNAVAILABLE",
        "reason": reason,
        "reason_code": reason_code,
        "reason_label": reason_label,
        "exclusion": bucket,
    }


def list_forecast_categories(bundle: dict) -> dict:
    """Discover categories from stored Budget Code metadata. Does not invent names."""
    selected = selected_account_codes(bundle)
    names = _account_name_map(bundle)
    seen: list[str] = []
    counts: dict[str, int] = {}
    missing: list[dict] = []
    accounts = []
    for code in selected:
        record = _account_record(bundle, code)
        category = account_category(bundle, code)
        status = production_status_of(record)
        if category:
            if category not in counts:
                seen.append(category)
                counts[category] = 0
            counts[category] += 1
        else:
            missing.append({"budget_code": code, "account_name": names.get(code) or None})
        accounts.append({
            "budget_code": code,
            "account_name": names.get(code) or None,
            "category": category or None,
            "production_status": status or None,
        })
    return {
        "categories": [
            {"id": name, "name": name, "budget_code_count": counts[name]}
            for name in seen
        ],
        "missing_category_codes": missing,
        "accounts": accounts,
    }


def accounts_for_category(bundle: dict, category: str | None = None) -> list[str]:
    selected = selected_account_codes(bundle)
    if is_all_categories(category):
        return selected
    requested = str(category).strip()
    matched = [code for code in selected if _categories_match(account_category(bundle, code), requested)]
    if matched:
        return matched
    bundle_category = str(bundle.get("category") or "").strip()
    if bundle_category and _categories_match(bundle_category, requested) and bundle_category.casefold() not in {"all", "*"}:
        return selected
    raise ForecastingError(
        f"Requested category {category!r} has no accounts in the loaded artifact."
    )


def validate_requested_accounts(bundle: dict, account_codes: list[str] | None) -> list[str]:
    selected = selected_account_codes(bundle)
    if not account_codes:
        return selected
    requested = [str(code).strip() for code in account_codes if str(code).strip()]
    if not requested:
        return selected
    unknown = [code for code in requested if code not in set(selected)]
    if unknown:
        raise UnsupportedAccountError(
            "Unsupported Budget Code(s): " + ", ".join(unknown)
        )
    return requested


def _predict_from_fitted(algorithm: str, fitted_model, steps: int):
    if fitted_model is None:
        raise ForecastingError("The loaded model is not available for forecasting.")
    if hasattr(fitted_model, "get_forecast"):
        prediction = fitted_model.get_forecast(steps=steps)
        raw = getattr(prediction, "predicted_mean", prediction)
        return _finite_floats(raw, steps)
    if hasattr(fitted_model, "forecast"):
        return _finite_floats(fitted_model.forecast(steps=steps), steps)
    raise ForecastingError(
        f"Prediction method is unavailable for the loaded {algorithm} model."
    )


def _account_model_entry(bundle: dict, code: str) -> dict:
    models = bundle.get("models") or {}
    records = bundle.get("account_records") or bundle.get("account_models") or {}
    entry = models.get(code) or models.get(str(code)) or records.get(code) or records.get(str(code))
    if not isinstance(entry, dict):
        raise ForecastingError(f"Forecast record for {code} is missing.")
    return entry


def _account_algorithm(entry: dict) -> str:
    algorithm = str(entry.get("algorithm") or entry.get("model_name") or "").strip()
    if not algorithm:
        status = str(entry.get("production_status") or "")
        if status == "ZERO_POLICY":
            return "ZERO_POLICY"
        raise ForecastingError("A selected model name is missing for a Budget Code.")
    return algorithm


def _account_fitted_model(entry: dict):
    fitted = entry.get("model") if entry.get("model") is not None else entry.get("trained_model")
    if fitted is None:
        raise ForecastingError("The loaded model is not available for forecasting.")
    return fitted


def _account_forecast_state(bundle: dict, code: str, entry: dict) -> dict:
    extra = (
        (bundle.get("account_models") or {}).get(code)
        or (bundle.get("account_models") or {}).get(str(code))
        or (bundle.get("account_records") or {}).get(code)
        or (bundle.get("account_records") or {}).get(str(code))
        or {}
    )
    metadata = entry.get("model_metadata") or {}
    state = (
        extra.get("required_forecast_state")
        or entry.get("required_forecast_state")
        or metadata.get("required_future_features")
        or {}
    )
    return state if isinstance(state, dict) else {}


def objective_model_failure(algorithm: str, fitted_model) -> str | None:
    """Reject a loaded model that is objectively invalid. Does not refit it.

    ARIMA-family root checks apply to legacy bundles as well. ETS, XGBoost,
    and Prophet are not rejected for ARIMA roots. A finite but unusual
    forecast is not an objective failure.
    """
    if algorithm in ARIMA_FAMILY and fitted_model is not None:
        failures = inspect_arima_stability(fitted_model, order=None)
        if failures:
            return "The fitted model is not stationary or invertible."
    return None


def forecast_account_series(bundle: dict, code: str, steps: int) -> list[float]:
    entry = _account_model_entry(bundle, code)
    status = str(entry.get("production_status") or "")
    if status in {"NO_DATA", "ALL_MISSING"}:
        raise ForecastingError(
            f"{code} has no historical data and cannot produce a forecast."
        )
    if status == "ZERO_POLICY" or _account_algorithm(entry) == "ZERO_POLICY":
        return [0.0] * int(steps)
    if status and status not in {"FITTED_MODEL", ""}:
        raise ForecastingError(
            f"{code} cannot contribute a point forecast ({status})."
        )
    algorithm = _account_algorithm(entry)
    fitted = _account_fitted_model(entry)
    invalid = objective_model_failure(algorithm, fitted)
    if invalid:
        raise ForecastingError(f"{code} {invalid}")
    if algorithm in STATEFUL_ALGORITHMS:
        state = _account_forecast_state(bundle, code, entry)
        history_months = list(entry.get("history_months") or bundle.get("history_months") or state.get("history_months") or [])
        if not history_months:
            raise ForecastingError(f"{algorithm} forecast state for {code} is missing history months.")
        try:
            values = forecast_from_fitted(algorithm, fitted, state, history_months, steps)
        except Exception as error:
            raise ForecastingError(
                f"{algorithm} could not generate a forecast for {code}: {error}"
            ) from error
        return _finite_floats(values, steps)
    return _predict_from_fitted(algorithm, fitted, steps)


def calculate_required_horizon(
    historical_end,
    start_month: str | None,
    end_month: str | None,
    forecast_months: int | None,
) -> tuple[list[pd.Timestamp], list[pd.Timestamp], str, str]:
    history_end = parse_month_timestamp(str(historical_end))
    origin = history_end + pd.offsets.MonthBegin(1)
    horizon_end = pd.Timestamp(year=int(history_end.year) + 3, month=12, day=1)
    allowed = _allowed_forecast_message(origin, horizon_end)
    if origin > horizon_end:
        raise InvalidForecastWindowError(allowed)
    if start_month and end_month:
        requested_start = parse_month_timestamp(start_month)
        requested_end = parse_month_timestamp(end_month)
        if requested_end < requested_start:
            raise InvalidForecastWindowError("End month cannot be before start month.")
        if (
            requested_start <= history_end
            or requested_end < origin
            or requested_start > horizon_end
            or requested_end > horizon_end
        ):
            raise InvalidForecastWindowError(allowed)
        horizon_end_requested = requested_end
        steps = len(month_range(origin, horizon_end_requested))
        if steps > MAX_FORECAST_MONTHS:
            raise InvalidForecastWindowError(
                f"Forecast horizon cannot exceed {MAX_FORECAST_MONTHS} months."
            )
        full_index = month_range(origin, horizon_end_requested)
        window = [ts for ts in full_index if requested_start <= ts <= requested_end]
        if not window:
            raise InvalidForecastWindowError("The requested forecast period is empty.")
        return (
            full_index,
            window,
            timestamp_to_iso(requested_start),
            timestamp_to_iso(requested_end),
        )

    steps = int(forecast_months) if forecast_months is not None else 6
    if steps < 1 or steps > MAX_FORECAST_MONTHS:
        raise InvalidForecastWindowError(
            f"forecast_months must be between 1 and {MAX_FORECAST_MONTHS}."
        )
    requested_end = origin + pd.offsets.MonthBegin(steps - 1)
    if requested_end > horizon_end:
        raise InvalidForecastWindowError(allowed)
    full_index = month_range(origin, requested_end)
    return (
        full_index,
        list(full_index),
        timestamp_to_iso(full_index[0]),
        timestamp_to_iso(full_index[-1]),
    )


def _yearly_status(months: list[pd.Timestamp]) -> tuple[str, int]:
    included = {timestamp_to_iso(ts) for ts in months}
    count = len(months)
    if count == 12:
        year = int(months[0].year)
        expected = {to_iso_month(year, month) for month in range(1, 13)}
        if included == expected:
            return "FULL_YEAR", 12
    return "PARTIAL_YEAR", count


def _aggregate_yearly(window: list[pd.Timestamp], monthly_by_code: dict[str, dict[str, float]]):
    by_year: dict[int, list[pd.Timestamp]] = {}
    for timestamp in window:
        by_year.setdefault(int(timestamp.year), []).append(timestamp)

    account_rows = []
    combined_rows = []
    for year in sorted(by_year):
        months = by_year[year]
        status, months_included = _yearly_status(months)
        year_total = 0.0
        for code, series in monthly_by_code.items():
            amount = float(sum(series[timestamp_to_iso(ts)] for ts in months))
            year_total += amount
            account_rows.append({
                "budget_code": code,
                "year": year,
                "forecast_amount": amount,
                "year_status": status,
                "months_included": months_included,
            })
        combined_rows.append({
            "year": year,
            "forecast_amount": year_total,
            "year_status": status,
            "months_included": months_included,
        })
    return account_rows, combined_rows


def historical_monthly_baseline(actuals) -> tuple[float | None, int]:
    """One overall average from the complete combined historical monthly totals."""
    amounts = []
    for row in actuals or []:
        try:
            amount = effective_historical_actual((row or {}).get("actual_amount"))
        except (TypeError, ValueError):
            continue
        if np.isfinite(amount):
            amounts.append(float(amount))
    count = len(amounts)
    if count < 1:
        return None, 0
    return float(sum(amounts) / count), count


def specific_month_historical_baseline(actuals, iso_month) -> tuple[float | None, int]:
    """Average of valid historical combined totals for the same calendar month."""
    target = str(iso_month or "")
    if len(target) < 7 or target[4] != "-":
        return None, 0
    calendar_month = target[5:7]
    amounts = []
    for row in actuals or []:
        month = str((row or {}).get("month") or "")
        if len(month) < 7 or month[4] != "-" or month[5:7] != calendar_month:
            continue
        if month >= target:
            continue
        try:
            amount = effective_historical_actual((row or {}).get("actual_amount"))
        except (TypeError, ValueError):
            continue
        if np.isfinite(amount):
            amounts.append(float(amount))
    count = len(amounts)
    if count < 1:
        return None, 0
    return float(sum(amounts) / count), count


def historical_actuals_from_bundle(bundle: dict, account_codes: list[str] | None = None) -> list[dict]:
    panel_months = [str(label) for label in bundle.get("history_months") or []]
    account_models = bundle.get("account_models") or bundle.get("account_records") or {}
    codes = [str(code) for code in (account_codes or bundle.get("selected_accounts") or [])]
    available = []
    for code in codes:
        entry = _account_record(bundle, code)
        if is_book_entry_account(code) or is_unavailable_account(entry):
            continue
        available.append(code)
    codes = available
    if not panel_months or not account_models:
        return []
    lookups = {}
    for code in codes:
        entry = account_models.get(code) or account_models.get(str(code)) or {}
        history = [str(item) for item in entry.get("history_months") or panel_months]
        values = list(entry.get("historical_values") or [])
        mapped = {}
        for index, label in enumerate(history):
            if index < len(values):
                mapped[label] = values[index]
        lookups[code] = mapped
    items = []
    for label in panel_months:
        amounts = []
        complete = True
        for code in codes:
            mapped = lookups.get(code) or {}
            if label not in mapped:
                complete = False
                break
            amount = effective_historical_actual(mapped[label])
            if not np.isfinite(amount):
                complete = False
                break
            amounts.append(convert_amount(float(amount)))
        if not complete:
            continue
        items.append({
            "month": timestamp_to_iso(parse_month_timestamp(label)),
            "actual_amount": float(sum(amounts)),
        })
    return items


def _horizon_quantile(quantile_by_horizon, horizon: int):
    if not quantile_by_horizon or int(horizon) < 1:
        return None
    keyed = {}
    for key, value in dict(quantile_by_horizon).items():
        amount = float(value)
        if np.isfinite(amount):
            keyed[int(key)] = amount
    return keyed.get(int(horizon))


def _max_calibrated_horizon(payload: dict | None, quantile_by_horizon) -> int | None:
    available = []
    for key, value in dict(quantile_by_horizon or {}).items():
        amount = float(value)
        if np.isfinite(amount):
            available.append(int(key))
    if available:
        return max(available)
    if payload is not None and payload.get("max_calibrated_horizon") is not None:
        try:
            declared = int(payload.get("max_calibrated_horizon"))
        except (TypeError, ValueError):
            return None
        if declared >= 1:
            return declared
    return None


def _conformal_payload(bundle: dict, overall: str | None = None) -> dict | None:
    payload = bundle.get("conformal_prediction")
    if not isinstance(payload, dict):
        return None
    conformal_algo = payload.get("overall_best_algorithm")
    if (
        overall
        and overall not in {None, "", PER_BUDGET_CODE_ALGORITHM}
        and conformal_algo
        and conformal_algo != overall
    ):
        return None
    return payload


def _interval_bounds(amount: float, quantile, clip_negative: bool) -> tuple[float | None, float | None]:
    if quantile is None or not np.isfinite(float(quantile)):
        return None, None
    lower = float(amount) - float(quantile)
    upper = float(amount) + float(quantile)
    if clip_negative:
        lower = max(0.0, lower)
    if not np.isfinite(lower) or not np.isfinite(upper):
        return None, None
    return float(lower), float(upper)


def _month_interval(amount: float, quantile_by_horizon, horizon: int, conformal, clip_negative: bool) -> dict:
    empty = {
        "lower_bound": None,
        "upper_bound": None,
        "interval_level": None,
        "interval_status": None,
    }
    if not conformal:
        return empty
    quantile = _horizon_quantile(quantile_by_horizon, horizon)
    if quantile is not None:
        lower, upper = _interval_bounds(amount, quantile, clip_negative)
        if lower is None or upper is None:
            return empty
        coverage = conformal.get("nominal_coverage")
        level = float(coverage) if coverage is not None and np.isfinite(float(coverage)) else None
        return {
            "lower_bound": lower,
            "upper_bound": upper,
            "interval_level": level,
            "interval_status": None,
        }
    max_horizon = _max_calibrated_horizon(conformal, quantile_by_horizon)
    if max_horizon is not None and int(horizon) > max_horizon:
        return {
            "lower_bound": None,
            "upper_bound": None,
            "interval_level": None,
            "interval_status": UNAVAILABLE_BEYOND_CALIBRATED_HORIZON,
        }
    return empty


def generate_monthly_forecast(
    bundle: dict,
    start_month: str | None = None,
    end_month: str | None = None,
    forecast_months: int | None = None,
    account_codes: list[str] | None = None,
    category: str | None = None,
    period_type: str | None = None,
) -> dict:
    """Generate account-wise and combined monthly/yearly forecasts. Predict-only."""
    try:
        book_entry_account_codes()
    except BookEntryConfigurationError as error:
        raise ForecastingError(str(error)) from error
    bounds = published_forecast_bounds(bundle)
    historical_end = bounds["history_end"]
    if period_type == "remaining_year":
        validated = validate_remaining_year_request(
            start_month,
            end_month,
            bundle=bundle,
        )
        start_month = validated["start_month"]
        end_month = validated["end_month"]
    selected = validate_requested_accounts(bundle, account_codes)
    allowed = set(accounts_for_category(bundle, category))
    selected = [code for code in selected if code in allowed]
    if not selected:
        raise ForecastingError(
            f"Requested category {category!r} has no matching accounts in the loaded artifact."
        )
    overall = bundle.get("overall_best_algorithm") or PER_BUDGET_CODE_ALGORITHM
    full_index, window, requested_start, requested_end = calculate_required_horizon(
        historical_end,
        start_month,
        end_month,
        forecast_months,
    )
    steps = len(full_index)
    full_iso = [timestamp_to_iso(ts) for ts in full_index]
    window_iso = [timestamp_to_iso(ts) for ts in window]
    names = _account_name_map(bundle)
    conformal = _conformal_payload(bundle, str(overall))
    clip_negative = bool(bundle.get("clip_negative_applied"))
    combined_quantiles = ((conformal or {}).get("combined") or {}).get("quantile_by_horizon") or {}
    combined_status = str(((conformal or {}).get("combined") or {}).get("status") or "")
    if combined_status in {"UNAVAILABLE", "INSUFFICIENT_CALIBRATION"}:
        combined_quantiles = {}
    account_payloads = (conformal or {}).get("accounts") or {}

    monthly_by_code: dict[str, dict[str, float]] = {}
    account_monthly = []
    unavailable = []
    included = []
    zero_policy_codes = []
    fitted_codes = []
    budget_code_forecasts = []
    for code in selected:
        category_name = account_category(bundle, code) or None
        account_name = names.get(code) or None
        try:
            if is_book_entry_account(code):
                raise ForecastingError(BOOK_ENTRY_REASON)
            entry = _account_model_entry(bundle, code)
            status = production_status_of(entry)
            if is_unavailable_account(entry):
                raise ForecastingError(UNAVAILABLE_REASON)
            values = forecast_account_series(bundle, code, steps)
        except ForecastingError as error:
            status = production_status_of(_account_record(bundle, code))
            if str(error) == BOOK_ENTRY_REASON or is_book_entry_account(code):
                reason = BOOK_ENTRY_REASON
                reason_code = BOOK_ENTRY_REASON
                reason_label = BOOK_ENTRY_REASON_LABEL
                bucket = "book_entry"
                status = "BOOK_ENTRY_ACCOUNT"
            else:
                reason = UNAVAILABLE_REASON if status in UNAVAILABLE_STATUSES else SAFE_UNAVAILABLE_REASON
                reason_code, reason_label, bucket = _coverage_reason(status)
                if reason_code == "OTHER" and status not in UNAVAILABLE_STATUSES:
                    reason_label = reason
            unavailable.append(_unavailable_record(
                code, account_name, category_name, status, reason, reason_code, reason_label, bucket,
            ))
            budget_code_forecasts.append({
                "budget_code": code,
                "account_name": account_name,
                "category": category_name,
                "available": False,
                "forecast": None,
                "algorithm": None,
                "forecast_type": None,
                "production_status": status or None,
                "source_status": status or "UNAVAILABLE",
                "display_status": unavailable[-1]["display_status"],
                "status": "UNAVAILABLE",
                "reason": reason,
                "reason_code": reason_code,
                "reason_label": reason_label,
            })
            continue
        sliced = {
            full_iso[index]: values[index]
            for index in range(steps)
            if full_iso[index] in set(window_iso)
        }
        if len(sliced) != len(window_iso):
            raise ForecastingError("The requested forecast period could not be extracted.")
        monthly_by_code[code] = sliced
        included.append(code)
        zero_policy = is_zero_policy_account(entry)
        if zero_policy:
            zero_policy_codes.append(code)
            algorithm = None
            forecast_type = "ZERO_POLICY"
            display_status = ZERO_POLICY_DISPLAY_STATUS
        else:
            fitted_codes.append(code)
            algorithm = _account_algorithm(entry)
            forecast_type = "FITTED_MODEL"
            display_status = AVAILABLE_DISPLAY_STATUS
        budget_code_forecasts.append({
            "budget_code": code,
            "account_name": account_name,
            "category": category_name,
            "available": True,
            "forecast": sliced,
            "algorithm": algorithm,
            "forecast_type": forecast_type,
            "production_status": status or forecast_type,
            "source_status": status or forecast_type,
            "display_status": display_status,
            "reason": None,
        })
        account_quantiles = (account_payloads.get(code) or account_payloads.get(str(code)) or {}).get("quantile_by_horizon") or {}
        for month in window_iso:
            horizon = full_iso.index(month) + 1
            interval = _month_interval(sliced[month], account_quantiles, horizon, conformal, clip_negative)
            account_monthly.append({
                "budget_code": code,
                "account_name": account_name,
                "month": month,
                "forecast_amount": sliced[month],
                **interval,
            })

    coverage_only = not included
    combined_amounts = []
    monthly_forecasts = []
    if not coverage_only:
        combined_available = bool(included) and not unavailable and combined_quantiles
        for month in window_iso:
            total = float(sum(monthly_by_code[code][month] for code in included))
            combined_amounts.append(total)
            horizon = full_iso.index(month) + 1
            interval = _month_interval(total, combined_quantiles if combined_available else {}, horizon, conformal if combined_available else None, clip_negative)
            monthly_forecasts.append({
                "month": month,
                "forecast_amount": total,
                **interval,
            })

    account_yearly, combined_yearly = _aggregate_yearly(window, monthly_by_code)
    for row in account_yearly:
        row["account_name"] = names.get(row["budget_code"]) or None

    overall_total = None if coverage_only else float(sum(combined_amounts))
    count = len(combined_amounts)
    historical_actuals = historical_actuals_from_bundle(bundle, included)
    historical_monthly_average, historical_month_count = historical_monthly_baseline(historical_actuals)
    for row in monthly_forecasts:
        specific_average, specific_count = specific_month_historical_baseline(historical_actuals, row["month"])
        row["specific_month_historical_average"] = specific_average
        row["specific_month_history_count"] = specific_count
    history_end_iso = timestamp_to_iso(parse_month_timestamp(str(historical_end)))
    forecast_start_iso = timestamp_to_iso(first_forecast_month(historical_end))
    response_category = ALL_CATEGORIES_LABEL if is_all_categories(category) else str(category)
    book_entry_count = sum(1 for item in unavailable if item.get("exclusion") == "book_entry")
    insufficient_count = sum(1 for item in unavailable if item.get("exclusion") == "insufficient")
    other_count = sum(1 for item in unavailable if item.get("exclusion") == "other")
    coverage = {
        "requested_account_count": len(selected),
        "total_budget_codes": len(selected),
        "total_selected_codes": len(selected),
        "forecasted_budget_codes": len(included),
        "forecasted_codes": len(included),
        "fitted_model_budget_codes": len(fitted_codes),
        "zero_policy_budget_codes": len(zero_policy_codes),
        "unavailable_budget_codes": len(unavailable),
        "book_entry_excluded": book_entry_count,
        "insufficient_data": insufficient_count,
        "other_unavailable": other_count,
        "included_in_totals": list(included),
        "included_count": len(included),
        "zero_policy_accounts": list(zero_policy_codes),
        "unavailable_accounts": unavailable,
        "unavailable_count": len(unavailable),
        "partial_total": bool(unavailable),
        "partial_forecast": bool(unavailable),
    }
    coverage_summary = {
        "total_selected_codes": len(selected),
        "forecasted_codes": len(included),
        "book_entry_excluded": book_entry_count,
        "insufficient_data": insufficient_count,
        "other_unavailable": other_count,
    }
    return {
        "forecast_type": str(bundle.get("forecast_type") or ""),
        "category": response_category,
        "target_description": "Selected-account monthly and yearly forecasts",
        "overall_best_algorithm": str(overall),
        "selected_account_count": len(selected),
        "selected_accounts": list(selected),
        "partial_forecast": bool(unavailable),
        "budget_code_forecasts": budget_code_forecasts,
        "historical_start": str(bundle.get("historical_start") or ""),
        "historical_end": str(bundle.get("historical_end") or ""),
        "history_end": history_end_iso,
        "forecast_start": forecast_start_iso,
        "forecast_end": requested_end,
        "requested_start_month": requested_start,
        "requested_end_month": requested_end,
        "forecast_month_count": count,
        "amount_unit": RESPONSE_AMOUNT_UNIT,
        "model_amount_unit": str(bundle.get("amount_unit") or MODEL_AMOUNT_UNIT),
        "response_amount_unit": RESPONSE_AMOUNT_UNIT,
        "conversion_factor": float(AMOUNT_CONVERSION_FACTOR),
        "clip_negative_applied": bool(bundle.get("clip_negative_applied")),
        "monthly_forecasts": monthly_forecasts,
        "account_monthly_forecasts": account_monthly,
        "combined_monthly_forecasts": monthly_forecasts,
        "account_yearly_forecasts": account_yearly,
        "combined_yearly_forecasts": combined_yearly,
        "historical_actuals": historical_actuals,
        "historical_monthly_average": historical_monthly_average,
        "historical_month_count": historical_month_count,
        "coverage_only": coverage_only,
        "overall_total": overall_total,
        "monthly_average": None if coverage_only or not count else float(overall_total / count),
        "minimum_monthly_forecast": None if coverage_only else float(min(combined_amounts)),
        "maximum_monthly_forecast": None if coverage_only else float(max(combined_amounts)),
        "generated_at": datetime.now(timezone.utc),
        "interval_method": str(conformal.get("method")) if conformal else None,
        "interval_coverage": float(conformal["nominal_coverage"]) if conformal and conformal.get("nominal_coverage") is not None else None,
        "forecast_coverage": coverage,
        "coverage": coverage_summary,
        "unavailable_accounts": unavailable,
    }


def without_book_entry_contributions(payload: dict | None) -> dict | None:
    """Drop Book Entry values from a stored forecast in memory. The saved record stays unchanged."""
    if not payload:
        return payload
    blocked = book_entry_account_codes()
    if not blocked:
        return payload
    forecasts = list(payload.get("budget_code_forecasts") or [])
    monthly_rows = list(payload.get("account_monthly_forecasts") or [])
    touches = any(str(item.get("budget_code") or "") in blocked for item in forecasts + monthly_rows)
    if not touches:
        return payload
    if not monthly_rows and (payload.get("monthly_forecasts") or payload.get("overall_total") is not None):
        blocked_payload = dict(payload)
        blocked_payload["book_entry_recalculation_blocked"] = True
        blocked_payload["budget_code_forecasts"] = [
            item for item in forecasts if str(item.get("budget_code") or "") not in blocked
        ]
        blocked_payload["monthly_forecasts"] = []
        blocked_payload["combined_monthly_forecasts"] = []
        blocked_payload["coverage_only"] = True
        blocked_payload["overall_total"] = None
        blocked_payload["monthly_average"] = None
        blocked_payload["minimum_monthly_forecast"] = None
        blocked_payload["maximum_monthly_forecast"] = None
        return blocked_payload
    cleaned = dict(payload)
    removed = [item for item in forecasts if str(item.get("budget_code") or "") in blocked]
    cleaned["budget_code_forecasts"] = [
        item for item in forecasts if str(item.get("budget_code") or "") not in blocked
    ]
    cleaned["account_monthly_forecasts"] = [
        item for item in monthly_rows if str(item.get("budget_code") or "") not in blocked
    ]
    unavailable = list(payload.get("unavailable_accounts") or [])
    listed = {str(item.get("budget_code") or item.get("account_code") or "") for item in unavailable}
    added = 0
    for item in removed:
        code = str(item.get("budget_code") or "")
        if not code or code in listed:
            continue
        listed.add(code)
        added += 1
        unavailable.append({
            "budget_code": code,
            "budget_name": item.get("budget_name") or item.get("account_name"),
            "account_name": item.get("account_name") or item.get("budget_name"),
            "category": item.get("category"),
            "status": "UNAVAILABLE",
            "reason": BOOK_ENTRY_REASON,
            "reason_code": BOOK_ENTRY_REASON,
            "reason_label": BOOK_ENTRY_REASON_LABEL,
            "exclusion": "book_entry",
        })
    cleaned["unavailable_accounts"] = unavailable
    coverage = dict(payload.get("coverage") or {})
    detailed = dict(payload.get("forecast_coverage") or {})
    for target in (coverage, detailed):
        if not target:
            continue
        if "forecasted_codes" in target:
            target["forecasted_codes"] = max(0, int(target.get("forecasted_codes") or 0) - added)
        if "forecasted_budget_codes" in target:
            target["forecasted_budget_codes"] = max(0, int(target.get("forecasted_budget_codes") or 0) - added)
        if "book_entry_excluded" in target or added:
            target["book_entry_excluded"] = int(target.get("book_entry_excluded") or 0) + added
        target["unavailable_accounts"] = unavailable
    if coverage:
        cleaned["coverage"] = coverage
    if detailed:
        cleaned["forecast_coverage"] = detailed
    sums: dict[str, float] = {}
    for item in cleaned["account_monthly_forecasts"]:
        month = str(item.get("month") or "")
        amount = item.get("forecast_amount")
        if month and amount is not None:
            sums[month] = sums.get(month, 0.0) + float(amount)
    if not sums:
        cleaned["monthly_forecasts"] = []
        cleaned["combined_monthly_forecasts"] = []
        cleaned["coverage_only"] = True
        cleaned["overall_total"] = None
        cleaned["monthly_average"] = None
        cleaned["minimum_monthly_forecast"] = None
        cleaned["maximum_monthly_forecast"] = None
        return cleaned
    rebuilt = []
    for row in payload.get("monthly_forecasts") or []:
        month = str(row.get("month") or "")
        if month not in sums:
            continue
        updated = dict(row)
        updated["forecast_amount"] = float(sums[month])
        rebuilt.append(updated)
    if not rebuilt:
        cleaned["monthly_forecasts"] = []
        cleaned["combined_monthly_forecasts"] = []
        cleaned["coverage_only"] = True
        cleaned["overall_total"] = None
        cleaned["monthly_average"] = None
        cleaned["minimum_monthly_forecast"] = None
        cleaned["maximum_monthly_forecast"] = None
        return cleaned
    cleaned["monthly_forecasts"] = rebuilt
    cleaned["combined_monthly_forecasts"] = rebuilt
    total = float(sum(item["forecast_amount"] for item in rebuilt))
    cleaned["overall_total"] = total
    cleaned["monthly_average"] = total / len(rebuilt)
    cleaned["minimum_monthly_forecast"] = min(item["forecast_amount"] for item in rebuilt)
    cleaned["maximum_monthly_forecast"] = max(item["forecast_amount"] for item in rebuilt)
    return cleaned

