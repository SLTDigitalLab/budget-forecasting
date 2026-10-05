"""Historical analysis helpers used by Analytics & Charts (FR-11)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi import HTTPException

from app.routers import overview


def _prepared_history() -> dict:
    dates = list(pd.date_range("2024-01-01", "2025-06-01", freq="MS"))
    columns = [value.strftime("%b-%Y") for value in dates]
    values = {}
    for date, column in zip(dates, columns):
        amount = 11.0 if date.year == 2025 else 10.0
        values[column] = [amount, amount / 2]
    selected_all = pd.DataFrame(values, index=["A01", "A02"])
    month_dates = [pd.Timestamp(value).replace(day=1) for value in dates]
    return {
        "category": {"id": "international_settlement", "name": "International Settlement", "source_name": "Int'l Settlement"},
        "scope": {
            "history_start": "2024-01",
            "history_end": "2025-06",
            "latest_complete_year": 2024,
            "latest_year": 2024,
            "latest_year_is_partial": False,
            "latest_actual_month": "2025-06",
        },
        "date_to_column": dict(zip(month_dates, columns)),
        "month_dates": month_dates,
        "selected_codes": ["A01", "A02"],
        "names": {"A01": "Account A01", "A02": "Account A02"},
        "selected_all": selected_all,
    }


class OverviewAnalysisTests(unittest.TestCase):
    def test_like_for_like_growth_uses_overlapping_months(self):
        growth = overview._like_for_like_growth(
            {1: 12.0, 2: 12.0, 3: 12.0},
            {1: 10.0, 2: 10.0, 3: 10.0, 4: 10.0},
        )
        self.assertAlmostEqual(growth, 20.0)

    def test_partial_year_is_not_compared_as_a_full_year_total(self):
        rows = overview._yearly_actuals([
            ("2024-01", 10.0),
            ("2024-02", 10.0),
            ("2025-01", 12.0),
        ])
        self.assertEqual(rows[0]["year_status"], "PARTIAL_YEAR")
        self.assertEqual(rows[1]["year_status"], "PARTIAL_YEAR")
        self.assertAlmostEqual(rows[1]["growth_percent"], 20.0)
        self.assertAlmostEqual(rows[0]["actual_amount"], 20.0)
        self.assertAlmostEqual(rows[1]["actual_amount"], 12.0)

    def test_seasonal_profile_covers_all_calendar_months(self):
        profile = overview._seasonal_profile([
            ("2024-01", 10.0),
            ("2025-01", 20.0),
            ("2024-06", 30.0),
        ])
        self.assertEqual(len(profile), 12)
        january = next(item for item in profile if item["calendar_month"] == 1)
        june = next(item for item in profile if item["calendar_month"] == 6)
        july = next(item for item in profile if item["calendar_month"] == 7)
        self.assertAlmostEqual(january["average_amount"], 15.0)
        self.assertEqual(january["observation_count"], 2)
        self.assertAlmostEqual(june["average_amount"], 30.0)
        self.assertIsNone(july["average_amount"])
        self.assertEqual(july["observation_count"], 0)

    def test_analysis_payload_does_not_require_a_forecast(self):
        payload = overview._analysis_from_prepared(_prepared_history())
        self.assertEqual(payload["history_start"], "2024-01")
        self.assertEqual(payload["history_end"], "2025-06")
        self.assertEqual(payload["complete_month_count"], 18)
        self.assertEqual(len(payload["monthly_actuals"]), 18)
        self.assertEqual(len(payload["seasonal_profile"]), 12)
        self.assertIsNone(payload.get("monthly_forecasts"))
        self.assertAlmostEqual(payload["latest_year_growth_percent"], 10.0)
        self.assertEqual(payload["recurring_count"], 2)
        self.assertTrue(all(item["recurring"] for item in payload["budget_codes"]))

    def test_analysis_endpoint_returns_historical_actuals_without_forecast(self):
        payload = overview._analysis_from_prepared(_prepared_history())
        with patch.object(overview, "_get_analysis", return_value=payload):
            response = overview.overview_analysis(category="international_settlement")
        body = response.model_dump()
        self.assertEqual(body["category"]["id"], "international_settlement")
        self.assertEqual(len(body["monthly_actuals"]), 18)
        self.assertEqual(body["budget_code_count"], 2)

    def test_unknown_category_is_not_empty_forecast_state(self):
        with patch.object(overview, "_get_analysis", side_effect=overview.OverviewNotFoundError("Unknown category: missing")):
            with self.assertRaises(HTTPException) as caught:
                overview.overview_analysis(category="missing")
        self.assertEqual(caught.exception.status_code, 404)

    def test_complete_history_mask_rejects_a_partial_account(self):
        frame = pd.DataFrame({"m1": [10.0, 10.0], "m2": [10.0, np.nan]})
        mask = overview._complete_history_mask(frame)
        self.assertTrue(bool(mask.iloc[0]))
        self.assertFalse(bool(mask.iloc[1]))

    def test_analysis_keeps_months_when_an_incomplete_account_is_larger_in_the_latest_year(self):
        months = list(pd.date_range("2023-01-01", "2026-06-01", freq="MS"))
        columns = [f"{value.strftime('%b')} {str(value.year)[2:]} Actuals" for value in months]
        rows = []
        for index in range(1, 13):
            code = f"A{index:02d}"
            row = {
                "ACT CODE": code,
                "ACT NAME": f"Account {code}",
                "category": "Int'l Settlement",
                "is_category_row": False,
                "numeric_account_code": float(index),
            }
            row.update({column: 10.0 for column in columns})
            rows.append(row)
        incomplete = {
            "ACT CODE": "Z99",
            "ACT NAME": "Incomplete",
            "category": "Int'l Settlement",
            "is_category_row": False,
            "numeric_account_code": 99.0,
        }
        for date, column in zip(months, columns):
            if date.year == 2024 and date.month <= 5:
                incomplete[column] = np.nan
            else:
                incomplete[column] = 1000.0
        rows.append(incomplete)
        workbook = {
            "assigned": pd.DataFrame(rows),
            "account_code_col": "ACT CODE",
            "account_name_col": "ACT NAME",
            "monthly_columns": columns,
            "month_dates": [pd.Timestamp(value).replace(day=1) for value in months],
        }
        category = {
            "id": "international_settlement",
            "name": "International Settlement",
            "source_name": "Int'l Settlement",
        }
        canonical = [f"A{index:02d}" for index in range(1, 13)]
        with patch.object(overview, "_load_workbook", return_value=workbook), patch.object(
            overview, "_production_selected_accounts", return_value=canonical
        ):
            prepared = overview._prepare_category_actuals(category)
        self.assertTrue(set(canonical).issubset(set(prepared["selected_codes"])))
        self.assertIn("Z99", prepared["selected_codes"])
        self.assertEqual(len(prepared["selected_codes"]), 13)
        payload = overview._analysis_from_prepared(prepared)
        self.assertEqual(payload["history_start"], "2023-01")
        self.assertEqual(payload["history_end"], "2026-06")
        self.assertEqual(payload["complete_month_count"], 42)
        self.assertEqual(len(payload["monthly_actuals"]), 42)
        self.assertEqual([row["month"] for row in payload["monthly_actuals"][:5]], [
            "2023-01",
            "2023-02",
            "2023-03",
            "2023-04",
            "2023-05",
        ])
        gap = [row for row in payload["monthly_actuals"] if row["month"] in {
            "2024-01",
            "2024-02",
            "2024-03",
            "2024-04",
            "2024-05",
        }]
        self.assertEqual(len(gap), 5)
        self.assertTrue(all(row["actual_amount"] is not None for row in gap))
        self.assertTrue(all(row["actual_amount"] is not None for row in payload["monthly_actuals"]))

    def test_production_accounts_are_read_from_the_validated_bundle(self):
        from_bundle = [f"P{index:02d}" for index in range(1, 13)]
        with patch.object(overview, "get_model_bundle", return_value={"selected_accounts": from_bundle}):
            codes = overview._production_selected_accounts({
                "id": "international_settlement",
                "name": "International Settlement",
            })
        self.assertEqual(codes, from_bundle)

    def test_international_settlement_uses_bundle_selected_accounts_not_abs_sum(self):
        months = list(pd.date_range("2023-01-01", "2026-06-01", freq="MS"))
        columns = [f"{value.strftime('%b')} {str(value.year)[2:]} Actuals" for value in months]
        from_bundle = [f"P{index:02d}" for index in range(1, 13)]
        rivals = ["R88", "R99"]
        rows = []
        for code in from_bundle + rivals:
            row = {
                "ACT CODE": code,
                "ACT NAME": f"Account {code}",
                "category": "Int'l Settlement",
                "is_category_row": False,
                "numeric_account_code": float(code[1:]),
            }
            amount = 1000.0 if code in rivals else 10.0
            row.update({column: amount for column in columns})
            rows.append(row)
        workbook = {
            "assigned": pd.DataFrame(rows),
            "account_code_col": "ACT CODE",
            "account_name_col": "ACT NAME",
            "monthly_columns": columns,
            "month_dates": [pd.Timestamp(value).replace(day=1) for value in months],
        }
        category = {
            "id": "international_settlement",
            "name": "International Settlement",
            "source_name": "Int'l Settlement",
        }
        with patch.object(overview, "_load_workbook", return_value=workbook), patch.object(
            overview, "_production_selected_accounts", return_value=from_bundle
        ):
            prepared = overview._prepare_category_actuals(category)
        self.assertEqual(set(prepared["selected_codes"]), set(from_bundle + rivals))
        self.assertIn("R88", prepared["selected_codes"])
        self.assertIn("R99", prepared["selected_codes"])
        payload = overview._analysis_from_prepared(prepared)
        returned = [row["budget_code"] for row in payload["budget_codes"]]
        self.assertEqual(set(returned), set(from_bundle + rivals))
        self.assertIn("R88", returned)
        self.assertIn("R99", returned)
        self.assertEqual(payload["budget_code_count"], 14)

    def test_missing_production_account_does_not_crash_or_become_zero(self):
        months = list(pd.date_range("2023-01-01", "2023-02-01", freq="MS"))
        columns = [f"{value.strftime('%b')} {str(value.year)[2:]} Actuals" for value in months]
        workbook = {
            "assigned": pd.DataFrame([{
                "ACT CODE": "KEEP1",
                "ACT NAME": "One",
                "category": "Int'l Settlement",
                "is_category_row": False,
                "numeric_account_code": 1.0,
                columns[0]: 10.0,
                columns[1]: 10.0,
            }]),
            "account_code_col": "ACT CODE",
            "account_name_col": "ACT NAME",
            "monthly_columns": columns,
            "month_dates": [pd.Timestamp(value).replace(day=1) for value in months],
        }
        category = {
            "id": "international_settlement",
            "name": "International Settlement",
            "source_name": "Int'l Settlement",
        }
        with patch.object(overview, "_load_workbook", return_value=workbook), patch.object(
            overview, "_production_selected_accounts", return_value=["KEEP1", "MISSING2"]
        ):
            prepared = overview._prepare_category_actuals(category)
            payload = overview._build_historical(category)
        self.assertEqual(prepared["selected_codes"], ["KEEP1"])
        self.assertEqual(prepared["historical_missing_budget_codes"], ["MISSING2"])
        self.assertEqual(payload["historical_data_partial"], True)
        self.assertEqual(payload["historical_missing_budget_code_count"], 1)
        self.assertNotIn("MISSING2", [row["budget_code"] for row in payload["latest_month_budget_codes"]])
        self.assertFalse(any(row["budget_code"] == "MISSING2" and row["actual_amount"] == 0 for row in payload["latest_month_budget_codes"]))
        self.assertAlmostEqual(payload["latest_year_monthly_actuals"][0]["actual_amount"], 10.0)
        self.assertEqual(payload["latest_complete_year_budget_codes"], [])
        self.assertNotIn("Production Budget Codes are missing", str(payload))

    def test_missing_latest_month_value_is_omitted_not_zeroed(self):
        months = list(pd.date_range("2023-01-01", "2023-02-01", freq="MS"))
        columns = [f"{value.strftime('%b')} {str(value.year)[2:]} Actuals" for value in months]
        workbook = {
            "assigned": pd.DataFrame([
                {
                    "ACT CODE": "KEEP1",
                    "ACT NAME": "One",
                    "category": "Int'l Settlement",
                    "is_category_row": False,
                    "numeric_account_code": 1.0,
                    columns[0]: 10.0,
                    columns[1]: 4.0,
                },
                {
                    "ACT CODE": "GAP2",
                    "ACT NAME": "Gap",
                    "category": "Int'l Settlement",
                    "is_category_row": False,
                    "numeric_account_code": 2.0,
                    columns[0]: 6.0,
                    columns[1]: np.nan,
                },
            ]),
            "account_code_col": "ACT CODE",
            "account_name_col": "ACT NAME",
            "monthly_columns": columns,
            "month_dates": [pd.Timestamp(value).replace(day=1) for value in months],
        }
        category = {
            "id": "international_settlement",
            "name": "International Settlement",
            "source_name": "Int'l Settlement",
        }
        with patch.object(overview, "_load_workbook", return_value=workbook), patch.object(
            overview, "_production_selected_accounts", return_value=None
        ):
            payload = overview._build_historical(category)
        january = next(row for row in payload["latest_year_monthly_actuals"] if row["month"] == "2023-01")
        february = next(row for row in payload["latest_year_monthly_actuals"] if row["month"] == "2023-02")
        self.assertAlmostEqual(january["actual_amount"], 16.0)
        self.assertAlmostEqual(february["actual_amount"], 4.0)
        codes = {row["budget_code"]: row["actual_amount"] for row in payload["latest_month_budget_codes"]}
        self.assertEqual(set(codes), {"KEEP1"})
        self.assertNotIn("GAP2", codes)

    def test_missing_workbook_still_raises_a_real_error(self):
        from pathlib import Path

        overview.invalidate_overview_cache()
        with patch("app.services.dataset_repository.list_historical_actuals", return_value=[]), patch.object(
            overview, "_resolve_original_actuals_path", return_value=Path("missing_actuals.xlsx")
        ):
            with self.assertRaises(overview.OverviewDataError) as caught:
                overview._load_workbook()
        self.assertIn("was not found", str(caught.exception).casefold())
        overview.invalidate_overview_cache()


class ForecastRuntimeUnchangedByOverviewTests(unittest.TestCase):
    def test_generate_forecast_still_uses_all_budget_artifact(self):
        from app.config import FORECAST_ARTIFACT_NAME, FORECAST_ARTIFACT_PATH
        from app.services.model_loader import resolve_forecast_artifact_path

        self.assertEqual(FORECAST_ARTIFACT_NAME, "all_budget_code_models.pkl")
        self.assertEqual(FORECAST_ARTIFACT_PATH.name, "all_budget_code_models.pkl")
        self.assertEqual(resolve_forecast_artifact_path().name, "all_budget_code_models.pkl")

    def test_forecast_account_counts_remain_458_379_69_10(self):
        import hashlib
        from collections import Counter

        from app.config import ALL_ACCOUNT_SCHEMA_VERSION, DEPLOYED_IS_ARTIFACT_PATH, FORECAST_ARTIFACT_PATH
        from app.services.model_loader import load_and_validate_artifact, reset_model_cache

        if not FORECAST_ARTIFACT_PATH.exists():
            self.skipTest("All-budget artifact is not present.")
        before = hashlib.sha256(FORECAST_ARTIFACT_PATH.read_bytes()).hexdigest()
        protected_before = hashlib.sha256(DEPLOYED_IS_ARTIFACT_PATH.read_bytes()).hexdigest()
        reset_model_cache()
        bundle = load_and_validate_artifact(FORECAST_ARTIFACT_PATH)
        self.assertEqual(bundle.get("artifact_schema_version"), ALL_ACCOUNT_SCHEMA_VERSION)
        records = bundle.get("account_records") or {}
        statuses = Counter(
            str((records.get(code) or {}).get("production_status") or "")
            for code in bundle["selected_accounts"]
        )
        self.assertEqual(len(bundle["selected_accounts"]), 458)
        self.assertEqual(statuses["FITTED_MODEL"], 379)
        self.assertEqual(statuses["ZERO_POLICY"], 69)
        self.assertEqual(sum(statuses[key] for key in ("NO_DATA", "NOT_EVALUABLE", "NO_VALID_WINNER")), 10)
        self.assertEqual(hashlib.sha256(FORECAST_ARTIFACT_PATH.read_bytes()).hexdigest(), before)
        self.assertEqual(hashlib.sha256(DEPLOYED_IS_ARTIFACT_PATH.read_bytes()).hexdigest(), protected_before)
        self.assertEqual(protected_before, "bd190c661c9f5dd25b52aef15a5b4ec84db70bac7a1bffdbb1e7b65d7fdb9e76")
        reset_model_cache()

    def test_forecast_aggregation_still_excludes_unavailable_and_keeps_zero_policy(self):
        from app.services.forecasting_service import generate_monthly_forecast
        from app.tests.test_international_settlement_forecasting import FakeResults

        bundle = {
            "forecast_type": "PER_BUDGET_CODE_BEST_MODELS",
            "category": "ALL",
            "historical_start": "Jan-2023",
            "historical_end": "Jun-2026",
            "selected_accounts": ["F1", "Z1", "U1"],
            "overall_best_algorithm": "PER_BUDGET_CODE",
            "models": {
                "F1": {
                    "algorithm": "ARIMA",
                    "model": FakeResults([5.0]),
                    "production_status": "FITTED_MODEL",
                    "category": "Int'l Settlement",
                },
            },
            "account_records": {
                "F1": {
                    "algorithm": "ARIMA",
                    "model": FakeResults([5.0]),
                    "production_status": "FITTED_MODEL",
                    "category": "Int'l Settlement",
                },
                "Z1": {"production_status": "ZERO_POLICY", "algorithm": None, "model": None, "category": "Int'l Settlement"},
                "U1": {"production_status": "NO_VALID_WINNER", "algorithm": None, "model": None, "category": "Int'l Settlement"},
            },
        }
        payload = generate_monthly_forecast(bundle, start_month="2026-07", end_month="2026-07", category="all")
        self.assertEqual(payload["overall_total"], 5.0)
        self.assertTrue(payload["partial_forecast"])
        self.assertEqual(payload["forecast_coverage"]["zero_policy_budget_codes"], 1)
        self.assertEqual(payload["forecast_coverage"]["unavailable_budget_codes"], 1)
        zero = next(item for item in payload["budget_code_forecasts"] if item["budget_code"] == "Z1")
        missing = next(item for item in payload["budget_code_forecasts"] if item["budget_code"] == "U1")
        self.assertEqual(zero["forecast"]["2026-07"], 0.0)
        self.assertIsNone(missing["forecast"])


if __name__ == "__main__":
    unittest.main()
