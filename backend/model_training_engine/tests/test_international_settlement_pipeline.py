"""Lightweight tests for International Settlement preprocessing and evaluation helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from unittest.mock import patch

from conformal import (  # noqa: E402
    InsufficientCalibrationError,
    calibrate_conformal_prediction,
    conformal_quantile,
    nonconformity_score,
    quantile_for_horizon,
)
from evaluation import (  # noqa: E402
    ALGORITHMS,
    assign_metric_wins,
    calculate_mase,
    chronological_train_test_split,
    evaluate_selected_accounts,
    require_successful_account_winners,
    select_account_winners,
    select_overall_algorithm,
)
from model_training import (  # noqa: E402
    PER_BUDGET_CODE_ALGORITHM,
    PER_BUDGET_CODE_FORECAST_TYPE,
    future_month_index,
    parse_args,
    verify_international_settlement_pickle,
)
import model_training as training_module  # noqa: E402
from preprocessing import (  # noqa: E402
    ACCOUNT_CODE_COLUMN,
    detect_monthly_columns,
    is_excluded_actuals_column,
    month_label,
    parse_month_header,
    validate_contiguous_month_range,
)


class MonthHeaderTests(unittest.TestCase):
    def test_abbreviated_and_full_month_names(self):
        self.assertEqual(parse_month_header("Jan 23 Actuals"), pd.Timestamp("2023-01-01"))
        self.assertEqual(parse_month_header("January 2023 Actuals"), pd.Timestamp("2023-01-01"))
        self.assertEqual(parse_month_header("Sept 23 Actuals"), pd.Timestamp("2023-09-01"))
        self.assertEqual(parse_month_header("September 2023 Actuals"), pd.Timestamp("2023-09-01"))
        self.assertEqual(parse_month_header("March 2026 actuals"), pd.Timestamp("2026-03-01"))

    def test_double_spaces_in_headers(self):
        self.assertEqual(parse_month_header("Feb  25 Actuals"), pd.Timestamp("2025-02-01"))

    def test_month_and_year_without_space(self):
        self.assertEqual(parse_month_header("July25 Actuals"), pd.Timestamp("2025-07-01"))
        self.assertEqual(parse_month_header("April 24 Actuals"), pd.Timestamp("2024-04-01"))
        self.assertEqual(parse_month_header("May 24 Actuals"), pd.Timestamp("2024-05-01"))

    def test_excludes_annual_and_ytd_columns(self):
        for header in (
            "Actuals 2020",
            "Actuals 2023",
            "YTD Dec 23 Actuals",
            "YTD Dec 24 Actuals",
            "YTD June 2026 actuals",
        ):
            self.assertTrue(is_excluded_actuals_column(header), header)
            self.assertTrue(pd.isna(parse_month_header(header)), header)

    def test_dynamic_detection_when_future_month_is_added(self):
        columns = [
            "Jan 23 Actuals",
            "Feb 23 Actuals",
            "Mar 23 Actuals",
            "Apr 23 Actuals",
        ]
        first_columns, first_dates = detect_monthly_columns(columns)
        self.assertEqual(len(first_columns), 4)
        self.assertEqual(month_label(first_dates[-1]), "Apr-2023")

        columns.append("May 23 Actuals")
        next_columns, next_dates = detect_monthly_columns(columns)
        self.assertEqual(len(next_columns), 5)
        self.assertEqual(month_label(next_dates[-1]), "May-2023")
        self.assertEqual(len(next_dates), len(next_columns))

    def test_missing_month_raises(self):
        columns = ["Jan 23 Actuals", "Feb 23 Actuals", "Apr 23 Actuals"]
        with self.assertRaises(ValueError) as error:
            detect_monthly_columns(columns)
        self.assertIn("Mar-2023", str(error.exception))

    def test_duplicate_monthly_date_raises(self):
        columns = ["Jan 23 Actuals", "January 2023 actuals", "Feb 23 Actuals"]
        with self.assertRaises(ValueError) as error:
            detect_monthly_columns(columns)
        self.assertIn("Duplicate", str(error.exception))

    def test_contiguous_validator_lists_missing_months(self):
        with self.assertRaises(ValueError) as error:
            validate_contiguous_month_range(
                [pd.Timestamp("2023-01-01"), pd.Timestamp("2023-03-01")]
            )
        self.assertIn("Feb-2023", str(error.exception))


class EligibilityTests(unittest.TestCase):
    def test_missing_and_zero_values_are_no_longer_rejected_by_eligibility_mask(self):
        import preprocessing

        self.assertFalse(hasattr(preprocessing, "eligibility_mask"))
        self.assertFalse(hasattr(preprocessing, "_select_final_accounts"))


class FeatureSelectionAndSplitTests(unittest.TestCase):
    def test_all_accounts_are_retained_beyond_twelve(self):
        from preprocessing import KIND_NUMBER, preprocess_account_panel

        months = [month_label(ts) for ts in pd.date_range("2023-01-01", periods=18, freq="MS")]
        codes = [f"A{index:02d}" for index in range(1, 16)]
        raw = pd.DataFrame(20.0, index=codes, columns=months)
        kinds = pd.DataFrame(KIND_NUMBER, index=codes, columns=months)
        metadata = pd.DataFrame(
            {"account_name": codes, "category": ["CatA"] * 8 + ["CatB"] * 7},
            index=codes,
        )
        result = preprocess_account_panel(raw, kinds, pd.date_range("2023-01-01", periods=18, freq="MS"), metadata)
        self.assertEqual(len(result["account_codes"]), 15)
        self.assertEqual(result["accounts_df"].shape[0], 15)
        self.assertEqual(len(result["month_columns"]), 18)

    def test_calendar_eighty_twenty_split_is_the_default(self):
        months = [month_label(ts) for ts in pd.date_range("2023-01-01", periods=20, freq="MS")]
        values = np.arange(20, dtype=float) + 3
        split = chronological_train_test_split(months, values, quiet=True)
        self.assertEqual(len(split["train_months"]), 16)
        self.assertEqual(len(split["test_months"]), 4)
        self.assertEqual(split["train_months"][-1], months[15])
        self.assertEqual(split["test_months"][0], months[16])


class MaseAndWinnerTests(unittest.TestCase):
    def test_seasonal_mase_calculation(self):
        train = np.arange(1, 25, dtype=float)
        actual = np.array([25.0, 26.0, 27.0])
        predicted = np.array([24.0, 26.0, 29.0])
        mase, note = calculate_mase(train, actual, predicted, season=12)
        expected = float(np.mean(np.abs(actual - predicted)) / np.mean(np.abs(train[12:] - train[:-12])))
        self.assertIsNone(note)
        self.assertAlmostEqual(mase, expected)

    def test_mase_uses_training_data_only(self):
        train = np.arange(1.0, 25.0)
        actual = np.array([1000.0, 1100.0, 1200.0])
        predicted = np.array([1001.0, 1102.0, 1203.0])
        mase, note = calculate_mase(train, actual, predicted, season=12)
        train_scale = float(np.mean(np.abs(train[12:] - train[:-12])))
        leaked_scale = float(np.mean(np.abs(actual[1:] - actual[:-1])))
        self.assertIsNone(note)
        self.assertAlmostEqual(mase, float(np.mean(np.abs(actual - predicted)) / train_scale))
        self.assertNotAlmostEqual(train_scale, leaked_scale)

    def test_mase_nan_when_train_too_short_or_denominator_invalid(self):
        short, note = calculate_mase(np.arange(12, dtype=float), [1.0], [1.0], season=12)
        self.assertTrue(np.isnan(short))
        self.assertIn("not longer than the seasonal period", note)
        constant = np.ones(24, dtype=float)
        zero, zero_note = calculate_mase(constant, [2.0], [3.0], season=12)
        self.assertTrue(np.isnan(zero))
        self.assertIn("zero or non-finite", zero_note)

    def test_evaluation_table_row_count_follows_requested_accounts_and_algorithms(self):
        codes = [f"A{index:02d}" for index in range(1, 16)]
        months = [month_label(ts) for ts in pd.date_range("2023-01-01", periods=18, freq="MS")]
        matrix = pd.DataFrame(np.ones((15, 18)) * 5, index=codes, columns=months)

        def fake_evaluate(history_months, series, **kwargs):
            results = []
            for name in kwargs.get("algorithms") or ALGORITHMS:
                results.append({
                    "model_name": name,
                    "status": "success",
                    "mae": 1.0,
                    "rmse": 1.0,
                    "mape": 1.0,
                    "wape": 1.0,
                    "mase": 1.0,
                    "r2": 0.5,
                    "parameters": {"demo": True},
                    "raw_predictions": [1.0] * 4,
                    "error": None,
                })
            split = {
                "test_values": [1.0] * 4,
                "test_months": history_months[-4:],
                "train_values": [1.0] * 14,
                "train_count": 14,
                "test_count": 4,
            }
            return results, split, {}

        with patch("evaluation.evaluate_seven_models", side_effect=fake_evaluate):
            payload = evaluate_selected_accounts(codes, matrix, months, algorithms=ALGORITHMS)
        self.assertEqual(len(payload["evaluation_table"]), 15 * 7)
        self.assertEqual(payload["evaluation_table"].groupby("Budget Code").size().min(), 7)
        self.assertGreater(payload["evaluation_table"]["Budget Code"].nunique(), 12)

    def test_account_winner_uses_metric_wins_not_lowest_wape(self):
        table = pd.DataFrame([
            {"Budget Code": "A1", "Algorithm": "ARIMA", "Status": "SUCCESS",
             "MAE": 1, "RMSE": 5, "MAPE": 5, "WAPE": 1, "MASE": 0.4, "R2": 0.1},
            {"Budget Code": "A1", "Algorithm": "XGBoost", "Status": "SUCCESS",
             "MAE": 4, "RMSE": 1, "MAPE": 4, "WAPE": 3, "MASE": 0.9, "R2": 0.9},
            {"Budget Code": "A1", "Algorithm": "ETS", "Status": "SUCCESS",
             "MAE": 3, "RMSE": 2, "MAPE": 1, "WAPE": 2, "MASE": 0.8, "R2": 0.2},
        ])
        scored, _metric_winners = assign_metric_wins(table)
        winners = select_account_winners(scored)
        self.assertEqual(winners.iloc[0]["Winning Algorithm"], "ARIMA")
        self.assertEqual(int(winners.iloc[0]["Metric Wins"]), 3)
        self.assertIn("MAE", winners.iloc[0]["Won Metrics"])
        self.assertIn("MASE", winners.iloc[0]["Won Metrics"])

    def test_account_winner_tie_uses_wape_then_mase_then_rmse(self):
        table = pd.DataFrame([
            {"Budget Code": "A1", "Algorithm": "ARIMA", "Status": "SUCCESS",
             "MAE": 1, "RMSE": 3, "MAPE": 1, "WAPE": 5, "MASE": 0.8, "R2": 0.9},
            {"Budget Code": "A1", "Algorithm": "ETS", "Status": "SUCCESS",
             "MAE": 2, "RMSE": 1, "MAPE": 2, "WAPE": 4, "MASE": 0.7, "R2": 0.2},
        ])
        scored, _metric_winners = assign_metric_wins(table)
        self.assertEqual(set(scored["Metric_Wins"]), {3})
        winners = select_account_winners(scored)
        self.assertEqual(winners.iloc[0]["Winning Algorithm"], "ETS")

    def test_two_budget_codes_can_select_different_algorithms(self):
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
        scored, _metric_winners = assign_metric_wins(table)
        winners = select_account_winners(scored)
        by_code = dict(zip(winners["Budget Code"], winners["Winning Algorithm"]))
        self.assertEqual(by_code["A1"], "ARIMA")
        self.assertEqual(by_code["A2"], "ETS")

    def test_failed_candidate_algorithms_are_excluded(self):
        table = pd.DataFrame([
            {"Budget Code": "A1", "Algorithm": "ARIMA", "Status": "FAILED",
             "MAE": 0.1, "RMSE": 0.1, "MAPE": 0.1, "WAPE": 0.1, "MASE": 0.1, "R2": 0.99,
             "Error": "fit failed"},
            {"Budget Code": "A1", "Algorithm": "ETS", "Status": "SUCCESS",
             "MAE": 2, "RMSE": 2, "MAPE": 2, "WAPE": 2, "MASE": 0.7, "R2": 0.4},
        ])
        scored, _metric_winners = assign_metric_wins(table)
        winners = select_account_winners(scored)
        self.assertEqual(winners.iloc[0]["Winning Algorithm"], "ETS")
        self.assertEqual(int(scored.loc[scored["Algorithm"] == "ARIMA", "Metric_Wins"].iloc[0]), 0)

    def test_all_algorithm_failure_raises_a_clear_error(self):
        table = pd.DataFrame([
            {"Budget Code": "A99", "Algorithm": "ARIMA", "Status": "FAILED",
             "MAE": np.nan, "RMSE": np.nan, "MAPE": np.nan, "WAPE": np.nan, "MASE": np.nan, "R2": np.nan},
            {"Budget Code": "A99", "Algorithm": "ETS", "Status": "FAILED",
             "MAE": np.nan, "RMSE": np.nan, "MAPE": np.nan, "WAPE": np.nan, "MASE": np.nan, "R2": np.nan},
        ])
        winners = select_account_winners(table)
        with self.assertRaises(AssertionError) as caught:
            require_successful_account_winners(winners)
        self.assertIn("A99", str(caught.exception))
        self.assertIn("All forecasting algorithms failed", str(caught.exception))

    def test_overall_winner_uses_account_win_count(self):
        evaluation = []
        winners = []
        for code, algo in [("A1", "ARIMA"), ("A2", "ARIMA"), ("A3", "XGBoost")]:
            for name in ["ARIMA", "XGBoost"]:
                evaluation.append({
                    "Budget Code": code,
                    "Algorithm": name,
                    "Status": "SUCCESS",
                    "MAE": 1.0,
                    "RMSE": 1.0 if name == "ARIMA" else 2.0,
                    "MAPE": 1.0,
                    "WAPE": 2.0 if name == "ARIMA" else 3.0,
                    "MASE": 0.5 if name == "ARIMA" else 0.8,
                    "R2": 0.8,
                })
            winners.append({"Budget Code": code, "Winning Algorithm": algo})
        result = select_overall_algorithm(
            pd.DataFrame(evaluation),
            pd.DataFrame(winners),
            ["A1", "A2", "A3"],
            algorithms=["ARIMA", "XGBoost"],
        )
        self.assertEqual(result["overall_best_algorithm"], "ARIMA")
        self.assertEqual(result["account_wins"]["ARIMA"], 2)

    def test_overall_tie_uses_mean_wape_then_mase_then_rmse(self):
        evaluation = []
        winners = []
        for code, algo in [("A1", "ARIMA"), ("A2", "XGBoost")]:
            evaluation.extend([
                {"Budget Code": code, "Algorithm": "ARIMA", "Status": "SUCCESS",
                 "MAE": 1, "RMSE": 3, "MAPE": 1, "WAPE": 4, "MASE": 0.9, "R2": 0.5},
                {"Budget Code": code, "Algorithm": "XGBoost", "Status": "SUCCESS",
                 "MAE": 1, "RMSE": 2, "MAPE": 1, "WAPE": 3, "MASE": 0.6, "R2": 0.5},
            ])
            winners.append({"Budget Code": code, "Winning Algorithm": algo})
        result = select_overall_algorithm(
            pd.DataFrame(evaluation),
            pd.DataFrame(winners),
            ["A1", "A2"],
            algorithms=["ARIMA", "XGBoost"],
        )
        self.assertEqual(result["overall_best_algorithm"], "XGBoost")


class ForecastAndPickleTests(unittest.TestCase):
    def test_forecast_starts_after_latest_historical_month(self):
        index = future_month_index(pd.Timestamp("2026-06-01"), forecast_months=3)
        self.assertEqual(index[0], pd.Timestamp("2026-07-01"))
        self.assertEqual(len(index), 3)
        longer = future_month_index(pd.Timestamp("2026-07-01"), forecast_months=2)
        self.assertEqual(longer[0], pd.Timestamp("2026-08-01"))

    def test_single_pickle_metadata_validation(self):
        codes = [f"B{index:02d}" for index in range(1, 13)]
        months = ["Jan 23 Actuals"]
        def _model_entry(code, algorithm):
            return {
                "budget_code": code,
                "algorithm": algorithm,
                "model_name": algorithm,
                "model": object(),
                "trained_model": object(),
                "model_specification": {"order": (1, 0, 0)},
                "evaluation_metrics": {"MAE": 1, "RMSE": 1, "MAPE": 1, "WAPE": 1, "R2": 0.5, "MASE": 0.4},
                "metric_win_count": 3,
                "training_metadata": {
                    "training_start": "Jan-2023",
                    "training_end": "Jun-2026",
                    "number_of_observations": 42,
                    "frequency": "MS",
                    "retrained_on_full_history": True,
                },
                "model_metadata": {"required_future_features": {}, "model_parameters": {}},
            }

        bundle = {
            "forecast_type": PER_BUDGET_CODE_FORECAST_TYPE,
            "category": "Int'l Settlement",
            "historical_start": "Jan-2023",
            "historical_end": "Jun-2026",
            "detected_month_count": 1,
            "detected_month_columns": months,
            "eligible_accounts": codes,
            "selected_accounts": codes,
            "selected_budget_codes": codes,
            "overall_best_algorithm": PER_BUDGET_CODE_ALGORITHM,
            "evaluation_metrics": ["MAE", "RMSE", "MAPE", "WAPE", "MASE", "R2"],
            "metric_winners": [],
            "models": {
                code: _model_entry(code, "ARIMA" if index < 6 else "ETS")
                for index, code in enumerate(codes)
            },
            "feature_selection_results": {},
            "evaluation_results": [],
            "evaluation_results_by_code": {},
            "account_winners": [],
            "forecast_start": "Jul-2026",
            "forecast_end": "Dec-2027",
            "monthly_account_forecasts": [],
            "monthly_combined_forecast": [],
            "yearly_account_forecasts": [],
            "yearly_combined_forecast": [],
            "year_status": [],
            "conformal_prediction": {
                "method": "rolling_origin_absolute_residual",
                "alpha": 0.1,
                "nominal_coverage": 0.9,
                "selected_algorithms": {
                    code: "ARIMA" if index < 6 else "ETS"
                    for index, code in enumerate(codes)
                },
                "min_train_months": 12,
                "max_calibrated_horizon": 6,
                "accounts": {
                    code: {"n_scores_by_horizon": {1: 10}, "quantile_by_horizon": {1: 1.5}}
                    for code in codes
                },
                "combined": {"n_scores_by_horizon": {1: 10}, "quantile_by_horizon": {1: 4.0}},
            },
        }
        self.assertEqual(verify_international_settlement_pickle(bundle, codes), [])
        self.assertEqual(len({entry["algorithm"] for entry in bundle["models"].values()}), 2)
        incomplete = dict(bundle)
        incomplete["models"] = {codes[0]: bundle["models"][codes[0]]}
        failures = verify_international_settlement_pickle(incomplete, codes)
        self.assertTrue(any("model count" in item for item in failures))
        overall = dict(bundle)
        overall["overall_best_algorithm"] = "ARIMA"
        self.assertTrue(
            any("must not override" in item for item in verify_international_settlement_pickle(overall, codes))
        )
        missing_mase = dict(bundle)
        missing_mase["evaluation_metrics"] = ["MAE", "WAPE"]
        self.assertTrue(any("MASE" in item for item in verify_international_settlement_pickle(missing_mase, codes)))
        missing_conformal = dict(bundle)
        missing_conformal.pop("conformal_prediction")
        self.assertTrue(
            any("conformal_prediction is missing" in item for item in verify_international_settlement_pickle(missing_conformal, codes))
        )

    def test_training_path_does_not_use_overall_best_selection(self):
        source = Path(training_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("select_overall_algorithm(", source)
        self.assertIn("PER_BUDGET_CODE_BEST_MODELS", source)
        self.assertIn("all_budget_code_models.pkl", source)

    def test_pipeline_does_not_export_csv_or_png_helpers(self):
        self.assertFalse(hasattr(training_module, "save_demo_tables"))
        self.assertFalse(hasattr(training_module, "save_demo_plots"))
        self.assertFalse(hasattr(training_module, "_use_matplotlib"))
        args = parse_args(["--output-dir", "output"])
        self.assertEqual(Path(args.output_dir).name, "output")


class ConformalHelperTests(unittest.TestCase):
    def test_nonconformity_score_is_absolute_residual(self):
        self.assertEqual(nonconformity_score(100, 90), 10)
        self.assertEqual(nonconformity_score(90, 100), 10)

    def test_ninety_percent_quantile_uses_finite_sample_formula(self):
        scores = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        self.assertEqual(conformal_quantile(scores, alpha=0.10), 10)
        with self.assertRaises(InsufficientCalibrationError):
            conformal_quantile([1, 2, 3, 4, 5], alpha=0.10)

    def test_horizon_beyond_calibration_returns_none(self):
        quantiles = {1: 2.0, 2: 3.5}
        self.assertEqual(quantile_for_horizon(quantiles, 1), 2.0)
        self.assertEqual(quantile_for_horizon(quantiles, 2), 3.5)
        self.assertIsNone(quantile_for_horizon(quantiles, 8))
        self.assertIsNone(quantile_for_horizon(quantiles, 3))

    def test_rolling_origin_calibration_stores_winner_family_quantiles(self):
        codes = ["A01", "A02"]
        histories = {
            "A01": [float(index) for index in range(1, 25)],
            "A02": [float(index + 2) for index in range(1, 25)],
        }
        months = [f"M{index}" for index in range(1, 25)]

        def fit_model(spec, history, origin_months):
            return ("fitted", spec["parameters"], {"history": list(history)}, [0.0])

        def forecast_fn(algorithm, fitted, state, origin_months, steps):
            self.assertIn(algorithm, {"SARIMA", "ETS"})
            last = float(state["history"][-1])
            return [last for _ in range(steps)]

        payload = calibrate_conformal_prediction(
            codes,
            histories,
            months,
            {"A01": "SARIMA", "A02": "ETS"},
            {"A01": {"order": (1, 0, 0)}, "A02": {"error": "add"}},
            min_train_months=12,
            max_horizon=2,
            alpha=0.10,
            fit_model=fit_model,
            forecast_fn=forecast_fn,
        )
        self.assertEqual(payload["method"], "rolling_origin_absolute_residual")
        self.assertEqual(payload["selected_algorithms"]["A01"], "SARIMA")
        self.assertEqual(payload["selected_algorithms"]["A02"], "ETS")
        self.assertNotIn("overall_best_algorithm", payload)
        self.assertEqual(payload["alpha"], 0.10)
        self.assertIn(1, payload["combined"]["quantile_by_horizon"])
        self.assertIn(1, payload["accounts"]["A01"]["quantile_by_horizon"])
        self.assertGreater(payload["accounts"]["A01"]["n_scores_by_horizon"][1], 0)


if __name__ == "__main__":
    unittest.main()
