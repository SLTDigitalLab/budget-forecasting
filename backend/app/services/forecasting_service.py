"""Predict-only forecasts from the cached all-budget artifact."""

from __future__ import annotations

from calendar import month_abbr
from datetime import datetime, timezone
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
MAX_FORECAST_END = pd.Timestamp(year=2030, month=12, day=1)
UNAVAILABLE_BEYOND_CALIBRATED_HORIZON = "unavailable_beyond_calibrated_horizon"
STATEFUL_ALGORITHMS = {"XGBoost", "Prophet", "ARIMAX", "SARIMAX"}
UNAVAILABLE_STATUSES = {
    "NO_DATA",
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
ZERO_POLICY_DISPLAY_STATUS = "Zero Policy"
AVAILABLE_DISPLAY_STATUS = "Available"
_MONTH_LOOKUP = {month_abbr[number]: number for number in range(1, 13)}
ENGINE_DIR = BACKEND_DIR / "model_training_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

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


def is_unavailable_account(entry: dict) -> bool:
    return production_status_of(entry) in UNAVAILABLE_STATUSES


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
    origin = first_forecast_month(historical_end)
    horizon_end = pd.Timestamp(MAX_FORECAST_END).replace(day=1)
    if origin > horizon_end:
        raise InvalidForecastWindowError(
            "The first forecast month is after the December 2030 horizon."
        )
    if start_month and end_month:
        requested_start = parse_month_timestamp(start_month)
        requested_end = parse_month_timestamp(end_month)
        if requested_start <= parse_month_timestamp(str(historical_end)):
            raise InvalidForecastWindowError(
                "Start month must be after the model's historical end."
            )
        if requested_end < requested_start:
            raise InvalidForecastWindowError("End month cannot be before start month.")
        if requested_end < origin:
            raise InvalidForecastWindowError(
                "End month must be after the model's historical end."
            )
        if requested_start > horizon_end or requested_end > horizon_end:
            raise InvalidForecastWindowError(
                "Forecast dates after December 2030 are not supported."
            )
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
        raise InvalidForecastWindowError(
            "Forecast dates after December 2030 are not supported."
        )
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
            amount = float((row or {}).get("actual_amount"))
        except (TypeError, ValueError):
            continue
        if np.isfinite(amount):
            amounts.append(amount)
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
            amount = float((row or {}).get("actual_amount"))
        except (TypeError, ValueError):
            continue
        if np.isfinite(amount):
            amounts.append(amount)
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
        if is_unavailable_account(entry):
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
            amount = mapped[label]
            if amount is None or (isinstance(amount, float) and not np.isfinite(amount)):
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
) -> dict:
    """Generate account-wise and combined monthly/yearly forecasts. Predict-only."""
    historical_end = bundle.get("historical_end")
    if not historical_end:
        raise ForecastingError("The loaded model is missing historical_end.")
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
        entry = _account_model_entry(bundle, code)
        status = production_status_of(entry)
        category_name = account_category(bundle, code) or None
        account_name = names.get(code) or None
        if is_unavailable_account(entry):
            unavailable.append({
                "account_code": code,
                "budget_code": code,
                "budget_name": account_name,
                "account_name": account_name,
                "category": category_name,
                "source_status": status,
                "display_status": UNAVAILABLE_DISPLAY_STATUS,
                "reason": UNAVAILABLE_REASON,
            })
            budget_code_forecasts.append({
                "budget_code": code,
                "account_name": account_name,
                "category": category_name,
                "available": False,
                "forecast": None,
                "algorithm": None,
                "forecast_type": None,
                "production_status": status,
                "source_status": status,
                "display_status": UNAVAILABLE_DISPLAY_STATUS,
                "reason": UNAVAILABLE_REASON,
            })
            continue
        try:
            values = forecast_account_series(bundle, code, steps)
        except ForecastingError as error:
            if status in UNAVAILABLE_STATUSES:
                unavailable.append({
                    "account_code": code,
                    "budget_code": code,
                    "budget_name": account_name,
                    "account_name": account_name,
                    "category": category_name,
                    "source_status": status or "UNAVAILABLE",
                    "display_status": UNAVAILABLE_DISPLAY_STATUS,
                    "reason": UNAVAILABLE_REASON,
                })
                budget_code_forecasts.append({
                    "budget_code": code,
                    "account_name": account_name,
                    "category": category_name,
                    "available": False,
                    "forecast": None,
                    "algorithm": None,
                    "forecast_type": None,
                    "production_status": status,
                    "source_status": status or "UNAVAILABLE",
                    "display_status": UNAVAILABLE_DISPLAY_STATUS,
                    "reason": UNAVAILABLE_REASON,
                })
                continue
            raise
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

    if not included:
        raise ForecastingError("No requested accounts could produce a point forecast.")

    combined_amounts = []
    monthly_forecasts = []
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

    overall_total = float(sum(combined_amounts))
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
    coverage = {
        "requested_account_count": len(selected),
        "total_budget_codes": len(selected),
        "forecasted_budget_codes": len(included),
        "fitted_model_budget_codes": len(fitted_codes),
        "zero_policy_budget_codes": len(zero_policy_codes),
        "unavailable_budget_codes": len(unavailable),
        "included_in_totals": list(included),
        "included_count": len(included),
        "zero_policy_accounts": list(zero_policy_codes),
        "unavailable_accounts": unavailable,
        "unavailable_count": len(unavailable),
        "partial_total": bool(unavailable),
        "partial_forecast": bool(unavailable),
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
        "overall_total": overall_total,
        "monthly_average": float(overall_total / count) if count else 0.0,
        "minimum_monthly_forecast": float(min(combined_amounts)),
        "maximum_monthly_forecast": float(max(combined_amounts)),
        "generated_at": datetime.now(timezone.utc),
        "interval_method": str(conformal.get("method")) if conformal else None,
        "interval_coverage": float(conformal["nominal_coverage"]) if conformal and conformal.get("nominal_coverage") is not None else None,
        "forecast_coverage": coverage,
    }

