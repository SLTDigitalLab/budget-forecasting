"""Current Data Summary source priority: database, then production workbook."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from app.routers.overview import OverviewDataError
from app.services.dataset_service import summary


EMPTY = {
    "earliest_month": None,
    "latest_month": None,
    "category_count": 0,
    "budget_code_count": 0,
    "last_upload_at": None,
    "empty": True,
}


class DatasetSummarySourceTests(unittest.TestCase):
    def test_database_rows_are_used_without_opening_the_workbook(self):
        stored = {
            "earliest_month": "2024-01",
            "latest_month": "2024-03",
            "category_count": 2,
            "budget_code_count": 3,
            "last_upload_at": "2026-01-01T00:00:00Z",
            "empty": False,
        }
        with (
            patch("app.services.dataset_service.ensure_forecast_tables"),
            patch("app.services.dataset_service.historical_summary", return_value=stored),
            patch("app.routers.overview._load_workbook") as load_workbook,
        ):
            result = summary()
        load_workbook.assert_not_called()
        self.assertEqual(result["data_source"], "database")
        self.assertEqual(result["earliest_month"], "2024-01")
        self.assertEqual(result["budget_code_count"], 3)
        self.assertEqual(result["last_upload_at"], "2026-01-01T00:00:00Z")
        self.assertFalse(result["empty"])

    def test_empty_database_summarizes_the_loaded_production_workbook(self):
        assigned = pd.DataFrame(
            {
                "is_category_row": [True, False, False, False, False],
                "numeric_account_code": [None, 100, 100, 200, None],
                "cleaned_account_code": [None, "100", "100", "200", None],
                "category": ["Staff Cost", "Staff Cost", "Staff Cost", "Travelling", "Travelling"],
            }
        )
        workbook = {
            "assigned": assigned,
            "month_dates": [pd.Timestamp("2023-02-01"), pd.Timestamp("2023-01-01")],
            "sheet_name": "Actuals",
        }
        with (
            patch("app.services.dataset_service.ensure_forecast_tables"),
            patch("app.services.dataset_service.historical_summary", return_value=dict(EMPTY)),
            patch("app.routers.overview._load_workbook", return_value=workbook),
        ):
            result = summary()
        self.assertEqual(result["earliest_month"], "2023-01")
        self.assertEqual(result["latest_month"], "2023-02")
        self.assertEqual(result["budget_code_count"], 2)
        self.assertEqual(result["category_count"], 2)
        self.assertIsNone(result["last_upload_at"])
        self.assertEqual(result["data_source"], "production_workbook")
        self.assertEqual(result["data_source_label"], "Production Historical Dataset")
        self.assertFalse(result["empty"])

    def test_missing_workbook_stays_a_genuine_empty_summary(self):
        with (
            patch("app.services.dataset_service.ensure_forecast_tables"),
            patch("app.services.dataset_service.historical_summary", return_value=dict(EMPTY)),
            patch("app.routers.overview._load_workbook", side_effect=OverviewDataError("missing")),
        ):
            result = summary()
        self.assertTrue(result["empty"])
        self.assertIsNone(result["earliest_month"])
        self.assertEqual(result["budget_code_count"], 0)
        self.assertIsNone(result["data_source"])
