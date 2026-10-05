"""Integration tests for all-account local-winner training, evaluation, and conformal."""

from __future__ import annotations

import pickle
import sys
import tempfile
import unittest
from calendar import month_abbr
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ENGINE_DIR.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from conformal import (  # noqa: E402
    InsufficientCalibrationError,
    STATUS_UNAVAILABLE,
    calibrate_conformal_prediction,
    conformal_quantile,
)
from evaluation import (  # noqa: E402
    ALGORITHMS,
    assign_metric_wins,
    calculate_metrics,
    evaluate_rolling,
    evaluate_selected_accounts,
    evaluate_seven_models,
    observed_mask_from_kinds,
    select_account_winners,
)
from model_training import (  # noqa: E402
    ARTIFACT_SCHEMA_VERSION,
    CANDIDATE_ARTIFACT_NAME,
    DEFAULT_OUTPUT_DIR,
    DEPLOYED_ARTIFACT_NAME,
    FittedBudgetCodeModel,
    PER_BUDGET_CODE_ALGORITHM,
    PER_BUDGET_CODE_FORECAST_TYPE,
    _record_get,
    _record_mapping,
    _record_text,
    future_month_index,
    parse_args,
    resolve_candidate_pickle_path,
    verify_all_budget_code_models_pickle,
    verify_international_settlement_pickle,
)
from preprocessing import (  # noqa: E402
    KIND_MISSING,
    KIND_NUMBER,
    OUTCOME_READY,
    OUTCOME_ZERO_POLICY,
    STATUS_FIT_FAILED,
    STATUS_NO_VALID_WINNER,
    STATUS_ZERO_POLICY,
    calendar_split_sizes,
    prepare_series_window,
    preprocess_account_panel,
)


def _labels(count: int, start: str = "2023-01-01") -> list[str]:
    return [f"{month_abbr[ts.month]}-{ts.year}" for ts in pd.date_range(start, periods=count, freq="MS")]


def _panel(accounts, n_months: int, default: float = 5.0):
    labels = _labels(n_months)
    dates = list(pd.date_range("2023-01-01", periods=n_months, freq="MS"))
    codes = [row["code"] for row in accounts]
    raw = pd.DataFrame(default, index=codes, columns=labels, dtype=float)
    kinds = pd.DataFrame(KIND_NUMBER, index=codes, columns=labels, dtype=object)
    meta_rows = []
    for row in accounts:
        code = row["code"]
        for label, value in (row.get("cells") or {}).items():
            if value is None:
                raw.loc[code, label] = np.nan
                kinds.loc[code, label] = KIND_MISSING
            else:
                raw.loc[code, label] = float(value)
                kinds.loc[code, label] = KIND_NUMBER
        meta_rows.append({
            "account_code": code,
            "account_name": row.get("name", code),
            "category": row.get("category", "Int'l Settlement"),
            "history_start": pd.Timestamp(row.get("history_start", "2023-01-01")),
        })
    metadata = pd.DataFrame(meta_rows).set_index("account_code")
    return raw, kinds, dates, metadata


class SplitAndMetricTests(unittest.TestCase):
    def test_calendar_splits_24_6_25_7_33_9(self):
        self.assertEqual(calendar_split_sizes(30), (24, 6))
        self.assertEqual(calendar_split_sizes(32), (25, 7))
        self.assertEqual(calendar_split_sizes(42), (33, 9))

    def test_undefined_metrics_are_nan_not_zero_wins(self):
        actual = np.array([0.0, 0.0, np.nan])
        predicted = np.array([1.0, 2.0, 3.0])
        metrics = calculate_metrics(actual, predicted, train_values=np.ones(24))
        self.assertTrue(np.isnan(metrics["WAPE"]))
        self.assertTrue(np.isnan(metrics["MAPE"]))
        self.assertEqual(metrics["EVALUATION_STATUS"], "EVALUABLE")
        table = pd.DataFrame([
            {"Budget Code": "A1", "Algorithm": "ARIMA", "Status": "SUCCESS",
             "MAE": metrics["MAE"], "RMSE": metrics["RMSE"], "MAPE": metrics["MAPE"],
             "WAPE": metrics["WAPE"], "MASE": np.nan, "R2": np.nan},
            {"Budget Code": "A1", "Algorithm": "ETS", "Status": "SUCCESS",
             "MAE": 0.1, "RMSE": 0.1, "MAPE": 1.0, "WAPE": 1.0, "MASE": 0.2, "R2": 0.5},
        ])
        scored, _ = assign_metric_wins(table)
        arima_wins = int(scored.loc[scored["Algorithm"] == "ARIMA", "Metric_Wins"].iloc[0])
        self.assertEqual(arima_wins, 0)

    def test_no_observed_targets_are_not_evaluable(self):
        metrics = calculate_metrics([np.nan, np.nan], [1.0, 2.0], train_values=np.arange(24, dtype=float))
        self.assertEqual(metrics["EVALUATION_STATUS"], "NOT_EVALUABLE")
        self.assertTrue(np.isnan(metrics["MAE"]))

    def test_prepared_fit_does_not_replace_raw_targets(self):
        raw = np.array([1.0, np.nan, 3.0, 4.0], dtype=float)
        kinds = np.array([KIND_NUMBER, KIND_MISSING, KIND_NUMBER, KIND_NUMBER], dtype=object)
        prepared, _methods, outcome, _reason = prepare_series_window(raw, kinds)
        self.assertEqual(outcome, OUTCOME_READY)
        self.assertAlmostEqual(prepared[1], 8.0 / 3.0)
        self.assertTrue(np.isnan(raw[1]))
        mask = observed_mask_from_kinds(raw, kinds)
        metrics = calculate_metrics(raw, prepared, observed_mask=mask)
        self.assertEqual(metrics["observed_target_count"], 3)

    def test_all_supported_candidates_are_evaluated_per_ready_code(self):
        self.assertEqual(
            list(ALGORITHMS),
            ["ARIMA", "SARIMA", "ARIMAX", "SARIMAX", "ETS", "XGBoost", "Prophet"],
        )

    def test_evaluate_seven_models_uses_train_mean_and_keeps_raw_test(self):
        months = _labels(16)
        values = np.array([10.0] * 11 + [np.nan] + [999.0, 50.0, np.nan, 50.0], dtype=float)
        kinds = np.array([KIND_NUMBER] * 11 + [KIND_MISSING] + [KIND_NUMBER, KIND_NUMBER, KIND_MISSING, KIND_NUMBER], dtype=object)
        captured = []

        def fake_grid(model_name, candidates, split, *args, **kwargs):
            captured.append(split)
            return {
                "model_name": model_name,
                "status": "failed",
                "error": "skip",
                "mae": None,
                "rmse": None,
                "mape": None,
                "wape": None,
                "mase": None,
                "r2": None,
                "parameters": {},
                "raw_predictions": None,
                "rolling_wape_mean": None,
                "rolling_wape_std": None,
                "rolling_successful_folds": 0,
                "captures_seasonality": False,
            }

        with patch("evaluation._evaluate_candidate_grid", side_effect=fake_grid):
            _results, split, _diag = evaluate_seven_models(
                months,
                values,
                quiet=True,
                kinds=kinds,
                algorithms=["ARIMA"],
                split_index=12,
            )
        self.assertAlmostEqual(split["train_imputation_mean"], 10.0)
        self.assertAlmostEqual(split["train_values_prepared"][-1], 10.0)
        self.assertEqual(split["test_values"][0], 999.0)
        self.assertTrue(np.isnan(split["test_values"][2]))
        self.assertAlmostEqual(split["test_values_prepared"][2], 10.0)
        self.assertEqual(len(captured), 1)


class WinnerAndStatusTests(unittest.TestCase):
    def test_different_accounts_keep_different_local_winners(self):
        table = pd.DataFrame([
            {"Budget Code": "A1", "Algorithm": "ARIMA", "Status": "SUCCESS",
             "MAE": 1, "RMSE": 1, "MAPE": 1, "WAPE": 1, "MASE": 0.4, "R2": 0.9},
            {"Budget Code": "A1", "Algorithm": "ETS", "Status": "SUCCESS",
             "MAE": 3, "RMSE": 3, "MAPE": 3, "WAPE": 3, "MASE": 0.8, "R2": 0.2},
            {"Budget Code": "A2", "Algorithm": "ARIMA", "Status": "SUCCESS",
             "MAE": 3, "RMSE": 3, "MAPE": 3, "WAPE": 3, "MASE": 0.8, "R2": 0.2},
            {"Budget Code": "A2", "Algorithm": "ETS", "Status": "SUCCESS",
             "MAE": 1, "RMSE": 1, "MAPE": 1, "WAPE": 1, "MASE": 0.4, "R2": 0.9},
        ])
        scored, _ = assign_metric_wins(table)
        winners = select_account_winners(scored)
        by_code = dict(zip(winners["Budget Code"], winners["Winning Algorithm"]))
        self.assertEqual(by_code["A1"], "ARIMA")
        self.assertEqual(by_code["A2"], "ETS")

    def test_zero_policy_does_not_create_algorithm_wins(self):
        codes = ["Z1", "R1"]
        months = _labels(18)
        matrix = pd.DataFrame(0.0, index=codes, columns=months)
        matrix.loc["R1"] = 4.0
        kinds = pd.DataFrame(KIND_NUMBER, index=codes, columns=months, dtype=object)
        outcomes = pd.DataFrame([
            {
                "account_code": "Z1",
                "training_outcome": OUTCOME_ZERO_POLICY,
                "training_reason": "ALL_ZERO",
                "ready_for_complete_data_model": False,
                "zero_policy": True,
                "train_months": months[:14],
                "test_months": months[14:],
                "split_index": 14,
                "train_size": 14,
                "test_size": 4,
                "observed_test_count": 4,
            },
            {
                "account_code": "R1",
                "training_outcome": OUTCOME_READY,
                "training_reason": OUTCOME_READY,
                "ready_for_complete_data_model": True,
                "zero_policy": False,
                "train_months": months[:14],
                "test_months": months[14:],
                "split_index": 14,
                "train_size": 14,
                "test_size": 4,
                "observed_test_count": 4,
            },
        ])

        def fake_evaluate(history_months, series, **kwargs):
            self.assertEqual(kwargs.get("account_code"), "R1")
            results = []
            for name in kwargs.get("algorithms") or ALGORITHMS:
                results.append({
                    "model_name": name,
                    "status": "success" if name == "ARIMA" else "failed",
                    "mae": 1.0,
                    "rmse": 1.0,
                    "mape": 1.0,
                    "wape": 1.0,
                    "mase": 1.0,
                    "r2": 0.5,
                    "parameters": {"order": (1, 0, 0)},
                    "raw_predictions": [1.0] * 4,
                    "error": None if name == "ARIMA" else "failed",
                })
            split = {
                "test_values": [4.0] * 4,
                "test_months": history_months[-4:],
                "train_values": [4.0] * 14,
                "train_count": 14,
                "test_count": 4,
            }
            return results, split, {}

        with patch("evaluation.evaluate_seven_models", side_effect=fake_evaluate):
            payload = evaluate_selected_accounts(
                codes,
                matrix,
                months,
                algorithms=ALGORITHMS,
                account_outcomes=outcomes,
                kind_matrix=kinds,
            )
        table = payload["evaluation_table"]
        self.assertEqual(int((table["Budget Code"] == "Z1").sum()), 1)
        self.assertEqual(table.loc[table["Budget Code"] == "Z1", "Status"].iloc[0], STATUS_ZERO_POLICY)
        self.assertEqual(int((table["Budget Code"] == "R1").sum()), 7)
        self.assertEqual(
            set(table.loc[table["Budget Code"] == "R1", "Algorithm"]),
            set(ALGORITHMS),
        )
        winners = select_account_winners(table)
        by_code = winners.set_index("Budget Code")
        self.assertEqual(by_code.loc["Z1", "Status"], STATUS_ZERO_POLICY)
        self.assertTrue(pd.isna(by_code.loc["Z1", "Winning Algorithm"]) or by_code.loc["Z1", "Winning Algorithm"] in {None, "ZERO_POLICY"})
        self.assertEqual(by_code.loc["R1", "Winning Algorithm"], "ARIMA")

    def test_unresolved_account_is_retained_without_a_winner(self):
        table = pd.DataFrame([
            {"Budget Code": "U1", "Algorithm": "UNRESOLVED_MISSING", "Status": "UNRESOLVED_MISSING",
             "MAE": np.nan, "RMSE": np.nan, "MAPE": np.nan, "WAPE": np.nan, "MASE": np.nan, "R2": np.nan,
             "Error": "UNRESOLVED_MISSING_GAPS", "Metric_Wins": 0, "Won_Metrics": ""},
            {"Budget Code": "R1", "Algorithm": "ETS", "Status": "SUCCESS",
             "MAE": 1, "RMSE": 1, "MAPE": 1, "WAPE": 1, "MASE": 0.4, "R2": 0.8, "Metric_Wins": 6, "Won_Metrics": "MAE"},
        ])
        winners = select_account_winners(table)
        by_code = winners.set_index("Budget Code")
        self.assertEqual(by_code.loc["U1", "Status"], "UNRESOLVED_MISSING")
        self.assertEqual(by_code.loc["R1", "Winning Algorithm"], "ETS")


class _DummyInnerForecast:
    def forecast(self, steps=1, **_kwargs):
        return np.full(int(steps), 4.5, dtype=float)


class WinnerMetricsAccessTests(unittest.TestCase):
    def test_series_truth_value_is_not_used_to_read_status(self):
        row = pd.Series({"Status": "SUCCESS", "Error": "none", "WAPE": 1.25, "Metric Wins": 3})
        with self.assertRaises(ValueError):
            _ = row or {}
        mapping = _record_mapping(row)
        self.assertEqual(mapping["Status"], "SUCCESS")
        self.assertEqual(_record_text(row, "Status"), "SUCCESS")
        self.assertEqual(_record_get(row, "WAPE"), 1.25)

    def test_dictionary_winner_metrics_are_read(self):
        row = {"Status": "ZERO_POLICY", "Error": "ALL_ZERO"}
        self.assertEqual(_record_text(row, "Status"), "ZERO_POLICY")
        self.assertEqual(_record_get(row, "Error"), "ALL_ZERO")

    def test_missing_winner_metrics_return_defaults(self):
        self.assertEqual(_record_mapping(None), {})
        self.assertEqual(_record_text(None, "Status"), "")
        self.assertIsNone(_record_get(None, "Status"))
        self.assertEqual(_record_text({}, "Status"), "")
        self.assertEqual(_record_get({}, "MAE", default=None), None)

    def test_missing_status_key_is_empty_string(self):
        row = pd.Series({"WAPE": 2.0, "Error": "failed"})
        self.assertEqual(_record_text(row, "Status"), "")
        self.assertEqual(_record_text({"Error": "failed"}, "Status"), "")

    def test_nan_status_is_treated_as_missing(self):
        row = pd.Series({"Status": np.nan, "Error": np.nan, "MAE": np.nan})
        self.assertEqual(_record_text(row, "Status"), "")
        self.assertEqual(_record_text(row, "Error"), "")
        self.assertIsNone(_record_get(row, "MAE"))
        self.assertEqual(_record_text({"Status": pd.NA}, "Status"), "")


class ArtifactAndCliTests(unittest.TestCase):
    def test_production_path_does_not_call_overall_selection(self):
        source = Path(ENGINE_DIR / "model_training.py").read_text(encoding="utf-8")
        self.assertNotIn("select_overall_algorithm(", source)
        self.assertIn("select_account_winners(", source)
        self.assertIn(CANDIDATE_ARTIFACT_NAME, source)
        self.assertIn("Refusing to overwrite the deployed production artifact", source)

    def test_cli_defaults_to_candidate_artifact(self):
        args = parse_args(["--output-dir", "output"])
        self.assertEqual(args.output_file, CANDIDATE_ARTIFACT_NAME)
        self.assertEqual(CANDIDATE_ARTIFACT_NAME, "all_budget_code_models.pkl")
        self.assertIsNone(args.category)

    def test_forecast_horizon_accepts_december_2030_and_rejects_later(self):
        months = future_month_index("2026-06-01", forecast_end="2030-12-01")
        self.assertEqual(months[0], pd.Timestamp("2026-07-01"))
        self.assertEqual(months[-1], pd.Timestamp("2030-12-01"))
        with self.assertRaises(ValueError):
            future_month_index("2026-06-01", forecast_end="2031-01-01")

    def test_zero_policy_is_data_driven_not_hardcoded(self):
        source = Path(ENGINE_DIR / "model_training.py").read_text(encoding="utf-8")
        self.assertNotIn("511101", source)
        self.assertIn("OUTCOME_ZERO_POLICY", source)
        self.assertIn("STATUS_NO_DATA", source)

    def test_final_training_retrains_on_full_history_not_eval_window(self):
        source = Path(ENGINE_DIR / "model_training.py").read_text(encoding="utf-8")
        self.assertIn("retrained_on_full_history", source)
        self.assertIn("Retraining on full", source)
        self.assertIn("[Final Training", source)
        self.assertIn("Saved fitted model.", source)
        self.assertIn("select_account_winners(", source)
        self.assertNotIn("select_overall_algorithm(", source)

    def test_deployed_artifact_overwrite_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AssertionError) as caught:
                resolve_candidate_pickle_path(Path(tmp), DEPLOYED_ARTIFACT_NAME)
            self.assertIn("Refusing to overwrite", str(caught.exception))

    def test_deployed_international_settlement_pickle_is_not_modified(self):
        import hashlib

        deployed = (DEFAULT_OUTPUT_DIR / DEPLOYED_ARTIFACT_NAME).resolve()
        before = hashlib.sha256(deployed.read_bytes()).hexdigest() if deployed.exists() else None
        with self.assertRaises(AssertionError):
            resolve_candidate_pickle_path(DEFAULT_OUTPUT_DIR, DEPLOYED_ARTIFACT_NAME)
        candidate = resolve_candidate_pickle_path(DEFAULT_OUTPUT_DIR, CANDIDATE_ARTIFACT_NAME)
        self.assertEqual(candidate.name, CANDIDATE_ARTIFACT_NAME)
        self.assertNotEqual(candidate, deployed)
        after = hashlib.sha256(deployed.read_bytes()).hexdigest() if deployed.exists() else None
        self.assertEqual(before, after)

    def test_new_schema_accepts_dynamic_counts_and_explicit_statuses(self):
        codes = [f"C{index:02d}" for index in range(1, 15)]
        records = {}
        models = {}
        for index, code in enumerate(codes):
            if index == 0:
                records[code] = {
                    "production_status": STATUS_ZERO_POLICY,
                    "status_reason": "ALL_ZERO",
                    "algorithm": None,
                    "model": None,
                    "history_months": ["Jan-2023"],
                    "train_months": ["Jan-2023"],
                    "test_months": [],
                    "category": "ALL",
                }
            else:
                records[code] = {
                    "production_status": "FITTED_MODEL",
                    "algorithm": "ARIMA" if index % 2 else "ETS",
                    "model": object(),
                    "trained_model": object(),
                    "training_metadata": {"retrained_on_full_history": True},
                    "history_months": ["Jan-2023"],
                    "train_months": ["Jan-2023"],
                    "test_months": [],
                    "category": "ALL",
                }
                models[code] = records[code]
        bundle = {
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            "forecast_type": PER_BUDGET_CODE_FORECAST_TYPE,
            "category": "ALL",
            "overall_best_algorithm": PER_BUDGET_CODE_ALGORITHM,
            "historical_start": "Jan-2023",
            "historical_end": "Jun-2026",
            "detected_month_count": 1,
            "detected_month_columns": ["Jan 23 Actuals"],
            "eligible_accounts": [],
            "selected_accounts": codes,
            "evaluation_metrics": ["MAE", "WAPE", "MASE"],
            "metric_winners": [],
            "account_winners": [],
            "evaluation_results": [],
            "evaluation_results_by_code": {},
            "forecast_start": "Jul-2026",
            "forecast_end": "Dec-2027",
            "monthly_account_forecasts": [],
            "monthly_combined_forecast": [],
            "yearly_account_forecasts": [],
            "yearly_combined_forecast": [],
            "year_status": [],
            "models": models,
            "account_records": records,
            "history_months": ["Jan-2023"],
            "forecast_coverage": {
                "requested_account_count": 14,
                "included_in_totals": codes,
                "unavailable_accounts": [],
                "zero_policy_accounts": [codes[0]],
            },
            "conformal_prediction": {
                "method": "rolling_origin_absolute_residual",
                "combined": {"status": STATUS_UNAVAILABLE, "quantile_by_horizon": {}},
                "accounts": {},
            },
        }
        self.assertEqual(verify_international_settlement_pickle(bundle, codes), [])
        self.assertEqual(len(codes), 14)
        self.assertNotIn(codes[0], bundle["models"])
        stale = dict(bundle)
        stale["feature_selection_results"] = {"lasso_ranking": []}
        failures = verify_international_settlement_pickle(stale, codes)
        self.assertTrue(any("feature_selection_results" in item for item in failures))

    def test_fitted_wrapper_roundtrip_preserves_point_forecast(self):
        wrapper = FittedBudgetCodeModel("ETS", _DummyInnerForecast(), {}, ["Jan-2023", "Feb-2023"])
        self.assertEqual(list(wrapper.forecast(steps=2)), [4.5, 4.5])
        reloaded = pickle.loads(pickle.dumps(wrapper, protocol=pickle.HIGHEST_PROTOCOL))
        self.assertEqual(list(reloaded.forecast(steps=2)), [4.5, 4.5])
        self.assertEqual(verify_all_budget_code_models_pickle({"A01": reloaded}), [])

    def test_legacy_schema_still_expects_twelve_fitted_models(self):
        codes = [f"B{index:02d}" for index in range(1, 13)]
        models = {
            code: {
                "algorithm": "ARIMA",
                "model": object(),
                "trained_model": object(),
                "training_metadata": {"retrained_on_full_history": True},
            }
            for code in codes
        }
        bundle = {
            "forecast_type": PER_BUDGET_CODE_FORECAST_TYPE,
            "category": "Int'l Settlement",
            "overall_best_algorithm": PER_BUDGET_CODE_ALGORITHM,
            "historical_start": "Jan-2023",
            "historical_end": "Jun-2026",
            "detected_month_count": 1,
            "detected_month_columns": ["Jan 23 Actuals"],
            "eligible_accounts": [],
            "selected_accounts": codes,
            "evaluation_metrics": ["MAE", "WAPE", "MASE"],
            "metric_winners": [],
            "account_winners": [],
            "evaluation_results": [],
            "evaluation_results_by_code": {},
            "forecast_start": "Jul-2026",
            "forecast_end": "Dec-2027",
            "monthly_account_forecasts": [],
            "monthly_combined_forecast": [],
            "yearly_account_forecasts": [],
            "yearly_combined_forecast": [],
            "year_status": [],
            "models": models,
            "conformal_prediction": {
                "method": "rolling_origin_absolute_residual",
                "combined": {"quantile_by_horizon": {1: 1.0}},
                "accounts": {code: {"quantile_by_horizon": {1: 1.0}} for code in codes},
            },
        }
        self.assertEqual(verify_international_settlement_pickle(bundle, codes), [])
        incomplete = dict(bundle)
        incomplete["models"] = {codes[0]: models[codes[0]]}
        failures = verify_international_settlement_pickle(incomplete, codes)
        self.assertTrue(any("model count" in item for item in failures))


class ConformalIsolationTests(unittest.TestCase):
    def test_each_account_uses_its_own_algorithm_and_calendar(self):
        histories = {
            "A01": {"values": list(range(1, 17)), "months": _labels(16), "kinds": [KIND_NUMBER] * 16, "raw_targets": list(range(1, 17))},
            "A02": {"values": list(range(3, 21)), "months": _labels(18, "2023-03-01"), "kinds": [KIND_NUMBER] * 18, "raw_targets": list(range(3, 21))},
        }
        seen = []

        def fit_model(spec, history, origin_months):
            seen.append((spec["model_name"], len(origin_months)))
            return ("fitted", spec["parameters"], {"history": list(history)}, [0.0])

        def forecast_fn(algorithm, fitted, state, origin_months, steps):
            self.assertIn(algorithm, {"SARIMA", "ETS"})
            last = float(state["history"][-1])
            return [last for _ in range(steps)]

        payload = calibrate_conformal_prediction(
            ["A01", "A02"],
            histories,
            _labels(16),
            {"A01": "SARIMA", "A02": "ETS"},
            {"A01": {"order": (1, 0, 0)}, "A02": {"error": "add"}},
            min_train_months=12,
            max_horizon=2,
            alpha=0.10,
            fit_model=fit_model,
            forecast_fn=forecast_fn,
        )
        self.assertEqual(payload["selected_algorithms"]["A01"], "SARIMA")
        self.assertEqual(payload["selected_algorithms"]["A02"], "ETS")
        self.assertNotIn("overall_best_algorithm", payload)
        self.assertGreater(len({item[1] for item in seen}), 1)

    def test_one_account_failure_does_not_stop_the_other(self):
        histories = {
            "OK": [float(index) for index in range(1, 17)],
            "BAD": [float(index) for index in range(1, 17)],
        }
        months = _labels(16)

        def fit_model(spec, history, origin_months):
            if spec["model_name"] == "ETS":
                raise RuntimeError("boom")
            return ("fitted", spec["parameters"], {"history": list(history)}, [0.0])

        def forecast_fn(algorithm, fitted, state, origin_months, steps):
            return [float(state["history"][-1]) for _ in range(steps)]

        payload = calibrate_conformal_prediction(
            ["OK", "BAD"],
            histories,
            months,
            {"OK": "ARIMA", "BAD": "ETS"},
            {"OK": {"order": (1, 0, 0)}, "BAD": {"error": "add"}},
            min_train_months=12,
            max_horizon=1,
            alpha=0.10,
            fit_model=fit_model,
            forecast_fn=forecast_fn,
        )
        self.assertTrue(payload["accounts"]["OK"]["quantile_by_horizon"] or payload["accounts"]["OK"]["n_scores_by_horizon"])
        self.assertEqual(payload["combined"]["status"], STATUS_UNAVAILABLE)
        self.assertTrue(any(row["account_code"] == "BAD" for row in payload["failures"]))

    def test_missing_target_is_skipped_without_discarding_other_scores(self):
        values = [float(index) for index in range(1, 17)]
        raw = list(values)
        raw[-1] = np.nan
        kinds = [KIND_NUMBER] * 15 + [KIND_MISSING]
        payload = calibrate_conformal_prediction(
            ["A1"],
            {"A1": {"values": values, "raw_targets": raw, "months": _labels(16), "kinds": kinds}},
            _labels(16),
            {"A1": "ARIMA"},
            {"A1": {"order": (0, 0, 0)}},
            min_train_months=12,
            max_horizon=2,
            alpha=0.10,
            fit_model=lambda spec, history, months: ("fitted", spec["parameters"], {"history": list(history)}, [0.0]),
            forecast_fn=lambda algorithm, fitted, state, months, steps: [float(state["history"][-1])] * steps,
        )
        self.assertGreater(payload["accounts"]["A1"]["n_scores_by_horizon"].get(1, 0), 0)

    def test_combined_requires_same_origin_and_target_dates(self):
        short = {"values": list(range(1, 15)), "months": _labels(14), "kinds": [KIND_NUMBER] * 14, "raw_targets": list(range(1, 15))}
        long = {"values": list(range(1, 17)), "months": _labels(16), "kinds": [KIND_NUMBER] * 16, "raw_targets": list(range(1, 17))}

        def fit_model(spec, history, origin_months):
            return ("fitted", spec["parameters"], {"history": list(history)}, [0.0])

        def forecast_fn(algorithm, fitted, state, origin_months, steps):
            return [float(state["history"][-1]) for _ in range(steps)]

        payload = calibrate_conformal_prediction(
            ["S", "L"],
            {"S": short, "L": long},
            _labels(16),
            {"S": "ARIMA", "L": "ETS"},
            {"S": {}, "L": {}},
            min_train_months=12,
            max_horizon=1,
            alpha=0.10,
            fit_model=fit_model,
            forecast_fn=forecast_fn,
            intended_codes=["S", "L"],
        )
        self.assertEqual(payload["combined"]["status"], STATUS_UNAVAILABLE)

    def test_insufficient_calibration_is_explicit(self):
        with self.assertRaises(InsufficientCalibrationError):
            conformal_quantile([1.0], alpha=0.10)


class ForecastCategoryTests(unittest.TestCase):
    def test_category_filter_does_not_silently_use_all_accounts(self):
        from app.services.forecasting_service import generate_monthly_forecast

        class _Model:
            def get_forecast(self, steps=1, **kwargs):
                class _Result:
                    predicted_mean = np.full(int(steps), 2.0)
                return _Result()

        codes = ["IS1", "IS2", "OT1"]
        models = {
            "IS1": {"algorithm": "ARIMA", "model": _Model(), "production_status": "FITTED_MODEL", "category": "Int'l Settlement"},
            "IS2": {"algorithm": "ETS", "model": _Model(), "production_status": "FITTED_MODEL", "category": "Int'l Settlement"},
            "OT1": {"algorithm": "ARIMA", "model": _Model(), "production_status": "FITTED_MODEL", "category": "Domestic"},
        }
        bundle = {
            "forecast_type": PER_BUDGET_CODE_FORECAST_TYPE,
            "category": "ALL",
            "overall_best_algorithm": PER_BUDGET_CODE_ALGORITHM,
            "historical_end": "Jun-2026",
            "historical_start": "Jan-2023",
            "selected_accounts": codes,
            "models": models,
            "account_records": models,
            "account_models": {
                code: {"account_name": code, "historical_values": [1.0] * 6, "history_months": _labels(6), "required_forecast_state": {}}
                for code in codes
            },
            "history_months": _labels(6),
            "clip_negative_applied": False,
        }
        payload = generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-07", category="Int'l Settlement")
        self.assertEqual(set(payload["selected_accounts"]), {"IS1", "IS2"})
        self.assertNotIn("OT1", payload["selected_accounts"])
        self.assertEqual(payload["monthly_forecasts"][0]["forecast_amount"], 4.0)


class RollingFoldTests(unittest.TestCase):
    def test_rolling_folds_stay_inside_training_window(self):
        months = _labels(20)
        values = np.arange(20, dtype=float) + 3
        captured = []

        def fake_fit(model_name, parameters, train_values, train_months, steps, future_months):
            captured.append((list(train_months), list(future_months)))
            return np.full(steps, float(train_values[-1])), None, {}, {}

        with patch("evaluation._fit_forecast", side_effect=fake_fit):
            info = evaluate_rolling("ARIMA", {"order": (0, 0, 0)}, months[:16], values[:16], horizon=4)
        self.assertTrue(info["rolling_folds"])
        train_index = {month: index for index, month in enumerate(months)}
        for train_months, val_months in captured:
            self.assertLessEqual(train_index[train_months[-1]], 15)
            self.assertTrue(all(train_index[month] < 16 for month in val_months))

    def test_scaler_fits_on_training_window_only(self):
        from evaluation import _fit_forecast

        months = _labels(16)
        train = np.linspace(10, 20, 16)
        future = _labels(4, "2024-05-01")
        predicted, _fitted, state, _resid = _fit_forecast(
            "ARIMAX",
            {"order": (0, 0, 0), "seasonal_order": (0, 0, 0, 0)},
            train,
            months,
            4,
            future,
        )
        self.assertEqual(int(state["scaler"].n_samples_seen_), 16)
        self.assertEqual(len(predicted), 4)


if __name__ == "__main__":
    unittest.main()
