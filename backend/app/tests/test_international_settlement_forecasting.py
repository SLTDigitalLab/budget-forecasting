"""Lightweight tests for International Settlement forecast loading and prediction."""

from __future__ import annotations

import hashlib
import pickle
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.config import (
    ALLOWED_FORECAST_TYPES,
    DEPLOYED_IS_ARTIFACT_PATH,
    FORECAST_ARTIFACT_NAME,
    FORECAST_ARTIFACT_PATH,
    PER_BUDGET_CODE_ALGORITHM,
)
from app.services import forecasting_service as forecasting_service_module
from app.services.forecasting_service import (
    InvalidForecastWindowError,
    UnsupportedAccountError,
    first_forecast_month,
    generate_monthly_forecast,
    historical_monthly_baseline,
    parse_month_timestamp,
    specific_month_historical_baseline,
)
from app.services import model_loader
from app.services.model_loader import (
    ModelLoadError,
    get_model_bundle,
    load_and_validate_artifact,
    reset_model_cache,
    resolve_all_budget_artifact_path,
    resolve_deployed_is_artifact_path,
    resolve_forecast_artifact_path,
)

CODES = [f"A{index:02d}" for index in range(1, 13)]
REAL_ARTIFACT = FORECAST_ARTIFACT_PATH.resolve()


class FakeForecast:
    def __init__(self, values, tracker=None):
        self.predicted_mean = np.asarray(values, dtype=float)
        self.tracker = tracker

    def conf_int(self, *args, **kwargs):
        if self.tracker is not None:
            self.tracker["conf_int"] = self.tracker.get("conf_int", 0) + 1
        raise AssertionError("conf_int() must not be used for conformal intervals")


class FakeResults:
    def __init__(self, values, tracker=None):
        self._values = np.asarray(values, dtype=float)
        self.tracker = tracker

    def fit(self, *args, **kwargs):
        if self.tracker is not None:
            self.tracker["fit"] += 1
        raise AssertionError(".fit() must not be called during prediction")

    def get_forecast(self, steps=1, **kwargs):
        if self.tracker is not None:
            self.tracker["get_forecast"] += 1
        values = self._values
        if values.size == 1:
            values = np.full(int(steps), float(values[0]), dtype=float)
        return FakeForecast(values[: int(steps)], tracker=self.tracker)


def _valid_bundle(models=None, tracker=None, historical_end="Jun-2026", algorithm="SARIMA"):
    fitted = models
    if fitted is None:
        fitted = {
            code: {
                "algorithm": algorithm,
                "model": FakeResults([float(index + 1)], tracker=tracker),
                "model_specification": {"order": (1, 0, 0)},
            }
            for index, code in enumerate(CODES)
        }
    return {
        "forecast_type": "INTERNATIONAL_SETTLEMENT_DEMO",
        "category": "Int'l Settlement",
        "historical_start": "Jan-2023",
        "historical_end": historical_end,
        "detected_month_count": 18,
        "detected_month_columns": ["Jan 23 Actuals"],
        "eligible_accounts": [
            {"Account Code": code, "Account Name": f"Account {code}"}
            for code in CODES
        ],
        "selected_accounts": list(CODES),
        "overall_best_algorithm": algorithm,
        "evaluation_metrics": ["MAE", "RMSE", "MAPE", "WAPE", "MASE", "R2"],
        "metric_winners": [],
        "account_winners": [],
        "account_win_counts": {algorithm: 12},
        "algorithm_summary": [],
        "models": fitted,
        "evaluation_results": [],
        "forecast_start": "Jul-2026",
        "forecast_end": "Dec-2027",
        "monthly_account_forecasts": [],
        "monthly_combined_forecast": [],
        "yearly_account_forecasts": [],
        "yearly_combined_forecast": [],
        "year_status": [],
        "amount_unit": "LKR_MILLIONS",
        "clip_negative_applied": False,
        "history_months": [],
        "account_models": {},
    }


def _conformal_payload(algorithm="SARIMA", combined_q=None, account_q=None):
    combined = combined_q or {1: 5.0, 2: 6.0}
    account = account_q or {1: 0.4, 2: 0.5}
    return {
        "method": "rolling_origin_absolute_residual",
        "alpha": 0.1,
        "nominal_coverage": 0.9,
        "overall_best_algorithm": algorithm,
        "min_train_months": 12,
        "max_calibrated_horizon": 6,
        "accounts": {
            code: {"n_scores_by_horizon": {1: 10, 2: 9}, "quantile_by_horizon": dict(account)}
            for code in CODES
        },
        "combined": {"n_scores_by_horizon": {1: 10, 2: 9}, "quantile_by_horizon": dict(combined)},
    }


def _write_pickle(bundle) -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=".pkl", delete=False)
    path = Path(handle.name)
    with handle:
        pickle.dump(bundle, handle)
    return path


class ArtifactPathAndValidationTests(unittest.TestCase):
    def setUp(self):
        reset_model_cache()

    def tearDown(self):
        reset_model_cache()

    def test_resolves_new_artifact_path(self):
        path = resolve_forecast_artifact_path()
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.name, "all_budget_code_models.pkl")
        self.assertEqual(path.name, FORECAST_ARTIFACT_NAME)
        self.assertNotEqual(path, Path.cwd() / path.name)

    def test_old_pickle_paths_are_not_used(self):
        loader_source = Path(model_loader.__file__).read_text(encoding="utf-8")
        from app import config as app_config
        config_text = Path(app_config.__file__).read_text(encoding="utf-8")
        for name in (
            "best_monthly_model.pkl",
            "best_model.pkl",
            "best_top12_account_models.pkl",
            "best_overall_family_top12_models.pkl",
        ):
            self.assertNotIn(name, loader_source)
            self.assertNotIn(name, config_text)
        self.assertEqual(FORECAST_ARTIFACT_PATH.name, "all_budget_code_models.pkl")
        self.assertEqual(resolve_forecast_artifact_path().name, "all_budget_code_models.pkl")
        self.assertEqual(resolve_all_budget_artifact_path().name, "all_budget_code_models.pkl")
        self.assertEqual(resolve_forecast_artifact_path(), resolve_all_budget_artifact_path())
        self.assertEqual(resolve_deployed_is_artifact_path().name, "international_settlement_top12_models.pkl")
        self.assertEqual(DEPLOYED_IS_ARTIFACT_PATH.name, "international_settlement_top12_models.pkl")
        self.assertNotEqual(resolve_forecast_artifact_path(), resolve_deployed_is_artifact_path())
        self.assertIn("international_settlement_top12_models.pkl", config_text)
        self.assertIn("all_budget_code_models.pkl", config_text)

    def test_production_pickle_output_is_bind_mounted(self):
        compose = Path(__file__).resolve().parents[3] / "docker-compose.yml"
        text = compose.read_text(encoding="utf-8")
        output_mount = "./backend/model_training_engine/output:/app/model_training_engine/output"
        self.assertIn(output_mount, text)
        self.assertNotIn(f"{output_mount}:ro", text)
        self.assertIn(
            "./backend/model_training_engine/dataset:/app/model_training_engine/dataset:ro",
            text,
        )
        self.assertNotEqual(output_mount, "./backend/model_training_engine/dataset:/app/model_training_engine/dataset")

    def test_successful_new_schema_validation(self):
        path = _write_pickle(_valid_bundle())
        bundle = load_and_validate_artifact(path)
        self.assertEqual(bundle["forecast_type"], "INTERNATIONAL_SETTLEMENT_DEMO")
        self.assertEqual(len(bundle["models"]), 12)
        self.assertIs(get_model_bundle(), bundle)
        path.unlink()

    def test_per_budget_code_artifact_allows_different_algorithms(self):
        from app.config import EXPECTED_FORECAST_TYPE, PER_BUDGET_CODE_ALGORITHM

        bundle = _valid_bundle()
        bundle["forecast_type"] = EXPECTED_FORECAST_TYPE
        bundle["overall_best_algorithm"] = PER_BUDGET_CODE_ALGORITHM
        bundle["models"][CODES[0]]["algorithm"] = "ARIMA"
        bundle["models"][CODES[1]]["algorithm"] = "ETS"
        path = _write_pickle(bundle)
        loaded = load_and_validate_artifact(path)
        self.assertEqual(loaded["models"][CODES[0]]["algorithm"], "ARIMA")
        self.assertEqual(loaded["models"][CODES[1]]["algorithm"], "ETS")
        path.unlink()

    def test_missing_pickle(self):
        missing = Path(tempfile.gettempdir()) / "missing_is_artifact.pkl"
        if missing.exists():
            missing.unlink()
        with self.assertRaises(ModelLoadError) as error:
            load_and_validate_artifact(missing)
        self.assertIn("not found", str(error.exception).casefold())
        self.assertIsNone(get_model_bundle())

    def test_corrupt_pickle(self):
        handle = tempfile.NamedTemporaryFile(suffix=".pkl", delete=False)
        path = Path(handle.name)
        handle.write(b"not-a-pickle")
        handle.close()
        with self.assertRaises(ModelLoadError) as error:
            load_and_validate_artifact(path)
        self.assertIn("corrupt", str(error.exception).casefold())
        path.unlink()

    def test_wrong_forecast_type(self):
        bundle = _valid_bundle()
        bundle["forecast_type"] = "MONTHLY_COMBINED_TOTAL"
        path = _write_pickle(bundle)
        with self.assertRaises(ModelLoadError) as error:
            load_and_validate_artifact(path)
        self.assertIn("PER_BUDGET_CODE_BEST_MODELS", str(error.exception))
        path.unlink()

    def test_missing_required_keys(self):
        bundle = _valid_bundle()
        del bundle["yearly_combined_forecast"]
        path = _write_pickle(bundle)
        with self.assertRaises(ModelLoadError) as error:
            load_and_validate_artifact(path)
        self.assertIn("metadata", str(error.exception).casefold())
        path.unlink()

    def test_model_count_not_equal_to_twelve(self):
        bundle = _valid_bundle()
        bundle["models"] = {code: bundle["models"][code] for code in CODES[:11]}
        path = _write_pickle(bundle)
        with self.assertRaises(ModelLoadError) as error:
            load_and_validate_artifact(path)
        self.assertIn("12", str(error.exception))
        path.unlink()

    def test_selected_account_model_key_mismatch(self):
        bundle = _valid_bundle()
        models = dict(bundle["models"])
        models["ZZZZ"] = models.pop(CODES[0])
        bundle["models"] = models
        path = _write_pickle(bundle)
        with self.assertRaises(ModelLoadError) as error:
            load_and_validate_artifact(path)
        self.assertIn("match", str(error.exception).casefold())
        path.unlink()

    def test_model_algorithm_differs_from_overall(self):
        bundle = _valid_bundle()
        bundle["models"][CODES[0]]["algorithm"] = "XGBoost"
        path = _write_pickle(bundle)
        with self.assertRaises(ModelLoadError) as error:
            load_and_validate_artifact(path)
        self.assertIn("expected SARIMA", str(error.exception))
        path.unlink()

    def test_artifact_cache_reuse(self):
        path = _write_pickle(_valid_bundle())
        with patch("app.services.model_loader.pickle.load", wraps=pickle.load) as mocked:
            first = load_and_validate_artifact(path)
            second = get_model_bundle()
            third = get_model_bundle()
        self.assertIs(first, second)
        self.assertIs(second, third)
        self.assertEqual(mocked.call_count, 1)
        path.unlink()

    def test_cache_reset_for_tests(self):
        path = _write_pickle(_valid_bundle())
        load_and_validate_artifact(path)
        self.assertIsNotNone(get_model_bundle())
        reset_model_cache()
        self.assertIsNone(get_model_bundle())
        path.unlink()

    def test_missing_conformal_payload_still_loads(self):
        bundle = _valid_bundle()
        self.assertNotIn("conformal_prediction", bundle)
        path = _write_pickle(bundle)
        loaded = load_and_validate_artifact(path)
        self.assertIsNone(loaded.get("conformal_prediction"))
        path.unlink()

    def test_conformal_algorithm_mismatch_is_rejected_at_load(self):
        bundle = _valid_bundle()
        bundle["conformal_prediction"] = _conformal_payload(algorithm="XGBoost")
        path = _write_pickle(bundle)
        with self.assertRaises(ModelLoadError) as error:
            load_and_validate_artifact(path)
        self.assertIn("conformal_prediction algorithm", str(error.exception))
        self.assertIsNone(get_model_bundle())
        path.unlink()

    def test_matching_conformal_payload_loads(self):
        bundle = _valid_bundle()
        bundle["conformal_prediction"] = _conformal_payload(algorithm="SARIMA")
        path = _write_pickle(bundle)
        loaded = load_and_validate_artifact(path)
        self.assertEqual(loaded["conformal_prediction"]["overall_best_algorithm"], "SARIMA")
        path.unlink()


class ForecastWindowAndAggregationTests(unittest.TestCase):
    def test_dynamic_first_forecast_month(self):
        self.assertEqual(first_forecast_month("May-2026"), pd.Timestamp("2026-06-01"))
        self.assertEqual(first_forecast_month("2026-06"), pd.Timestamp("2026-07-01"))
        self.assertEqual(first_forecast_month("Jun-2026"), pd.Timestamp("2026-07-01"))

    def test_valid_start_end_month_parsing(self):
        self.assertEqual(parse_month_timestamp("2026-07"), pd.Timestamp("2026-07-01"))
        self.assertEqual(parse_month_timestamp("Jul-2026"), pd.Timestamp("2026-07-01"))
        with self.assertRaises(InvalidForecastWindowError):
            parse_month_timestamp("2026-13")
        with self.assertRaises(InvalidForecastWindowError):
            parse_month_timestamp("not-a-month")

    def test_start_before_or_equal_to_historical_end_rejected(self):
        bundle = _valid_bundle(historical_end="Jun-2026")
        with self.assertRaises(InvalidForecastWindowError):
            generate_monthly_forecast(bundle, start_month="2026-06", end_month="2026-12")
        with self.assertRaises(InvalidForecastWindowError):
            generate_monthly_forecast(bundle, start_month="2026-05", end_month="2026-12")

    def test_horizon_accepts_december_2030_and_rejects_later(self):
        bundle = _valid_bundle(historical_end="Jun-2026")
        payload = generate_monthly_forecast(bundle, start_month="2030-12", end_month="2030-12")
        self.assertEqual(payload["requested_start_month"], "2030-12")
        self.assertEqual(payload["requested_end_month"], "2030-12")
        self.assertEqual(payload["forecast_start"], "2026-07")
        with self.assertRaises(InvalidForecastWindowError):
            generate_monthly_forecast(bundle, start_month="2031-01", end_month="2031-01")
        with self.assertRaises(InvalidForecastWindowError):
            generate_monthly_forecast(bundle, start_month="2030-12", end_month="2031-01")

    def test_later_requested_start_slices_forecasts(self):
        tracker = {"fit": 0, "get_forecast": 0}
        models = {
            code: {
                "algorithm": "SARIMA",
                "model": FakeResults(np.arange(1, 13, dtype=float), tracker=tracker),
                "model_specification": {},
            }
            for code in CODES
        }
        bundle = _valid_bundle(models=models, historical_end="Jun-2026")
        payload = generate_monthly_forecast(bundle, start_month="2027-01", end_month="2027-02")
        self.assertEqual(payload["requested_start_month"], "2027-01")
        self.assertEqual([row["month"] for row in payload["monthly_forecasts"]], ["2027-01", "2027-02"])
        self.assertEqual(payload["monthly_forecasts"][0]["forecast_amount"], 12 * 7)
        self.assertEqual(payload["monthly_forecasts"][1]["forecast_amount"], 12 * 8)
        self.assertEqual(tracker["fit"], 0)

    def test_exactly_twelve_account_predictions_and_monthly_outputs(self):
        bundle = _valid_bundle()
        payload = generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-08")
        self.assertEqual(payload["selected_account_count"], 12)
        self.assertEqual(len(payload["selected_accounts"]), 12)
        months = ["2026-07", "2026-08"]
        account_months = payload["account_monthly_forecasts"]
        self.assertEqual(len(account_months), 24)
        for code in CODES:
            rows = [row for row in account_months if row["budget_code"] == code]
            self.assertEqual([row["month"] for row in rows], months)
            self.assertTrue(all(row["account_name"] == f"Account {code}" for row in rows))
        combined = {row["month"]: row["forecast_amount"] for row in payload["monthly_forecasts"]}
        for month in months:
            expected = sum(
                row["forecast_amount"]
                for row in account_months
                if row["month"] == month
            )
            self.assertEqual(combined[month], expected)
        self.assertEqual(payload["overall_best_algorithm"], "SARIMA")

    def test_july_2027_combined_equals_the_twelve_trained_budget_codes(self):
        bundle = _valid_bundle()
        payload = generate_monthly_forecast(bundle, start_month="2027-07", end_month="2027-07")
        self.assertEqual(payload["requested_start_month"], "2027-07")
        self.assertEqual(payload["requested_end_month"], "2027-07")
        self.assertEqual(payload["selected_accounts"], CODES)
        self.assertEqual(len(payload["account_monthly_forecasts"]), 12)
        self.assertTrue(all(row["month"] == "2027-07" for row in payload["account_monthly_forecasts"]))
        self.assertEqual({row["budget_code"] for row in payload["account_monthly_forecasts"]}, set(CODES))
        self.assertFalse(any(str(row["budget_code"]).lower() == "other" for row in payload["account_monthly_forecasts"]))
        combined = payload["monthly_forecasts"][0]["forecast_amount"]
        account_sum = sum(row["forecast_amount"] for row in payload["account_monthly_forecasts"])
        self.assertEqual(combined, account_sum)
        self.assertEqual(payload["overall_total"], account_sum)

    def test_backend_uses_each_budget_code_model_and_sums_them(self):
        from app.config import EXPECTED_FORECAST_TYPE, PER_BUDGET_CODE_ALGORITHM

        tracker = {"fit": 0, "get_forecast": 0}
        models = {}
        for index, code in enumerate(CODES):
            algorithm = "ARIMA" if index % 2 == 0 else "ETS"
            models[code] = {
                "algorithm": algorithm,
                "model_name": algorithm,
                "model": FakeResults([float(index + 1)], tracker=tracker),
                "model_specification": {},
            }
        bundle = _valid_bundle(models=models)
        bundle["forecast_type"] = EXPECTED_FORECAST_TYPE
        bundle["overall_best_algorithm"] = PER_BUDGET_CODE_ALGORITHM
        payload = generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-07")
        expected = sum(float(index + 1) for index in range(12))
        self.assertEqual(payload["monthly_forecasts"][0]["forecast_amount"], expected)
        by_code = {row["budget_code"]: row["forecast_amount"] for row in payload["account_monthly_forecasts"]}
        self.assertEqual(by_code[CODES[0]], 1.0)
        self.assertEqual(by_code[CODES[1]], 2.0)
        self.assertEqual(tracker["fit"], 0)
        self.assertGreater(tracker["get_forecast"], 0)

    def test_yearly_aggregation_and_labels(self):
        bundle = _valid_bundle()
        full = generate_monthly_forecast(bundle, start_month="2027-01", end_month="2027-12")
        self.assertEqual(len(full["combined_yearly_forecasts"]), 1)
        self.assertEqual(full["combined_yearly_forecasts"][0]["year_status"], "FULL_YEAR")
        self.assertEqual(full["combined_yearly_forecasts"][0]["months_included"], 12)
        self.assertTrue(all(row["year_status"] == "FULL_YEAR" for row in full["account_yearly_forecasts"]))
        self.assertEqual(len(full["account_yearly_forecasts"]), 12)
        expected_year = sum(
            row["forecast_amount"]
            for row in full["account_monthly_forecasts"]
            if row["month"].startswith("2027-")
        )
        self.assertEqual(full["combined_yearly_forecasts"][0]["forecast_amount"], expected_year)
        account_year = {
            row["budget_code"]: row["forecast_amount"]
            for row in full["account_yearly_forecasts"]
        }
        for code in CODES:
            account_sum = sum(
                row["forecast_amount"]
                for row in full["account_monthly_forecasts"]
                if row["budget_code"] == code
            )
            self.assertEqual(account_year[code], account_sum)

        partial = generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-12")
        self.assertEqual(partial["combined_yearly_forecasts"][0]["year_status"], "PARTIAL_YEAR")
        self.assertEqual(partial["combined_yearly_forecasts"][0]["months_included"], 6)

    def test_unsupported_account_code_error(self):
        bundle = _valid_bundle()
        with self.assertRaises(UnsupportedAccountError):
            generate_monthly_forecast(
                bundle,
                start_month="2026-07",
                end_month="2026-07",
                account_codes=["999999"],
            )

    def test_non_finite_prediction_rejected(self):
        models = {
            code: {
                "algorithm": "SARIMA",
                "model": FakeResults([np.nan if code == CODES[0] else 1.0]),
                "model_specification": {},
            }
            for code in CODES
        }
        bundle = _valid_bundle(models=models)
        with self.assertRaises(Exception) as error:
            generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-07")
        self.assertIn("non-finite", str(error.exception).casefold())

    def test_no_fit_call_during_prediction(self):
        tracker = {"fit": 0, "get_forecast": 0}
        models = {
            code: {
                "algorithm": "SARIMA",
                "model": FakeResults([1.0], tracker=tracker),
                "model_specification": {},
            }
            for code in CODES
        }
        bundle = _valid_bundle(models=models)
        generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-07")
        self.assertEqual(tracker["fit"], 0)
        self.assertEqual(tracker["get_forecast"], 12)

    def test_amount_conversion_exactly_once(self):
        models = {
            code: {
                "algorithm": "SARIMA",
                "model": FakeResults([2.5]),
                "model_specification": {},
            }
            for code in CODES
        }
        bundle = _valid_bundle(models=models)
        payload = generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-07")
        self.assertEqual(payload["conversion_factor"], 1.0)
        self.assertEqual(payload["model_amount_unit"], "LKR_MILLIONS")
        self.assertEqual(payload["response_amount_unit"], "LKR_MILLIONS")
        self.assertEqual(payload["amount_unit"], "LKR_MILLIONS")
        self.assertEqual(payload["monthly_forecasts"][0]["forecast_amount"], 12 * 2.5)
        self.assertNotEqual(payload["monthly_forecasts"][0]["forecast_amount"], 12 * 2.5 * 1_000_000)

    def test_overall_algorithm_metadata_returned(self):
        payload = generate_monthly_forecast(
            _valid_bundle(),
            start_month="2026-07",
            end_month="2026-07",
        )
        self.assertEqual(payload["overall_best_algorithm"], "SARIMA")
        self.assertEqual(payload["forecast_type"], "INTERNATIONAL_SETTLEMENT_DEMO")
        self.assertEqual(payload["category"], "All Categories")
        filtered = generate_monthly_forecast(
            _valid_bundle(),
            start_month="2026-07",
            end_month="2026-07",
            category="Int'l Settlement",
        )
        self.assertEqual(filtered["category"], "Int'l Settlement")
        self.assertEqual(payload["historical_end"], "Jun-2026")
        self.assertEqual(payload["forecast_start"], "2026-07")
        self.assertIsNone(payload["monthly_forecasts"][0]["lower_bound"])
        self.assertIsNone(payload["monthly_forecasts"][0]["upper_bound"])
        self.assertIsNone(payload["account_monthly_forecasts"][0]["lower_bound"])
        self.assertIsNone(payload["interval_method"])
        self.assertIsNone(payload["interval_coverage"])
        self.assertIsNone(payload["monthly_forecasts"][0]["interval_level"])
        self.assertIsNone(payload["monthly_forecasts"][0]["interval_status"])

    def test_generate_exposes_one_combined_historical_monthly_average(self):
        history_months = [f"2023-{index:02d}" for index in range(1, 13)]
        history_months += [f"2024-{index:02d}" for index in range(1, 13)]
        history_months += [f"2025-{index:02d}" for index in range(1, 13)]
        history_months += [f"2026-{index:02d}" for index in range(1, 7)]
        self.assertEqual(len(history_months), 42)
        bundle = _valid_bundle()
        bundle["history_months"] = history_months
        bundle["account_models"] = {
            code: {"historical_values": [10.0 + offset for offset in range(42)]}
            for code in CODES
        }
        payload = generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-09")
        expected_average = sum(12 * (10.0 + offset) for offset in range(42)) / 42
        self.assertEqual(payload["historical_month_count"], 42)
        self.assertAlmostEqual(payload["historical_monthly_average"], expected_average)
        self.assertEqual(len(payload["historical_actuals"]), 42)
        self.assertEqual(len(payload["monthly_forecasts"]), 3)
        self.assertTrue(
            all("historical_monthly_average" not in row for row in payload["monthly_forecasts"])
        )
        first_forecast = payload["monthly_forecasts"][0]["forecast_amount"]
        self.assertEqual(first_forecast, sum(float(index + 1) for index in range(12)))
        average, count = historical_monthly_baseline(payload["historical_actuals"])
        self.assertEqual(count, 42)
        self.assertAlmostEqual(average, expected_average)
        july_avg, july_count = specific_month_historical_baseline(payload["historical_actuals"], "2026-07")
        august_avg, august_count = specific_month_historical_baseline(payload["historical_actuals"], "2026-08")
        self.assertEqual(july_count, 3)
        self.assertEqual(august_count, 3)
        self.assertAlmostEqual(july_avg, (12 * 16 + 12 * 28 + 12 * 40) / 3)
        self.assertAlmostEqual(august_avg, (12 * 17 + 12 * 29 + 12 * 41) / 3)
        self.assertNotAlmostEqual(july_avg, august_avg)
        self.assertAlmostEqual(payload["monthly_forecasts"][0]["specific_month_historical_average"], july_avg)
        self.assertAlmostEqual(payload["monthly_forecasts"][1]["specific_month_historical_average"], august_avg)
        self.assertEqual(payload["monthly_forecasts"][0]["specific_month_history_count"], 3)
        missing_avg, missing_count = specific_month_historical_baseline([], "2026-08")
        self.assertIsNone(missing_avg)
        self.assertEqual(missing_count, 0)
        self.assertNotAlmostEqual(payload["monthly_forecasts"][1]["forecast_amount"], first_forecast * 1.1)
        self.assertTrue(
            all(row["forecast_amount"] == first_forecast or row["month"] != "2026-07" for row in payload["monthly_forecasts"])
        )

    def test_specific_month_baseline_skips_non_finite_and_later_months(self):
        actuals = [
            {"month": "2023-08", "actual_amount": 100.0},
            {"month": "2024-08", "actual_amount": None},
            {"month": "2025-08", "actual_amount": float("nan")},
            {"month": "2026-08", "actual_amount": 999.0},
            {"month": "2027-08", "actual_amount": 111.0},
            {"month": "2023-07", "actual_amount": 50.0},
        ]
        average, count = specific_month_historical_baseline(actuals, "2026-08")
        self.assertEqual(count, 1)
        self.assertAlmostEqual(average, 100.0)
        missing, missing_count = specific_month_historical_baseline(actuals, "2026-02")
        self.assertIsNone(missing)
        self.assertEqual(missing_count, 0)
        overall, overall_count = historical_monthly_baseline(actuals)
        self.assertEqual(overall_count, 4)
        self.assertAlmostEqual(overall, (100.0 + 999.0 + 111.0 + 50.0) / 4)


class ConformalIntervalTests(unittest.TestCase):
    def test_missing_payload_leaves_bounds_null(self):
        payload = generate_monthly_forecast(
            _valid_bundle(),
            start_month="2026-07",
            end_month="2026-07",
        )
        self.assertIsNone(payload["monthly_forecasts"][0]["lower_bound"])
        self.assertIsNone(payload["monthly_forecasts"][0]["upper_bound"])
        self.assertIsNone(payload["monthly_forecasts"][0]["interval_level"])
        self.assertIsNone(payload["monthly_forecasts"][0]["interval_status"])
        self.assertTrue(
            all(row["lower_bound"] is None and row["upper_bound"] is None for row in payload["account_monthly_forecasts"])
        )

    def test_conformal_payload_fills_combined_and_account_bounds(self):
        bundle = _valid_bundle()
        bundle["conformal_prediction"] = _conformal_payload()
        payload = generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-07")
        combined = payload["monthly_forecasts"][0]
        self.assertAlmostEqual(combined["forecast_amount"], 78.0)
        self.assertAlmostEqual(combined["lower_bound"], 73.0)
        self.assertAlmostEqual(combined["upper_bound"], 83.0)
        self.assertEqual(combined["interval_level"], 0.9)
        self.assertIsNone(combined["interval_status"])
        self.assertEqual(payload["interval_method"], "rolling_origin_absolute_residual")
        self.assertEqual(payload["interval_coverage"], 0.9)
        first_account = next(row for row in payload["account_monthly_forecasts"] if row["budget_code"] == "A01")
        self.assertAlmostEqual(first_account["forecast_amount"], 1.0)
        self.assertAlmostEqual(first_account["lower_bound"], 0.6)
        self.assertAlmostEqual(first_account["upper_bound"], 1.4)
        self.assertEqual(first_account["interval_level"], 0.9)
        self.assertIsNone(first_account["interval_status"])

    def test_horizon_beyond_calibration_is_unavailable(self):
        bundle = _valid_bundle()
        bundle["conformal_prediction"] = _conformal_payload()
        payload = generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-09")
        rows = payload["monthly_forecasts"]
        self.assertEqual([row["month"] for row in rows], ["2026-07", "2026-08", "2026-09"])
        self.assertAlmostEqual(rows[0]["lower_bound"], 78.0 - 5.0)
        self.assertAlmostEqual(rows[0]["upper_bound"], 78.0 + 5.0)
        self.assertEqual(rows[0]["interval_level"], 0.9)
        self.assertIsNone(rows[0]["interval_status"])
        self.assertAlmostEqual(rows[1]["lower_bound"], 78.0 - 6.0)
        self.assertEqual(rows[1]["interval_level"], 0.9)
        self.assertIsNone(rows[1]["interval_status"])
        self.assertIsNone(rows[2]["lower_bound"])
        self.assertIsNone(rows[2]["upper_bound"])
        self.assertIsNone(rows[2]["interval_level"])
        self.assertEqual(rows[2]["interval_status"], "unavailable_beyond_calibrated_horizon")
        later_account = next(
            row for row in payload["account_monthly_forecasts"]
            if row["budget_code"] == "A01" and row["month"] == "2026-09"
        )
        self.assertIsNone(later_account["lower_bound"])
        self.assertIsNone(later_account["upper_bound"])
        self.assertIsNone(later_account["interval_level"])
        self.assertEqual(later_account["interval_status"], "unavailable_beyond_calibrated_horizon")

    def test_bounds_path_does_not_call_conf_int(self):
        tracker = {"fit": 0, "get_forecast": 0, "conf_int": 0}
        models = {
            code: {
                "algorithm": "SARIMA",
                "model": FakeResults([1.0], tracker=tracker),
                "model_specification": {},
            }
            for code in CODES
        }
        bundle = _valid_bundle(models=models)
        bundle["conformal_prediction"] = _conformal_payload()
        generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-08")
        self.assertEqual(tracker["fit"], 0)
        self.assertEqual(tracker["get_forecast"], 12)
        self.assertEqual(tracker["conf_int"], 0)
        source = Path(forecasting_service_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("conf_int", source)

    def test_clip_negative_floors_lower_without_changing_point(self):
        bundle = _valid_bundle()
        bundle["clip_negative_applied"] = True
        bundle["conformal_prediction"] = _conformal_payload(combined_q={1: 100.0}, account_q={1: 100.0})
        payload = generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-07")
        combined = payload["monthly_forecasts"][0]
        self.assertAlmostEqual(combined["forecast_amount"], 78.0)
        self.assertAlmostEqual(combined["lower_bound"], 0.0)
        self.assertAlmostEqual(combined["upper_bound"], 178.0)
        first_account = next(row for row in payload["account_monthly_forecasts"] if row["budget_code"] == "A01")
        self.assertAlmostEqual(first_account["forecast_amount"], 1.0)
        self.assertAlmostEqual(first_account["lower_bound"], 0.0)

    def test_generate_ignores_mismatched_conformal_algorithm(self):
        bundle = _valid_bundle()
        bundle["conformal_prediction"] = _conformal_payload(algorithm="XGBoost")
        payload = generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-07")
        self.assertIsNone(payload["monthly_forecasts"][0]["lower_bound"])
        self.assertIsNone(payload["monthly_forecasts"][0]["upper_bound"])
        self.assertIsNone(payload["monthly_forecasts"][0]["interval_level"])
        self.assertIsNone(payload["monthly_forecasts"][0]["interval_status"])


class RealArtifactSmokeTests(unittest.TestCase):
    def test_real_artifact_predicts_without_refit(self):
        if not REAL_ARTIFACT.exists():
            self.skipTest("Production artifact is not present.")
        before = REAL_ARTIFACT.stat()
        digest_before = hashlib.sha256(REAL_ARTIFACT.read_bytes()).hexdigest()
        reset_model_cache()
        bundle = load_and_validate_artifact(REAL_ARTIFACT)
        self.assertIn(bundle.get("forecast_type"), ALLOWED_FORECAST_TYPES)
        overall = bundle.get("overall_best_algorithm")
        if bundle.get("forecast_type") == "PER_BUDGET_CODE_BEST_MODELS":
            self.assertIn(overall, {None, "", PER_BUDGET_CODE_ALGORITHM})
        else:
            self.assertTrue(overall)
        self.assertEqual(bundle.get("artifact_schema_version"), "all_account_per_budget_code_v1")
        self.assertEqual(len(bundle["selected_accounts"]), 458)
        self.assertEqual(len(bundle["account_records"]), 458)
        self.assertEqual(len(bundle["models"]), 379)
        records = bundle.get("account_records") or {}
        statuses = [str((records.get(code) or {}).get("production_status") or "") for code in bundle["selected_accounts"]]
        self.assertEqual(statuses.count("FITTED_MODEL"), 379)
        self.assertEqual(statuses.count("ZERO_POLICY"), 69)
        self.assertEqual(sum(1 for status in statuses if status in {"NO_DATA", "NOT_EVALUABLE", "NO_VALID_WINNER"}), 10)
        sample = None
        for code in bundle["selected_accounts"]:
            record = records.get(code) or {}
            if record.get("production_status") == "FITTED_MODEL" and record.get("algorithm") in {"ARIMA", "SARIMA", "ETS"}:
                sample = code
                break
        self.assertIsNotNone(sample)
        payload = generate_monthly_forecast(
            bundle,
            start_month="2026-07",
            end_month="2026-07",
            account_codes=[sample],
        )
        self.assertEqual(payload["selected_account_count"], 1)
        self.assertEqual(len(payload["account_monthly_forecasts"]), 1)
        self.assertTrue(all(np.isfinite(row["forecast_amount"]) for row in payload["monthly_forecasts"]))
        combined = payload["monthly_forecasts"][0]["forecast_amount"]
        account_sum = sum(row["forecast_amount"] for row in payload["account_monthly_forecasts"])
        self.assertAlmostEqual(combined, account_sum)
        after = REAL_ARTIFACT.stat()
        digest_after = hashlib.sha256(REAL_ARTIFACT.read_bytes()).hexdigest()
        self.assertEqual(before.st_size, after.st_size)
        self.assertEqual(int(before.st_mtime), int(after.st_mtime))
        self.assertEqual(digest_before, digest_after)
        reset_model_cache()

    def test_loader_and_generate_do_not_call_training(self):
        loader_source = Path(model_loader.__file__).read_text(encoding="utf-8")
        service_source = Path(forecasting_service_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("run_training", loader_source)
        self.assertNotIn("run_training", service_source)
        self.assertNotIn("evaluate_seven_models", service_source)
        self.assertNotIn("fit_production_model", service_source)

    def test_new_schema_loads_dynamic_account_counts_and_zero_policy(self):
        from app.config import ALL_ACCOUNT_SCHEMA_VERSION

        codes = [f"N{index:02d}" for index in range(1, 15)]
        models = {}
        records = {}
        for index, code in enumerate(codes):
            if index == 0:
                records[code] = {
                    "production_status": "ZERO_POLICY",
                    "algorithm": None,
                    "model": None,
                    "category": "Int'l Settlement",
                }
            else:
                entry = {
                    "algorithm": "ARIMA",
                    "model": FakeResults([float(index + 1)]),
                    "production_status": "FITTED_MODEL",
                    "category": "Int'l Settlement",
                }
                models[code] = entry
                records[code] = entry
        bundle = _valid_bundle(models=models)
        bundle["artifact_schema_version"] = ALL_ACCOUNT_SCHEMA_VERSION
        bundle["selected_accounts"] = codes
        bundle["account_records"] = records
        bundle["account_models"] = records
        bundle["category"] = "ALL"
        bundle["forecast_type"] = "PER_BUDGET_CODE_BEST_MODELS"
        bundle["overall_best_algorithm"] = PER_BUDGET_CODE_ALGORITHM
        bundle["history_months"] = ["Jan-2023"]
        bundle["forecast_coverage"] = {
            "requested_account_count": 14,
            "included_in_totals": codes[1:],
            "included_count": 13,
            "unavailable_accounts": [],
            "unavailable_count": 0,
            "partial_total": False,
            "zero_policy_accounts": [codes[0]],
        }
        bundle["conformal_prediction"] = {
            "method": "rolling_origin_absolute_residual",
            "selected_algorithms": {code: "ARIMA" for code in codes[1:]},
            "combined": {"status": "UNAVAILABLE", "quantile_by_horizon": {}},
            "accounts": {},
        }
        path = _write_pickle(bundle)
        loaded = load_and_validate_artifact(path)
        self.assertEqual(len(loaded["selected_accounts"]), 14)
        payload = generate_monthly_forecast(loaded, start_month="2026-07", end_month="2026-07")
        self.assertIn(codes[0], payload["selected_accounts"])
        zero_row = next(row for row in payload["account_monthly_forecasts"] if row["budget_code"] == codes[0])
        self.assertEqual(zero_row["forecast_amount"], 0.0)
        path.unlink()

    def test_no_data_account_is_unavailable_and_does_not_return_fake_zero(self):
        from app.config import ALL_ACCOUNT_SCHEMA_VERSION

        codes = ["Z1", "N1", "F1"]
        records = {
            "Z1": {
                "production_status": "ZERO_POLICY",
                "algorithm": None,
                "model": None,
                "category": "Int'l Settlement",
            },
            "N1": {
                "production_status": "NO_DATA",
                "status_reason": "ALL_MISSING",
                "algorithm": None,
                "model": None,
                "category": "Int'l Settlement",
            },
            "F1": {
                "algorithm": "ARIMA",
                "model": FakeResults([8.0]),
                "production_status": "FITTED_MODEL",
                "category": "Int'l Settlement",
            },
        }
        bundle = _valid_bundle(models={"F1": records["F1"]})
        bundle["artifact_schema_version"] = ALL_ACCOUNT_SCHEMA_VERSION
        bundle["selected_accounts"] = codes
        bundle["account_records"] = records
        bundle["account_models"] = records
        bundle["category"] = "ALL"
        bundle["forecast_type"] = "PER_BUDGET_CODE_BEST_MODELS"
        bundle["overall_best_algorithm"] = PER_BUDGET_CODE_ALGORITHM
        bundle["history_months"] = ["Jan-2023"]
        bundle["forecast_coverage"] = {
            "requested_account_count": 3,
            "included_in_totals": ["Z1", "F1"],
            "included_count": 2,
            "unavailable_accounts": [{"account_code": "N1", "reason": "ALL_MISSING"}],
            "unavailable_count": 1,
            "partial_total": True,
            "zero_policy_accounts": ["Z1"],
            "no_data_accounts": ["N1"],
        }
        bundle["conformal_prediction"] = {
            "method": "rolling_origin_absolute_residual",
            "selected_algorithms": {"F1": "ARIMA"},
            "combined": {"status": "UNAVAILABLE", "quantile_by_horizon": {}},
            "accounts": {},
        }
        path = _write_pickle(bundle)
        loaded = load_and_validate_artifact(path)
        payload = generate_monthly_forecast(loaded, start_month="2026-07", end_month="2026-07")
        self.assertIn("Z1", payload["selected_accounts"])
        self.assertIn("N1", payload["selected_accounts"])
        self.assertTrue(payload["partial_forecast"])
        zero_row = next(row for row in payload["account_monthly_forecasts"] if row["budget_code"] == "Z1")
        self.assertEqual(zero_row["forecast_amount"], 0.0)
        self.assertFalse(any(row["budget_code"] == "N1" for row in payload["account_monthly_forecasts"]))
        unavailable = payload["forecast_coverage"]["unavailable_accounts"]
        self.assertTrue(any(item["account_code"] == "N1" for item in unavailable))
        missing = next(item for item in payload["budget_code_forecasts"] if item["budget_code"] == "N1")
        self.assertFalse(missing["available"])
        self.assertIsNone(missing["forecast"])
        self.assertEqual(missing["display_status"], "Forecast Unavailable")
        self.assertNotEqual(missing["forecast"], 0)
        self.assertEqual(payload["monthly_forecasts"][0]["forecast_amount"], 8.0)
        path.unlink()

    def test_category_filter_uses_that_category_only(self):
        models = {
            "IS1": {
                "algorithm": "ARIMA",
                "model": FakeResults([3.0]),
                "production_status": "FITTED_MODEL",
                "category": "Int'l Settlement",
            },
            "OT1": {
                "algorithm": "ETS",
                "model": FakeResults([9.0]),
                "production_status": "FITTED_MODEL",
                "category": "Domestic",
            },
        }
        bundle = _valid_bundle(models=models)
        bundle["selected_accounts"] = ["IS1", "OT1"]
        bundle["account_records"] = models
        bundle["forecast_type"] = "PER_BUDGET_CODE_BEST_MODELS"
        bundle["overall_best_algorithm"] = PER_BUDGET_CODE_ALGORITHM
        payload = generate_monthly_forecast(
            bundle,
            start_month="2026-07",
            end_month="2026-07",
            category="Int'l Settlement",
        )
        self.assertEqual(payload["selected_accounts"], ["IS1"])
        self.assertEqual(payload["monthly_forecasts"][0]["forecast_amount"], 3.0)


if __name__ == "__main__":
    unittest.main()
