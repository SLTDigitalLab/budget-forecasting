from __future__ import annotations

import math

import numpy as np

from preprocessing import (
    DEFAULT_MAX_INTERNAL_GAP,
    KIND_NUMBER,
    OUTCOME_READY,
    prepare_series_window,
)

CONFORMAL_METHOD = "rolling_origin_absolute_residual"
CONFORMAL_ALPHA = 0.10
CONFORMAL_COVERAGE = 1.0 - CONFORMAL_ALPHA
STATUS_CALIBRATED = "CALIBRATED"
STATUS_INSUFFICIENT_CALIBRATION = "INSUFFICIENT_CALIBRATION"
STATUS_ACCOUNT_FAILED = "ACCOUNT_FAILED"
STATUS_UNAVAILABLE = "UNAVAILABLE"
COVERAGE_LIMITATION = (
    "Nominal coverage is not guaranteed for dependent time-series residuals. "
    "Selection-holdout and calibration windows may overlap."
)


class InsufficientCalibrationError(ValueError):
    """Raised when finite-sample conformal rank is unsupported."""


def _validate_alpha(alpha: float) -> float:
    value = float(alpha)
    if not (0.0 < value < 1.0):
        raise ValueError("Conformal alpha must satisfy 0 < alpha < 1.")
    return value


def nonconformity_score(actual, prediction) -> float:
    score = abs(float(actual) - float(prediction))
    if not np.isfinite(score) or score < 0:
        raise ValueError("Nonconformity score must be finite and nonnegative.")
    return float(score)


def finite_sample_quantile_rank(n: int, alpha: float) -> int:
    """Return the 1-based finite-sample conformal rank without clamping."""
    _validate_alpha(alpha)
    count = int(n)
    if count < 1:
        raise InsufficientCalibrationError("Conformal quantile requires at least one finite score.")
    rank = int(math.ceil((count + 1) * (1.0 - float(alpha))))
    if rank < 1 or rank > count:
        raise InsufficientCalibrationError(
            f"Finite-sample quantile rank {rank} is outside 1..{count}; "
            "supported coverage is not claimed."
        )
    return rank


def conformal_quantile(scores, alpha: float = CONFORMAL_ALPHA) -> float:
    values = sorted(float(item) for item in scores if np.isfinite(item) and float(item) >= 0)
    if not values:
        raise InsufficientCalibrationError("Conformal quantile requires at least one finite score.")
    rank = finite_sample_quantile_rank(len(values), alpha)
    return values[rank - 1]


def quantile_for_horizon(quantile_by_horizon: dict | None, horizon: int):
    if not quantile_by_horizon or int(horizon) < 1:
        return None
    keyed = {}
    for key, value in dict(quantile_by_horizon).items():
        amount = float(value)
        if np.isfinite(amount) and amount >= 0:
            keyed[int(key)] = amount
    return keyed.get(int(horizon))


def _quantiles_from_scores(scores_by_horizon: dict[int, list[float]], alpha: float) -> tuple[dict, dict, dict]:
    n_scores = {}
    quantiles = {}
    statuses = {}
    for horizon, scores in sorted(scores_by_horizon.items()):
        finite = [float(item) for item in scores if np.isfinite(item) and float(item) >= 0]
        n_scores[int(horizon)] = len(finite)
        try:
            quantiles[int(horizon)] = conformal_quantile(finite, alpha=alpha)
            statuses[int(horizon)] = STATUS_CALIBRATED
        except InsufficientCalibrationError as error:
            statuses[int(horizon)] = STATUS_INSUFFICIENT_CALIBRATION
            quantiles[int(horizon)] = None
            statuses[f"{int(horizon)}_reason"] = str(error)
    return n_scores, quantiles, statuses


def _algorithms_by_code(selected_codes, algorithm_by_code) -> dict[str, str]:
    codes = [str(code) for code in selected_codes]
    if isinstance(algorithm_by_code, str):
        name = str(algorithm_by_code)
        return {code: name for code in codes}
    mapping = dict(algorithm_by_code or {})
    resolved = {}
    for code in codes:
        name = mapping.get(code) or mapping.get(str(code))
        if not name:
            raise AssertionError(f"Conformal calibration is missing the selected algorithm for {code}.")
        resolved[code] = str(name)
    return resolved


def _history_payload(code: str, histories: dict, fallback_months) -> dict:
    payload = histories[code]
    if isinstance(payload, dict):
        values = [float(value) if value is not None and np.isfinite(float(value)) else np.nan for value in payload["values"]]
        months = list(payload.get("months") or fallback_months)
        kinds = list(payload.get("kinds") or [])
        raw_targets = payload.get("raw_targets")
        if raw_targets is None:
            raw_targets = list(values)
        else:
            raw_targets = [
                float(value) if value is not None and np.isfinite(float(value)) else np.nan
                for value in raw_targets
            ]
    else:
        values = [float(value) if value is not None and np.isfinite(float(value)) else np.nan for value in payload]
        months = list(fallback_months)
        kinds = []
        raw_targets = list(values)
    if len(months) != len(values):
        raise AssertionError(f"Conformal history months for {code} do not match the series length.")
    if kinds and len(kinds) != len(values):
        raise AssertionError(f"Conformal observation kinds for {code} do not match the series length.")
    if len(raw_targets) != len(values):
        raise AssertionError(f"Conformal raw targets for {code} do not match the series length.")
    return {
        "values": values,
        "raw_targets": raw_targets,
        "months": months,
        "kinds": kinds,
    }


def _target_observed(raw_value, kind=None) -> bool:
    if kind is not None and str(kind) != KIND_NUMBER:
        return False
    try:
        return np.isfinite(float(raw_value))
    except (TypeError, ValueError):
        return False


def _prepare_origin_window(values, kinds, interpolate: bool, max_internal_gap: int):
    array = np.asarray(values, dtype=float)
    if kinds:
        kind_row = np.asarray(kinds, dtype=object)
    else:
        kind_row = np.full(array.shape, KIND_NUMBER, dtype=object)
        kind_row[~np.isfinite(array)] = "missing"
    prepared, methods, outcome, reason = prepare_series_window(
        array,
        kind_row,
        interpolate=bool(interpolate),
        max_internal_gap=int(max_internal_gap),
    )
    del interpolate, max_internal_gap
    return prepared, methods, outcome, reason


def calibrate_conformal_prediction(
    selected_codes,
    histories: dict,
    history_months,
    algorithm_by_code,
    parameters_by_code: dict,
    *,
    min_train_months: int,
    max_horizon: int,
    alpha: float = CONFORMAL_ALPHA,
    fit_model,
    forecast_fn,
    account_calendars: dict | None = None,
    interpolate_accounts=None,
    max_internal_gap: int = DEFAULT_MAX_INTERNAL_GAP,
    intended_codes=None,
    preprocess_origin=None,
    requested_horizons=None,
    selection_holdout_months: int | None = None,
) -> dict:
    alpha = _validate_alpha(alpha)
    codes = [str(code) for code in selected_codes]
    intended = [str(code) for code in (intended_codes or codes)]
    algorithms = _algorithms_by_code(codes, algorithm_by_code)
    interpolate_set = {str(code) for code in (interpolate_accounts or [])}
    requested = [int(item) for item in (requested_horizons or range(1, int(max_horizon) + 1))]
    requested = [item for item in requested if item >= 1]
    if not requested:
        requested = list(range(1, int(max_horizon) + 1))
    max_requested = max(requested)

    series = {}
    for code in codes:
        payload = _history_payload(code, histories, history_months)
        if account_calendars and code in account_calendars:
            payload["months"] = list(account_calendars[code])
            if len(payload["months"]) != len(payload["values"]):
                raise AssertionError(f"Account calendar length for {code} does not match history.")
        series[code] = payload

    account_scores = {code: {horizon: [] for horizon in requested} for code in codes}
    combined_scores = {horizon: [] for horizon in requested}
    failures = []
    origin_records: dict[tuple[str, str], dict[str, dict]] = {}

    for code in codes:
        months = series[code]["months"]
        values = series[code]["values"]
        raw_targets = series[code]["raw_targets"]
        kinds = series[code]["kinds"]
        n_months = len(months)
        if n_months <= int(min_train_months):
            failures.append({
                "account_code": code,
                "origin": None,
                "horizon": None,
                "reason": "Not enough historical months for conformal calibration.",
            })
            continue
        for origin_len in range(int(min_train_months), n_months):
            remaining = n_months - origin_len
            steps = min(int(max_requested), remaining)
            if steps < 1:
                continue
            origin_months = months[:origin_len]
            origin_date = origin_months[-1]
            origin_values = values[:origin_len]
            origin_kinds = kinds[:origin_len] if kinds else []
            try:
                if preprocess_origin is not None:
                    prepared, _methods, outcome, reason = preprocess_origin(
                        code,
                        origin_values,
                        origin_kinds,
                        origin_months,
                    )
                else:
                    prepared, _methods, outcome, reason = _prepare_origin_window(
                        origin_values,
                        origin_kinds,
                        interpolate=code in interpolate_set,
                        max_internal_gap=max_internal_gap,
                    )
                if outcome != OUTCOME_READY:
                    raise AssertionError(reason or outcome)
                if not np.isfinite(np.asarray(prepared, dtype=float)).all():
                    raise AssertionError("Prepared calibration window is not finite.")
                algorithm = algorithms[code]
                fitted, _parameters, state, _smoke = fit_model(
                    {
                        "model_name": algorithm,
                        "parameters": dict(parameters_by_code.get(code) or {}),
                    },
                    [float(value) for value in prepared],
                    origin_months,
                )
                predicted = forecast_fn(
                    algorithm,
                    fitted,
                    state,
                    origin_months,
                    steps,
                )
                if len(predicted) != steps:
                    raise AssertionError(
                        f"Conformal origin forecast length {len(predicted)} does not match {steps}."
                    )
                if not all(np.isfinite(float(value)) for value in predicted):
                    raise AssertionError("Conformal origin forecast is not finite.")
            except Exception as error:
                failures.append({
                    "account_code": code,
                    "origin": origin_date,
                    "horizon": None,
                    "reason": f"{type(error).__name__}: {error}",
                })
                continue
            for horizon in range(1, steps + 1):
                if horizon not in account_scores[code]:
                    continue
                target_index = origin_len + horizon - 1
                target_date = months[target_index]
                kind = kinds[target_index] if kinds else None
                actual = raw_targets[target_index]
                if not _target_observed(actual, kind):
                    failures.append({
                        "account_code": code,
                        "origin": origin_date,
                        "horizon": horizon,
                        "reason": "Missing or invalid observed calibration target.",
                    })
                    continue
                try:
                    score = nonconformity_score(actual, predicted[horizon - 1])
                except Exception as error:
                    failures.append({
                        "account_code": code,
                        "origin": origin_date,
                        "horizon": horizon,
                        "reason": f"{type(error).__name__}: {error}",
                    })
                    continue
                account_scores[code][horizon].append(score)
                key = (str(origin_date), str(target_date))
                origin_records.setdefault(key, {})[code] = {
                    "horizon": horizon,
                    "actual": float(actual),
                    "prediction": float(predicted[horizon - 1]),
                    "score": score,
                }

    for (_origin_date, _target_date), by_code in origin_records.items():
        if any(code not in by_code for code in intended):
            continue
        horizons = {by_code[code]["horizon"] for code in intended}
        if len(horizons) != 1:
            continue
        horizon = next(iter(horizons))
        if horizon not in combined_scores:
            continue
        actual_total = sum(by_code[code]["actual"] for code in intended)
        predicted_total = sum(by_code[code]["prediction"] for code in intended)
        try:
            combined_scores[horizon].append(nonconformity_score(actual_total, predicted_total))
        except Exception as error:
            failures.append({
                "account_code": "COMBINED",
                "origin": _origin_date,
                "horizon": horizon,
                "reason": f"{type(error).__name__}: {error}",
            })

    accounts = {}
    calibrated_horizons = set()
    for code in codes:
        n_scores, quantiles, statuses = _quantiles_from_scores(account_scores[code], alpha)
        available = {int(horizon): float(value) for horizon, value in quantiles.items() if value is not None}
        account_status = STATUS_CALIBRATED if 1 in available else STATUS_INSUFFICIENT_CALIBRATION
        if any(row["account_code"] == code and row["horizon"] is None for row in failures) and not available:
            account_status = STATUS_ACCOUNT_FAILED
        accounts[code] = {
            "status": account_status,
            "n_scores_by_horizon": n_scores,
            "quantile_by_horizon": available,
            "horizon_status": {
                str(horizon): statuses.get(horizon, STATUS_INSUFFICIENT_CALIBRATION)
                for horizon in requested
            },
            "algorithm": algorithms[code],
            "history_months": list(series[code]["months"]),
            "history_length": int(len(series[code]["months"])),
        }
        calibrated_horizons.update(available.keys())

    combined_n, combined_q, combined_status = _quantiles_from_scores(combined_scores, alpha)
    combined_available = {int(horizon): float(value) for horizon, value in combined_q.items() if value is not None}
    combined_payload_status = STATUS_CALIBRATED if combined_available else STATUS_UNAVAILABLE
    if intended and any(code not in codes for code in intended):
        combined_payload_status = STATUS_UNAVAILABLE

    payload = {
        "method": CONFORMAL_METHOD,
        "alpha": float(alpha),
        "nominal_coverage": float(1.0 - float(alpha)),
        "coverage_limitation": COVERAGE_LIMITATION,
        "selected_algorithms": algorithms,
        "min_train_months": int(min_train_months),
        "requested_horizons": requested,
        "max_requested_horizon": int(max_requested),
        "max_calibrated_horizon": int(max(calibrated_horizons)) if calibrated_horizons else None,
        "selection_holdout_months": None if selection_holdout_months is None else int(selection_holdout_months),
        "selection_calibration_overlap": (
            "Calibration origins may overlap the selection-holdout window; "
            "results are not an untouched final test."
        ),
        "intended_accounts": intended,
        "accounts": accounts,
        "combined": {
            "status": combined_payload_status,
            "n_scores_by_horizon": combined_n,
            "quantile_by_horizon": combined_available,
            "horizon_status": {
                str(horizon): combined_status.get(horizon, STATUS_UNAVAILABLE)
                for horizon in requested
            },
            "requires_complete_intended_coverage": True,
        },
        "failures": failures,
        "insufficient_calibration": combined_payload_status != STATUS_CALIBRATED
        or any(row["status"] != STATUS_CALIBRATED for row in accounts.values()),
    }
    return payload
