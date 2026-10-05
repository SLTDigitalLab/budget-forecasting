"""Seven-algorithm evaluation for each eligible budget-code series.

Model families:
ARIMA, SARIMA, ARIMAX, SARIMAX, ETS, XGBoost, Prophet.

Each account is evaluated on its own historical window. Parameter-tuning folds
stay inside the training window. The outer 20% is a selection-holdout, not an
untouched final test. Production does not use overall-algorithm selection.
"""

from __future__ import annotations

import itertools
import logging
import time
import warnings
from calendar import month_abbr
from contextlib import contextmanager

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.exponential_smoothing.ets import ETSModel
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.stattools import acf
from statsmodels.tsa.statespace.sarimax import SARIMAX
from xgboost import XGBRegressor

from preprocessing import (
    DEFAULT_MAX_INTERNAL_GAP,
    KIND_NUMBER,
    KIND_MISSING,
    OUTCOME_INVALID,
    OUTCOME_NO_DATA,
    OUTCOME_READY,
    OUTCOME_UNRESOLVED,
    OUTCOME_ZERO_POLICY,
    STATUS_INSUFFICIENT_HISTORY,
    STATUS_INVALID_DATA,
    STATUS_NO_DATA,
    STATUS_NO_VALID_WINNER,
    STATUS_NOT_EVALUABLE,
    STATUS_UNRESOLVED_MISSING,
    STATUS_ZERO_POLICY,
    account_numeric_mean,
    calendar_split_sizes,
    fill_missing_with_mean,
    prepare_series_window,
)

DEFAULT_TEST_MONTHS = 6
MIN_TRAIN_MONTHS = 12
# Holdout length is derived from the preprocessing calendar 80/20 split.
# DEFAULT_TEST_MONTHS remains only for model-specific rolling-horizon helpers.
SEASONAL_PERIOD = 12
SEASONAL_MASE_PERIOD = 12
EVALUATION_METRICS = ["MAE", "RMSE", "MAPE", "WAPE", "MASE", "R2"]
RANDOM_STATE = 42
EXPLOSION_MULTIPLIER = 8.0
TIE_WAPE_POINTS = 1.0
ALGORITHMS = [
    "ARIMA",
    "SARIMA",
    "ARIMAX",
    "SARIMAX",
    "ETS",
    "XGBoost",
    "Prophet",
]
MODEL_NAMES = list(ALGORITHMS)
TEST_MONTHS = DEFAULT_TEST_MONTHS
TRAIN_MONTHS = None
XGBOOST_FEATURE_COLUMNS = [
    "time",
    "month",
    "month_sin",
    "month_cos",
    "lag_1",
    "lag_2",
    "lag_3",
    "lag_6",
    "lag_12",
    "rolling_mean_3",
    "rolling_mean_6",
    "rolling_mean_12",
]
XGBOOST_PARAMS = {
    "n_estimators": 100,
    "max_depth": 2,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "objective": "reg:squarederror",
    "random_state": RANDOM_STATE,
    "n_jobs": 1,
}
EXOGENOUS_FEATURES = ["month_sin", "month_cos", "time"]
_MONTH_LOOKUP = {month_abbr[number]: number for number in range(1, 13)}
ROLLING_FOLDS = [
    {
        "name": "fold_1",
        "description": "train through Dec-2024, validate Jan-2025 to Jun-2025",
        "train_end": 24,
        "val_end": 30,
        "expected_train_end": "Dec-2024",
        "expected_val_start": "Jan-2025",
        "expected_val_end": "Jun-2025",
    },
    {
        "name": "fold_2",
        "description": "train through Jun-2025, validate Jul-2025 to Dec-2025",
        "train_end": 30,
        "val_end": 36,
        "expected_train_end": "Jun-2025",
        "expected_val_start": "Jul-2025",
        "expected_val_end": "Dec-2025",
    },
    {
        "name": "fold_3",
        "description": "train through Dec-2025, validate Jan-2026 to Jun-2026",
        "train_end": 36,
        "val_end": 42,
        "expected_train_end": "Dec-2025",
        "expected_val_start": "Jan-2026",
        "expected_val_end": "Jun-2026",
    },
]
# SARIMA/SARIMAX: p,q in 0..2 would be 144 orders. With 42 observations that
# grid is reduced scientifically to p,q in 0..1 (64 orders) so rolling
# validation can finish for the demo. Seasonal period remains 12; D/P/Q still
# cover no-seasonal and seasonal differencing controls.
SARIMA_P_Q = range(0, 2)
SARIMA_D = range(0, 2)
SARIMA_P_SEASONAL = range(0, 2)
SARIMA_D_SEASONAL = range(0, 2)
SARIMA_Q_SEASONAL = range(0, 2)
ARIMA_P_Q = range(0, 3)
ARIMA_D = range(0, 2)
GRID_REDUCTIONS = [
    "SARIMA/SARIMAX p,q reduced from 0..2 to 0..1 (64 seasonal orders instead of 144) because only 42 observations are available.",
    "ARIMA/ARIMAX remain p,q in 0..2 and d in 0..1.",
    "Prophet uses a constrained monthly grid: linear growth, no weekly/daily seasonality, n_changepoints=5, changepoint_prior_scale in {0.01, 0.05, 0.1}, and yearly Fourier order 3 when seasonality is enabled.",
]


class ForecastEvaluationError(Exception):
    """Raised when a candidate model cannot produce a valid test forecast."""


def parse_month_label(label: str) -> tuple[int, int]:
    month_name, year_text = str(label).split("-")
    return int(year_text), _MONTH_LOOKUP[month_name]


def add_month_label(label: str, count: int = 1) -> str:
    year, month = parse_month_label(label)
    index = year * 12 + (month - 1) + count
    return f"{month_abbr[(index % 12) + 1]}-{index // 12}"


def calendar_exog(month_labels: list[str], start_time: int = 0) -> pd.DataFrame:
    rows = []
    for offset, label in enumerate(month_labels):
        _year, month = parse_month_label(label)
        rows.append({
            "month_sin": float(np.sin(2.0 * np.pi * month / 12.0)),
            "month_cos": float(np.cos(2.0 * np.pi * month / 12.0)),
            "time": float(start_time + offset),
        })
    return pd.DataFrame(rows, columns=EXOGENOUS_FEATURES)


def month_start_timestamp(label: str) -> pd.Timestamp:
    year, month = parse_month_label(label)
    return pd.Timestamp(year=year, month=month, day=1)


def prophet_training_frame(month_labels, values) -> pd.DataFrame:
    frame = pd.DataFrame({
        "ds": [month_start_timestamp(label) for label in month_labels],
        "y": np.asarray(values, dtype=float),
    })
    if frame["y"].isna().any() or not np.isfinite(frame["y"].to_numpy(dtype=float)).all():
        raise ForecastEvaluationError("Prophet training values must be finite.")
    return frame


def prophet_future_frame(month_labels) -> pd.DataFrame:
    return pd.DataFrame({"ds": [month_start_timestamp(label) for label in month_labels]})


@contextmanager
def _prophet_library_quiet():
    """Mute known non-actionable cmdstan/Prophet chatter without silencing all warnings."""
    names = ("cmdstanpy", "prophet", "prophet.forecaster")
    previous = {}
    for name in names:
        logger = logging.getLogger(name)
        previous[name] = (logger.level, logger.disabled)
        logger.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning, module=r"prophet(\.|$)")
            warnings.filterwarnings("ignore", message=".*stan_backend.*")
            yield
    finally:
        for name, (level, disabled) in previous.items():
            logger = logging.getLogger(name)
            logger.setLevel(level)
            logger.disabled = disabled


def _import_prophet():
    try:
        from prophet import Prophet
    except ImportError as error:
        raise ForecastEvaluationError(
            "Prophet is not installed. Install with: py -3.10 -m pip install prophet"
        ) from error
    return Prophet


def prophet_candidates(all_positive: bool) -> list[dict]:
    candidates = []
    for changepoint_prior_scale in (0.01, 0.05, 0.1):
        candidates.append({
            "growth": "linear",
            "seasonality_mode": "additive",
            "yearly_seasonality": False,
            "weekly_seasonality": False,
            "daily_seasonality": False,
            "changepoint_prior_scale": changepoint_prior_scale,
            "seasonality_prior_scale": 10.0,
            "n_changepoints": 5,
            "yearly_fourier_order": None,
        })
    for changepoint_prior_scale in (0.01, 0.05, 0.1):
        for seasonality_prior_scale in (1.0, 5.0, 10.0):
            candidates.append({
                "growth": "linear",
                "seasonality_mode": "additive",
                "yearly_seasonality": False,
                "weekly_seasonality": False,
                "daily_seasonality": False,
                "changepoint_prior_scale": changepoint_prior_scale,
                "seasonality_prior_scale": seasonality_prior_scale,
                "n_changepoints": 5,
                "yearly_fourier_order": 3,
            })
    if all_positive:
        candidates.append({
            "growth": "linear",
            "seasonality_mode": "multiplicative",
            "yearly_seasonality": False,
            "weekly_seasonality": False,
            "daily_seasonality": False,
            "changepoint_prior_scale": 0.05,
            "seasonality_prior_scale": 10.0,
            "n_changepoints": 5,
            "yearly_fourier_order": 3,
        })
    return candidates


def build_prophet_model(parameters: dict):
    Prophet = _import_prophet()
    if parameters.get("growth") == "logistic":
        raise ForecastEvaluationError("Prophet logistic growth is not used without valid floor and cap values.")
    model = Prophet(
        growth=parameters.get("growth") or "linear",
        seasonality_mode=parameters.get("seasonality_mode") or "additive",
        yearly_seasonality=bool(parameters.get("yearly_seasonality", False)),
        weekly_seasonality=False,
        daily_seasonality=False,
        changepoint_prior_scale=float(parameters.get("changepoint_prior_scale", 0.05)),
        seasonality_prior_scale=float(parameters.get("seasonality_prior_scale", 10.0)),
        n_changepoints=int(parameters.get("n_changepoints", 5)),
    )
    fourier_order = parameters.get("yearly_fourier_order")
    if fourier_order:
        model.add_seasonality(name="yearly", period=365.25, fourier_order=int(fourier_order))
    return model


def _align_prophet_yhat(forecast: pd.DataFrame, expected_dates: pd.Series) -> np.ndarray:
    aligned = forecast.copy()
    aligned["ds"] = pd.to_datetime(aligned["ds"]).dt.to_period("M").dt.to_timestamp(how="start")
    expected = pd.to_datetime(expected_dates).dt.to_period("M").dt.to_timestamp(how="start")
    yhat = aligned.set_index("ds").reindex(expected)["yhat"]
    if yhat.isna().any():
        raise ForecastEvaluationError("Prophet predictions could not be aligned to the requested months.")
    return yhat.to_numpy(dtype=float)


def forecast_prophet(train_values, train_months, future_months, parameters):
    model = build_prophet_model(parameters)
    train_df = prophet_training_frame(train_months, train_values)
    future_df = prophet_future_frame(future_months)
    with _prophet_library_quiet():
        model.fit(train_df)
        forecast = model.predict(future_df)
        train_forecast = model.predict(train_df[["ds"]])
    yhat = _align_prophet_yhat(forecast, future_df["ds"])
    train_yhat = _align_prophet_yhat(train_forecast, train_df["ds"])
    residuals = train_df["y"].to_numpy(dtype=float) - train_yhat
    state = {
        "freq": "MS",
        "history_months": list(train_months),
        "history_end": train_months[-1],
        "train_residuals": residuals,
    }
    return yhat, model, state


def prophet_predict_future(fitted_model, history_months, steps: int) -> list[float]:
    future_labels = []
    current = history_months[-1]
    for _ in range(steps):
        current = add_month_label(current, 1)
        future_labels.append(current)
    future_df = prophet_future_frame(future_labels)
    with _prophet_library_quiet():
        forecast = fitted_model.predict(future_df)
    yhat = _align_prophet_yhat(forecast, future_df["ds"])
    if yhat.size != steps or not np.isfinite(yhat).all():
        raise ForecastEvaluationError("Prophet produced a non-finite future forecast.")
    return [float(value) for value in yhat]


def _to_float_or_none(value):
    if value is None:
        return None
    number = float(value)
    if not np.isfinite(number):
        return None
    return number


def calculate_mase(train_values, actual, predicted, season: int = SEASONAL_MASE_PERIOD, train_observed=None):
    """Seasonal MASE. Scale uses training-window observed lag pairs only, keeping calendar positions."""
    train = np.asarray(train_values, dtype=float).reshape(-1)
    actual = np.asarray(actual, dtype=float).reshape(-1)
    predicted = np.asarray(predicted, dtype=float).reshape(-1)
    if train_observed is None:
        train_observed = np.isfinite(train)
    else:
        train_observed = np.asarray(train_observed, dtype=bool) & np.isfinite(train)
    if train.size <= season:
        return np.nan, "MASE is undefined because the training series is not longer than the seasonal period."
    pairs = []
    for index in range(season, train.size):
        if train_observed[index] and train_observed[index - season]:
            pairs.append(abs(float(train[index]) - float(train[index - season])))
    if not pairs:
        return np.nan, "MASE is undefined because no observed seasonal lag pairs exist in the training window."
    scale = float(np.mean(pairs))
    if not np.isfinite(scale) or scale == 0:
        return np.nan, "MASE denominator is zero or non-finite."
    model_mae = float(np.mean(np.abs(actual - predicted)))
    return float(model_mae / scale), None


def calculate_metrics(actual, predicted, train_values=None, observed_mask=None, train_observed=None):
    actual = np.asarray(actual, dtype=float).reshape(-1)
    predicted = np.asarray(predicted, dtype=float).reshape(-1)
    if actual.size != predicted.size:
        raise ForecastEvaluationError("Predictions and actuals have different lengths.")
    if observed_mask is None:
        mask = np.isfinite(actual)
    else:
        mask = np.asarray(observed_mask, dtype=bool).reshape(-1)
        if mask.size != actual.size:
            raise ForecastEvaluationError("Observed-target mask length does not match actuals.")
        mask = mask & np.isfinite(actual)
    observed_count = int(mask.sum())
    notes = {
        "observed_target_count": observed_count,
        "missing_or_invalid_target_count": int(actual.size - observed_count),
        "mape_convention": "MAPE is computed only on nonzero observed targets.",
        "evaluation_label": "selection-holdout evaluation",
    }
    if observed_count == 0:
        return {
            "MAE": np.nan,
            "RMSE": np.nan,
            "WAPE": np.nan,
            "MAPE": np.nan,
            "sMAPE": np.nan,
            "MASE": np.nan,
            "R2": np.nan,
            "MAPE_NOTE": "NOT_EVALUABLE: no observed test targets.",
            "MASE_NOTE": "NOT_EVALUABLE: no observed test targets.",
            "EVALUATION_STATUS": "NOT_EVALUABLE",
            **notes,
        }
    if not np.isfinite(predicted[mask]).all():
        raise ForecastEvaluationError("Predictions on observed target dates are not finite.")
    obs_actual = actual[mask]
    obs_predicted = predicted[mask]

    mae = float(mean_absolute_error(obs_actual, obs_predicted))
    rmse = float(np.sqrt(mean_squared_error(obs_actual, obs_predicted)))

    wape_denominator = float(np.sum(np.abs(obs_actual)))
    if wape_denominator == 0:
        wape = np.nan
    else:
        wape = float((np.sum(np.abs(obs_actual - obs_predicted)) / wape_denominator) * 100)

    nonzero = obs_actual != 0
    mape_note = "MAPE uses nonzero observed targets only."
    if nonzero.sum() == 0:
        mape = np.nan
        mape_note = "MAPE is undefined because all observed test values are zero."
    else:
        mape = float(
            np.mean(np.abs((obs_actual[nonzero] - obs_predicted[nonzero]) / obs_actual[nonzero])) * 100
        )
        mape_note = f"MAPE uses {int(nonzero.sum())} nonzero observed targets of {observed_count} observed targets."

    smape_denominator = np.abs(obs_actual) + np.abs(obs_predicted)
    valid = smape_denominator != 0
    if valid.sum() == 0:
        smape = np.nan
    else:
        smape = float(
            np.mean(200.0 * np.abs(obs_actual[valid] - obs_predicted[valid]) / smape_denominator[valid])
        )

    if obs_actual.size < 2 or np.allclose(obs_actual, obs_actual[0]):
        r_squared = np.nan
    else:
        r_squared = float(r2_score(obs_actual, obs_predicted))

    if train_values is None:
        mase = np.nan
        mase_note = "MASE was not calculated because training values were not provided."
    else:
        mase, mase_note = calculate_mase(
            train_values, obs_actual, obs_predicted, train_observed=train_observed
        )

    return {
        "MAE": mae,
        "RMSE": rmse,
        "WAPE": wape,
        "MAPE": mape,
        "sMAPE": smape,
        "MASE": mase,
        "R2": r_squared,
        "MAPE_NOTE": mape_note,
        "MASE_NOTE": mase_note,
        "EVALUATION_STATUS": "EVALUABLE",
        **notes,
    }


def require_finite_forecast(predictions, model_name: str, expected: int | None = None) -> np.ndarray:
    values = np.asarray(predictions, dtype=float).reshape(-1)
    if expected is not None and values.size != int(expected):
        raise ForecastEvaluationError(
            f"{model_name} returned {values.size} predictions, expected {int(expected)}."
        )
    if not np.isfinite(values).all():
        raise ForecastEvaluationError(
            f"{model_name} produced a non-finite prediction; invalid values were not replaced with zero."
        )
    return np.array([float(value) for value in values], dtype=float)


def align_holdout_by_calendar(holdout_actuals_by_account, calendar_months):
    """Align per-account holdout actuals by calendar date and report coverage.

    Each account's first test month stays on its own date. Those months are not
    summed together when they differ.
    """
    months = list(calendar_months)
    codes = [str(code) for code in holdout_actuals_by_account]
    frame = pd.DataFrame(np.nan, index=codes, columns=months, dtype=float)
    for code, payload in holdout_actuals_by_account.items():
        if not isinstance(payload, dict):
            raise TypeError(
                "Holdout actuals must be month-keyed mappings so combined evaluation "
                "can align by calendar date."
            )
        for month, value in payload.items():
            if month not in frame.columns:
                continue
            if value is None or (isinstance(value, float) and not np.isfinite(value)):
                continue
            frame.loc[str(code), month] = float(value)
    coverage_rows = []
    for month in months:
        observed = int(frame[month].notna().sum())
        coverage_rows.append(
            {
                "Month": month,
                "observed_accounts": observed,
                "missing_accounts": int(len(codes) - observed),
            }
        )
    combined = pd.Series(np.nan, index=months, dtype=float)
    if len(codes):
        month_complete = frame.notna().all(axis=0)
        combined.loc[month_complete] = frame.loc[:, month_complete].sum(axis=0)
    return {
        "aligned_actuals": frame,
        "coverage": pd.DataFrame(coverage_rows),
        "combined_where_complete": combined,
    }


def chronological_train_test_split(
    month_labels,
    monthly_history,
    quiet: bool = False,
    test_months: int | None = None,
    min_train_months: int | None = None,
    split_index: int | None = None,
):
    months = list(month_labels)
    values = np.asarray(monthly_history, dtype=float)
    detected_month_count = len(months)
    if detected_month_count != values.size:
        raise AssertionError(
            f"Month labels ({detected_month_count}) and values ({values.size}) are different lengths."
        )
    if split_index is not None:
        train_count = int(split_index)
        holdout_count = detected_month_count - train_count
    elif test_months is None:
        train_count, holdout_count = calendar_split_sizes(detected_month_count)
    else:
        if int(test_months) <= 0:
            raise ValueError("test_months must be a positive integer.")
        holdout_count = int(test_months)
        train_count = detected_month_count - holdout_count
    if train_count < 1 or holdout_count < 1:
        raise ValueError(
            f"The calendar of {detected_month_count} months cannot produce both nonempty "
            f"train and test windows (train_size={train_count}, test_size={holdout_count})."
        )
    if min_train_months is not None and train_count < int(min_train_months):
        raise ValueError(
            f"Need at least {min_train_months} training months plus {holdout_count} test months. "
            f"Detected history has {detected_month_count} months."
        )
    train_months = months[:train_count]
    holdout_months = months[train_count:]
    train_values = values[:train_count]
    test_values = values[train_count:]
    if len(holdout_months) != int(holdout_count):
        raise AssertionError("The test period must be the final chronological months.")

    if not quiet:
        print("========== TRAIN/TEST SPLIT ==========")
        print(f"Training period: {train_months[0]} to {train_months[-1]}")
        print(f"Testing period: {holdout_months[0]} to {holdout_months[-1]}")
        print(f"Training observations: {len(train_months)}")
        print(f"Testing observations: {len(holdout_months)}")

    return {
        "train_months": train_months,
        "test_months": holdout_months,
        "train_values": train_values,
        "test_values": test_values,
        "train_count": len(train_months),
        "test_count": len(holdout_months),
    }


def _as_series(values) -> pd.Series:
    array = np.asarray(values, dtype=float)
    return pd.Series(array, index=pd.RangeIndex(start=1, stop=array.size + 1), dtype=float)


def _month_cycle(month: int) -> tuple[float, float]:
    return (
        float(np.sin(2.0 * np.pi * month / 12.0)),
        float(np.cos(2.0 * np.pi * month / 12.0)),
    )


def xgboost_feature_row(history_values, history_months, next_label: str) -> dict:
    year, month = parse_month_label(next_label)
    month_sin, month_cos = _month_cycle(month)
    values = list(np.asarray(history_values, dtype=float))
    if len(values) < 12:
        raise ForecastEvaluationError("XGBoost requires at least 12 historical months.")
    return {
        "time": len(values),
        "month": month,
        "year": year,
        "month_sin": month_sin,
        "month_cos": month_cos,
        "lag_1": values[-1],
        "lag_2": values[-2],
        "lag_3": values[-3],
        "lag_6": values[-6],
        "lag_12": values[-12],
        "rolling_mean_3": float(np.mean(values[-3:])),
        "rolling_mean_6": float(np.mean(values[-6:])),
        "rolling_mean_12": float(np.mean(values[-12:])),
    }


def create_xgboost_features(series, month_labels):
    feature_df = pd.DataFrame({"target": np.asarray(series, dtype=float)})
    parsed = [parse_month_label(label) for label in month_labels]
    feature_df["time"] = np.arange(len(feature_df))
    feature_df["year"] = [year for year, _month in parsed]
    feature_df["month"] = [month for _year, month in parsed]
    feature_df["month_sin"] = np.sin(2.0 * np.pi * feature_df["month"] / 12.0)
    feature_df["month_cos"] = np.cos(2.0 * np.pi * feature_df["month"] / 12.0)
    feature_df["lag_1"] = feature_df["target"].shift(1)
    feature_df["lag_2"] = feature_df["target"].shift(2)
    feature_df["lag_3"] = feature_df["target"].shift(3)
    feature_df["lag_6"] = feature_df["target"].shift(6)
    feature_df["lag_12"] = feature_df["target"].shift(12)
    shifted = feature_df["target"].shift(1)
    feature_df["rolling_mean_3"] = shifted.rolling(3).mean()
    feature_df["rolling_mean_6"] = shifted.rolling(6).mean()
    feature_df["rolling_mean_12"] = shifted.rolling(12).mean()
    return feature_df.dropna()


def recursive_xgboost_forecast(model, history, history_months, feature_columns, steps):
    values = list(np.asarray(history, dtype=float))
    months = list(history_months)
    forecasts = []
    for _ in range(steps):
        next_label = add_month_label(months[-1], 1)
        features = pd.DataFrame([xgboost_feature_row(values, months, next_label)])
        missing = [column for column in feature_columns if column not in features.columns]
        if missing:
            raise ForecastEvaluationError(f"XGBoost is missing features: {missing}")
        prediction = float(model.predict(features[feature_columns])[0])
        if not np.isfinite(prediction):
            raise ForecastEvaluationError("XGBoost produced a non-finite future prediction.")
        forecasts.append(prediction)
        values.append(prediction)
        months.append(next_label)
    return [float(value) for value in forecasts]


def forecast_xgboost(train_series, train_months, steps):
    history = list(np.asarray(train_series, dtype=float))
    history_months = list(train_months)
    training_features = create_xgboost_features(history, history_months)
    if training_features.empty:
        raise ForecastEvaluationError("XGBoost has no leakage-safe training rows.")
    feature_columns = list(XGBOOST_FEATURE_COLUMNS)
    model = XGBRegressor(**XGBOOST_PARAMS)
    model.fit(training_features[feature_columns], training_features["target"])
    forecasts = recursive_xgboost_forecast(
        model, history, history_months, feature_columns, steps
    )
    return np.asarray(forecasts, dtype=float), model


def fit_xgboost_full_history(series, month_labels):
    history = list(np.asarray(series, dtype=float))
    training_features = create_xgboost_features(history, month_labels)
    feature_columns = list(XGBOOST_FEATURE_COLUMNS)
    model = XGBRegressor(**XGBOOST_PARAMS)
    model.fit(training_features[feature_columns], training_features["target"])
    return model, feature_columns


def _captures_seasonality(model_name: str, parameters: dict) -> bool:
    if model_name == "ETS":
        return parameters.get("seasonal") in {"add", "mul"}
    if model_name == "Holt-Winters":
        return parameters.get("seasonal") in {"add", "mul"}
    if model_name in {"SARIMA", "SARIMAX"}:
        seasonal_order = parameters.get("seasonal_order") or (0, 0, 0, 12)
        return any(int(value) != 0 for value in seasonal_order[:3])
    if model_name in {"XGBoost", "ARIMAX"}:
        return True
    if model_name == "Prophet":
        return bool(parameters.get("yearly_fourier_order") or parameters.get("yearly_seasonality"))
    return False


def _implausible(predictions, train_values) -> str | None:
    predicted = np.asarray(predictions, dtype=float)
    if not np.isfinite(predicted).all():
        return "non-finite forecast"
    train = np.asarray(train_values, dtype=float)
    finite_train = train[np.isfinite(train)]
    if finite_train.size == 0:
        return None
    train_max = float(np.max(np.abs(finite_train)))
    if train_max > 0 and float(np.max(np.abs(predicted))) > EXPLOSION_MULTIPLIER * train_max:
        return "exploding forecast"
    return None


def residual_diagnostics(residuals) -> dict:
    values = np.asarray(residuals, dtype=float)
    values = values[np.isfinite(values)]
    result = {
        "residual_count": int(values.size),
        "residual_mean": None,
        "residual_acf_lag_1": None,
        "residual_acf_lag_12": None,
        "residual_acf": [],
    }
    if values.size < 3:
        return result
    result["residual_mean"] = float(np.mean(values))
    nlags = int(min(12, values.size - 1))
    acf_values = acf(values, nlags=nlags, fft=False)
    result["residual_acf"] = [float(value) for value in acf_values]
    if nlags >= 1:
        result["residual_acf_lag_1"] = float(acf_values[1])
    if nlags >= 12:
        result["residual_acf_lag_12"] = float(acf_values[12])
    return result


def _fit_forecast(model_name, parameters, train_values, train_months, steps, future_months):
    series = _as_series(train_values)
    residuals = None
    state = {}
    fitted = None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if model_name == "ETS":
            kwargs = {
                "error": parameters["error"],
                "trend": parameters["trend"],
                "seasonal": parameters["seasonal"],
                "damped_trend": parameters["damped_trend"],
            }
            if parameters.get("seasonal") is not None:
                kwargs["seasonal_periods"] = parameters.get("seasonal_periods") or SEASONAL_PERIOD
            initialization = parameters.get("initialization_method")
            if initialization:
                kwargs["initialization_method"] = initialization
            try:
                fitted = ETSModel(series, **kwargs).fit(disp=False)
            except Exception:
                if initialization == "estimated":
                    kwargs["initialization_method"] = "heuristic"
                    fitted = ETSModel(series, **kwargs).fit(disp=False)
                else:
                    raise
            raw = fitted.forecast(steps)
            residuals = getattr(fitted, "resid", None)
        elif model_name == "Holt-Winters":
            kwargs = {
                "trend": parameters["trend"],
                "damped_trend": bool(parameters.get("damped_trend", False)),
                "initialization_method": parameters.get("initialization_method") or "estimated",
            }
            if parameters.get("seasonal") is not None:
                kwargs["seasonal"] = parameters["seasonal"]
                kwargs["seasonal_periods"] = parameters.get("seasonal_periods") or SEASONAL_PERIOD
            try:
                fitted = ExponentialSmoothing(series, **kwargs).fit(optimized=True)
            except Exception:
                kwargs["initialization_method"] = "heuristic"
                fitted = ExponentialSmoothing(series, **kwargs).fit(optimized=True)
            raw = fitted.forecast(steps)
            residuals = getattr(fitted, "resid", None)
        elif model_name == "ARIMA":
            fitted = ARIMA(
                series,
                order=tuple(parameters["order"]),
                enforce_stationarity=False,
                enforce_invertibility=False,
            ).fit()
            raw = fitted.forecast(steps)
            residuals = getattr(fitted, "resid", None)
        elif model_name == "SARIMA":
            fitted = SARIMAX(
                series,
                order=tuple(parameters["order"]),
                seasonal_order=tuple(parameters["seasonal_order"]),
                enforce_stationarity=False,
                enforce_invertibility=False,
            ).fit(disp=False, maxiter=50)
            raw = fitted.forecast(steps)
            residuals = getattr(fitted, "resid", None)
        elif model_name == "XGBoost":
            raw_array, fitted = forecast_xgboost(train_values, train_months, steps)
            raw = raw_array
            state = {
                "feature_columns": list(XGBOOST_FEATURE_COLUMNS),
                "history": [float(value) for value in train_values],
                "history_months": list(train_months),
            }
        elif model_name in {"ARIMAX", "SARIMAX"}:
            scaler = StandardScaler()
            train_exog = calendar_exog(train_months, start_time=0)
            future_exog = calendar_exog(future_months, start_time=len(train_months))
            train_exog_scaled = scaler.fit_transform(train_exog)
            future_exog_scaled = scaler.transform(future_exog)
            seasonal_order = tuple(parameters.get("seasonal_order") or (0, 0, 0, 0))
            fitted = SARIMAX(
                series,
                exog=train_exog_scaled,
                order=tuple(parameters["order"]),
                seasonal_order=seasonal_order,
                enforce_stationarity=False,
                enforce_invertibility=False,
            ).fit(disp=False, maxiter=50)
            raw = fitted.forecast(steps=steps, exog=future_exog_scaled)
            residuals = getattr(fitted, "resid", None)
            state = {
                "scaler": scaler,
                "exogenous_features": list(EXOGENOUS_FEATURES),
                "history_length": len(train_months),
            }
        elif model_name == "Prophet":
            raw_array, fitted, state = forecast_prophet(
                train_values,
                train_months,
                future_months,
                parameters,
            )
            raw = raw_array
            residuals = None
            if state.get("train_residuals") is not None:
                residuals = state.pop("train_residuals")
        else:
            raise ForecastEvaluationError(f"Unsupported model family: {model_name}")

    predicted = require_finite_forecast(raw, model_name, expected=steps)
    reason = _implausible(predicted, train_values)
    if reason:
        raise ForecastEvaluationError(f"{model_name} failed sanity check: {reason}.")
    residual_info = residual_diagnostics(residuals) if residuals is not None else residual_diagnostics([])
    return predicted, fitted, state, residual_info


def _empty_result(model_name, error, elapsed, extra=None):
    payload = {
        "model_name": model_name,
        "status": "failed",
        "parameters": {},
        "mae": None,
        "rmse": None,
        "wape": None,
        "mape": None,
        "smape": None,
        "mase": None,
        "r2": None,
        "training_time_seconds": elapsed,
        "error": str(error),
        "raw_predictions": None,
        "clipped_predictions": None,
        "evaluation_fit": None,
        "required_forecast_state": {},
        "captures_seasonality": False,
        "rolling_wape_mean": None,
        "rolling_wape_std": None,
        "rolling_successful_folds": 0,
        "rolling_failed_folds": 0,
        "rolling_folds": [],
        "residual_diagnostics": {},
        "candidates_evaluated": 0,
        "candidates_succeeded": 0,
    }
    if extra:
        payload.update(extra)
    return payload


def _success_result(model_name, parameters, metrics, elapsed, raw, clipped, fit, state, extra=None):
    payload = {
        "model_name": model_name,
        "status": "success",
        "parameters": parameters,
        "mae": _to_float_or_none(metrics["MAE"]),
        "rmse": _to_float_or_none(metrics["RMSE"]),
        "wape": _to_float_or_none(metrics["WAPE"]),
        "mape": _to_float_or_none(metrics["MAPE"]),
        "smape": _to_float_or_none(metrics.get("sMAPE")),
        "mase": _to_float_or_none(metrics.get("MASE")),
        "r2": _to_float_or_none(metrics.get("R2")),
        "training_time_seconds": elapsed,
        "error": metrics.get("MAPE_NOTE") or metrics.get("MASE_NOTE"),
        "raw_predictions": [float(value) for value in raw],
        "clipped_predictions": [float(value) for value in clipped],
        "evaluation_fit": fit,
        "required_forecast_state": state,
        "captures_seasonality": _captures_seasonality(model_name, parameters),
    }
    if extra:
        payload.update(extra)
    return payload


def _needs_two_seasons(model_name: str, parameters: dict) -> bool:
    if model_name in {"ETS", "Holt-Winters"} and parameters.get("seasonal") in {"add", "mul"}:
        return True
    if model_name in {"SARIMA", "SARIMAX"}:
        seasonal_order = parameters.get("seasonal_order") or (0, 0, 0, 12)
        return any(int(value) != 0 for value in seasonal_order[:3])
    if model_name == "XGBoost":
        return True
    if model_name == "Prophet" and (
        parameters.get("yearly_fourier_order") or parameters.get("yearly_seasonality")
    ):
        return True
    return False


def build_rolling_folds(month_labels, horizon: int = DEFAULT_TEST_MONTHS) -> list[dict]:
    months = list(month_labels)
    n_months = len(months)
    folds = []
    for index, train_end in enumerate([n_months - 3 * horizon, n_months - 2 * horizon, n_months - horizon], 1):
        val_end = train_end + horizon
        if train_end < MIN_TRAIN_MONTHS or val_end > n_months or train_end <= 0:
            continue
        folds.append({
            "name": f"fold_{index}",
            "description": (
                f"train through {months[train_end - 1]}, "
                f"validate {months[train_end]} to {months[val_end - 1]}"
            ),
            "train_end": train_end,
            "val_end": val_end,
        })
    return folds


def observed_mask_from_kinds(values, kinds=None) -> np.ndarray:
    array = np.asarray(values, dtype=float).reshape(-1)
    if kinds is None:
        return np.isfinite(array)
    kind_row = np.asarray(kinds, dtype=object).reshape(-1)
    if kind_row.size != array.size:
        raise ForecastEvaluationError("Observation-kind length does not match values.")
    return (kind_row == KIND_NUMBER) & np.isfinite(array)


def evaluate_rolling(
    model_name,
    parameters,
    month_labels,
    monthly_history,
    horizon: int = DEFAULT_TEST_MONTHS,
    kinds=None,
    interpolate: bool = False,
    max_internal_gap: int = DEFAULT_MAX_INTERNAL_GAP,
):
    months = list(month_labels)
    values = np.asarray(monthly_history, dtype=float)
    kind_row = None if kinds is None else np.asarray(kinds, dtype=object)
    fold_rows = []
    wapes = []
    failed = 0
    folds = build_rolling_folds(months, horizon=horizon)
    if not folds:
        return {
            "rolling_folds": [],
            "rolling_wape_mean": None,
            "rolling_wape_std": None,
            "rolling_successful_folds": 0,
            "rolling_failed_folds": 0,
        }
    for fold in folds:
        train_end = fold["train_end"]
        val_end = fold["val_end"]
        train_months = months[:train_end]
        val_months = months[train_end:val_end]
        train_raw = values[:train_end]
        val_raw = values[train_end:val_end]
        train_kinds = None if kind_row is None else kind_row[:train_end]
        val_kinds = None if kind_row is None else kind_row[train_end:val_end]
        if _needs_two_seasons(model_name, parameters) and len(train_raw) < 24:
            failed += 1
            fold_rows.append({
                "name": fold["name"],
                "description": fold["description"],
                "status": "skipped",
                "error": "Seasonal model needs at least 24 training months for period=12.",
                "wape": None,
            })
            continue
        try:
            prepared, _methods, outcome, reason = prepare_series_window(
                train_raw,
                train_kinds if train_kinds is not None else np.full(train_raw.shape, KIND_NUMBER, dtype=object),
                interpolate=interpolate,
                max_internal_gap=max_internal_gap,
            )
            if outcome != OUTCOME_READY:
                raise ForecastEvaluationError(f"Fold training window is not model-ready: {reason}")
            predicted, _fitted, _state, _resid = _fit_forecast(
                model_name,
                parameters,
                prepared,
                train_months,
                len(val_raw),
                val_months,
            )
            metrics = calculate_metrics(
                val_raw,
                predicted,
                train_values=train_raw,
                observed_mask=observed_mask_from_kinds(val_raw, val_kinds),
                train_observed=observed_mask_from_kinds(train_raw, train_kinds),
            )
            if metrics.get("EVALUATION_STATUS") == "NOT_EVALUABLE":
                raise ForecastEvaluationError("Rolling fold has no observed validation targets.")
            wape = _to_float_or_none(metrics["WAPE"])
            fold_rows.append({
                "name": fold["name"],
                "description": fold["description"],
                "status": "success",
                "error": None,
                "wape": wape,
                "mae": _to_float_or_none(metrics["MAE"]),
                "rmse": _to_float_or_none(metrics["RMSE"]),
                "mape": _to_float_or_none(metrics["MAPE"]),
            })
            if wape is not None and np.isfinite(wape):
                wapes.append(wape)
        except Exception as error:
            failed += 1
            fold_rows.append({
                "name": fold["name"],
                "description": fold["description"],
                "status": "failed",
                "error": str(error),
                "exception_type": type(error).__name__,
                "wape": None,
            })
    successful = len([row for row in fold_rows if row.get("status") == "success"])
    return {
        "rolling_folds": fold_rows,
        "rolling_wape_mean": float(np.mean(wapes)) if wapes else None,
        "rolling_wape_std": float(np.std(wapes, ddof=1)) if len(wapes) > 1 else (0.0 if len(wapes) == 1 else None),
        "rolling_successful_folds": successful,
        "rolling_failed_folds": failed,
    }


def _candidate_score(holdout_wape, rolling_info):
    rolling_mean = rolling_info.get("rolling_wape_mean")
    folds = rolling_info.get("rolling_successful_folds") or 0
    if folds >= 2 and rolling_mean is not None and np.isfinite(rolling_mean):
        return (0, float(rolling_mean), float(holdout_wape))
    if holdout_wape is not None and np.isfinite(holdout_wape):
        return (1, float(holdout_wape), float(holdout_wape))
    return (2, float("inf"), float("inf"))


def _summarize_family_failures(failure_log, account_code, model_name) -> str:
    rows = [
        row for row in (failure_log or [])
        if row.get("account_code") == account_code and row.get("model_family") == model_name
    ]
    if not rows:
        return (
            "No valid configuration produced a finite holdout forecast. "
            "No underlying exception details were recorded."
        )
    counts = {}
    for row in rows:
        name = str(row.get("exception_type") or "Unknown")
        counts[name] = counts.get(name, 0) + 1
    summary = ", ".join(
        f"{name} ({count})" for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )
    examples = []
    for row in rows[:5]:
        examples.append(f"{row.get('exception_type')}: {row.get('reason')}")
    return (
        f"No valid configuration produced a finite holdout forecast. "
        f"Failure types: {summary}. Examples: " + " | ".join(examples)
    )


def _evaluate_candidate_grid(
    model_name,
    candidates,
    split,
    month_labels,
    monthly_history,
    failure_log=None,
    account_code=None,
    kinds=None,
    interpolate: bool = False,
    max_internal_gap: int = DEFAULT_MAX_INTERNAL_GAP,
):
    started = time.perf_counter()
    best = None
    succeeded = 0
    train_values = np.asarray(split.get("train_values_prepared", split["train_values"]), dtype=float)
    train_raw = np.asarray(split.get("train_values_raw", split["train_values"]), dtype=float)
    test_raw = np.asarray(split["test_values"], dtype=float)
    test_mask = split.get("test_observed_mask")
    train_observed = split.get("train_observed_mask")
    rolling_months = list(split["train_months"])
    rolling_history = train_raw
    rolling_kinds = split.get("train_kinds")
    for parameters in candidates:
        try:
            predicted, fitted, state, residual_info = _fit_forecast(
                model_name,
                parameters,
                train_values,
                split["train_months"],
                len(split["test_months"]),
                split["test_months"],
            )
            metrics = calculate_metrics(
                test_raw,
                predicted,
                train_values=train_raw,
                observed_mask=test_mask,
                train_observed=train_observed,
            )
            if metrics.get("EVALUATION_STATUS") == "NOT_EVALUABLE":
                if failure_log is not None:
                    failure_log.append({
                        "account_code": account_code,
                        "model_family": model_name,
                        "configuration": dict(parameters),
                        "exception_type": "NotEvaluable",
                        "reason": "No observed test targets.",
                    })
                continue
            holdout_wape = _to_float_or_none(metrics["WAPE"])
            rolling_info = evaluate_rolling(
                model_name,
                parameters,
                rolling_months,
                rolling_history,
                horizon=len(split["test_months"]),
                kinds=rolling_kinds if rolling_kinds is not None else kinds[: len(rolling_months)] if kinds is not None else None,
                interpolate=interpolate,
                max_internal_gap=max_internal_gap,
            )
            succeeded += 1
            score = _candidate_score(holdout_wape, rolling_info)
            current = {
                "parameters": dict(parameters),
                "metrics": metrics,
                "raw": predicted,
                "clipped": predicted,
                "fitted": fitted,
                "state": state,
                "residual_info": residual_info,
                "rolling_info": rolling_info,
                "score": score,
            }
            if best is None or score < best["score"]:
                best = current
        except Exception as error:
            if failure_log is not None:
                failure_log.append({
                    "account_code": account_code,
                    "model_family": model_name,
                    "configuration": dict(parameters),
                    "exception_type": type(error).__name__,
                    "reason": str(error)[:400],
                })
            continue
    elapsed = time.perf_counter() - started
    extra_counts = {
        "candidates_evaluated": len(candidates),
        "candidates_succeeded": succeeded,
    }
    if best is None:
        failure_message = _summarize_family_failures(failure_log, account_code, model_name)
        extra_counts["failure_examples"] = [
            row for row in (failure_log or [])
            if row.get("account_code") == account_code and row.get("model_family") == model_name
        ][:8]
        return _empty_result(model_name, failure_message, elapsed, extra_counts)
    extra = {
        **best["rolling_info"],
        **extra_counts,
        "residual_diagnostics": best["residual_info"],
    }
    return _success_result(
        model_name,
        best["parameters"],
        best["metrics"],
        elapsed,
        best["raw"],
        best["clipped"],
        best["fitted"],
        best["state"],
        extra,
    )


def ets_candidates(all_positive: bool) -> list[dict]:
    candidates = []
    for trend in [None, "add"]:
        damped_options = [False] if trend is None else [False, True]
        for damped_trend in damped_options:
            seasonal_options = [None, "add"]
            if all_positive:
                seasonal_options.append("mul")
            for seasonal in seasonal_options:
                candidates.append({
                    "error": "add",
                    "trend": trend,
                    "seasonal": seasonal,
                    "damped_trend": damped_trend,
                    "seasonal_periods": None if seasonal is None else SEASONAL_PERIOD,
                    "initialization_method": "estimated",
                })
    return candidates


def holt_winters_candidates(all_positive: bool) -> list[dict]:
    candidates = [
        {
            "trend": None,
            "damped_trend": False,
            "seasonal": None,
            "seasonal_periods": None,
            "initialization_method": "estimated",
        },
        {
            "trend": "add",
            "damped_trend": False,
            "seasonal": None,
            "seasonal_periods": None,
            "initialization_method": "estimated",
        },
        {
            "trend": "add",
            "damped_trend": True,
            "seasonal": None,
            "seasonal_periods": None,
            "initialization_method": "estimated",
        },
        {
            "trend": "add",
            "damped_trend": False,
            "seasonal": "add",
            "seasonal_periods": SEASONAL_PERIOD,
            "initialization_method": "estimated",
        },
        {
            "trend": "add",
            "damped_trend": True,
            "seasonal": "add",
            "seasonal_periods": SEASONAL_PERIOD,
            "initialization_method": "estimated",
        },
    ]
    if all_positive:
        candidates.extend([
            {
                "trend": "add",
                "damped_trend": False,
                "seasonal": "mul",
                "seasonal_periods": SEASONAL_PERIOD,
                "initialization_method": "estimated",
            },
            {
                "trend": "add",
                "damped_trend": True,
                "seasonal": "mul",
                "seasonal_periods": SEASONAL_PERIOD,
                "initialization_method": "estimated",
            },
        ])
    return candidates


def arima_candidates() -> list[dict]:
    return [
        {"order": (p, d, q)}
        for p, d, q in itertools.product(ARIMA_P_Q, ARIMA_D, ARIMA_P_Q)
    ]


def sarima_candidates() -> list[dict]:
    candidates = []
    for order in itertools.product(SARIMA_P_Q, SARIMA_D, SARIMA_P_Q):
        for seasonal_part in itertools.product(
            SARIMA_P_SEASONAL, SARIMA_D_SEASONAL, SARIMA_Q_SEASONAL
        ):
            candidates.append({
                "order": tuple(order),
                "seasonal_order": (
                    seasonal_part[0],
                    seasonal_part[1],
                    seasonal_part[2],
                    SEASONAL_PERIOD,
                ),
            })
    return candidates


def arimax_candidates() -> list[dict]:
    return [
        {
            "order": (p, d, q),
            "seasonal_order": (0, 0, 0, 0),
            "exogenous_features": list(EXOGENOUS_FEATURES),
        }
        for p, d, q in itertools.product(ARIMA_P_Q, ARIMA_D, ARIMA_P_Q)
    ]


def sarimax_candidates() -> list[dict]:
    candidates = []
    for order in itertools.product(SARIMA_P_Q, SARIMA_D, SARIMA_P_Q):
        for seasonal_part in itertools.product(
            SARIMA_P_SEASONAL, SARIMA_D_SEASONAL, SARIMA_Q_SEASONAL
        ):
            candidates.append({
                "order": tuple(order),
                "seasonal_order": (
                    seasonal_part[0],
                    seasonal_part[1],
                    seasonal_part[2],
                    SEASONAL_PERIOD,
                ),
                "exogenous_features": list(EXOGENOUS_FEATURES),
            })
    return candidates


def analyze_seasonality(month_labels, monthly_history, observed_mask=None) -> dict:
    values = np.asarray(monthly_history, dtype=float)
    months = list(month_labels)
    mask = observed_mask_from_kinds(values) if observed_mask is None else np.asarray(observed_mask, dtype=bool)
    observed = values[mask]
    parsed = [parse_month_label(label) for label in months]
    month_numbers = np.array([month for _year, month in parsed], dtype=int)
    years = np.array([year for year, _month in parsed], dtype=int)
    all_positive = bool(observed.size > 0 and np.all(observed > 0))
    has_nonpositive = bool(observed.size == 0 or np.any(observed <= 0))

    acf_source = observed if observed.size else values[np.isfinite(values)]
    acf_lags = int(min(24, acf_source.size - 1)) if acf_source.size > 1 else 0
    acf_values = acf(acf_source, nlags=acf_lags, fft=False) if acf_lags > 0 else np.array([1.0])
    lag12 = float(acf_values[12]) if acf_lags >= 12 else None

    month_stats = []
    for month in range(1, 13):
        subset = values[(month_numbers == month) & mask]
        month_stats.append({
            "month": month,
            "label": month_abbr[month],
            "count": int(subset.size),
            "mean": float(np.mean(subset)) if subset.size else None,
            "std": float(np.std(subset, ddof=1)) if subset.size > 1 else None,
            "min": float(np.min(subset)) if subset.size else None,
            "max": float(np.max(subset)) if subset.size else None,
        })
    month_means = [row["mean"] for row in month_stats if row["mean"] is not None]
    month_of_year_variation = float(np.std(month_means, ddof=1)) if len(month_means) > 1 else None

    year_comparison = {}
    for year in sorted(set(years.tolist())):
        year_comparison[str(year)] = [
            {"month": months[index], "value": float(values[index])}
            for index in range(len(months))
            if years[index] == year
        ]

    trend_strength = None
    seasonal_strength = None
    stl_available = False
    stl_error = None
    trend = None
    seasonal = None
    remainder = None
    try:
        from statsmodels.tsa.seasonal import STL

        stl = STL(values, period=SEASONAL_PERIOD, robust=True)
        stl_result = stl.fit()
        trend = np.asarray(stl_result.trend, dtype=float)
        seasonal = np.asarray(stl_result.seasonal, dtype=float)
        remainder = np.asarray(stl_result.resid, dtype=float)
        resid_var = float(np.var(remainder))
        seasonal_plus_resid = float(np.var(seasonal + remainder))
        trend_plus_resid = float(np.var(trend + remainder))
        if seasonal_plus_resid > 0:
            seasonal_strength = float(max(0.0, 1.0 - resid_var / seasonal_plus_resid))
        if trend_plus_resid > 0:
            trend_strength = float(max(0.0, 1.0 - resid_var / trend_plus_resid))
        stl_available = True
    except Exception as error:
        stl_error = str(error)

    limitation = (
        f"The training window contains {int(mask.sum())} observed months of {values.size} calendar months. "
        "Seasonality estimates from lag-12 ACF and STL(period=12) are limited "
        "and should not be treated as conclusive evidence of a stable seasonal pattern."
    )
    multiplicative_valid = all_positive and not has_nonpositive

    print("\n========== SEASONALITY DIAGNOSTICS ==========")
    print(limitation)
    print(f"observations: {values.size}")
    print(f"lag-12 autocorrelation: {lag12}")
    print("ACF[0:13]:", ", ".join(f"{float(value):.4f}" for value in acf_values[:13]))
    print(f"strictly positive values: {all_positive}")
    print(f"multiplicative seasonality valid: {multiplicative_valid}")
    print(f"trend strength (STL): {trend_strength}")
    print(f"seasonal strength (STL): {seasonal_strength}")
    print("Month-of-year averages:")
    for row in month_stats:
        print(
            f"  {row['label']}: n={row['count']} mean={row['mean']:.4f} "
            f"std={row['std'] if row['std'] is not None else 'n/a'}"
        )
    print(f"month-of-year variation (std of monthly means): {month_of_year_variation}")

    return {
        "n_observations": int(values.size),
        "annual_cycles_approx": round(values.size / 12.0, 2),
        "limitation": limitation,
        "lag12_acf": lag12,
        "acf": [float(value) for value in acf_values],
        "month_of_year": month_stats,
        "month_of_year_variation": month_of_year_variation,
        "trend_strength": trend_strength,
        "seasonal_strength": seasonal_strength,
        "stl_available": stl_available,
        "stl_error": stl_error,
        "all_positive": all_positive,
        "multiplicative_seasonality_valid": multiplicative_valid,
        "year_comparison": year_comparison,
        "series": [float(value) for value in values],
        "months": months,
        "stl_trend": [float(value) for value in trend] if trend is not None else None,
        "stl_seasonal": [float(value) for value in seasonal] if seasonal is not None else None,
        "stl_resid": [float(value) for value in remainder] if remainder is not None else None,
    }


def smoke_forecast_stats(values, month_labels) -> dict:
    array = np.asarray(values, dtype=float)
    mean = float(np.mean(array)) if array.size else None
    std = float(np.std(array, ddof=1)) if array.size > 1 else 0.0
    cv = float(std / mean) if mean not in (None, 0) and np.isfinite(mean) else None
    changes = []
    for index in range(1, array.size):
        previous = array[index - 1]
        current = array[index]
        pct = None if previous == 0 else float((current - previous) / previous * 100.0)
        changes.append({"from": month_labels[index - 1], "to": month_labels[index], "pct": pct})
    nearly_constant = bool(cv is not None and abs(cv) < 0.005)
    return {
        "values": [float(value) for value in array],
        "months": list(month_labels),
        "minimum": float(np.min(array)),
        "maximum": float(np.max(array)),
        "range": float(np.max(array) - np.min(array)),
        "coefficient_of_variation": cv,
        "month_to_month_pct": changes,
        "nearly_constant": nearly_constant,
        "has_nan_or_inf": bool(not np.isfinite(array).all()),
        "has_negative": bool(np.any(array < 0)),
    }


def generate_holdout_smoke(result, split, steps=13):
    if result["status"] != "success":
        return None
    future_months = []
    current = split["train_months"][-1]
    # Smoke from the 36-month evaluation fit would start Jan-2026, but the
    # required demo window is Jul-2026 to Jul-2027. That window is generated
    # after the production fit on all 42 months. Here we only stash holdout
    # predictions.
    return {
        "holdout_months": list(split["test_months"]),
        "holdout_forecast": list(result["raw_predictions"]),
        "future_placeholder_steps": steps,
        "future_start_after_history": add_month_label("Jun-2026", 1),
    }


def _algorithm_candidates(model_name: str, all_positive: bool) -> list[dict]:
    builders = {
        "ETS": lambda: ets_candidates(all_positive),
        "Holt-Winters": lambda: holt_winters_candidates(all_positive),
        "ARIMA": arima_candidates,
        "SARIMA": sarima_candidates,
        "XGBoost": lambda: [{"feature_columns": list(XGBOOST_FEATURE_COLUMNS)}],
        "ARIMAX": arimax_candidates,
        "SARIMAX": sarimax_candidates,
        "Prophet": lambda: prophet_candidates(all_positive),
    }
    if model_name not in builders:
        raise ForecastEvaluationError(f"Unsupported model family: {model_name}")
    return builders[model_name]()


def evaluate_seven_models(
    month_labels,
    monthly_history,
    quiet: bool = False,
    account_code: str | None = None,
    failure_log: list | None = None,
    algorithms=None,
    test_months: int | None = None,
    split_index: int | None = None,
    kinds=None,
    interpolate: bool = False,
    max_internal_gap: int = DEFAULT_MAX_INTERNAL_GAP,
    prepared_train=None,
    test_observed_mask=None,
):
    requested = list(algorithms or ALGORITHMS)

    def _progress(message: str) -> None:
        print(message, flush=True)

    split = chronological_train_test_split(
        month_labels,
        monthly_history,
        quiet=quiet,
        test_months=test_months,
        split_index=split_index,
    )
    train_raw = np.asarray(split["train_values"], dtype=float)
    test_raw = np.asarray(split["test_values"], dtype=float)
    kind_row = None if kinds is None else np.asarray(kinds, dtype=object)
    train_kinds = None if kind_row is None else kind_row[: split["train_count"]]
    test_kinds = None if kind_row is None else kind_row[split["train_count"]:]
    kind_for_prep = train_kinds if train_kinds is not None else np.full(train_raw.shape, KIND_NUMBER, dtype=object)
    if train_kinds is None:
        kind_for_prep = np.where(np.isfinite(train_raw), KIND_NUMBER, KIND_MISSING).astype(object)
    train_mean = account_numeric_mean(train_raw, kind_for_prep)
    if prepared_train is None:
        prepared_train, _methods, outcome, reason = prepare_series_window(
            train_raw,
            kind_for_prep,
            interpolate=interpolate,
            max_internal_gap=max_internal_gap,
            imputation_mean=train_mean,
        )
        if outcome != OUTCOME_READY:
            raise ForecastEvaluationError(f"Training window is not model-ready: {reason}")
    prepared_train = np.asarray(prepared_train, dtype=float)
    test_kind_for_prep = test_kinds if test_kinds is not None else np.where(
        np.isfinite(test_raw), KIND_NUMBER, KIND_MISSING
    ).astype(object)
    prepared_test, _test_methods, _test_fills = fill_missing_with_mean(
        test_raw, test_kind_for_prep, train_mean
    )
    train_observed = observed_mask_from_kinds(train_raw, train_kinds)
    test_mask = (
        np.asarray(test_observed_mask, dtype=bool)
        if test_observed_mask is not None
        else observed_mask_from_kinds(test_raw, test_kinds)
    )
    split = {
        **split,
        "train_values": prepared_train,
        "train_values_prepared": prepared_train,
        "train_values_raw": train_raw,
        "test_values": test_raw,
        "test_values_prepared": prepared_test,
        "train_imputation_mean": train_mean,
        "train_observed_mask": train_observed,
        "test_observed_mask": test_mask,
        "train_kinds": train_kinds,
        "evaluation_label": "selection-holdout evaluation",
    }
    observed_train = train_raw[train_observed]
    all_positive = bool(observed_train.size > 0 and np.all(observed_train > 0))
    if not quiet:
        _progress(f"[5/8] Evaluating {len(requested)} algorithms")
        _progress("Grid reductions:")
        for note in GRID_REDUCTIONS:
            _progress(f"  - {note}")
        diagnostics = analyze_seasonality(
            split["train_months"],
            train_raw,
            observed_mask=train_observed,
        )
    else:
        diagnostics = {
            "all_positive": all_positive,
            "multiplicative_seasonality_valid": all_positive,
            "lag12_acf": None,
            "seasonal_strength": None,
            "n_observations": int(train_observed.sum()),
        }
        if int(train_observed.sum()) > 13:
            acf_values = acf(observed_train, nlags=12, fft=False)
            diagnostics["lag12_acf"] = float(acf_values[12])
    diagnostics["all_positive"] = all_positive
    diagnostics["diagnostics_source"] = "training_window_only"
    results = []
    for model_name in requested:
        _progress(f"  Evaluating {model_name}...")
        try:
            results.append(
                _evaluate_candidate_grid(
                    model_name,
                    _algorithm_candidates(model_name, all_positive),
                    split,
                    month_labels,
                    monthly_history,
                    failure_log=failure_log,
                    account_code=account_code,
                    kinds=kind_row,
                    interpolate=interpolate,
                    max_internal_gap=max_internal_gap,
                )
            )
        except Exception as error:
            if failure_log is not None:
                failure_log.append({
                    "account_code": account_code,
                    "model_family": model_name,
                    "configuration": {},
                    "exception_type": type(error).__name__,
                    "reason": str(error)[:400],
                })
            results.append(_empty_result(model_name, f"{type(error).__name__}: {error}", 0.0))
    attempted = [row["model_name"] for row in results]
    if attempted != requested:
        raise AssertionError(f"Expected models {requested}, attempted {attempted}.")

    if not quiet:
        print("\nModel evaluation completed.", flush=True)
        print(
            f"{'Model':<14} {'Holdout WAPE':>13} {'Roll WAPE':>11} {'Roll std':>9} "
            f"{'Folds':>7} {'RMSE':>10} {'Seasonal':>9}",
            flush=True,
        )
        for row in results:
            if row["status"] == "success":
                print(
                    f"{row['model_name']:<14} {row['wape']:>13.4f} "
                    f"{(row['rolling_wape_mean'] if row['rolling_wape_mean'] is not None else float('nan')):>11.4f} "
                    f"{(row['rolling_wape_std'] if row['rolling_wape_std'] is not None else float('nan')):>9.4f} "
                    f"{row['rolling_successful_folds']}/3 "
                    f"{row['rmse']:>10.4f} "
                    f"{str(row['captures_seasonality']):>9} "
                    f"{row['parameters']}"
                )
            else:
                print(f"  {row['model_name']}: FAILED - {row['error']}")
    return results, split, diagnostics


def _primary_metric(row) -> tuple:
    rolling_mean = row.get("rolling_wape_mean")
    folds = row.get("rolling_successful_folds") or 0
    if row["status"] != "success" or row.get("wape") is None or not np.isfinite(row["wape"]):
        return (9, float("inf"), float("inf"), float("inf"), row["model_name"])
    if folds >= 2 and rolling_mean is not None and np.isfinite(rolling_mean):
        return (0, float(rolling_mean), float(row["wape"]), float(row["rmse"]), row["model_name"])
    return (1, float(row["wape"]), float(row["wape"]), float(row["rmse"]), row["model_name"])


def select_best_model(evaluation_results, seasonality_diagnostics=None, quiet: bool = False):
    if not quiet:
        print("[6/8] Selecting the best model by rolling-origin WAPE, with holdout WAPE as secondary", flush=True)
    successful = [
        row for row in evaluation_results
        if row["status"] == "success" and row["wape"] is not None and np.isfinite(row["wape"])
    ]
    if not successful:
        raise AssertionError("No successful model produced a finite WAPE. Production training stopped.")

    ranked = sorted(successful, key=_primary_metric)
    best = ranked[0]
    diagnostics = seasonality_diagnostics or {}
    lag12 = diagnostics.get("lag12_acf")
    seasonal_strength = diagnostics.get("seasonal_strength")
    seasonality_supported = bool(
        (lag12 is not None and abs(float(lag12)) >= 0.20)
        or (seasonal_strength is not None and float(seasonal_strength) >= 0.30)
    )

    best_metric = _primary_metric(best)
    contenders = [
        row for row in ranked
        if abs(_primary_metric(row)[1] - best_metric[1]) <= TIE_WAPE_POINTS
        and _primary_metric(row)[0] == best_metric[0]
    ]
    selection_notes = []
    if len(contenders) > 1:
        seasonal_contenders = [row for row in contenders if row.get("captures_seasonality")]
        if seasonal_contenders and seasonality_supported:
            def _tie_key(row):
                resid = (row.get("residual_diagnostics") or {}).get("residual_acf_lag_12")
                resid_abs = abs(float(resid)) if resid is not None else 1.0
                stability = row.get("rolling_wape_std")
                stability_value = float(stability) if stability is not None else 0.0
                return (resid_abs, stability_value, row["wape"], row["rmse"])
            tied = sorted(seasonal_contenders, key=_tie_key)[0]
            if tied["model_name"] != best["model_name"] or tied["parameters"] != best["parameters"]:
                selection_notes.append(
                    "Tie-break within 1 rolling-WAPE point preferred a seasonal specification "
                    "with lower residual lag-12 autocorrelation."
                )
                best = tied

    if (
        best["model_name"] == "ETS"
        and not best.get("captures_seasonality")
        and seasonality_supported
    ):
        seasonal_alternatives = [
            row for row in successful
            if row.get("captures_seasonality") and row.get("rolling_successful_folds", 0) >= 2
        ]
        if seasonal_alternatives:
            alternative = sorted(seasonal_alternatives, key=_primary_metric)[0]
            best_roll = best.get("rolling_wape_mean")
            alt_roll = alternative.get("rolling_wape_mean")
            residual_lag12 = (best.get("residual_diagnostics") or {}).get("residual_acf_lag_12")
            unmodelled = residual_lag12 is not None and abs(float(residual_lag12)) >= 0.20
            if alt_roll is not None and best_roll is not None:
                if unmodelled or alt_roll <= best_roll + TIE_WAPE_POINTS:
                    selection_notes.append(
                        "Non-seasonal ETS was not kept solely for a favourable six-month holdout "
                        "because rolling validation or residual lag-12 autocorrelation showed "
                        "unmodelled seasonality."
                    )
                    best = alternative

    reason = (
        f"Selected {best['model_name']} with parameters {best['parameters']}. "
        f"Primary metric is mean rolling-origin WAPE "
        f"({best.get('rolling_wape_mean')}, std={best.get('rolling_wape_std')}, "
        f"successful folds={best.get('rolling_successful_folds')}). "
        f"Secondary holdout WAPE is {best['wape']}. "
        f"Captures 12-month seasonality: {best.get('captures_seasonality')}. "
        f"Seasonality supported by diagnostics: {seasonality_supported}."
    )
    if selection_notes:
        reason = reason + " " + " ".join(selection_notes)
    best["selection_reason"] = reason
    print(f"BEST MODEL: {best['model_name']}")
    print(f"BEST PARAMETERS: {best['parameters']}")
    print(f"SELECTION REASON: {reason}")
    return best


def compact_evaluation_row(row: dict) -> dict:
    return {
        "model_name": row.get("model_name"),
        "status": row.get("status"),
        "parameters": row.get("parameters") or {},
        "mae": row.get("mae"),
        "rmse": row.get("rmse"),
        "wape": row.get("wape"),
        "mape": row.get("mape"),
        "smape": row.get("smape"),
        "mase": row.get("mase"),
        "r2": row.get("r2"),
        "error": row.get("error"),
        "raw_predictions": row.get("raw_predictions"),
        "rolling_wape_mean": row.get("rolling_wape_mean"),
        "rolling_wape_std": row.get("rolling_wape_std"),
        "rolling_successful_folds": row.get("rolling_successful_folds"),
        "rolling_failed_folds": row.get("rolling_failed_folds"),
        "candidates_evaluated": row.get("candidates_evaluated"),
        "candidates_succeeded": row.get("candidates_succeeded"),
    }


def _failed_evaluation_row(code, algorithm, error, specification=None, status="FAILED", extra=None) -> dict:
    payload = {
        "Budget Code": str(code),
        "Algorithm": algorithm,
        "MAE": np.nan,
        "RMSE": np.nan,
        "MAPE": np.nan,
        "WAPE": np.nan,
        "MASE": np.nan,
        "R2": np.nan,
        "Status": status,
        "Error": str(error),
        "Model specification": specification or {},
        "Metric_Wins": 0,
        "Won_Metrics": "",
        "holdout_forecast": None,
        "evaluation_label": "selection-holdout evaluation",
    }
    if extra:
        payload.update(extra)
    return payload


def _successful_status(status) -> bool:
    return str(status).strip().casefold() in {"success", "successful"}


def _success_mask(status) -> pd.Series:
    return status.astype(str).str.strip().str.casefold().isin({"success", "successful"})


def assign_metric_wins(evaluation_table: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    table = evaluation_table.copy()
    if "Metric_Wins" not in table.columns:
        table["Metric_Wins"] = 0
    if "Won_Metrics" not in table.columns:
        table["Won_Metrics"] = ""
    metric_winner_rows = []
    lower_better = ["MAE", "RMSE", "MAPE", "WAPE", "MASE"]
    for code, group in table.groupby("Budget Code", sort=False):
        winner_row = {"Budget Code": str(code)}
        successful = group[_success_mask(group["Status"])].copy()
        won = {idx: [] for idx in group.index}
        for metric in lower_better:
            finite = successful[metric].apply(lambda value: value is not None and np.isfinite(float(value)))
            candidates = successful.loc[finite]
            if candidates.empty:
                winner_row[f"{metric}_Winner"] = None
                winner_row[f"{metric}_Value"] = np.nan
                continue
            best_value = float(candidates[metric].min())
            winners = candidates.index[np.isclose(candidates[metric].astype(float), best_value, equal_nan=False)]
            winner_row[f"{metric}_Winner"] = ", ".join(candidates.loc[winners, "Algorithm"].astype(str).tolist())
            winner_row[f"{metric}_Value"] = best_value
            for idx in winners:
                won[idx].append(metric)
        finite_r2 = successful["R2"].apply(lambda value: value is not None and np.isfinite(float(value)))
        r2_candidates = successful.loc[finite_r2]
        if r2_candidates.empty:
            winner_row["R2_Winner"] = None
            winner_row["R2_Value"] = np.nan
        else:
            best_r2 = float(r2_candidates["R2"].max())
            winners = r2_candidates.index[np.isclose(r2_candidates["R2"].astype(float), best_r2, equal_nan=False)]
            winner_row["R2_Winner"] = ", ".join(r2_candidates.loc[winners, "Algorithm"].astype(str).tolist())
            winner_row["R2_Value"] = best_r2
            for idx in winners:
                won[idx].append("R2")
        for idx, metrics in won.items():
            table.at[idx, "Metric_Wins"] = len(metrics)
            table.at[idx, "Won_Metrics"] = ", ".join(metrics)
        metric_winner_rows.append(winner_row)
    return table, pd.DataFrame(metric_winner_rows)


def _window_split_meta(outcome_row, history_months, split_index=None, test_months=None) -> dict:
    if outcome_row is not None:
        train_months = list(outcome_row.get("train_months") or [])
        test_month_labels = list(outcome_row.get("test_months") or [])
        account_months = train_months + test_month_labels
        if account_months:
            return {
                "account_months": account_months,
                "train_months": train_months,
                "test_months": test_month_labels,
                "split_index": int(outcome_row.get("split_index") or len(train_months)),
                "train_count": int(outcome_row.get("train_size") or len(train_months)),
                "test_count": int(outcome_row.get("test_size") or len(test_month_labels)),
                "observed_test_count": int(outcome_row.get("observed_test_count") or 0),
            }
    months = list(history_months)
    if split_index is not None:
        train_count = int(split_index)
        holdout_count = len(months) - train_count
    elif test_months is None:
        train_count, holdout_count = calendar_split_sizes(len(months))
    else:
        holdout_count = int(test_months)
        train_count = len(months) - holdout_count
    return {
        "account_months": months,
        "train_months": months[:train_count],
        "test_months": months[train_count:],
        "split_index": train_count,
        "train_count": train_count,
        "test_count": holdout_count,
        "observed_test_count": None,
    }


def _status_from_outcome(outcome_row) -> tuple[str, str]:
    outcome = str(outcome_row.get("training_outcome") or "")
    reason = str(outcome_row.get("training_reason") or outcome)
    if int(outcome_row.get("train_size") or 0) < MIN_TRAIN_MONTHS:
        return STATUS_INSUFFICIENT_HISTORY, "Training window is shorter than the minimum 12 months."
    if outcome == OUTCOME_ZERO_POLICY:
        return STATUS_ZERO_POLICY, reason
    if outcome == OUTCOME_NO_DATA:
        return STATUS_NO_DATA, reason
    if outcome == OUTCOME_INVALID:
        return STATUS_INVALID_DATA, reason
    if outcome == OUTCOME_UNRESOLVED:
        return STATUS_UNRESOLVED_MISSING, reason
    return str(outcome_row.get("evaluation_status") or STATUS_NOT_EVALUABLE), reason


def _zero_baseline_row(code, split_meta, test_values, test_mask, train_raw, train_observed) -> dict:
    zeros = np.zeros(len(test_values), dtype=float)
    metrics = calculate_metrics(
        test_values,
        zeros,
        train_values=train_raw,
        observed_mask=test_mask,
        train_observed=train_observed,
    )
    status = STATUS_ZERO_POLICY
    if metrics.get("EVALUATION_STATUS") == "NOT_EVALUABLE":
        status = STATUS_NOT_EVALUABLE
    return {
        "Budget Code": str(code),
        "Algorithm": "ZERO_POLICY",
        "MAE": metrics.get("MAE"),
        "RMSE": metrics.get("RMSE"),
        "MAPE": metrics.get("MAPE"),
        "WAPE": metrics.get("WAPE"),
        "MASE": metrics.get("MASE"),
        "R2": metrics.get("R2"),
        "Status": status,
        "Error": metrics.get("MAPE_NOTE") or "",
        "Model specification": {"policy": "ZERO_POLICY"},
        "Metric_Wins": 0,
        "Won_Metrics": "",
        "holdout_forecast": [0.0] * len(test_values),
        "evaluation_label": "selection-holdout evaluation",
        "train_months": list(split_meta["train_months"]),
        "test_months": list(split_meta["test_months"]),
        "train_count": split_meta["train_count"],
        "test_count": split_meta["test_count"],
        "observed_test_count": int(np.asarray(test_mask, dtype=bool).sum()) if test_mask is not None else metrics.get("observed_target_count"),
        "missing_or_invalid_test_count": metrics.get("missing_or_invalid_target_count"),
        "forecast_coverage": 1.0,
        "candidate_status": status,
    }


def evaluate_selected_accounts(
    selected_codes,
    selected_matrix,
    history_months,
    test_months: int | None = None,
    algorithms=None,
    split_index: int | None = None,
    account_outcomes: pd.DataFrame | None = None,
    kind_matrix=None,
    prepared_train_by_account=None,
    observed_test_mask=None,
    interpolate_accounts=None,
    max_internal_gap: int = DEFAULT_MAX_INTERNAL_GAP,
):
    requested = list(algorithms or ALGORITHMS)
    selected_codes = [str(code) for code in selected_codes]
    interpolate_set = {str(code) for code in (interpolate_accounts or [])}
    evaluation_rows = []
    account_family_evaluations = {}
    holdout_actuals = {}
    splits_by_account = {}
    failure_log = []
    split_info = None
    expected_rows = 0
    outcome_lookup = {}
    if account_outcomes is not None:
        for _, row in account_outcomes.iterrows():
            outcome_lookup[str(row["account_code"])] = row
    for index, code in enumerate(selected_codes, 1):
        print(f"\n[Evaluation {index}/{len(selected_codes)}] Budget Code {code}", flush=True)
        outcome_row = outcome_lookup.get(code)
        split_meta = _window_split_meta(outcome_row, history_months, split_index=split_index, test_months=test_months)
        account_months = split_meta["account_months"]
        series = np.asarray(selected_matrix.loc[code, account_months], dtype=float)
        kinds = None
        if kind_matrix is not None and code in getattr(kind_matrix, "index", []):
            kinds = np.asarray(kind_matrix.loc[code, account_months], dtype=object)
        train_raw = series[: split_meta["split_index"]]
        test_raw = series[split_meta["split_index"]:]
        train_kinds = None if kinds is None else kinds[: split_meta["split_index"]]
        test_kinds = None if kinds is None else kinds[split_meta["split_index"]:]
        test_mask = observed_mask_from_kinds(test_raw, test_kinds)
        if observed_test_mask is not None and code in getattr(observed_test_mask, "index", []):
            test_mask = np.asarray(observed_test_mask.loc[code, split_meta["test_months"]], dtype=bool)
        train_observed = observed_mask_from_kinds(train_raw, train_kinds)
        observed_test_count = int(test_mask.sum())
        missing_test_count = int(len(test_raw) - observed_test_count)
        coverage_extra = {
            "train_months": list(split_meta["train_months"]),
            "test_months": list(split_meta["test_months"]),
            "train_count": split_meta["train_count"],
            "test_count": split_meta["test_count"],
            "observed_test_count": observed_test_count,
            "missing_or_invalid_test_count": missing_test_count,
            "evaluation_label": "selection-holdout evaluation",
        }
        splits_by_account[code] = {
            **coverage_extra,
            "test_values": [None if not np.isfinite(value) else float(value) for value in test_raw],
        }
        holdout_actuals[code] = {
            month: None if not np.isfinite(value) else float(value)
            for month, value in zip(split_meta["test_months"], test_raw)
            if np.isfinite(value)
        }

        ready = True
        status_reason = ""
        status_name = None
        if outcome_row is not None:
            status_name, status_reason = _status_from_outcome(outcome_row)
            ready = bool(outcome_row.get("ready_for_complete_data_model")) and status_name not in {
                STATUS_INSUFFICIENT_HISTORY,
                STATUS_ZERO_POLICY,
                STATUS_NO_DATA,
                STATUS_INVALID_DATA,
                STATUS_UNRESOLVED_MISSING,
            }
        if ready and split_meta["train_count"] < MIN_TRAIN_MONTHS:
            ready = False
            status_name = STATUS_INSUFFICIENT_HISTORY
            status_reason = "Training window is shorter than the minimum 12 months."

        if not ready:
            expected_rows += 1
            if status_name == STATUS_ZERO_POLICY:
                evaluation_rows.append(
                    _zero_baseline_row(code, split_meta, test_raw, test_mask, train_raw, train_observed)
                )
            else:
                evaluation_rows.append(
                    _failed_evaluation_row(
                        code,
                        status_name or STATUS_NOT_EVALUABLE,
                        status_reason or "Account is not eligible for algorithm evaluation.",
                        status=status_name or STATUS_NOT_EVALUABLE,
                        extra=coverage_extra,
                    )
                )
            account_family_evaluations[code] = {}
            print(f"  Winner: {status_name or STATUS_NOT_EVALUABLE}", flush=True)
            print(f"Evaluation progress: status record for {code}", flush=True)
            continue

        expected_rows += len(requested)
        prepared = None
        if prepared_train_by_account and code in prepared_train_by_account:
            prepared_series = prepared_train_by_account[code]
            if isinstance(prepared_series, pd.Series):
                prepared = np.asarray(prepared_series.reindex(split_meta["train_months"]), dtype=float)
            else:
                prepared = np.asarray(prepared_series, dtype=float)
        present = set()
        try:
            family_results, split_info, _diagnostics = evaluate_seven_models(
                account_months,
                series,
                quiet=True,
                account_code=str(code),
                failure_log=failure_log,
                algorithms=requested,
                test_months=None if split_meta["split_index"] is not None else test_months,
                split_index=split_meta["split_index"],
                kinds=kinds,
                interpolate=code in interpolate_set,
                max_internal_gap=max_internal_gap,
                prepared_train=prepared,
                test_observed_mask=test_mask,
            )
        except Exception as error:
            for algorithm in requested:
                evaluation_rows.append(
                    _failed_evaluation_row(
                        code,
                        algorithm,
                        f"{type(error).__name__}: {error}",
                        extra=coverage_extra,
                    )
                )
            account_family_evaluations[code] = {}
            print(
                f"Evaluation progress: {index * len(requested)}/{len(selected_codes) * len(requested)}",
                flush=True,
            )
            continue
        print(
            f"Evaluation progress: {index * len(requested)}/{len(selected_codes) * len(requested)}",
            flush=True,
        )
        compact = {}
        for row in family_results:
            algorithm = row["model_name"]
            present.add(algorithm)
            compact[algorithm] = compact_evaluation_row(row)
            forecast = row.get("raw_predictions")
            coverage = None
            if forecast is not None and split_meta["test_count"]:
                finite_forecast = np.isfinite(np.asarray(forecast, dtype=float)).sum()
                coverage = float(finite_forecast / split_meta["test_count"])
            extra = {
                **coverage_extra,
                "forecast_coverage": coverage,
                "candidate_status": "SUCCESS" if _successful_status(row.get("status")) else "FAILED",
                "failure_reason": "" if _successful_status(row.get("status")) else (row.get("error") or "Algorithm evaluation failed."),
            }
            if _successful_status(row.get("status")):
                evaluation_rows.append({
                    "Budget Code": code,
                    "Algorithm": algorithm,
                    "MAE": row.get("mae"),
                    "RMSE": row.get("rmse"),
                    "MAPE": row.get("mape"),
                    "WAPE": row.get("wape"),
                    "MASE": row.get("mase"),
                    "R2": row.get("r2"),
                    "Status": "SUCCESS",
                    "Error": "",
                    "Model specification": row.get("parameters") or {},
                    "Metric_Wins": 0,
                    "Won_Metrics": "",
                    "holdout_forecast": forecast,
                    **extra,
                })
            else:
                evaluation_rows.append(
                    _failed_evaluation_row(
                        code,
                        algorithm,
                        row.get("error") or "Algorithm evaluation failed.",
                        row.get("parameters") or {},
                        extra=extra,
                    )
                )
        for algorithm in requested:
            if algorithm not in present:
                evaluation_rows.append(
                    _failed_evaluation_row(
                        code,
                        algorithm,
                        "Algorithm evaluation row is missing.",
                        extra=coverage_extra,
                    )
                )
        account_family_evaluations[code] = compact
        account_table = pd.DataFrame(
            [row for row in evaluation_rows if str(row.get("Budget Code")) == code]
        )
        if not account_table.empty:
            scored, _ = assign_metric_wins(account_table)
            local_winners = select_account_winners(scored)
            if not local_winners.empty:
                win = local_winners.iloc[0]
                winner_label = win.get("Winning Algorithm") or win.get("Status") or "none"
                print(f"  Winner: {winner_label}", flush=True)
    evaluation_table = pd.DataFrame(evaluation_rows)
    if len(evaluation_table) != expected_rows:
        raise AssertionError(
            f"Evaluation table has {len(evaluation_table)} rows, expected {expected_rows} "
            "from requested accounts/algorithms plus explicit status records."
        )
    evaluation_table, metric_winners = assign_metric_wins(evaluation_table)
    holdout_alignment = align_holdout_by_calendar(holdout_actuals, history_months)
    return {
        "evaluation_table": evaluation_table,
        "metric_winners": metric_winners,
        "account_family_evaluations": account_family_evaluations,
        "holdout_actuals": holdout_actuals,
        "holdout_alignment": holdout_alignment,
        "failure_log": failure_log,
        "split_info": split_info,
        "splits_by_account": splits_by_account,
        "algorithms": requested,
        "evaluation_label": "selection-holdout evaluation",
        "expected_rows": expected_rows,
    }


def select_account_winners(evaluation_table: pd.DataFrame) -> pd.DataFrame:
    winners = []
    non_algorithm_statuses = {
        STATUS_ZERO_POLICY,
        STATUS_NO_DATA,
        STATUS_UNRESOLVED_MISSING,
        STATUS_INVALID_DATA,
        STATUS_INSUFFICIENT_HISTORY,
        STATUS_NOT_EVALUABLE,
        STATUS_NO_VALID_WINNER,
    }
    for code, group in evaluation_table.groupby("Budget Code", sort=False):
        statuses = {str(value).strip() for value in group["Status"].tolist()}
        explicit = statuses & non_algorithm_statuses
        successful = group[_success_mask(group["Status"])].copy()
        if successful.empty:
            status = next(iter(explicit), STATUS_NO_VALID_WINNER)
            if status == "FAILED" or not explicit:
                status = STATUS_NO_VALID_WINNER
            error = str(group.iloc[0].get("Error") or "No valid evaluated local winner.")
            winners.append({
                "Budget Code": str(code),
                "Winning Algorithm": None,
                "Metric Wins": 0,
                "Won Metrics": "",
                "MAE": np.nan,
                "RMSE": np.nan,
                "MAPE": np.nan,
                "WAPE": np.nan,
                "MASE": np.nan,
                "R2": np.nan,
                "Status": status,
                "Error": error,
            })
            continue
        successful["_wape_sort"] = successful["WAPE"].apply(
            lambda value: float(value) if value is not None and np.isfinite(float(value)) else np.inf
        )
        successful["_mase_sort"] = successful["MASE"].apply(
            lambda value: float(value) if value is not None and np.isfinite(float(value)) else np.inf
        )
        successful["_rmse_sort"] = successful["RMSE"].apply(
            lambda value: float(value) if value is not None and np.isfinite(float(value)) else np.inf
        )
        successful = successful.sort_values(
            ["Metric_Wins", "_wape_sort", "_mase_sort", "_rmse_sort", "Algorithm"],
        ascending=[False, True, True, True, True],
            kind="mergesort",
        )
        best = successful.iloc[0]
        winners.append({
            "Budget Code": str(code),
            "Winning Algorithm": best["Algorithm"],
            "Metric Wins": int(best["Metric_Wins"]),
            "Won Metrics": best.get("Won_Metrics") or "",
            "MAE": best.get("MAE"),
            "RMSE": best.get("RMSE"),
            "MAPE": best.get("MAPE"),
            "WAPE": best.get("WAPE"),
            "MASE": best.get("MASE"),
            "R2": best.get("R2"),
            "Status": "SUCCESS",
            "Error": "",
            "Model specification": best.get("Model specification") or {},
        })
    return pd.DataFrame(winners)


def require_successful_account_winners(
    account_winners: pd.DataFrame,
    required_codes=None,
) -> pd.DataFrame:
    """Raise when required eligible accounts have no local winner.

    Zero-policy, unresolved, invalid, and non-evaluable accounts are not treated
    as missing algorithm winners unless they are explicitly required.
    """
    if account_winners is None or account_winners.empty:
        raise AssertionError("Account-level model selection produced no winners.")
    skip_statuses = {
        STATUS_ZERO_POLICY.casefold(),
        STATUS_NO_DATA.casefold(),
        STATUS_UNRESOLVED_MISSING.casefold(),
        STATUS_INVALID_DATA.casefold(),
        STATUS_INSUFFICIENT_HISTORY.casefold(),
        STATUS_NOT_EVALUABLE.casefold(),
    }
    required = None if required_codes is None else {str(code) for code in required_codes}
    failed = []
    for _, row in account_winners.iterrows():
        code = str(row.get("Budget Code"))
        if required is not None and code not in required:
            continue
        algorithm = row.get("Winning Algorithm")
        status = str(row.get("Status") or "").strip()
        if status.casefold() in skip_statuses and required is None:
            continue
        missing_algorithm = algorithm is None or (isinstance(algorithm, float) and pd.isna(algorithm))
        if missing_algorithm or status.casefold() in {"failed", STATUS_NO_VALID_WINNER.casefold()}:
            failed.append(code)
    if failed:
        listed = ", ".join(failed)
        raise AssertionError(
            f"All forecasting algorithms failed for Budget Code(s): {listed}. "
            "A production artifact was not created."
        )
    return account_winners


def evaluation_results_by_code(evaluation_table: pd.DataFrame) -> dict:
    nested: dict[str, dict] = {}
    if evaluation_table is None or evaluation_table.empty:
        return nested
    for _, row in evaluation_table.iterrows():
        code = str(row.get("Budget Code"))
        algorithm = str(row.get("Algorithm"))
        status = "success" if _successful_status(row.get("Status")) else "failed"
        entry = {"status": status}
        if status == "success":
            entry["metrics"] = {
                "MAE": row.get("MAE"),
                "RMSE": row.get("RMSE"),
                "MAPE": row.get("MAPE"),
                "WAPE": row.get("WAPE"),
                "R2": row.get("R2"),
                "MASE": row.get("MASE"),
            }
        else:
            entry["error"] = str(row.get("Error") or "Algorithm evaluation failed.")
        nested.setdefault(code, {})[algorithm] = entry
    return nested


def select_overall_algorithm(
    evaluation_table: pd.DataFrame,
    account_winners: pd.DataFrame,
    selected_codes,
    algorithms=None,
):
    requested = list(algorithms or ALGORITHMS)
    selected_codes = [str(code) for code in selected_codes]
    win_counts = {name: 0 for name in requested}
    for algorithm in account_winners["Winning Algorithm"].dropna():
        if algorithm in win_counts:
            win_counts[algorithm] += 1

    summary_rows = []
    for algorithm in requested:
        group = evaluation_table[evaluation_table["Algorithm"] == algorithm]
        successful = group[_success_mask(group["Status"])]
        summary_rows.append({
            "Algorithm": algorithm,
            "Account Wins": int(win_counts[algorithm]),
            "Mean MAE": float(successful["MAE"].mean()) if not successful.empty else np.nan,
            "Mean RMSE": float(successful["RMSE"].mean()) if not successful.empty else np.nan,
            "Mean MAPE": float(successful["MAPE"].mean()) if not successful.empty else np.nan,
            "Mean WAPE": float(successful["WAPE"].mean()) if not successful.empty else np.nan,
            "Mean MASE": float(successful["MASE"].mean()) if not successful.empty else np.nan,
            "Mean R2": float(successful["R2"].mean()) if not successful.empty else np.nan,
            "Successful Accounts": int(len(successful)),
            "Full coverage": bool(len(successful) == len(selected_codes)),
        })
    algorithm_summary = pd.DataFrame(summary_rows)
    eligible = algorithm_summary[algorithm_summary["Full coverage"]].copy()
    if eligible.empty:
        raise AssertionError(
            "No algorithm successfully evaluated all selected accounts. "
            "A production artifact was not created. Coverage:\n"
            + algorithm_summary.to_string(index=False)
        )
    eligible = eligible.sort_values(
        ["Account Wins", "Mean WAPE", "Mean MASE", "Mean RMSE", "Algorithm"],
        ascending=[False, True, True, True, True],
        kind="mergesort",
    )
    ranked = algorithm_summary.sort_values(
        ["Account Wins", "Mean WAPE", "Mean MASE", "Mean RMSE", "Algorithm"],
        ascending=[False, True, True, True, True],
        kind="mergesort",
    )
    winner = eligible.iloc[0]
    reason = (
        f"Selected {winner['Algorithm']} because it won {int(winner['Account Wins'])} of "
        f"{len(selected_codes)} account-level metric-win comparisons, succeeded on all "
        f"{len(selected_codes)} accounts, and had mean WAPE {winner['Mean WAPE']:.4f}, "
        f"mean MASE {winner['Mean MASE']:.4f}, and mean RMSE {winner['Mean RMSE']:.4f}."
    )
    print("\n========== ACCOUNT-LEVEL WINNERS ==========")
    for _, row in account_winners.iterrows():
        wins = row["Metric Wins"] if "Metric Wins" in account_winners.columns else ""
        suffix = f" ({wins} metric wins)" if wins != "" and pd.notna(wins) else ""
        print(f"  {row['Budget Code']}: {row['Winning Algorithm']}{suffix}")
    print("\n========== ACCOUNT WIN COUNTS ==========")
    for algorithm, count in win_counts.items():
        print(f"  {algorithm}: {count}")
    print(f"\nOVERALL BEST ALGORITHM: {winner['Algorithm']}")
    print(f"SELECTION REASON: {reason}")
    return {
        "overall_best_algorithm": winner["Algorithm"],
        "selection_reason": reason,
        "algorithm_summary": algorithm_summary.drop(columns=["Full coverage"]),
        "account_wins": win_counts,
        "ranking": ranked.drop(columns=["Full coverage"]),
    }
