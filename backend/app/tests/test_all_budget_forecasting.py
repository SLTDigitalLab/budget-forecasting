"""Runtime tests for the all-budget Generate Forecast artifact. Predict-only."""

from __future__ import annotations

import hashlib
import pickle
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.config import (
    ALL_ACCOUNT_SCHEMA_VERSION,
    DEPLOYED_IS_ARTIFACT_PATH,
    FORECAST_ARTIFACT_PATH,
    PER_BUDGET_CODE_ALGORITHM,
)
from app.services.forecasting_service import (
    InvalidForecastWindowError,
    generate_monthly_forecast,
    list_forecast_categories,
)
from app.services.model_loader import (
    load_and_validate_artifact,
    reset_model_cache,
    resolve_deployed_is_artifact_path,
    resolve_forecast_artifact_path,
)
from app.tests.test_international_settlement_forecasting import FakeResults, _valid_bundle


UNAVAILABLE_REASON = "Insufficient historical data for reliable model evaluation."


class FitTracker:
    def __init__(self, values):
        self._values = np.asarray(values, dtype=float)
        self.fit_calls = 0

    def fit(self, *args, **kwargs):
        self.fit_calls += 1
        raise AssertionError(".fit() must not be called during prediction")

    def get_forecast(self, steps=1, **kwargs):
        values = self._values
        if values.size == 1:
            values = np.full(int(steps), float(values[0]), dtype=float)
        return type("Forecast", (), {"predicted_mean": values[: int(steps)]})()


def _write_pickle(bundle) -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=".pkl", delete=False)
    path = Path(handle.name)
    with handle:
        pickle.dump(bundle, handle)
    return path


def _mixed_bundle():
    tracker = FitTracker([10.0])
    records = {
        "C1": {
            "algorithm": "ARIMA",
            "model": tracker,
            "production_status": "FITTED_MODEL",
            "category": "Category A",
            "account_name": "Fitted A",
        },
        "C2": {
            "algorithm": "ETS",
            "model": FakeResults([4.0]),
            "production_status": "FITTED_MODEL",
            "category": "Category A",
            "account_name": "Fitted A2",
        },
        "Z1": {
            "production_status": "ZERO_POLICY",
            "algorithm": None,
            "model": None,
            "category": "Category A",
            "account_name": "Zero A",
        },
        "U1": {
            "production_status": "NO_VALID_WINNER",
            "algorithm": None,
            "model": None,
            "category": "Category A",
            "account_name": "Unavailable A",
        },
        "C3": {
            "algorithm": "SARIMA",
            "model": FakeResults([7.0]),
            "production_status": "FITTED_MODEL",
            "category": "Category B",
            "account_name": "Fitted B",
        },
        "Z2": {
            "production_status": "ZERO_POLICY",
            "algorithm": None,
            "model": None,
            "category": "Category B",
            "account_name": "Zero B",
        },
        "U2": {
            "production_status": "NOT_EVALUABLE",
            "algorithm": None,
            "model": None,
            "category": "Category B",
            "account_name": "Unavailable B",
        },
        "U3": {
            "production_status": "NO_DATA",
            "algorithm": None,
            "model": None,
            "category": "Category B",
            "account_name": "No Data B",
        },
    }
    models = {code: records[code] for code in ("C1", "C2", "C3")}
    bundle = _valid_bundle(models=models)
    bundle["artifact_schema_version"] = ALL_ACCOUNT_SCHEMA_VERSION
    bundle["selected_accounts"] = list(records)
    bundle["account_records"] = records
    bundle["account_models"] = records
    bundle["category"] = "ALL"
    bundle["forecast_type"] = "PER_BUDGET_CODE_BEST_MODELS"
    bundle["overall_best_algorithm"] = PER_BUDGET_CODE_ALGORITHM
    bundle["history_months"] = ["Jan-2023"]
    bundle["forecast_coverage"] = {
        "requested_account_count": 8,
        "included_count": 5,
        "unavailable_count": 3,
        "partial_total": True,
        "zero_policy_accounts": ["Z1", "Z2"],
    }
    bundle["conformal_prediction"] = {
        "method": "rolling_origin_absolute_residual",
        "selected_algorithms": {"C1": "ARIMA", "C2": "ETS", "C3": "SARIMA"},
        "combined": {"status": "UNAVAILABLE", "quantile_by_horizon": {}},
        "accounts": {},
    }
    bundle["eligible_accounts"] = [
        {"Account Code": code, "Account Name": records[code]["account_name"]}
        for code in records
    ]
    return bundle, tracker


class AllBudgetArtifactLoadTests(unittest.TestCase):
    def setUp(self):
        reset_model_cache()

    def tearDown(self):
        reset_model_cache()

    def test_all_budget_artifact_loads_successfully(self):
        path = resolve_forecast_artifact_path()
        self.assertEqual(path, FORECAST_ARTIFACT_PATH.resolve())
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 0)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        bundle = load_and_validate_artifact(path)
        self.assertEqual(bundle.get("artifact_schema_version"), ALL_ACCOUNT_SCHEMA_VERSION)
        self.assertEqual(len(bundle.get("selected_accounts") or []), 458)
        self.assertEqual(len(bundle.get("account_records") or {}), 458)
        self.assertEqual(len(bundle.get("models") or {}), 379)
        after = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_fitted_zero_policy_and_unavailable_counts(self):
        bundle = load_and_validate_artifact(resolve_forecast_artifact_path())
        records = bundle.get("account_records") or {}
        statuses = [
            str((records.get(code) or {}).get("production_status") or "")
            for code in bundle["selected_accounts"]
        ]
        self.assertEqual(statuses.count("FITTED_MODEL"), 379)
        self.assertEqual(statuses.count("ZERO_POLICY"), 69)
        unavailable = [status for status in statuses if status in {"NO_DATA", "NOT_EVALUABLE", "NO_VALID_WINNER"}]
        self.assertEqual(len(unavailable), 10)
        catalog = list_forecast_categories(bundle)
        self.assertEqual(len(catalog["accounts"]), 458)
        self.assertGreater(len(catalog["categories"]), 1)
        self.assertFalse(catalog["missing_category_codes"])
        names = [item["name"] for item in catalog["categories"]]
        self.assertNotEqual(names, ["Int'l Settlement"])
        self.assertTrue(any("settlement" in name.casefold() for name in names))

    def test_protected_international_settlement_pickle_is_unchanged(self):
        path = resolve_deployed_is_artifact_path()
        self.assertEqual(path, DEPLOYED_IS_ARTIFACT_PATH.resolve())
        self.assertTrue(path.exists())
        before_stat = path.stat()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        load_and_validate_artifact(resolve_forecast_artifact_path())
        after_stat = path.stat()
        after = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(before_stat.st_size, after_stat.st_size)
        self.assertEqual(int(before_stat.st_mtime), int(after_stat.st_mtime))
        self.assertEqual(path.name, "international_settlement_top12_models.pkl")


class MixedStatusForecastTests(unittest.TestCase):
    def test_zero_policy_returns_exact_zero_and_unavailable_stays_null(self):
        bundle, tracker = _mixed_bundle()
        payload = generate_monthly_forecast(
            bundle,
            start_month="2026-07",
            end_month="2026-07",
            category="Category A",
        )
        self.assertEqual(tracker.fit_calls, 0)
        by_code = {item["budget_code"]: item for item in payload["budget_code_forecasts"]}
        self.assertEqual(by_code["Z1"]["forecast"]["2026-07"], 0.0)
        self.assertEqual(by_code["Z1"]["forecast_type"], "ZERO_POLICY")
        self.assertTrue(by_code["Z1"]["available"])
        self.assertIsNone(by_code["Z1"]["algorithm"])
        self.assertFalse(by_code["U1"]["available"])
        self.assertIsNone(by_code["U1"]["forecast"])
        self.assertEqual(by_code["U1"]["display_status"], "Forecast Unavailable")
        self.assertEqual(by_code["U1"]["reason"], UNAVAILABLE_REASON)
        self.assertEqual(by_code["U1"]["source_status"], "NO_VALID_WINNER")
        self.assertEqual(payload["monthly_forecasts"][0]["forecast_amount"], 14.0)
        self.assertTrue(payload["partial_forecast"])
        coverage = payload["forecast_coverage"]
        self.assertEqual(coverage["total_budget_codes"], 4)
        self.assertEqual(coverage["forecasted_budget_codes"], 3)
        self.assertEqual(coverage["zero_policy_budget_codes"], 1)
        self.assertEqual(coverage["unavailable_budget_codes"], 1)
        self.assertNotIn("U1", coverage["included_in_totals"])

    def test_unavailable_is_excluded_from_numeric_sum(self):
        bundle, _tracker = _mixed_bundle()
        payload = generate_monthly_forecast(
            bundle,
            start_month="2026-07",
            end_month="2026-07",
            category="Category A",
        )
        self.assertEqual(payload["overall_total"], 14.0)
        self.assertFalse(any(row["budget_code"] == "U1" for row in payload["account_monthly_forecasts"]))
        unavailable_amount = next(
            item["forecast"] for item in payload["budget_code_forecasts"] if item["budget_code"] == "U1"
        )
        self.assertIsNone(unavailable_amount)

    def test_category_list_is_dynamic(self):
        bundle, _tracker = _mixed_bundle()
        catalog = list_forecast_categories(bundle)
        names = [item["name"] for item in catalog["categories"]]
        self.assertEqual(names, ["Category A", "Category B"])
        self.assertEqual(catalog["categories"][0]["budget_code_count"], 4)
        self.assertEqual(catalog["categories"][1]["budget_code_count"], 4)
        self.assertEqual(len(catalog["accounts"]), 8)

    def test_category_forecast_aggregates_matching_codes_only(self):
        bundle, _tracker = _mixed_bundle()
        payload = generate_monthly_forecast(
            bundle,
            start_month="2026-07",
            end_month="2026-07",
            category="Category B",
        )
        self.assertEqual(set(payload["selected_accounts"]), {"C3", "Z2", "U2", "U3"})
        self.assertEqual(payload["monthly_forecasts"][0]["forecast_amount"], 7.0)
        self.assertTrue(payload["partial_forecast"])
        self.assertEqual(payload["forecast_coverage"]["forecasted_budget_codes"], 2)
        self.assertEqual(payload["forecast_coverage"]["unavailable_budget_codes"], 2)

    def test_all_categories_aggregation_and_coverage(self):
        bundle, tracker = _mixed_bundle()
        payload = generate_monthly_forecast(
            bundle,
            start_month="2026-07",
            end_month="2026-07",
            category="all",
        )
        self.assertEqual(tracker.fit_calls, 0)
        self.assertEqual(payload["category"], "All Categories")
        self.assertEqual(payload["overall_total"], 21.0)
        self.assertTrue(payload["partial_forecast"])
        coverage = payload["forecast_coverage"]
        self.assertEqual(coverage["total_budget_codes"], 8)
        self.assertEqual(coverage["forecasted_budget_codes"], 5)
        self.assertEqual(coverage["zero_policy_budget_codes"], 2)
        self.assertEqual(coverage["unavailable_budget_codes"], 3)
        kinds = {item["forecast_type"] or item["source_status"] for item in payload["budget_code_forecasts"]}
        self.assertIn("FITTED_MODEL", kinds)
        self.assertIn("ZERO_POLICY", kinds)
        self.assertTrue({"NO_VALID_WINNER", "NOT_EVALUABLE", "NO_DATA"} <= kinds)

    def test_fitted_model_uses_stored_object_without_retraining(self):
        bundle, tracker = _mixed_bundle()
        generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-07", category="Category A")
        self.assertEqual(tracker.fit_calls, 0)
        generate_monthly_forecast(bundle, start_month="2030-12", end_month="2030-12", category="Category A")
        self.assertEqual(tracker.fit_calls, 0)

    def test_horizon_accepts_july_2026_and_december_2030(self):
        bundle, _tracker = _mixed_bundle()
        july = generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-07", category="all")
        self.assertEqual(july["requested_start_month"], "2026-07")
        december = generate_monthly_forecast(bundle, start_month="2030-12", end_month="2030-12", category="all")
        self.assertEqual(december["requested_end_month"], "2030-12")

    def test_horizon_rejects_june_2026_and_january_2031(self):
        bundle, _tracker = _mixed_bundle()
        with self.assertRaises(InvalidForecastWindowError):
            generate_monthly_forecast(bundle, start_month="2026-06", end_month="2026-06", category="all")
        with self.assertRaises(InvalidForecastWindowError):
            generate_monthly_forecast(bundle, start_month="2031-01", end_month="2031-01", category="all")

    def test_loader_accepts_known_non_fitted_statuses(self):
        bundle, _tracker = _mixed_bundle()
        path = _write_pickle(bundle)
        loaded = load_and_validate_artifact(path)
        self.assertEqual(len(loaded["models"]), 3)
        self.assertEqual(len(loaded["selected_accounts"]), 8)
        path.unlink()


if __name__ == "__main__":
    unittest.main()
