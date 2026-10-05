"""Read-only latest forecast history endpoint tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.db import DatabaseError
from app.routers import forecasting


class ForecastHistoryLatestTests(unittest.TestCase):
    def test_latest_returns_completed_run(self):
        payload = {
            "forecast_type": "INTERNATIONAL_SETTLEMENT_DEMO",
            "category": "Int'l Settlement",
            "target_description": "test",
            "overall_best_algorithm": "SARIMA",
            "selected_account_count": 1,
            "selected_accounts": ["A01"],
            "historical_start": "2023-01",
            "historical_end": "2026-06",
            "history_end": "2026-06",
            "forecast_start": "2026-07",
            "forecast_end": "2026-07",
            "requested_start_month": "2026-07",
            "requested_end_month": "2026-07",
            "forecast_month_count": 1,
            "amount_unit": "LKR_MILLIONS",
            "model_amount_unit": "LKR_MILLIONS",
            "response_amount_unit": "LKR_MILLIONS",
            "conversion_factor": 1.0,
            "clip_negative_applied": True,
            "monthly_forecasts": [{"month": "2026-07", "forecast_amount": 12.5}],
            "account_monthly_forecasts": [
                {"budget_code": "A01", "account_name": "Account A01", "month": "2026-07", "forecast_amount": 12.5}
            ],
            "combined_monthly_forecasts": [{"month": "2026-07", "forecast_amount": 12.5}],
            "account_yearly_forecasts": [
                {
                    "budget_code": "A01",
                    "account_name": "Account A01",
                    "year": 2026,
                    "forecast_amount": 12.5,
                    "year_status": "PARTIAL_YEAR",
                    "months_included": 1,
                }
            ],
            "combined_yearly_forecasts": [
                {
                    "year": 2026,
                    "forecast_amount": 12.5,
                    "year_status": "PARTIAL_YEAR",
                    "months_included": 1,
                }
            ],
            "historical_actuals": [{"month": "2026-06", "actual_amount": 10.0}],
            "overall_total": 12.5,
            "monthly_average": 12.5,
            "minimum_monthly_forecast": 12.5,
            "maximum_monthly_forecast": 12.5,
            "generated_at": "2026-08-25T10:00:00Z",
        }
        with patch.object(forecasting, "get_latest_forecast_payload", return_value=payload):
            response = forecasting.forecast_history_latest()
        self.assertEqual(response.overall_total, 12.5)
        self.assertEqual(response.requested_start_month, "2026-07")
        self.assertEqual(response.monthly_forecasts[0].forecast_amount, 12.5)

    def test_latest_is_empty_when_no_completed_run_exists(self):
        with patch.object(forecasting, "get_latest_forecast_payload", return_value=None):
            with self.assertRaises(HTTPException) as caught:
                forecasting.forecast_history_latest()
        self.assertEqual(caught.exception.status_code, 404)

    def test_latest_backend_failure_is_not_empty(self):
        with patch.object(
            forecasting,
            "get_latest_forecast_payload",
            side_effect=DatabaseError("Unable to connect to the forecast database."),
        ):
            with self.assertRaises(HTTPException) as caught:
                forecasting.forecast_history_latest()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotEqual(caught.exception.status_code, 404)
