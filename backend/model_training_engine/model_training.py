from __future__ import annotations

import argparse
import contextvars
import hashlib
import json
import os
import pickle
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.exponential_smoothing.ets import ETSModel
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX
from xgboost import XGBRegressor

ENGINE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_FILE = ENGINE_DIR / "dataset" / "original_actual_data.xlsx"
DEFAULT_OUTPUT_DIR = ENGINE_DIR / "output"
DEFAULT_MONTHLY_MODEL_FILE = DEFAULT_OUTPUT_DIR / "best_monthly_model.pkl"
DEFAULT_TOP12_MODEL_FILE = DEFAULT_OUTPUT_DIR / "best_top12_account_models.pkl"
DEFAULT_OVERALL_FAMILY_MODEL_FILE = DEFAULT_OUTPUT_DIR / "best_overall_family_top12_models.pkl"
LEGACY_ACCOUNTWISE_MODEL_FILE = DEFAULT_OUTPUT_DIR / "best_model.pkl"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from evaluation import (
    ALGORITHMS,
    DEFAULT_TEST_MONTHS,
    EVALUATION_METRICS,
    EXOGENOUS_FEATURES,
    GRID_REDUCTIONS,
    MIN_TRAIN_MONTHS,
    MODEL_NAMES,
    SEASONAL_PERIOD,
    XGBOOST_FEATURE_COLUMNS,
    XGBOOST_PARAMS,
    add_month_label,
    calendar_exog,
    evaluate_selected_accounts,
    evaluate_seven_models,
    evaluation_results_by_code,
    recursive_xgboost_forecast,
    require_successful_account_winners,
    select_account_winners,
    select_best_model,
    smoke_forecast_stats,
)
from conformal import CONFORMAL_ALPHA, calibrate_conformal_prediction
from preprocessing import (
    AMOUNT_UNIT,
    DEFAULT_CATEGORY,
    DEFAULT_MAX_INTERNAL_GAP,
    KIND_NUMBER,
    MAX_FORECAST_END,
    OUTCOME_NO_DATA,
    OUTCOME_READY,
    OUTCOME_ZERO_POLICY,
    STATUS_FIT_FAILED,
    STATUS_FITTED_MODEL,
    STATUS_INSUFFICIENT_HISTORY,
    STATUS_INVALID_DATA,
    STATUS_NO_DATA,
    STATUS_NO_VALID_WINNER,
    STATUS_NOT_EVALUABLE,
    STATUS_UNRESOLVED_MISSING,
    STATUS_ZERO_POLICY,
    calendar_split_sizes,
    category_matches,
    month_label,
    prepare_series_window,
    run_preprocessing,
    split_from_preprocessing,
)

DEFAULT_TOP_N = None

DEFAULT_FORECAST_MONTHS = 18
DEFAULT_IS_MODEL_FILE = DEFAULT_OUTPUT_DIR / "international_settlement_top12_models.pkl"
DEPLOYED_ARTIFACT_NAME = "international_settlement_top12_models.pkl"
CANDIDATE_ARTIFACT_NAME = "all_budget_code_models.pkl"
CANDIDATE_ALL_ACCOUNT_MODEL_FILE = DEFAULT_OUTPUT_DIR / CANDIDATE_ARTIFACT_NAME
TRAINING_README_NAME = "README.md"
ENGINE_README_PATH = ENGINE_DIR / TRAINING_README_NAME
ARTIFACT_SCHEMA_VERSION = "all_account_per_budget_code_v1"
RUNTIME_DEPENDENCIES_MARKDOWN = """\
`all_budget_code_models.pkl` uses the same runtime bundle schema as
`international_settlement_top12_models.pkl`. Loaders and forecast services read
those pickle fields directly. This README is an evaluation report only and is
not a runtime data source.

The live Generate Forecast path still loads `international_settlement_top12_models.pkl`
until the all-budget artifact is explicitly activated.
"""
PER_BUDGET_CODE_FORECAST_TYPE = "PER_BUDGET_CODE_BEST_MODELS"
PER_BUDGET_CODE_ALGORITHM = "PER_BUDGET_CODE"
PROTECTED_DEPLOYED_ARTIFACTS = {
    (DEFAULT_OUTPUT_DIR / DEPLOYED_ARTIFACT_NAME).resolve(),
}


def resolve_candidate_pickle_path(output_dir: Path, output_file: str | None = None) -> Path:
    name = str(output_file or CANDIDATE_ARTIFACT_NAME).strip() or CANDIDATE_ARTIFACT_NAME
    candidate = Path(name)
    path = candidate.resolve() if candidate.is_absolute() else (Path(output_dir) / candidate).resolve()
    if path in PROTECTED_DEPLOYED_ARTIFACTS or path.name == DEPLOYED_ARTIFACT_NAME:
        raise AssertionError(
            f"Refusing to overwrite the deployed production artifact at {path}. "
            f"New training must write {CANDIDATE_ARTIFACT_NAME} or another candidate filename."
        )
    return path


def _finite_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return number


def _is_na_scalar(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (pd.Series, pd.DataFrame, np.ndarray, list, tuple, dict, set)):
        return False
    try:
        result = pd.isna(value)
    except (ValueError, TypeError):
        return False
    if isinstance(result, (bool, np.bool_)):
        return bool(result)
    return False


def _record_mapping(record) -> dict:
    """Normalize a winner/outcome row to a dict. iterrows() yields a Series."""
    if record is None:
        return {}
    if isinstance(record, pd.Series):
        return record.to_dict()
    if isinstance(record, dict):
        return dict(record)
    return {}


def _record_get(record, key, default=None):
    mapping = _record_mapping(record)
    if key not in mapping:
        return default
    value = mapping[key]
    if _is_na_scalar(value):
        return default
    return value


def _record_text(record, key, default="") -> str:
    value = _record_get(record, key, default=None)
    if value is None:
        return default
    return str(value)


def _record_list(record, key) -> list:
    value = _record_get(record, key, default=None)
    if value is None:
        return []
    if isinstance(value, pd.Series):
        return list(value.tolist())
    if isinstance(value, np.ndarray):
        return list(value.tolist())
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _history_as_stored(values) -> list:
    stored = []
    for value in values:
        number = _finite_or_none(value)
        stored.append(number if number is not None else None)
    return stored


def _account_split_fields(outcome_row, window_months) -> dict:
    train_months = _record_list(outcome_row, "train_months")
    test_months = _record_list(outcome_row, "test_months")
    train_size = _record_get(outcome_row, "train_size")
    test_size = _record_get(outcome_row, "test_size")
    split_index = _record_get(outcome_row, "split_index")
    window_n = _record_get(outcome_row, "window_n_months")
    verified = _record_get(outcome_row, "history_start_verified")
    return {
        "history_months": list(window_months),
        "history_start": window_months[0] if window_months else None,
        "history_end": window_months[-1] if window_months else None,
        "train_months": train_months,
        "test_months": test_months,
        "train_size": int(train_size) if train_size is not None else int(len(train_months) or 0),
        "test_size": int(test_size) if test_size is not None else int(len(test_months) or 0),
        "split_index": int(split_index) if split_index is not None else int(len(train_months) or 0),
        "history_start_source": None if outcome_row is None else _record_get(outcome_row, "history_start_source"),
        "history_start_verified": None if outcome_row is None else (None if verified is None else bool(verified)),
        "window_n_months": int(window_n) if window_n is not None else int(len(window_months) or 0),
    }


def _eligible_account_records(preprocessing_result) -> list[dict]:
    eligible = preprocessing_result.get("eligible_accounts")
    if isinstance(eligible, pd.DataFrame):
        return eligible.to_dict(orient="records")
    if isinstance(eligible, list):
        return list(eligible)
    return []


def fit_production_model(best_result, monthly_history, history_months, label=None, quiet=False):
    heading = label or "the account's full prepared historical window"
    if not quiet:
        print(f"  Fitting a fresh production model on {heading}", flush=True)
    series = pd.Series(
        np.asarray(monthly_history, dtype=float),
        index=pd.RangeIndex(start=1, stop=len(monthly_history) + 1),
        dtype=float,
    )
    name = best_result["model_name"]
    parameters = dict(best_result["parameters"])
    state = {}

    if name == "ETS":
        ets_kwargs = {
            "error": parameters["error"],
            "trend": parameters["trend"],
            "seasonal": parameters["seasonal"],
            "damped_trend": parameters["damped_trend"],
        }
        if parameters.get("seasonal") is not None:
            ets_kwargs["seasonal_periods"] = parameters.get("seasonal_periods") or SEASONAL_PERIOD
        if parameters.get("initialization_method"):
            ets_kwargs["initialization_method"] = parameters["initialization_method"]
        fitted = ETSModel(series, **ets_kwargs).fit(disp=False)
    elif name == "Holt-Winters":
        hw_kwargs = {
            "trend": parameters["trend"],
            "damped_trend": bool(parameters.get("damped_trend", False)),
            "initialization_method": parameters.get("initialization_method") or "estimated",
        }
        if parameters.get("seasonal") is not None:
            hw_kwargs["seasonal"] = parameters["seasonal"]
            hw_kwargs["seasonal_periods"] = parameters.get("seasonal_periods") or SEASONAL_PERIOD
        fitted = ExponentialSmoothing(series, **hw_kwargs).fit(optimized=True)
    elif name == "ARIMA":
        fitted = ARIMA(
            series,
            order=tuple(parameters["order"]),
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit()
    elif name == "SARIMA":
        fitted = SARIMAX(
            series,
            order=tuple(parameters["order"]),
            seasonal_order=tuple(parameters["seasonal_order"]),
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit(disp=False)
    elif name == "XGBoost":
        from evaluation import create_xgboost_features

        fitted = XGBRegressor(**XGBOOST_PARAMS)
        feature_df = create_xgboost_features(series.to_numpy(dtype=float), history_months)
        fitted.fit(feature_df[XGBOOST_FEATURE_COLUMNS], feature_df["target"])
        state = {
            "feature_columns": list(XGBOOST_FEATURE_COLUMNS),
            "history": [float(value) for value in monthly_history],
            "history_months": list(history_months),
        }
        parameters = dict(XGBOOST_PARAMS)
        parameters["feature_columns"] = list(XGBOOST_FEATURE_COLUMNS)
    elif name == "ARIMAX":
        scaler = StandardScaler()
        exog = calendar_exog(history_months, start_time=0)
        exog_scaled = scaler.fit_transform(exog)
        fitted = SARIMAX(
            series,
            exog=exog_scaled,
            order=tuple(parameters["order"]),
            seasonal_order=(0, 0, 0, 0),
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit(disp=False)
        state = {
            "scaler": scaler,
            "exogenous_features": list(EXOGENOUS_FEATURES),
            "history_length": len(history_months),
        }
    elif name == "SARIMAX":
        scaler = StandardScaler()
        exog = calendar_exog(history_months, start_time=0)
        exog_scaled = scaler.fit_transform(exog)
        fitted = SARIMAX(
            series,
            exog=exog_scaled,
            order=tuple(parameters["order"]),
            seasonal_order=tuple(parameters["seasonal_order"]),
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit(disp=False)
        state = {
            "scaler": scaler,
            "exogenous_features": list(EXOGENOUS_FEATURES),
            "history_length": len(history_months),
        }
    elif name == "Prophet":
        from evaluation import build_prophet_model, prophet_training_frame

        fitted = build_prophet_model(parameters)
        train_df = prophet_training_frame(history_months, monthly_history)
        from evaluation import _prophet_library_quiet

        with _prophet_library_quiet():
            fitted.fit(train_df)
        state = {
            "freq": "MS",
            "history_months": list(history_months),
            "history_end": history_months[-1],
        }
    else:
        raise ValueError(f"Unsupported production model: {name}")

    if fitted is None:
        raise AssertionError("Production training did not produce a fitted model.")
    smoke = forecast_from_fitted(name, fitted, state, history_months, steps=3)
    if len(smoke) != 3 or not all(np.isfinite(smoke)):
        raise AssertionError("Production model failed the future-forecast smoke test.")
    if name == "Prophet":
        reloaded = pickle.loads(pickle.dumps(fitted, protocol=pickle.HIGHEST_PROTOCOL))
        reload_smoke = forecast_from_fitted(name, reloaded, state, history_months, steps=3)
        if len(reload_smoke) != 3 or not all(np.isfinite(reload_smoke)):
            raise AssertionError("Prophet failed the pickle reload smoke test.")
        state["serialization_reload_passed"] = True
        print("  Prophet serialization/reload: passed", flush=True)
    print("Production fit: PASSED", flush=True)
    print("Smoke-test future months:", ", ".join(f"{value:.4f}" for value in smoke), flush=True)
    return fitted, parameters, state, smoke


def _future_month_labels(history_months, steps):
    from evaluation import add_month_label

    labels = []
    current = history_months[-1]
    for _ in range(steps):
        current = add_month_label(current, 1)
        labels.append(current)
    return labels


def forecast_from_fitted(model_name, fitted_model, state, history_months, steps):
    if model_name == "XGBoost":
        return recursive_xgboost_forecast(
            fitted_model,
            state["history"],
            state["history_months"],
            state["feature_columns"],
            steps,
        )
    if model_name in {"ARIMAX", "SARIMAX"}:
        future_labels = _future_month_labels(history_months, steps)
        start_time = int(state.get("history_length") or len(history_months))
        future_exog = state["scaler"].transform(
            calendar_exog(future_labels, start_time=start_time)
        )
        values = np.asarray(fitted_model.forecast(steps=steps, exog=future_exog), dtype=float).reshape(-1)
    elif model_name == "Prophet":
        from evaluation import prophet_predict_future

        values = np.asarray(
            prophet_predict_future(fitted_model, history_months, steps),
            dtype=float,
        ).reshape(-1)
    else:
        values = np.asarray(fitted_model.forecast(steps), dtype=float).reshape(-1)
    if values.size != steps or not np.isfinite(values).all():
        raise AssertionError(f"{model_name} production forecast is not finite.")
    return [float(value) for value in values]


class _PointForecast:
    def __init__(self, values):
        self.predicted_mean = np.asarray(values, dtype=float)


class FittedBudgetCodeModel:
    """Pickleable per-account wrapper. Encapsulates the fitted estimator and any
    learned preprocessing needed to produce original-unit point forecasts.
    """

    def __init__(self, algorithm, fitted_model, state=None, history_months=None):
        self.algorithm = str(algorithm)
        self.fitted_model = fitted_model
        self.state = dict(state or {})
        self.history_months = list(history_months or [])

    def get_forecast(self, steps=1, **_kwargs):
        values = forecast_from_fitted(
            self.algorithm,
            self.fitted_model,
            self.state,
            self.history_months,
            int(steps),
        )
        return _PointForecast(values)

    def forecast(self, steps=1, **_kwargs):
        return np.asarray(self.get_forecast(steps=steps).predicted_mean, dtype=float)


def verify_all_budget_code_models_pickle(payload) -> list[str]:
    failures = []
    if not isinstance(payload, dict):
        return ["all_budget_code_models.pkl must be a dictionary of budget_code -> fitted model."]
    forbidden = {
        "evaluation_results",
        "conformal_prediction",
        "account_records",
        "account_models",
        "forecast_coverage",
        "metric_winners",
        "account_winners",
        "monthly_account_forecasts",
        "zero_policy_accounts",
    }
    extra = [key for key in payload if str(key) in forbidden]
    if extra:
        failures.append("Pickle contains metadata keys that are not fitted models: " + ", ".join(extra))
    if not payload:
        failures.append("Pickle contains no successfully trained models.")
    for code, model in payload.items():
        if not str(code).strip():
            failures.append("A model key is missing a Budget Code.")
            continue
        if isinstance(model, dict):
            failures.append(f"{code} value must be a fitted model object, not a metadata dictionary.")
            continue
        if not (hasattr(model, "forecast") or hasattr(model, "get_forecast")):
            failures.append(f"{code} fitted model has no forecast or get_forecast method.")
    return failures


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, pd.DataFrame):
        return _jsonable(value.to_dict(orient="records"))
    if isinstance(value, pd.Series):
        return _jsonable(value.to_dict())
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if not np.isfinite(number) else number
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (pd.Timestamp, datetime, Path)):
        return str(value)
    if value is None or isinstance(value, (str, bool)):
        return value
    return str(value)


def _readme_json(value) -> str:
    return json.dumps(_jsonable(value), indent=2)


def _records_for_readme(records: dict) -> list[dict]:
    drop = {"model", "trained_model", "required_forecast_state"}
    rows = []
    for code, record in (records or {}).items():
        if not isinstance(record, dict):
            continue
        row = {key: item for key, item in record.items() if key not in drop}
        row.setdefault("budget_code", code)
        rows.append(_jsonable(row))
    return rows


def write_training_readme(readme_path: Path, payload: dict) -> Path:
    readme_path = Path(readme_path)
    lines = [
        "# All-budget-code training outputs",
        "",
        "This file is documentation only. The backend must not load it as a runtime data source.",
        "",
        "## Pickle contract",
        "",
        f"`{CANDIDATE_ARTIFACT_NAME}` is a runtime forecast bundle with the same load/predict",
        "schema as `international_settlement_top12_models.pkl`, covering every eligible budget code.",
        "It is a metadata dictionary (not a bare `{code: model}` map) and includes model records,",
        "account identities, production statuses, conformal quantiles, coverage, and monthly/annual tables.",
        "",
        "ZERO_POLICY, unresolved, invalid, insufficient-history, and FIT_FAILED accounts are stored",
        "as explicit records. Each fitted account keeps its own local winning algorithm.",
        "Do not copy stale top-12 account lists or `feature_selection_results`.",
        "",
        "## Runtime dependencies that cannot live in this pickle",
        "",
        payload.get("runtime_dependencies_markdown") or RUNTIME_DEPENDENCIES_MARKDOWN,
        "",
        "## Latest training run",
        "",
        f"- Timestamp (UTC): {payload.get('training_timestamp') or 'NOT RUN'}",
        f"- Duration seconds: {payload.get('training_duration_seconds')}",
        f"- Pickle: {payload.get('pickle_path')}",
        f"- SHA-256: {payload.get('sha256')}",
        f"- Deployed artifact modified: {payload.get('deployed_modified', False)}",
        f"- Candidate activated: no",
        "",
        "## Selected algorithms (local winners)",
        "",
        "```json",
        _readme_json(payload.get("account_winners") or []),
        "```",
        "",
        "## Evaluation results (selection-holdout)",
        "",
        "```json",
        _readme_json(payload.get("evaluation_results") or []),
        "```",
        "",
        "## Preprocessing details",
        "",
        "```json",
        _readme_json(payload.get("preprocessing") or {}),
        "```",
        "",
        "## Zero-policy accounts",
        "",
        "```json",
        _readme_json(payload.get("zero_policy_accounts") or []),
        "```",
        "",
        "## Unresolved and failed accounts",
        "",
        "```json",
        _readme_json(payload.get("unavailable_accounts") or []),
        "```",
        "",
        "## Conformal calibration (documentation only)",
        "",
        "```json",
        _readme_json(payload.get("conformal_prediction") or {}),
        "```",
        "",
        "## Account status records (documentation only)",
        "",
        "```json",
        _readme_json(payload.get("account_records") or []),
        "```",
        "",
        "## Verification",
        "",
        "```json",
        _readme_json(payload.get("verification") or {}),
        "```",
        "",
    ]
    readme_path.parent.mkdir(parents=True, exist_ok=True)
    readme_path.write_text("\n".join(lines), encoding="utf-8")
    return readme_path


def _json_safe_diagnostics(diagnostics: dict | None) -> dict:
    if not diagnostics:
        return {}
    skip = {"stl_trend", "stl_seasonal", "stl_resid", "series"}
    payload = {key: value for key, value in diagnostics.items() if key not in skip}
    payload["has_stl_components"] = bool(diagnostics.get("stl_available"))
    return payload


def build_monthly_model_bundle(
    preprocessing_result,
    evaluation_results,
    best_result,
    fitted_model,
    production_parameters,
    forecast_state,
    seasonality_diagnostics=None,
    production_smoke=None,
):
    successful = [row for row in evaluation_results if row["status"] == "success"]
    failed = [row for row in evaluation_results if row["status"] == "failed"]
    evaluated_models = []
    for row in evaluation_results:
        evaluated_models.append({
            "model_name": row["model_name"],
            "status": row["status"],
            "parameters": row["parameters"],
            "mae": row["mae"],
            "rmse": row["rmse"],
            "wape": row["wape"],
            "mape": row["mape"],
            "error": row["error"],
            "captures_seasonality": row.get("captures_seasonality"),
            "rolling_wape_mean": row.get("rolling_wape_mean"),
            "rolling_wape_std": row.get("rolling_wape_std"),
            "rolling_successful_folds": row.get("rolling_successful_folds"),
            "rolling_failed_folds": row.get("rolling_failed_folds"),
            "rolling_folds": row.get("rolling_folds"),
            "residual_diagnostics": row.get("residual_diagnostics"),
            "candidates_evaluated": row.get("candidates_evaluated"),
            "candidates_succeeded": row.get("candidates_succeeded"),
        })
    metrics = {
        "mae": best_result["mae"],
        "rmse": best_result["rmse"],
        "wape": best_result["wape"],
        "mape": best_result["mape"],
        "rolling_wape_mean": best_result.get("rolling_wape_mean"),
        "rolling_wape_std": best_result.get("rolling_wape_std"),
        "rolling_successful_folds": best_result.get("rolling_successful_folds"),
        "rolling_failed_folds": best_result.get("rolling_failed_folds"),
    }
    return {
        "artifact_version": 2,
        "forecast_type": "MONTHLY_COMBINED_TOTAL",
        "target_description": "Combined monthly total of selected 12 accounts",
        "amount_unit": preprocessing_result.get("amount_unit") or AMOUNT_UNIT,
        "selected_account_codes": list(preprocessing_result["selected_codes"]),
        "selected_account_count": len(preprocessing_result["selected_codes"]),
        "history_start": preprocessing_result["history_months"][0],
        "history_end": preprocessing_result["history_months"][-1],
        "history_months": list(preprocessing_result["history_months"]),
        "monthly_history": [float(value) for value in preprocessing_result["monthly_history"]],
        "train_month_count": TRAIN_MONTHS,
        "test_month_count": TEST_MONTHS,
        "evaluated_models": evaluated_models,
        "successful_model_count": len(successful),
        "failed_model_count": len(failed),
        "best_model_name": best_result["model_name"],
        "best_model_parameters": production_parameters,
        "best_model_params": production_parameters,
        "best_evaluation_metrics": metrics,
        "selection_reason": best_result.get("selection_reason"),
        "models_seasonality": best_result.get("captures_seasonality"),
        "rolling_validation": {
            "folds": best_result.get("rolling_folds"),
            "wape_mean": best_result.get("rolling_wape_mean"),
            "wape_std": best_result.get("rolling_wape_std"),
            "successful_folds": best_result.get("rolling_successful_folds"),
            "failed_folds": best_result.get("rolling_failed_folds"),
        },
        "seasonality_diagnostics": _json_safe_diagnostics(seasonality_diagnostics),
        "grid_reductions": list(GRID_REDUCTIONS),
        "production_smoke_forecast": production_smoke,
        "fitted_model": fitted_model,
        "required_forecast_state": forecast_state,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _structure_failures(bundle):
    failures = []
    if bundle.get("artifact_version") != 2:
        failures.append(f"artifact_version is {bundle.get('artifact_version')}, expected 2.")
    if bundle.get("forecast_type") != "MONTHLY_COMBINED_TOTAL":
        failures.append(f"forecast_type is {bundle.get('forecast_type')}.")
    if bundle.get("selected_account_count") != 12:
        failures.append("selected_account_count must be 12.")
    codes = bundle.get("selected_account_codes") or []
    if len(codes) != 12 or len(set(codes)) != 12:
        failures.append("selected_account_codes must contain exactly 12 unique codes.")
    history = bundle.get("monthly_history") or []
    months = bundle.get("history_months") or []
    if len(history) != 42:
        failures.append(f"monthly_history has {len(history)} values, expected 42.")
    else:
        try:
            values = [float(value) for value in history]
            if not all(np.isfinite(values)):
                failures.append("monthly_history contains a non-finite value.")
        except (TypeError, ValueError):
            failures.append("monthly_history contains a non-numeric value.")
    if len(months) != 42:
        failures.append(f"history_months has {len(months)} labels, expected 42.")
    elif months[0] != "Jan-2023" or months[-1] != "Jun-2026":
        failures.append("history_months are not chronological from Jan-2023 through Jun-2026.")
    if not bundle.get("best_model_name"):
        failures.append("best_model_name is missing.")
    if bundle.get("fitted_model") is None:
        failures.append("fitted_model is missing.")
    metrics = bundle.get("best_evaluation_metrics") or {}
    for key in ("mae", "rmse", "wape"):
        if metrics.get(key) is None:
            failures.append(f"best_evaluation_metrics.{key} is missing.")
    evaluated = bundle.get("evaluated_models") or []
    if len(evaluated) != len(MODEL_NAMES):
        failures.append(
            f"evaluated_models contains {len(evaluated)} entries, expected {len(MODEL_NAMES)}."
        )
    names = [row.get("model_name") for row in evaluated]
    if names != MODEL_NAMES:
        failures.append(f"evaluated_models names are {names}, expected {MODEL_NAMES}.")
    for row in evaluated:
        if row.get("status") not in {"success", "failed"}:
            failures.append(f"{row.get('model_name')} has invalid status {row.get('status')}.")
    if bundle.get("successful_model_count", 0) < 1:
        failures.append("At least one model must have succeeded.")
    if "models" in bundle:
        failures.append("Account-specific models must not be stored in this artifact.")
    return failures


def atomic_save_pickle(bundle, pickle_path: Path):
    pickle_path = pickle_path.resolve()
    pickle_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_handle = tempfile.NamedTemporaryFile(
        dir=str(pickle_path.parent),
        prefix=".best_monthly_model.",
        suffix=".pkl.tmp",
        delete=False,
    )
    tmp_path = Path(tmp_handle.name)
    try:
        with tmp_handle:
            pickle.dump(bundle, tmp_handle, protocol=pickle.HIGHEST_PROTOCOL)
        with open(tmp_path, "rb") as file:
            reloaded = pickle.load(file)
        failures = _structure_failures(reloaded)
        if failures:
            raise AssertionError("Temporary Pickle failed structure validation: " + "; ".join(failures))
        tmp_path.replace(pickle_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return pickle_path


def verify_saved_monthly_pickle(pickle_path: Path):
    pickle_path = pickle_path.resolve()
    print("[8/8] Verifying best_monthly_model.pkl")
    if not pickle_path.exists():
        raise FileNotFoundError(f"Monthly model was not found at: {pickle_path}")
    file_size = pickle_path.stat().st_size
    if file_size <= 0:
        raise AssertionError("best_monthly_model.pkl is empty.")
    with open(pickle_path, "rb") as file:
        bundle = pickle.load(file)
    failures = _structure_failures(bundle)
    smoke = forecast_from_fitted(
        bundle["best_model_name"],
        bundle["fitted_model"],
        bundle.get("required_forecast_state") or {},
        bundle["history_months"],
        steps=3,
    )
    if len(smoke) != 3:
        failures.append("Production smoke forecast did not return 3 months.")
    if failures:
        for failure in failures:
            print(f"  - {failure}")
        raise AssertionError("best_monthly_model.pkl verification failed.")
    print("MONTHLY PICKLE VERIFICATION: PASSED")
    print(f"Pickle file size: {file_size} bytes")
    print("Smoke-test forecast:", ", ".join(f"{value:.4f}" for value in smoke))
    return bundle, file_size, smoke


_progress_callback = contextvars.ContextVar("retrain_progress_callback", default=None)
_progress_meta = contextvars.ContextVar("retrain_progress_meta", default=None)


def _log(message: str = "") -> None:
    print(message, flush=True)
    callback = _progress_callback.get()
    if callback is None or not message:
        return
    meta = _progress_meta.get() or {}
    try:
        callback(
            {
                "stage": meta.get("stage") or "training",
                "message": message,
                "current": meta.get("current"),
                "total": meta.get("total"),
                "budget_code": meta.get("budget_code"),
            }
        )
    except Exception:
        return


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compact_family_result(row: dict) -> dict:
    return {
        "model_name": row.get("model_name"),
        "status": row.get("status"),
        "parameters": row.get("parameters") or {},
        "mae": row.get("mae"),
        "rmse": row.get("rmse"),
        "wape": row.get("wape"),
        "mape": row.get("mape"),
        "smape": row.get("smape"),
        "error": row.get("error"),
        "failure_examples": row.get("failure_examples") or [],
        "captures_seasonality": row.get("captures_seasonality"),
        "rolling_wape_mean": row.get("rolling_wape_mean"),
        "rolling_wape_std": row.get("rolling_wape_std"),
        "rolling_successful_folds": row.get("rolling_successful_folds"),
        "rolling_failed_folds": row.get("rolling_failed_folds"),
        "candidates_evaluated": row.get("candidates_evaluated"),
        "candidates_succeeded": row.get("candidates_succeeded"),
    }


def _account_name_map(preprocessing_result) -> dict[str, str]:
    stats = preprocessing_result.get("account_statistics")
    names = {}
    if stats is None:
        return names
    for _, row in stats.iterrows():
        names[str(row["Account Code"])] = str(row.get("Account Name") or "")
    return names


def _top12_structure_failures(bundle: dict) -> list[str]:
    failures = []
    if bundle.get("artifact_version") != 1:
        failures.append("artifact_version must be 1.")
    if bundle.get("forecast_type") != "TOP12_ACCOUNT_MODELS_MONTHLY_AGGREGATE":
        failures.append("forecast_type is invalid.")
    if bundle.get("amount_unit") != AMOUNT_UNIT:
        failures.append("amount_unit must be LKR_MILLIONS.")
    if bundle.get("selected_account_count") != 12:
        failures.append("selected_account_count must be 12.")
    if bundle.get("candidate_family_count") != 8:
        failures.append("candidate_family_count must be 8.")
    codes = [str(code) for code in bundle.get("selected_account_codes") or []]
    if len(codes) != 12 or len(set(codes)) != 12:
        failures.append("selected_account_codes must contain 12 unique codes.")
    models = bundle.get("account_models") or {}
    if not isinstance(models, dict) or len(models) != 12:
        failures.append("account_models must contain exactly 12 entries.")
    history = bundle.get("combined_monthly_history") or []
    if len(history) != 42:
        failures.append("combined_monthly_history must contain 42 values.")
    else:
        try:
            values = np.asarray(history, dtype=float)
            if values.size != 42 or not np.isfinite(values).all():
                failures.append("combined_monthly_history contains a non-finite value.")
        except (TypeError, ValueError):
            failures.append("combined_monthly_history is not numeric.")
    months = list(bundle.get("history_months") or [])
    if len(months) != 42:
        failures.append("history_months must contain 42 labels.")
    elif months[0] != "Jan-2023" or months[-1] != "Jun-2026":
        failures.append("history_months must run Jan-2023 through Jun-2026.")
    for code in codes:
        entry = models.get(code)
        if not isinstance(entry, dict):
            failures.append(f"account_models is missing {code}.")
            continue
        if not entry.get("model_name"):
            failures.append(f"{code} model_name is missing.")
        if entry.get("fitted_model") is None:
            failures.append(f"{code} fitted_model is missing.")
        if not isinstance(entry.get("required_forecast_state"), dict):
            failures.append(f"{code} required_forecast_state is missing.")
        historical = entry.get("historical_values") or []
        if len(historical) != 42:
            failures.append(f"{code} historical_values length is {len(historical)}.")
        else:
            try:
                hist_values = np.asarray(historical, dtype=float)
                if not np.isfinite(hist_values).all():
                    failures.append(f"{code} historical_values contain a non-finite value.")
            except (TypeError, ValueError):
                failures.append(f"{code} historical_values are not numeric.")
        holdout = entry.get("holdout_metrics") or {}
        rolling = entry.get("rolling_validation") or {}
        for key in ("mae", "rmse", "wape"):
            value = holdout.get(key)
            if value is None or not np.isfinite(float(value)):
                failures.append(f"{code} holdout_metrics.{key} is missing or not finite.")
        mean_wape = rolling.get("mean_wape")
        if mean_wape is not None and not np.isfinite(float(mean_wape)):
            failures.append(f"{code} rolling_validation.mean_wape is not finite.")
        if entry.get("model_name") in {"XGBoost", "ARIMAX", "SARIMAX", "Prophet"} and not entry.get("required_forecast_state"):
            failures.append(f"{code} is missing required inference state.")
        if entry.get("model_name") == "Prophet":
            state = entry.get("required_forecast_state") or {}
            if state.get("freq") != "MS" or not state.get("history_end"):
                failures.append(f"{code} Prophet inference state is incomplete.")
    evaluation_results = bundle.get("evaluation_results") or {}
    if set(evaluation_results) != set(codes):
        failures.append("evaluation_results must contain one record per selected account.")
    else:
        for code, rows in evaluation_results.items():
            names = [row.get("model_name") for row in rows]
            if names != MODEL_NAMES:
                failures.append(f"{code} evaluation_results must include all 8 families.")
    return failures


def atomic_save_top12_pickle(bundle: dict, pickle_path: Path) -> Path:
    pickle_path = pickle_path.resolve()
    pickle_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_handle = tempfile.NamedTemporaryFile(
        dir=str(pickle_path.parent),
        prefix=".best_top12_account_models.",
        suffix=".pkl.tmp",
        delete=False,
    )
    tmp_path = Path(tmp_handle.name)
    try:
        with tmp_handle:
            pickle.dump(bundle, tmp_handle, protocol=pickle.HIGHEST_PROTOCOL)
        with open(tmp_path, "rb") as file:
            reloaded = pickle.load(file)
        failures = _top12_structure_failures(reloaded)
        if failures:
            raise AssertionError("Temporary Pickle failed validation: " + "; ".join(failures))
        for code, entry in reloaded["account_models"].items():
            smoke = forecast_from_fitted(
                entry["model_name"],
                entry["fitted_model"],
                entry.get("required_forecast_state") or {},
                reloaded["history_months"],
                steps=3,
            )
            if len(smoke) != 3 or not all(np.isfinite(smoke)):
                raise AssertionError(f"{code} failed the reloaded smoke test.")
        tmp_path.replace(pickle_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return pickle_path


def _finite_stats(values):
    numbers = [float(value) for value in values if value is not None and np.isfinite(float(value))]
    if not numbers:
        return None, None, None
    array = np.asarray(numbers, dtype=float)
    mean = float(np.mean(array))
    median = float(np.median(array))
    std = float(np.std(array, ddof=1)) if array.size > 1 else 0.0
    return mean, median, std


def compare_overall_families(account_family_evaluations, selected_codes):
    rows = []
    for family in MODEL_NAMES:
        rolling = []
        holdout = []
        mae = []
        rmse = []
        mape = []
        smape = []
        successful = []
        failed = []
        for code in selected_codes:
            result = (account_family_evaluations.get(code) or {}).get(family) or {}
            ok = (
                result.get("status") == "success"
                and result.get("rolling_wape_mean") is not None
                and np.isfinite(float(result["rolling_wape_mean"]))
                and result.get("wape") is not None
                and np.isfinite(float(result["wape"]))
            )
            if ok:
                successful.append(code)
                rolling.append(result["rolling_wape_mean"])
                holdout.append(result["wape"])
                mae.append(result.get("mae"))
                rmse.append(result.get("rmse"))
                mape.append(result.get("mape"))
                smape.append(result.get("smape"))
            else:
                failed.append({
                    "account_code": code,
                    "reason": result.get("error") or "Family evaluation failed or metrics are missing.",
                })
        avg_roll, med_roll, std_roll = _finite_stats(rolling)
        avg_hold, med_hold, _std_hold = _finite_stats(holdout)
        avg_mae, _med_mae, _std_mae = _finite_stats(mae)
        avg_rmse, _med_rmse, _std_rmse = _finite_stats(rmse)
        avg_mape, _med_mape, _std_mape = _finite_stats(mape)
        avg_smape, _med_smape, _std_smape = _finite_stats(smape)
        rows.append({
            "model_family": family,
            "successful_accounts": len(successful),
            "failed_accounts": len(failed),
            "failed_account_details": failed,
            "average_rolling_wape": avg_roll,
            "median_rolling_wape": med_roll,
            "rolling_wape_std_across_accounts": std_roll,
            "average_holdout_wape": avg_hold,
            "median_holdout_wape": med_hold,
            "average_mae": avg_mae,
            "average_rmse": avg_rmse,
            "average_mape": avg_mape,
            "average_smape": avg_smape,
            "full_coverage": len(successful) == 12,
        })
    return rows


def select_overall_family(comparison_rows):
    inf = float("inf")

    def _rank_key(row):
        return (
            row["average_rolling_wape"] if row["average_rolling_wape"] is not None else inf,
            row["median_rolling_wape"] if row["median_rolling_wape"] is not None else inf,
            row["average_holdout_wape"] if row["average_holdout_wape"] is not None else inf,
            row["average_rmse"] if row["average_rmse"] is not None else inf,
            row["average_mae"] if row["average_mae"] is not None else inf,
            row["rolling_wape_std_across_accounts"] if row["rolling_wape_std_across_accounts"] is not None else inf,
            row["model_family"],
        )

    eligible = [
        row for row in comparison_rows
        if row.get("full_coverage") and row.get("average_rolling_wape") is not None
    ]
    if eligible:
        winner = sorted(eligible, key=_rank_key)[0]
        reason = (
            f"Selected {winner['model_family']} because it succeeded on 12/12 accounts "
            f"with the lowest average rolling-origin WAPE ({winner['average_rolling_wape']:.4f}). "
            f"Secondary checks: median rolling WAPE={winner['median_rolling_wape']}, "
            f"average holdout WAPE={winner['average_holdout_wape']}, "
            f"average RMSE={winner['average_rmse']}."
        )
        return winner, reason, False

    fallback = sorted(
        comparison_rows,
        key=lambda row: (
            -int(row.get("successful_accounts") or 0),
            row["average_rolling_wape"] if row["average_rolling_wape"] is not None else inf,
            row["model_family"],
        ),
    )[0]
    reason = (
        f"No family succeeded on all 12 accounts. Fallback candidate is {fallback['model_family']} "
        f"by highest coverage ({fallback['successful_accounts']}/12) then lowest average rolling WAPE "
        f"({fallback['average_rolling_wape']}). A production artifact is not written unless this family "
        f"can successfully fit all 12 accounts."
    )
    return fallback, reason, True


def _overall_family_structure_failures(bundle: dict) -> list[str]:
    failures = []
    if bundle.get("artifact_version") != 1:
        failures.append("artifact_version must be 1.")
    if bundle.get("forecast_type") != "TOP12_ACCOUNT_WISE_MONTHLY":
        failures.append("forecast_type must be TOP12_ACCOUNT_WISE_MONTHLY.")
    if bundle.get("amount_unit") != AMOUNT_UNIT:
        failures.append("amount_unit must be LKR_MILLIONS.")
    for forbidden in (
        "combined_monthly_history",
        "combined_future_forecasts",
        "combined_monthly_totals",
        "overall_total",
    ):
        if forbidden in bundle:
            failures.append(f"{forbidden} must not be stored in this artifact.")
    if bundle.get("selected_account_count") != 12:
        failures.append("selected_account_count must be 12.")
    if bundle.get("candidate_family_count") != 8:
        failures.append("candidate_family_count must be 8.")
    if list(bundle.get("candidate_families") or []) != list(MODEL_NAMES):
        failures.append("candidate_families must match the eight supported families.")
    codes = [str(code) for code in bundle.get("selected_account_codes") or []]
    if len(codes) != 12 or len(set(codes)) != 12:
        failures.append("selected_account_codes must contain 12 unique codes.")
    overall_family = bundle.get("overall_best_model_family")
    if overall_family not in MODEL_NAMES:
        failures.append("overall_best_model_family is missing or invalid.")
    comparison = bundle.get("overall_comparison") or []
    if [row.get("model_family") for row in comparison] != list(MODEL_NAMES):
        failures.append("overall_comparison must contain all eight families in order.")
    models = bundle.get("account_models") or {}
    if not isinstance(models, dict) or len(models) != 12:
        failures.append("account_models must contain exactly 12 entries.")
    months = list(bundle.get("history_months") or [])
    if len(months) != 42 or months[0] != "Jan-2023" or months[-1] != "Jun-2026":
        failures.append("history_months must run Jan-2023 through Jun-2026.")
    evaluations = bundle.get("account_family_evaluations") or {}
    if set(evaluations) != set(codes):
        failures.append("account_family_evaluations must contain one record per selected account.")
    for code in codes:
        entry = models.get(code)
        if not isinstance(entry, dict):
            failures.append(f"account_models is missing {code}.")
            continue
        family = entry.get("model_family") or entry.get("model_name")
        if family != overall_family:
            failures.append(f"{code} production family is {family}, expected {overall_family}.")
        if entry.get("fitted_model") is None:
            failures.append(f"{code} fitted_model is missing.")
        if not isinstance(entry.get("required_forecast_state"), dict):
            failures.append(f"{code} required_forecast_state is missing.")
        historical = entry.get("historical_values") or []
        if len(historical) != 42:
            failures.append(f"{code} historical_values length is {len(historical)}.")
        elif not np.isfinite(np.asarray(historical, dtype=float)).all():
            failures.append(f"{code} historical_values contain a non-finite value.")
        holdout = entry.get("holdout_metrics") or {}
        for key in ("mae", "rmse", "wape"):
            value = holdout.get(key)
            if value is None or not np.isfinite(float(value)):
                failures.append(f"{code} holdout_metrics.{key} is missing or not finite.")
        if family in {"XGBoost", "ARIMAX", "SARIMAX", "Prophet"} and not entry.get("required_forecast_state"):
            failures.append(f"{code} is missing required inference state.")
        if family == "Prophet":
            state = entry.get("required_forecast_state") or {}
            if state.get("freq") != "MS" or not state.get("history_end"):
                failures.append(f"{code} Prophet inference state is incomplete.")
        account_eval = evaluations.get(code) or {}
        if list(account_eval) != list(MODEL_NAMES):
            failures.append(f"{code} family evaluations must include all 8 families.")
    return failures


def atomic_save_overall_family_pickle(bundle: dict, pickle_path: Path) -> Path:
    pickle_path = pickle_path.resolve()
    pickle_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_handle = tempfile.NamedTemporaryFile(
        dir=str(pickle_path.parent),
        prefix=".best_overall_family_top12_models.",
        suffix=".pkl.tmp",
        delete=False,
    )
    tmp_path = Path(tmp_handle.name)
    try:
        with tmp_handle:
            pickle.dump(bundle, tmp_handle, protocol=pickle.HIGHEST_PROTOCOL)
        with open(tmp_path, "rb") as file:
            reloaded = pickle.load(file)
        failures = _overall_family_structure_failures(reloaded)
        if failures:
            raise AssertionError("Temporary Pickle failed validation: " + "; ".join(failures))
        overall_family = reloaded["overall_best_model_family"]
        for code, entry in reloaded["account_models"].items():
            family = entry.get("model_family") or entry.get("model_name")
            if family != overall_family:
                raise AssertionError(f"{code} reloaded family does not match the overall winner.")
            smoke = forecast_from_fitted(
                family,
                entry["fitted_model"],
                entry.get("required_forecast_state") or {},
                reloaded["history_months"],
                steps=3,
            )
            if len(smoke) != 3 or not all(np.isfinite(smoke)):
                raise AssertionError(f"{code} failed the reloaded smoke test.")
        tmp_path.replace(pickle_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return pickle_path


def resolve_user_path(file_path: str | None, default_path: Path) -> Path:
    if not file_path:
        return default_path.resolve()
    path = Path(file_path)
    if path.is_absolute():
        return path.resolve()
    candidates = [
        Path.cwd() / path,
        ENGINE_DIR / path,
        ENGINE_DIR.parent.parent / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return (ENGINE_DIR / path).resolve()


def future_month_index(history_end, forecast_months=None, forecast_end=None):
    latest = pd.Timestamp(history_end).replace(day=1)
    start = latest + pd.offsets.MonthBegin(1)
    horizon_end = pd.Timestamp(MAX_FORECAST_END).replace(day=1)
    if start > horizon_end:
        raise ValueError(
            f"The first forecast month {start.date()} is after the December 2030 horizon."
        )
    if forecast_end:
        end = pd.Timestamp(forecast_end).replace(day=1)
        if end < start:
            raise ValueError(
                f"--forecast-end {end.date()} is before the first forecast month {start.date()}."
            )
        if end > horizon_end:
            raise ValueError("Forecast end cannot be after December 2030.")
        return pd.date_range(start, end, freq="MS")
    months = int(forecast_months if forecast_months is not None else DEFAULT_FORECAST_MONTHS)
    if months <= 0:
        raise ValueError("--forecast-months must be a positive integer.")
    end = start + pd.offsets.MonthBegin(months - 1)
    if end > horizon_end:
        end = horizon_end
    return pd.date_range(start, end, freq="MS")


def aggregate_yearly_forecasts(monthly_df: pd.DataFrame, value_columns):
    rows = []
    monthly_df = monthly_df.copy()
    monthly_df["year"] = pd.to_datetime(monthly_df["month"]).dt.year
    for year, group in monthly_df.groupby("year", sort=True):
        payload = {
            "year": int(year),
            "month_count": int(len(group)),
            "is_partial_year": bool(len(group) < 12),
            "year_status": (
                f"PARTIAL YEAR ({len(group)} months)" if len(group) < 12 else "FULL YEAR"
            ),
        }
        for column in value_columns:
            payload[column] = float(group[column].sum())
        rows.append(payload)
    return pd.DataFrame(rows)


def _model_algorithm(entry: dict | None) -> str:
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("algorithm") or entry.get("model_name") or "").strip()


def _fitted_model(entry: dict | None):
    if not isinstance(entry, dict):
        return None
    return entry.get("model") if entry.get("model") is not None else entry.get("trained_model")


def verify_international_settlement_pickle(bundle: dict, selected_codes) -> list[str]:
    failures = []
    schema = str(bundle.get("artifact_schema_version") or "")
    selected_codes = [str(code) for code in selected_codes]
    if bundle.get("forecast_type") != PER_BUDGET_CODE_FORECAST_TYPE:
        failures.append(f"forecast_type must be {PER_BUDGET_CODE_FORECAST_TYPE}.")
    overall = bundle.get("overall_best_algorithm")
    if overall not in {None, "", PER_BUDGET_CODE_ALGORITHM}:
        failures.append("overall_best_algorithm must not override per-budget-code selections.")
    if bundle.get("model_selection_policy") not in {None, PER_BUDGET_CODE_FORECAST_TYPE, PER_BUDGET_CODE_ALGORITHM}:
        failures.append("model_selection_policy must remain per-budget-code.")
    if not bundle.get("historical_start") or not bundle.get("historical_end"):
        failures.append("historical period is missing.")
    if int(bundle.get("detected_month_count") or 0) != len(bundle.get("detected_month_columns") or []):
        failures.append("detected_month_count does not match detected_month_columns.")
    models = bundle.get("models") or {}
    records = bundle.get("account_records") or bundle.get("account_models") or {}
    required = [
        "eligible_accounts",
        "selected_accounts",
        "evaluation_metrics",
        "metric_winners",
        "account_winners",
        "evaluation_results",
        "evaluation_results_by_code",
        "forecast_start",
        "forecast_end",
        "monthly_account_forecasts",
        "monthly_combined_forecast",
        "yearly_account_forecasts",
        "yearly_combined_forecast",
        "year_status",
    ]
    for key in required:
        if key not in bundle:
            failures.append(f"{key} is missing.")
    metrics = [str(item).upper() for item in bundle.get("evaluation_metrics") or []]
    if "MASE" not in metrics:
        failures.append("evaluation metrics must include MASE.")
    if schema == ARTIFACT_SCHEMA_VERSION:
        if "feature_selection_results" in bundle:
            failures.append("feature_selection_results must not be stored.")
        if bundle.get("category") in {None, ""}:
            failures.append("category is missing.")
        if set(str(code) for code in bundle.get("selected_accounts") or []) != set(selected_codes):
            failures.append("selected account list does not match the provided codes.")
        if not records:
            failures.append("account_records are missing.")
        fitted_codes = []
        for code in selected_codes:
            record = records.get(code) or records.get(str(code)) or models.get(code) or models.get(str(code))
            if not isinstance(record, dict):
                failures.append(f"{code} account record is missing.")
                continue
            status = str(record.get("production_status") or "")
            algorithm = _model_algorithm(record)
            fitted = _fitted_model(record)
            if status == STATUS_FITTED_MODEL:
                fitted_codes.append(code)
                if not algorithm or algorithm == PER_BUDGET_CODE_ALGORITHM:
                    failures.append(f"{code} selected model name is missing.")
                if fitted is None:
                    failures.append(f"{code} fitted model is missing.")
                metadata = record.get("training_metadata") or {}
                if metadata.get("retrained_on_full_history") is not True:
                    failures.append(f"{code} was not marked as retrained on full history.")
            elif status == STATUS_ZERO_POLICY:
                if fitted is not None:
                    failures.append(f"{code} zero-policy record must not include a fitted model.")
            elif status == STATUS_NO_DATA:
                if fitted is not None:
                    failures.append(f"{code} no-data record must not include a fitted model.")
                if not record.get("status_reason"):
                    failures.append(f"{code} no-data record is missing a reason.")
            elif status in {
                STATUS_UNRESOLVED_MISSING,
                STATUS_INVALID_DATA,
                STATUS_INSUFFICIENT_HISTORY,
                STATUS_NOT_EVALUABLE,
                STATUS_NO_VALID_WINNER,
                STATUS_FIT_FAILED,
            }:
                if not record.get("status_reason"):
                    failures.append(f"{code} unresolved/failed record is missing a reason.")
            else:
                failures.append(f"{code} production_status {status!r} is not recognized.")
        if set(str(code) for code in models) != set(fitted_codes):
            failures.append("models keys must match FITTED_MODEL accounts only.")
        if "forecast_coverage" not in bundle:
            failures.append("forecast_coverage is missing.")
        if "history_months" not in bundle:
            failures.append("history_months is missing.")
        conformal = bundle.get("conformal_prediction")
        if not isinstance(conformal, dict):
            failures.append("conformal_prediction is missing.")
        elif conformal.get("method") != "rolling_origin_absolute_residual":
            failures.append("conformal_prediction method is invalid.")
        elif conformal.get("overall_best_algorithm") not in {None, "", PER_BUDGET_CODE_ALGORITHM}:
            failures.append("conformal_prediction must not claim one overall-best algorithm.")
        return failures

    if bundle.get("category") != DEFAULT_CATEGORY and "int" not in str(bundle.get("category") or "").casefold():
        failures.append("category is missing.")
    if len(models) != 12:
        failures.append(f"model count is {len(models)}, expected 12.")
    if set(str(code) for code in models) != set(str(code) for code in selected_codes):
        failures.append("selected account list does not match the model dictionary keys.")
    selected_algorithms = []
    for code in selected_codes:
        entry = models.get(code) or models.get(str(code))
        if not isinstance(entry, dict):
            failures.append(f"{code} model entry is invalid.")
            continue
        algorithm = _model_algorithm(entry)
        if not algorithm or algorithm == PER_BUDGET_CODE_ALGORITHM:
            failures.append(f"{code} selected model name is missing.")
        if _fitted_model(entry) is None:
            failures.append(f"{code} fitted model is missing.")
        metadata = entry.get("training_metadata") or {}
        if metadata.get("retrained_on_full_history") is not True:
            failures.append(f"{code} was not marked as retrained on full history.")
        selected_algorithms.append(algorithm)
    if selected_algorithms and len(set(selected_algorithms)) == 1 and bundle.get("overall_best_algorithm") not in {None, "", PER_BUDGET_CODE_ALGORITHM}:
        failures.append("overall_best_algorithm must not override per-budget-code selections.")
    conformal = bundle.get("conformal_prediction")
    if not isinstance(conformal, dict):
        failures.append("conformal_prediction is missing.")
    else:
        if conformal.get("method") != "rolling_origin_absolute_residual":
            failures.append("conformal_prediction method is invalid.")
        combined = conformal.get("combined") or {}
        combined_quantiles = combined.get("quantile_by_horizon") or {}
        if 1 not in combined_quantiles and "1" not in combined_quantiles:
            failures.append("conformal_prediction combined 1-step quantile is missing.")
        accounts = conformal.get("accounts") or {}
        for code in selected_codes:
            entry = accounts.get(code) or accounts.get(str(code)) or {}
            quantiles = entry.get("quantile_by_horizon") or {}
            if 1 not in quantiles and "1" not in quantiles:
                failures.append(f"conformal_prediction 1-step quantile is missing for {code}.")
    return failures


class RetrainInProgress(RuntimeError):
    """Another retraining command already holds the production publish lock."""


def _validate_with_production_loader(bundle: dict) -> None:
    backend_dir = ENGINE_DIR.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.services.model_loader import ModelLoadError, _validate_bundle

    try:
        _validate_bundle(bundle)
    except ModelLoadError as error:
        raise AssertionError(f"Production artifact validation failed: {error}") from error


def publish_validated_production_pickle(
    temp_path: Path,
    destination: Path,
    bundle: dict,
    expected_account_count: int | None = None,
) -> Path:
    """Validate a complete candidate, then atomically replace the destination pickle."""
    temp_path = Path(temp_path)
    destination = Path(destination).resolve()
    try:
        if destination.name == DEPLOYED_ARTIFACT_NAME or destination in PROTECTED_DEPLOYED_ARTIFACTS:
            raise AssertionError(
                f"Refusing to overwrite the deployed production artifact at {destination}."
            )
        if not temp_path.is_file() or temp_path.stat().st_size <= 0:
            raise AssertionError("Temporary pickle is empty.")
        if destination.name == CANDIDATE_ARTIFACT_NAME:
            selected = [str(code) for code in bundle.get("selected_accounts") or []]
            if expected_account_count is not None and len(selected) != int(expected_account_count):
                raise AssertionError(
                    f"Trained budget-code count {len(selected)} does not match "
                    f"the master snapshot count {int(expected_account_count)}."
                )
            _validate_with_production_loader(bundle)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path.replace(destination)
        return destination
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def save_validated_candidate(
    temp_path: Path,
    destination: Path,
    bundle: dict,
    expected_account_count: int | None = None,
) -> Path:
    """Validate a deferred candidate without replacing the production pickle."""
    temp_path = Path(temp_path)
    destination = Path(destination).resolve()
    try:
        if destination.name in {CANDIDATE_ARTIFACT_NAME, DEPLOYED_ARTIFACT_NAME} or destination in PROTECTED_DEPLOYED_ARTIFACTS:
            raise AssertionError("Deferred training must not write a production pickle.")
        if not temp_path.is_file() or temp_path.stat().st_size <= 0:
            raise AssertionError("Temporary pickle is empty.")
        selected = [str(code) for code in bundle.get("selected_accounts") or []]
        if expected_account_count is not None and len(selected) != int(expected_account_count):
            raise AssertionError(
                f"Trained budget-code count {len(selected)} does not match "
                f"the master snapshot count {int(expected_account_count)}."
            )
        _validate_with_production_loader(bundle)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path.replace(destination)
        return destination
    except Exception:
        if temp_path.exists() and temp_path.resolve() != destination:
            temp_path.unlink()
        raise


def commit_training_artifact(
    temp_path: Path,
    destination: Path,
    bundle: dict,
    *,
    publish_production: bool,
    expected_account_count: int | None = None,
) -> Path:
    if publish_production:
        return publish_validated_production_pickle(
            temp_path,
            destination,
            bundle,
            expected_account_count=expected_account_count,
        )
    return save_validated_candidate(
        temp_path,
        destination,
        bundle,
        expected_account_count=expected_account_count,
    )


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _retrain_lock_path(output_dir: Path) -> Path:
    return Path(output_dir) / ".all_budget_code_models.retrain.lock"


def _lock_is_stale(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return False
    return not _process_is_running(pid)


def acquire_retrain_lock(output_dir: Path):
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = _retrain_lock_path(directory)
    for _attempt in range(2):
        try:
            descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if _lock_is_stale(path):
                try:
                    path.unlink()
                except OSError as error:
                    raise RetrainInProgress(
                        "Another retraining run is already publishing the production artifact."
                    ) from error
                continue
            raise RetrainInProgress(
                "Another retraining run is already publishing the production artifact."
            )
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        return descriptor, path
    raise RetrainInProgress("Another retraining run is already publishing the production artifact.")


def release_retrain_lock(descriptor: int, path: Path) -> None:
    try:
        os.close(descriptor)
    finally:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass


def run_training(
    input_file: str | None = None,
    output_dir: str | None = None,
    category: str | None = None,
    top_n: int | None = None,
    test_months: int | None = None,
    forecast_months: int | None = DEFAULT_FORECAST_MONTHS,
    forecast_end: str | None = None,
    clip_negative: bool = False,
    output_file: str | None = None,
    regular_expense_accounts=None,
    max_internal_gap: int = DEFAULT_MAX_INTERNAL_GAP,
    expected_account_count: int | None = None,
    publish_production: bool = True,
    progress_callback=None,
):
    """Evaluate seven algorithms per budget code, then refit each code's own winner."""
    callback_token = _progress_callback.set(progress_callback)
    meta_token = _progress_meta.set({"stage": "loading"})
    try:
        return _run_training_work(
            input_file=input_file,
            output_dir=output_dir,
            category=category,
            top_n=top_n,
            test_months=test_months,
            forecast_months=forecast_months,
            forecast_end=forecast_end,
            clip_negative=clip_negative,
            output_file=output_file,
            regular_expense_accounts=regular_expense_accounts,
            max_internal_gap=max_internal_gap,
            expected_account_count=expected_account_count,
            publish_production=publish_production,
        )
    finally:
        _progress_callback.reset(callback_token)
        _progress_meta.reset(meta_token)


def _run_training_work(
    input_file: str | None = None,
    output_dir: str | None = None,
    category: str | None = None,
    top_n: int | None = None,
    test_months: int | None = None,
    forecast_months: int | None = DEFAULT_FORECAST_MONTHS,
    forecast_end: str | None = None,
    clip_negative: bool = False,
    output_file: str | None = None,
    regular_expense_accounts=None,
    max_internal_gap: int = DEFAULT_MAX_INTERNAL_GAP,
    expected_account_count: int | None = None,
    publish_production: bool = True,
):
    started = time.perf_counter()
    input_path = resolve_user_path(input_file, DEFAULT_INPUT_FILE)
    output_path = resolve_user_path(output_dir, DEFAULT_OUTPUT_DIR) if output_dir else DEFAULT_OUTPUT_DIR.resolve()
    if output_dir and not Path(output_dir).exists() and not output_path.exists():
        output_path = (ENGINE_DIR / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    pickle_path = resolve_candidate_pickle_path(output_path, output_file)
    deployed_path = (DEFAULT_OUTPUT_DIR / DEPLOYED_ARTIFACT_NAME).resolve()
    monthly_path = (output_path / "best_monthly_model.pkl").resolve()
    backup_path = (output_path / "best_monthly_model_pre_seasonal.pkl").resolve()
    previous_top12_path = (output_path / "best_top12_account_models.pkl").resolve()
    previous_overall_path = (output_path / "best_overall_family_top12_models.pkl").resolve()
    excel_stat = input_path.stat() if input_path.exists() else None
    deployed_stat = deployed_path.stat() if deployed_path.exists() else None
    monthly_stat = monthly_path.stat() if monthly_path.exists() else None
    backup_stat = backup_path.stat() if backup_path.exists() else None
    previous_top12_stat = previous_top12_path.stat() if previous_top12_path.exists() else None
    previous_overall_stat = previous_overall_path.stat() if previous_overall_path.exists() else None

    interpolate_accounts = [str(code) for code in (regular_expense_accounts or [])]
    requested_category = None if category is None or str(category).strip().casefold() in {"", "all", "*"} else str(category)
    _log(f"Original dataset: {input_path}")
    _log(f"Training output: {output_path}")
    _log(f"Candidate Pickle: {pickle_path}")
    _log(f"Deployed artifact remains: {deployed_path}")
    _log(f"Category: {requested_category or 'ALL'}")
    _log("Missing values: per-Budget-Code mean (zeros included, NaN excluded)")
    if test_months is not None:
        _log("Ignoring a shared holdout length; each account uses its own historical-window 80/20 split.")
    if not input_path.exists():
        raise FileNotFoundError(f"Original Actuals Excel file was not found at: {input_path}")

    output_path.mkdir(parents=True, exist_ok=True)
    _progress_meta.set({"stage": "preprocessing"})
    preprocessing_result = run_preprocessing(
        str(input_path),
        category=requested_category,
        top_n=top_n,
        regular_expense_accounts=interpolate_accounts,
        max_internal_gap=max_internal_gap,
    )
    selected_codes = [str(code) for code in preprocessing_result["account_codes"]]
    if not selected_codes:
        raise AssertionError("No eligible budget codes were available after preprocessing.")
    summary = preprocessing_result.get("imputation_summary") or {}
    if summary:
        _log(f"Total Budget Codes: {summary.get('total_budget_codes')}")
        _log(f"Calendar Months: {summary.get('calendar_months')}")
        _log(f"Zero values preserved: {summary.get('zero_values_preserved')}")
        _log(f"Missing values detected: {summary.get('missing_values_detected')}")
        _log(f"Missing values filled using same-Budget-Code mean: {summary.get('missing_values_filled')}")
        _log(f"Unable to impute/train: {summary.get('unable_to_impute_or_train')}")
    history_months = list(preprocessing_result["history_months"])
    history_dates = list(preprocessing_result["month_dates"])
    detected_month_count = int(preprocessing_result["detected_month_count"])
    if len(history_months) != detected_month_count:
        raise AssertionError("Detected month labels do not match the detected month count.")
    selected_matrix = preprocessing_result["selected_matrix"]
    kind_matrix = preprocessing_result["kind_matrix"]
    outcomes = preprocessing_result["account_outcomes"]
    name_map = _account_name_map(preprocessing_result)
    _progress_meta.set({"stage": "evaluation"})
    evaluation_payload = evaluate_selected_accounts(
        selected_codes,
        selected_matrix,
        history_months,
        account_outcomes=outcomes,
        algorithms=ALGORITHMS,
        kind_matrix=kind_matrix,
        prepared_train_by_account=preprocessing_result.get("prepared_train_by_account"),
        observed_test_mask=preprocessing_result.get("observed_test_mask"),
        interpolate_accounts=interpolate_accounts,
        max_internal_gap=max_internal_gap,
    )
    evaluation_table = evaluation_payload["evaluation_table"]
    metric_winners = evaluation_payload["metric_winners"]
    account_winners = select_account_winners(evaluation_table)
    winner_by_code = {}
    winner_metrics = {}
    for _, row in account_winners.iterrows():
        code = str(row["Budget Code"])
        algorithm = row.get("Winning Algorithm")
        if algorithm is not None and not (isinstance(algorithm, float) and pd.isna(algorithm)):
            winner_by_code[code] = str(algorithm)
        winner_metrics[code] = row
    account_family_evaluations = evaluation_payload["account_family_evaluations"]
    winner_counts: dict[str, int] = {}
    for algorithm in winner_by_code.values():
        winner_counts[str(algorithm)] = winner_counts.get(str(algorithm), 0) + 1
    _log("")
    _log("========== WINNER SUMMARY ==========")
    for algorithm, count in sorted(winner_counts.items()):
        _log(f"  {algorithm}: {count}")
    _log(f"  No statistical winner: {len(selected_codes) - len(winner_by_code)}")
    _log("")
    _log("========== PER-BUDGET-CODE SELECTED MODELS ==========")
    _log(f"{'Budget Code':<16} {'Selected Model':<16} {'Status':<22} {'WAPE':>10}")
    _log("-" * 80)
    for code in selected_codes:
        row = winner_metrics.get(code)
        if row is None:
            _log(f"{code:<16} {'':<16} {'NO_RECORD':<22}")
            continue
        algorithm = _record_get(row, "Winning Algorithm")
        label = "" if algorithm is None else str(algorithm)
        wape = _record_get(row, "WAPE")
        wape_text = f"{float(wape):10.4f}" if wape is not None and np.isfinite(float(wape)) else f"{'n/a':>10}"
        _log(f"{code:<16} {label:<16} {_record_text(row, 'Status'):<22} {wape_text}")
    _log("")
    _log("Conformal calibration uses each budget code's selected algorithm.")
    _log("No overall-best algorithm was selected.")

    forecast_index = future_month_index(history_dates[-1], forecast_months, forecast_end)
    forecast_labels = [month_label(ts) for ts in forecast_index]
    _log(f"Forecast start: {forecast_labels[0]}")
    _log(f"Forecast end: {forecast_labels[-1]}")
    _log(f"Forecast months: {len(forecast_labels)}")

    account_models = {}
    models = {}
    account_records = {}
    monthly_forecast_rows = []
    included_codes = []
    unavailable = []
    conformal_codes = []
    conformal_histories = {}
    parameters_by_code = {}
    algorithm_by_code = {}
    outcome_lookup = {str(row["account_code"]): row for _, row in outcomes.iterrows()}

    for index, code in enumerate(selected_codes, 1):
        outcome_row = outcome_lookup.get(code)
        window_months = list(history_months)
        if outcome_row is not None:
            window_months = _record_list(outcome_row, "train_months") + _record_list(outcome_row, "test_months")
            if not window_months:
                window_months = list(history_months)
        raw_full = np.asarray(selected_matrix.loc[code, window_months], dtype=float)
        kinds_full = np.asarray(kind_matrix.loc[code, window_months], dtype=object)
        prepared_full, methods_full, full_outcome, full_reason = prepare_series_window(
            raw_full,
            kinds_full,
        )
        eval_winner = winner_by_code.get(code)
        winner_row = winner_metrics.get(code)
        eval_status = _record_text(winner_row, "Status")
        production_status = None
        status_reason = full_reason
        _progress_meta.set(
            {
                "stage": "final_training",
                "current": index,
                "total": len(selected_codes),
                "budget_code": code,
            }
        )
        _log("")
        _log(f"[Final Training {index}/{len(selected_codes)}] {code}")
        _log(f"  Winner: {eval_winner or eval_status or full_outcome}")

        if full_outcome == OUTCOME_NO_DATA:
            production_status = STATUS_NO_DATA
            status_reason = full_reason or "ALL_MISSING"
            unavailable.append({"account_code": code, "status": production_status, "reason": status_reason})
            record = {
                "budget_code": code,
                "account_name": name_map.get(code, ""),
                "category": "" if outcome_row is None else _record_text(outcome_row, "category"),
                "algorithm": None,
                "model_name": None,
                "model": None,
                "trained_model": None,
                "model_specification": {"policy": "NO_DATA"},
                "production_status": production_status,
                "status_reason": status_reason,
                "preprocessing_outcome": full_outcome,
                "history_start": window_months[0],
                "history_end": window_months[-1],
                "history_start_source": None if outcome_row is None else _record_get(outcome_row, "history_start_source"),
                "history_months": list(window_months),
                "historical_values": _history_as_stored(raw_full),
                "prepared_full_history": _history_as_stored(prepared_full),
                "imputation_methods": [str(item) for item in methods_full],
                "required_forecast_state": {},
                "evaluation_metrics": {},
                "metric_win_count": 0,
                "training_metadata": {
                    "training_start": window_months[0],
                    "training_end": window_months[-1],
                    "number_of_observations": int(len(window_months)),
                    "frequency": "MS",
                    "retrained_on_full_history": False,
                    "zero_policy": False,
                    "no_data": True,
                },
            }
            record.update(_account_split_fields(outcome_row, window_months))
            account_records[code] = record
            account_models[code] = record
            _log("  Status: NO_DATA (no historical numeric observations, no forecast)")
            continue

        if full_outcome == OUTCOME_ZERO_POLICY:
            production_status = STATUS_ZERO_POLICY
            status_reason = full_reason
            future_values = [0.0] * len(forecast_labels)
            included_codes.append(code)
            monthly_forecast_rows.append(pd.Series(future_values, name=code))
            record = {
                "budget_code": code,
                "account_name": name_map.get(code, ""),
                "category": "" if outcome_row is None else _record_text(outcome_row, "category"),
                "algorithm": None,
                "model_name": None,
                "model": None,
                "trained_model": None,
                "model_specification": {"policy": "ZERO_POLICY"},
                "production_status": production_status,
                "status_reason": status_reason,
                "preprocessing_outcome": full_outcome,
                "history_start": window_months[0],
                "history_end": window_months[-1],
                "history_start_source": None if outcome_row is None else _record_get(outcome_row, "history_start_source"),
                "history_months": list(window_months),
                "historical_values": _history_as_stored(raw_full),
                "prepared_full_history": _history_as_stored(prepared_full),
                "imputation_methods": [str(item) for item in methods_full],
                "required_forecast_state": {},
                "evaluation_metrics": {},
                "metric_win_count": 0,
                "training_metadata": {
                    "training_start": window_months[0],
                    "training_end": window_months[-1],
                    "number_of_observations": int(len(window_months)),
                    "frequency": "MS",
                    "retrained_on_full_history": False,
                    "zero_policy": True,
                },
            }
            record.update(_account_split_fields(outcome_row, window_months))
            account_records[code] = record
            account_models[code] = record
            _log("  Status: ZERO_POLICY (explicit zero contribution, no fitted model)")
            continue

        if full_outcome != OUTCOME_READY:
            if full_outcome == "INVALID_ENTRIES":
                production_status = STATUS_INVALID_DATA
            elif int(_record_get(outcome_row, "train_size", 0) or 0) < MIN_TRAIN_MONTHS and full_outcome == OUTCOME_READY:
                production_status = STATUS_INSUFFICIENT_HISTORY
            else:
                production_status = STATUS_UNRESOLVED_MISSING if full_outcome != "INVALID_ENTRIES" else STATUS_INVALID_DATA
                if _record_text(outcome_row, "training_outcome") == "INVALID_ENTRIES":
                    production_status = STATUS_INVALID_DATA
            status_reason = full_reason
            unavailable.append({"account_code": code, "status": production_status, "reason": status_reason})
            record = {
                "budget_code": code,
                "account_name": name_map.get(code, ""),
                "category": "" if outcome_row is None else _record_text(outcome_row, "category"),
                "algorithm": None,
                "model": None,
                "production_status": production_status,
                "status_reason": status_reason,
                "preprocessing_outcome": full_outcome,
                "history_start": window_months[0],
                "history_end": window_months[-1],
                "history_start_source": None if outcome_row is None else _record_get(outcome_row, "history_start_source"),
                "history_months": list(window_months),
                "historical_values": _history_as_stored(raw_full),
                "required_forecast_state": {},
                "evaluation_metrics": {},
                "metric_win_count": 0,
                "training_metadata": {
                    "training_start": window_months[0],
                    "training_end": window_months[-1],
                    "number_of_observations": int(len(window_months)),
                    "frequency": "MS",
                    "retrained_on_full_history": False,
                },
            }
            record.update(_account_split_fields(outcome_row, window_months))
            account_records[code] = record
            account_models[code] = record
            _log(f"  Status: {production_status} ({status_reason})")
            continue

        eval_outcome = None if outcome_row is None else _record_text(outcome_row, "training_outcome")
        if eval_outcome == OUTCOME_ZERO_POLICY or not eval_winner:
            production_status = STATUS_NO_VALID_WINNER
            status_reason = (
                "Full history has expenditure but no valid evaluated local winner exists."
                if eval_outcome == OUTCOME_ZERO_POLICY
                else (_record_text(winner_row, "Error") or "No valid evaluated local winner.")
            )
            unavailable.append({"account_code": code, "status": production_status, "reason": status_reason})
            record = {
                "budget_code": code,
                "account_name": name_map.get(code, ""),
                "category": "" if outcome_row is None else _record_text(outcome_row, "category"),
                "algorithm": None,
                "model": None,
                "production_status": production_status,
                "status_reason": status_reason,
                "preprocessing_outcome": full_outcome,
                "history_start": window_months[0],
                "history_end": window_months[-1],
                "history_start_source": None if outcome_row is None else _record_get(outcome_row, "history_start_source"),
                "history_months": list(window_months),
                "historical_values": _history_as_stored(raw_full),
                "required_forecast_state": {},
                "evaluation_metrics": {},
                "metric_win_count": 0,
                "training_metadata": {
                    "training_start": window_months[0],
                    "training_end": window_months[-1],
                    "number_of_observations": int(len(window_months)),
                    "frequency": "MS",
                    "retrained_on_full_history": False,
                },
            }
            record.update(_account_split_fields(outcome_row, window_months))
            account_records[code] = record
            account_models[code] = record
            _log(f"  Status: {production_status}")
            continue

        algorithm = eval_winner
        family_result = (account_family_evaluations.get(code) or {}).get(algorithm) or {}
        _log(f"  Parameters: {family_result.get('parameters')}")
        _log(f"  Retraining on full {len(window_months)}-month history...")
        try:
            if family_result.get("status") != "success":
                raise AssertionError(family_result.get("error") or f"{algorithm} has no valid configuration.")
            if not np.isfinite(np.asarray(prepared_full, dtype=float)).all():
                raise AssertionError("Prepared full-history window is not finite.")
            fitted, parameters, state, smoke = fit_production_model(
                {"model_name": algorithm, "parameters": dict(family_result.get("parameters") or {})},
                [float(value) for value in prepared_full],
                window_months,
                label=f"prepared full historical window for {code}",
            )
            wrapper = FittedBudgetCodeModel(algorithm, fitted, state, window_months)
            future_values = [float(value) for value in wrapper.forecast(steps=len(forecast_labels))]
            if clip_negative:
                future_values = [max(0.0, float(value)) for value in future_values]
            if len(future_values) != len(forecast_labels) or not all(np.isfinite(future_values)):
                raise AssertionError("Future forecast is missing or not finite.")
        except Exception as error:
            production_status = STATUS_FIT_FAILED
            status_reason = f"{type(error).__name__}: {error}"
            unavailable.append({"account_code": code, "status": production_status, "reason": status_reason})
            record = {
                "budget_code": code,
                "account_name": name_map.get(code, ""),
                "category": "" if outcome_row is None else _record_text(outcome_row, "category"),
                "algorithm": algorithm,
                "model": None,
                "production_status": production_status,
                "status_reason": status_reason,
                "preprocessing_outcome": full_outcome,
                "history_start": window_months[0],
                "history_end": window_months[-1],
                "history_start_source": None if outcome_row is None else _record_get(outcome_row, "history_start_source"),
                "history_months": list(window_months),
                "historical_values": _history_as_stored(raw_full),
                "required_forecast_state": {},
                "evaluation_metrics": {
                    "MAE": _record_get(winner_row, "MAE"),
                    "RMSE": _record_get(winner_row, "RMSE"),
                    "MAPE": _record_get(winner_row, "MAPE"),
                    "WAPE": _record_get(winner_row, "WAPE"),
                    "R2": _record_get(winner_row, "R2"),
                    "MASE": _record_get(winner_row, "MASE"),
                },
                "metric_win_count": int(_record_get(winner_row, "Metric Wins", 0) or 0),
                "training_metadata": {
                    "training_start": window_months[0],
                    "training_end": window_months[-1],
                    "number_of_observations": int(len(window_months)),
                    "frequency": "MS",
                    "retrained_on_full_history": False,
                },
            }
            record.update(_account_split_fields(outcome_row, window_months))
            account_records[code] = record
            account_models[code] = record
            _log(f"  Fit: FAILED {status_reason}")
            continue

        winner = winner_row
        holdout_metrics = {
            "MAE": _record_get(winner, "MAE"),
            "RMSE": _record_get(winner, "RMSE"),
            "MAPE": _record_get(winner, "MAPE"),
            "WAPE": _record_get(winner, "WAPE"),
            "R2": _record_get(winner, "R2"),
            "MASE": _record_get(winner, "MASE"),
        }
        training_metadata = {
            "training_start": window_months[0],
            "training_end": window_months[-1],
            "number_of_observations": int(len(window_months)),
            "frequency": "MS",
            "retrained_on_full_history": True,
        }
        record = {
            "model": fitted,
            "model_specification": parameters,
            "account_name": name_map.get(code, ""),
            "category": "" if outcome_row is None else _record_text(outcome_row, "category"),
            "algorithm": algorithm,
            "budget_code": code,
            "model_name": algorithm,
            "trained_model": fitted,
            "required_forecast_state": state,
            "historical_values": _history_as_stored(raw_full),
            "prepared_full_history": [float(value) for value in prepared_full],
            "preprocessing_outcome": full_outcome,
            "production_status": STATUS_FITTED_MODEL,
            "status_reason": "",
            "holdout_metrics": {
                "mae": family_result.get("mae"),
                "rmse": family_result.get("rmse"),
                "wape": family_result.get("wape"),
                "mape": family_result.get("mape"),
                "r2": family_result.get("r2"),
                "mase": family_result.get("mase"),
            },
            "evaluation_metrics": holdout_metrics,
            "metric_win_count": int(_record_get(winner, "Metric Wins", 0) or 0),
            "training_metadata": training_metadata,
            "model_metadata": {
                "required_future_features": state,
                "model_parameters": parameters,
            },
        }
        record.update(_account_split_fields(outcome_row, window_months))
        account_models[code] = record
        account_records[code] = record
        models[code] = {
            "budget_code": code,
            "algorithm": algorithm,
            "model_name": algorithm,
            "model": fitted,
            "trained_model": fitted,
            "model_specification": parameters,
            "evaluation_metrics": holdout_metrics,
            "metric_win_count": int(_record_get(winner, "Metric Wins", 0) or 0),
            "training_metadata": training_metadata,
            "production_status": STATUS_FITTED_MODEL,
            "model_metadata": {
                "required_future_features": state,
                "model_parameters": parameters,
            },
            "required_forecast_state": state,
            "history_months": list(window_months),
        }
        monthly_forecast_rows.append(pd.Series(future_values, name=code))
        included_codes.append(code)
        conformal_codes.append(code)
        parameters_by_code[code] = dict(family_result.get("parameters") or {})
        algorithm_by_code[code] = algorithm
        conformal_histories[code] = {
            "values": [float(value) for value in prepared_full],
            "raw_targets": list(raw_full),
            "months": list(window_months),
            "kinds": list(kinds_full),
        }
        _log("  Saved fitted model.")
        _log("  Smoke test: " + ", ".join(f"{value:.4f}" for value in smoke))

    conformal_prediction = {
        "method": "rolling_origin_absolute_residual",
        "alpha": CONFORMAL_ALPHA,
        "nominal_coverage": 1.0 - CONFORMAL_ALPHA,
        "selected_algorithms": algorithm_by_code,
        "accounts": {},
        "combined": {
            "status": "UNAVAILABLE",
            "n_scores_by_horizon": {},
            "quantile_by_horizon": {},
        },
        "intended_accounts": list(selected_codes),
        "insufficient_calibration": True,
    }
    if conformal_codes:
        def _fit_conformal(spec, history, months):
            return fit_production_model(spec, history, months, quiet=True)

        def _preprocess_origin(code, values, kinds, months):
            del code, months
            return prepare_series_window(
                np.asarray(values, dtype=float),
                np.asarray(kinds, dtype=object) if kinds is not None and len(kinds) else np.full(len(values), KIND_NUMBER, dtype=object),
            )

        conformal_prediction = calibrate_conformal_prediction(
            conformal_codes,
            conformal_histories,
            history_months,
            algorithm_by_code,
            parameters_by_code,
            min_train_months=MIN_TRAIN_MONTHS,
            max_horizon=DEFAULT_TEST_MONTHS,
            alpha=CONFORMAL_ALPHA,
            fit_model=_fit_conformal,
            forecast_fn=forecast_from_fitted,
            interpolate_accounts=interpolate_accounts,
            max_internal_gap=max_internal_gap,
            intended_codes=selected_codes,
            preprocess_origin=_preprocess_origin,
        )
        if set(conformal_codes) != set(selected_codes):
            conformal_prediction["combined"] = {
                **(conformal_prediction.get("combined") or {}),
                "status": "UNAVAILABLE",
                "quantile_by_horizon": {},
                "reason": "Combined interval is not reported for a fitted subset as an all-account interval.",
            }
        _log("Conformal combined status: " + str((conformal_prediction.get("combined") or {}).get("status")))

    if monthly_forecast_rows:
        monthly_account = pd.concat(monthly_forecast_rows, axis=1)
    else:
        monthly_account = pd.DataFrame(index=range(len(forecast_labels)))
    monthly_account.index = forecast_index
    if included_codes:
        monthly_account["combined"] = monthly_account[included_codes].sum(axis=1)
    else:
        monthly_account["combined"] = 0.0
    monthly_account_out = monthly_account.reset_index().rename(columns={"index": "month"})
    monthly_account_out.insert(1, "month_label", forecast_labels)
    yearly_account = aggregate_yearly_forecasts(monthly_account_out, included_codes + ["combined"])
    monthly_combined = monthly_account_out[["month", "month_label", "combined"]].copy()
    yearly_combined = yearly_account[["year", "month_count", "is_partial_year", "year_status", "combined"]].copy()
    year_status = yearly_account[["year", "month_count", "year_status"]].copy()
    coverage = {
        "requested_account_count": len(selected_codes),
        "included_in_totals": list(included_codes),
        "included_count": len(included_codes),
        "unavailable_accounts": unavailable,
        "unavailable_count": len(unavailable),
        "partial_total": bool(unavailable),
        "zero_policy_accounts": [
            code for code, record in account_records.items()
            if record.get("production_status") == STATUS_ZERO_POLICY
        ],
        "no_data_accounts": [
            code for code, record in account_records.items()
            if record.get("production_status") == STATUS_NO_DATA
        ],
    }

    duration = time.perf_counter() - started
    bundle_category = requested_category or preprocessing_result.get("requested_category") or "ALL"
    training_timestamp = datetime.now(timezone.utc).isoformat()
    eligible_records = _eligible_account_records(preprocessing_result)
    account_splits = split_from_preprocessing(preprocessing_result)
    bundle = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "forecast_type": PER_BUDGET_CODE_FORECAST_TYPE,
        "model_selection_policy": PER_BUDGET_CODE_FORECAST_TYPE,
        "category": bundle_category,
        "historical_start": preprocessing_result["history_start"],
        "historical_end": preprocessing_result["history_end"],
        "history_start": preprocessing_result["history_start"],
        "history_end": preprocessing_result["history_end"],
        "detected_month_count": detected_month_count,
        "detected_month_columns": list(preprocessing_result["monthly_source_columns"]),
        "eligible_accounts": eligible_records,
        "selected_accounts": selected_codes,
        "selected_budget_codes": selected_codes,
        "overall_best_algorithm": PER_BUDGET_CODE_ALGORITHM,
        "evaluation_metrics": list(EVALUATION_METRICS),
        "evaluation_label": "selection-holdout evaluation",
        "metric_winners": metric_winners.to_dict(orient="records"),
        "models": models,
        "account_records": account_records,
        "evaluation_results": evaluation_table.to_dict(orient="records"),
        "evaluation_results_by_code": evaluation_results_by_code(evaluation_table),
        "account_winners": account_winners.to_dict(orient="records"),
        "account_family_evaluations": account_family_evaluations,
        "forecast_start": forecast_labels[0],
        "forecast_end": forecast_labels[-1],
        "monthly_account_forecasts": monthly_account_out.to_dict(orient="records"),
        "monthly_combined_forecast": monthly_combined.to_dict(orient="records"),
        "yearly_account_forecasts": yearly_account.to_dict(orient="records"),
        "yearly_combined_forecast": yearly_combined.to_dict(orient="records"),
        "year_status": year_status.to_dict(orient="records"),
        "clip_negative_applied": bool(clip_negative),
        "training_timestamp": training_timestamp,
        "training_duration_seconds": duration,
        "amount_unit": preprocessing_result.get("amount_unit") or AMOUNT_UNIT,
        "account_models": account_models,
        "history_months": history_months,
        "account_splits": account_splits,
        "conformal_prediction": conformal_prediction,
        "forecast_coverage": coverage,
        "interpolation": {
            "regular_expense_accounts": interpolate_accounts,
            "max_internal_gap": int(max_internal_gap),
            "enabled_for_all_accounts": False,
        },
        "candidate_artifact": True,
        "active_deployed_artifact": str(deployed_path),
    }
    failures = verify_international_settlement_pickle(bundle, selected_codes)
    if failures:
        raise AssertionError("Pickle metadata validation failed: " + "; ".join(failures))

    tmp_handle = tempfile.NamedTemporaryFile(
        dir=str(output_path),
        prefix=".all_budget_code_models.",
        suffix=".pkl.tmp",
        delete=False,
    )
    tmp_path = Path(tmp_handle.name)
    try:
        with tmp_handle:
            pickle.dump(bundle, tmp_handle, protocol=pickle.HIGHEST_PROTOCOL)
        with open(tmp_path, "rb") as file:
            reloaded = pickle.load(file)
        reload_failures = verify_international_settlement_pickle(reloaded, selected_codes)
        if reload_failures:
            raise AssertionError("Reloaded pickle failed validation: " + "; ".join(reload_failures))
        if excel_stat is not None:
            excel_after = input_path.stat()
            if (excel_after.st_size, int(excel_after.st_mtime)) != (excel_stat.st_size, int(excel_stat.st_mtime)):
                raise AssertionError("The original Excel workbook was modified during training.")
        for path, before, label in (
            (deployed_path, deployed_stat, DEPLOYED_ARTIFACT_NAME),
            (monthly_path, monthly_stat, "best_monthly_model.pkl"),
            (backup_path, backup_stat, "best_monthly_model_pre_seasonal.pkl"),
            (previous_top12_path, previous_top12_stat, "best_top12_account_models.pkl"),
            (previous_overall_path, previous_overall_stat, "best_overall_family_top12_models.pkl"),
        ):
            if before is not None:
                after = path.stat()
                if (after.st_size, int(after.st_mtime)) != (before.st_size, int(before.st_mtime)):
                    raise AssertionError(f"{label} was modified during training.")
        _progress_meta.set({"stage": "validating"})
        commit_training_artifact(
            tmp_path,
            pickle_path,
            reloaded,
            publish_production=publish_production,
            expected_account_count=expected_account_count,
        )
    finally:
        if tmp_path.exists() and tmp_path.resolve() != pickle_path.resolve():
            tmp_path.unlink()

    file_size = pickle_path.stat().st_size
    digest = _sha256(pickle_path)
    zero_policy_accounts = [
        {
            "account_code": code,
            "status": record.get("production_status"),
            "reason": record.get("status_reason"),
            "history_start": record.get("history_start"),
            "history_end": record.get("history_end"),
        }
        for code, record in account_records.items()
        if record.get("production_status") == STATUS_ZERO_POLICY
    ]
    readme_payload = {
        "runtime_dependencies_markdown": RUNTIME_DEPENDENCIES_MARKDOWN,
        "training_timestamp": training_timestamp,
        "training_duration_seconds": duration,
        "pickle_path": str(pickle_path),
        "sha256": digest,
        "deployed_modified": False,
        "account_winners": account_winners.to_dict(orient="records"),
        "evaluation_results": evaluation_table.to_dict(orient="records"),
        "preprocessing": {
            "category": bundle_category,
            "history_start": preprocessing_result["history_start"],
            "history_end": preprocessing_result["history_end"],
            "detected_month_count": detected_month_count,
            "detected_month_columns": list(preprocessing_result["monthly_source_columns"]),
            "eligible_account_count": len(selected_codes),
            "selected_accounts": selected_codes,
            "interpolation": {
                "regular_expense_accounts": interpolate_accounts,
                "max_internal_gap": int(max_internal_gap),
                "enabled_for_all_accounts": False,
            },
            "account_outcomes": preprocessing_result["account_outcomes"].to_dict(orient="records"),
        },
        "zero_policy_accounts": zero_policy_accounts,
        "unavailable_accounts": unavailable,
        "conformal_prediction": conformal_prediction,
        "account_records": _records_for_readme(account_records),
        "verification": {
            "pickle_verified": True,
            "runtime_bundle_schema": ARTIFACT_SCHEMA_VERSION,
            "fitted_model_count": len(models),
            "selected_account_count": len(selected_codes),
            "file_size_bytes": file_size,
            "sha256": digest,
            "deployed_artifact_unmodified": True,
            "candidate_activated": False,
            "feature_selection_results_present": "feature_selection_results" in bundle,
            "forecast_start": forecast_labels[0],
            "forecast_end": forecast_labels[-1],
            "forecast_coverage": coverage,
            "conformal_combined_status": (conformal_prediction.get("combined") or {}).get("status"),
        },
    }
    readme_paths = []
    try:
        readme_paths = [write_training_readme(ENGINE_README_PATH, readme_payload)]
        output_readme = output_path / TRAINING_README_NAME
        if output_readme.resolve() != ENGINE_README_PATH.resolve():
            readme_paths.append(write_training_readme(output_readme, readme_payload))
    except Exception as error:
        _log(f"Training README was not updated: {error}")

    _log("")
    _log("========== ALL-ACCOUNT CANDIDATE TRAINING SUMMARY ==========")
    _log(f"Candidate pickle output path: {pickle_path}")
    _log(f"Pickle file size: {file_size} bytes")
    _log(f"SHA-256: {digest}")
    _log("Verification result: PASSED")
    _log(f"Deployed artifact was not modified: {deployed_path}")
    _log(f"Final artifact: {pickle_path}")
    _log(f"Production artifact replaced: {'yes' if publish_production else 'no'}")
    _log(f"Selected accounts: {len(selected_codes)}")
    _log("Per-budget-code selected models:")
    for _, row in account_winners.iterrows():
        _log(f"  {row['Budget Code']}: {row.get('Winning Algorithm')} ({row.get('Status')})")
    _log("No overall-best algorithm was used.")
    _log(f"Fitted model count: {len(models)}")
    _log(f"detected historical period: {history_months[0]} to {history_months[-1]}")
    _log(f"detected monthly columns: {detected_month_count}")
    _log(f"training duration: {duration:.1f} seconds")
    _log("README (documentation only): " + "; ".join(str(path) for path in readme_paths))
    return {
        "pickle_path": pickle_path,
        "pickle_verified": True,
        "file_size": file_size,
        "sha256": digest,
        "selected_codes": selected_codes,
        "selected_algorithms": winner_by_code,
        "fitted_budget_codes": sorted(models),
        "forecast_coverage": coverage,
        "candidate_deployed": False,
        "production_replaced": bool(publish_production),
        "readme_paths": readme_paths,
    }

def run_combined_monthly_training(input_file: str | None = None, output_dir: str | None = None):
    input_path = Path(input_file) if input_file else DEFAULT_INPUT_FILE
    input_path = input_path.resolve()
    output_path = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    output_path = output_path.resolve()
    pickle_path = output_path / "best_monthly_model.pkl"
    legacy_pickle = LEGACY_ACCOUNTWISE_MODEL_FILE.resolve()
    excel_stat = input_path.stat()
    legacy_stat = legacy_pickle.stat() if legacy_pickle.exists() else None

    print(f"Original dataset: {input_path}")
    print(f"Training output: {output_path}")
    print(f"Monthly Pickle: {pickle_path}")
    if not input_path.exists():
        raise FileNotFoundError(f"Original Actuals Excel file was not found at: {input_path}")
    if legacy_stat is not None:
        print(f"Existing account-wise Pickle size: {legacy_stat.st_size} bytes")

    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path.mkdir(parents=True, exist_ok=True)
    backup_path = output_path / "best_monthly_model_pre_seasonal.pkl"
    if not backup_path.exists():
        raise FileNotFoundError(
            f"Required recoverable backup is missing: {backup_path}. "
            "Copy the current artifact before retraining."
        )

    preprocessing_result = run_preprocessing(str(input_path))
    selected_codes = list(preprocessing_result["account_codes"])
    if len(selected_codes) != 12 or len(set(selected_codes)) != 12:
        raise AssertionError("Production bundle requires exactly 12 unique selected accounts.")
    combined_history = preprocessing_result["monthly_history"]
    combined_months = preprocessing_result["history_months"]
    train_size, _test_size = calendar_split_sizes(len(combined_months))
    evaluation_results, split, seasonality_diagnostics = evaluate_seven_models(
        combined_months,
        combined_history,
        split_index=train_size,
    )
    best_result = select_best_model(evaluation_results, seasonality_diagnostics)
    fitted_model, production_parameters, forecast_state, smoke = fit_production_model(
        best_result,
        preprocessing_result["monthly_history"],
        preprocessing_result["history_months"],
    )
    production_horizon = forecast_from_fitted(
        best_result["model_name"],
        fitted_model,
        forecast_state,
        preprocessing_result["history_months"],
        steps=13,
    )
    smoke_months = []
    current_label = preprocessing_result["history_months"][-1]
    for _ in range(13):
        current_label = add_month_label(current_label, 1)
        smoke_months.append(current_label)
    production_smoke = smoke_forecast_stats(production_horizon, smoke_months)
    print("\n========== 13-MONTH PRODUCTION SMOKE TEST ==========")
    print("Window: Jul-2026 through Jul-2027")
    for label, value in zip(smoke_months, production_horizon):
        print(f"  {label}: {value:.6f}")
    print(f"minimum: {production_smoke['minimum']:.6f}")
    print(f"maximum: {production_smoke['maximum']:.6f}")
    print(f"range: {production_smoke['range']:.6f}")
    print(f"coefficient of variation: {production_smoke['coefficient_of_variation']}")
    print(f"nearly constant: {production_smoke['nearly_constant']}")
    print(f"has negative: {production_smoke['has_negative']}")
    print(f"has NaN/Inf: {production_smoke['has_nan_or_inf']}")
    bundle = build_monthly_model_bundle(
        preprocessing_result,
        evaluation_results,
        best_result,
        fitted_model,
        production_parameters,
        forecast_state,
        seasonality_diagnostics=seasonality_diagnostics,
        production_smoke=production_smoke,
    )
    saved_path = atomic_save_pickle(bundle, pickle_path)
    reloaded, file_size, verified_smoke = verify_saved_monthly_pickle(saved_path)

    excel_after = input_path.stat()
    if (excel_after.st_size, int(excel_after.st_mtime)) != (excel_stat.st_size, int(excel_stat.st_mtime)):
        raise AssertionError("The original Excel workbook was modified during training.")
    if legacy_stat is not None:
        legacy_after = legacy_pickle.stat()
        if (legacy_after.st_size, int(legacy_after.st_mtime)) != (legacy_stat.st_size, int(legacy_stat.st_mtime)):
            raise AssertionError("The existing account-wise best_model.pkl was modified during training.")

    print(f"eligible account count: {preprocessing_result['eligible_account_count']}")
    print("selected 12 account codes:")
    for index, code in enumerate(preprocessing_result["selected_codes"], 1):
        print(f"  {index}. {code}")
    print(f"combined series length: {len(preprocessing_result['monthly_history'])}")
    print(f"train period: {split['train_months'][0]} to {split['train_months'][-1]}")
    print(f"test period: {split['test_months'][0]} to {split['test_months'][-1]}")
    print(f"successful models: {reloaded['successful_model_count']}")
    print(f"failed models: {reloaded['failed_model_count']}")
    print(f"best model: {reloaded['best_model_name']}")
    print(f"best parameters: {reloaded['best_model_parameters']}")
    print(f"amount unit: {reloaded['amount_unit']}")
    print(f"selection reason: {reloaded.get('selection_reason')}")
    print(f"Pickle output path: {saved_path}")
    print(f"Backup path left unchanged: {backup_path}")
    print(f"Smoke-test values were not used for model selection: {verified_smoke}")
    if not backup_path.exists():
        raise AssertionError("The pre-seasonal backup artifact was removed during training.")

    return {
        "preprocessing": preprocessing_result,
        "evaluation_results": evaluation_results,
        "split": split,
        "best_result": best_result,
        "pickle_path": saved_path,
        "pickle_verified": True,
        "file_size": file_size,
        "smoke_forecast": verified_smoke,
        "production_smoke_forecast": smoke,
    }


def _explicit_path(value: str | None, default: Path) -> bool:
    if value is None:
        return False
    return Path(value).resolve() != default.resolve()


def retrain_from_current_master(args, output_dir: str | Path | None = None) -> int:
    """Snapshot the current historical master and publish all_budget_code_models.pkl."""
    output_path = Path(output_dir).resolve() if output_dir else DEFAULT_OUTPUT_DIR.resolve()
    production = (output_path / CANDIDATE_ARTIFACT_NAME).resolve()
    replaced = False
    lock = None
    try:
        if _explicit_path(getattr(args, "input_file", None), DEFAULT_INPUT_FILE) or _explicit_path(
            getattr(args, "file_path", None), DEFAULT_INPUT_FILE
        ):
            raise AssertionError(
                "Retraining uses the current historical master. Do not pass a separate input workbook."
            )
        if _explicit_path(getattr(args, "output_dir", None), DEFAULT_OUTPUT_DIR):
            raise AssertionError("Retraining publishes only the fixed production artifact directory.")
        output_file = str(getattr(args, "output_file", None) or CANDIDATE_ARTIFACT_NAME)
        if Path(output_file).name != CANDIDATE_ARTIFACT_NAME:
            raise AssertionError(
                f"Retraining must publish {CANDIDATE_ARTIFACT_NAME}, not {Path(output_file).name}."
            )
        category = getattr(args, "category", None)
        if category is not None and str(category).strip().casefold() not in {"", "all", "*"}:
            raise AssertionError("Retraining the production artifact includes every category.")
        backend_dir = ENGINE_DIR.parent
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        from app.services.dataset_master import resolve_training_snapshot
        from app.services.retrain_jobs import cli_retrain_scope

        with cli_retrain_scope():
            lock = acquire_retrain_lock(output_path)
            try:
                snapshot = resolve_training_snapshot()
                snapshot_path = Path(snapshot["path"])
                _log(f"Master data source: {snapshot['source']}")
                _log(f"Resolved snapshot: {snapshot_path}")
                _log(f"Snapshot period: {snapshot['earliest_month']} to {snapshot['latest_month']}")
                _log(f"Budget-code count: {snapshot['budget_code_count']}")
                result = run_training(
                    input_file=str(snapshot_path),
                    output_dir=str(output_path),
                    category=None,
                    top_n=getattr(args, "top_n", None),
                    test_months=getattr(args, "test_months", None),
                    forecast_months=getattr(args, "forecast_months", DEFAULT_FORECAST_MONTHS),
                    forecast_end=getattr(args, "forecast_end", None),
                    clip_negative=bool(getattr(args, "clip_negative", False)),
                    output_file=CANDIDATE_ARTIFACT_NAME,
                    regular_expense_accounts=[
                        item.strip()
                        for item in str(getattr(args, "regular_expense_accounts", "") or "").split(",")
                        if item.strip()
                    ],
                    max_internal_gap=getattr(args, "max_internal_gap", DEFAULT_MAX_INTERNAL_GAP),
                    expected_account_count=int(snapshot["budget_code_count"]),
                )
                saved = Path(result["pickle_path"]).resolve()
                replaced = bool(result.get("pickle_verified")) and saved == production and production.is_file()
                if not replaced:
                    raise AssertionError("Training finished without replacing the production artifact.")
                _log(f"Final artifact: {production}")
                _log("Retraining succeeded.")
                _log("Production artifact replaced: yes")
                return 0
            finally:
                if lock is not None:
                    release_retrain_lock(*lock)
                    lock = None
    except Exception as error:
        _log(f"Retraining failed: {error}")
        _log("Production artifact replaced: no")
        return 1
    finally:
        if lock is not None:
            release_retrain_lock(*lock)
        if not replaced:
            _log("Existing production artifact retained.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Train per-budget-code forecasting models for eligible accounts. "
            f"Writes {CANDIDATE_ARTIFACT_NAME} and does not overwrite "
            f"{DEPLOYED_ARTIFACT_NAME}. Does not start automatically on import."
        )
    )
    parser.add_argument(
        "--file-path",
        default=str(DEFAULT_INPUT_FILE),
        help="Path to the original Actuals Excel workbook",
    )
    parser.add_argument(
        "--input-file",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--category",
        default=None,
        help="Optional category filter. Omit or pass all to include every category.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Ignored compatibility flag. All eligible accounts in the requested scope are used.",
    )
    parser.add_argument(
        "--test-months",
        type=int,
        default=None,
        help="Deprecated. The holdout is the preprocessing calendar 80/20 split.",
    )
    parser.add_argument(
        "--forecast-months",
        type=int,
        default=DEFAULT_FORECAST_MONTHS,
        help="Number of future months to forecast when --forecast-end is not set. Default: 18",
    )
    parser.add_argument(
        "--forecast-end",
        default=None,
        help="Optional last forecast month such as 2030-12-01. Cannot be after December 2030.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory for {CANDIDATE_ARTIFACT_NAME}.",
    )
    parser.add_argument(
        "--output-file",
        default=CANDIDATE_ARTIFACT_NAME,
        help=f"Candidate artifact filename. Default: {CANDIDATE_ARTIFACT_NAME}. Cannot overwrite {DEPLOYED_ARTIFACT_NAME}.",
    )
    parser.add_argument(
        "--regular-expense-accounts",
        default="",
        help="Comma-separated Budget Codes allowed to use short-gap interpolation. Default: none.",
    )
    parser.add_argument(
        "--max-internal-gap",
        type=int,
        default=DEFAULT_MAX_INTERNAL_GAP,
        help="Maximum internal gap length interpolated for allowed accounts. Default: 2.",
    )
    parser.add_argument(
        "--clip-negative",
        action="store_true",
        help="Clip negative forecasts to zero. Off by default.",
    )
    parser.add_argument(
        "--retrain-from-master",
        action="store_true",
        help=(
            "Snapshot the current historical master dataset and retrain "
            f"{CANDIDATE_ARTIFACT_NAME}. Does not use {DEFAULT_INPUT_FILE.name}."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.retrain_from_master:
        sys.exit(retrain_from_current_master(args))
    interpolate = [item.strip() for item in str(args.regular_expense_accounts or "").split(",") if item.strip()]
    result = run_training(
        input_file=args.input_file or args.file_path,
        output_dir=args.output_dir,
        category=args.category,
        top_n=args.top_n,
        test_months=args.test_months,
        forecast_months=args.forecast_months,
        forecast_end=args.forecast_end,
        clip_negative=args.clip_negative,
        output_file=args.output_file,
        regular_expense_accounts=interpolate,
        max_internal_gap=args.max_internal_gap,
    )
    if not result["pickle_verified"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
