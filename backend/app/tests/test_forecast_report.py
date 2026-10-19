"""Report preview and export tests for persisted completed forecasts."""

from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import patch

from fastapi import HTTPException
from openpyxl import load_workbook

from app.db import DatabaseError
from app.routers import forecasting
from app.services import forecasting_service, model_loader
from app.services.report_builder import UNAVAILABLE, build_forecast_report
from app.services.report_excel import SHEET_NAMES


def completed_payload(**overrides):
    payload = {
        "forecast_type": "INTERNATIONAL_SETTLEMENT_DEMO",
        "category": "Int'l Settlement",
        "target_description": "test",
        "overall_best_algorithm": "SARIMA",
        "selected_account_count": 2,
        "selected_accounts": ["A01", "A02"],
        "historical_start": "2023-01",
        "historical_end": "2026-06",
        "history_end": "2026-06",
        "forecast_start": "2026-07",
        "forecast_end": "2026-12",
        "requested_start_month": "2026-07",
        "requested_end_month": "2026-12",
        "forecast_month_count": 2,
        "amount_unit": "LKR_MILLIONS",
        "model_amount_unit": "LKR_MILLIONS",
        "response_amount_unit": "LKR_MILLIONS",
        "conversion_factor": 1.0,
        "clip_negative_applied": True,
        "monthly_forecasts": [
            {"month": "2026-07", "forecast_amount": 120.0, "lower_bound": 110.0, "upper_bound": 130.0},
            {"month": "2026-08", "forecast_amount": 130.0, "lower_bound": None, "upper_bound": None},
        ],
        "account_monthly_forecasts": [
            {"budget_code": "A01", "account_name": "First", "month": "2026-07", "forecast_amount": 80.0},
            {"budget_code": "A01", "account_name": "First", "month": "2026-08", "forecast_amount": 80.0},
            {"budget_code": "A02", "account_name": "Second", "month": "2026-07", "forecast_amount": 40.0},
            {"budget_code": "A02", "account_name": "Second", "month": "2026-08", "forecast_amount": 50.0},
        ],
        "combined_monthly_forecasts": [
            {"month": "2026-07", "forecast_amount": 120.0},
            {"month": "2026-08", "forecast_amount": 130.0},
        ],
        "account_yearly_forecasts": [
            {
                "budget_code": "A01",
                "account_name": "First",
                "year": 2026,
                "forecast_amount": 160.0,
                "year_status": "PARTIAL_YEAR",
                "months_included": 2,
            }
        ],
        "combined_yearly_forecasts": [
            {"year": 2026, "forecast_amount": 250.0, "year_status": "PARTIAL_YEAR", "months_included": 2}
        ],
        "historical_actuals": [
            {"month": "2025-07", "actual_amount": 100.0},
            {"month": "2026-05", "actual_amount": 100.0},
            {"month": "2026-06", "actual_amount": 110.0},
        ],
        "overall_total": 250.0,
        "monthly_average": 125.0,
        "minimum_monthly_forecast": 120.0,
        "maximum_monthly_forecast": 130.0,
        "generated_at": "2026-08-30T10:00:00Z",
    }
    payload.update(overrides)
    return payload


class ForecastReportTests(unittest.TestCase):
    def test_preview_uses_the_selected_completed_forecast(self):
        payload = completed_payload()
        with patch.object(forecasting, "get_forecast_payload", return_value=payload):
            report = forecasting.forecast_report_preview(17)
        self.assertEqual(report["overall_total"], 250.0)
        self.assertEqual(report["header"]["period"], "Jul-2026 to Dec-2026")
        self.assertEqual(report["selected_accounts"], ["A01", "A02"])
        self.assertNotIn("id", report)
        self.assertNotIn("overall_best_algorithm", report["header"])
        self.assertNotIn("SARIMA", str(report["insights"]))

    def test_preview_response_is_json_not_a_download(self):
        with patch.object(forecasting, "get_forecast_payload", return_value=completed_payload()):
            report = forecasting.forecast_report_preview(17)
        self.assertIsInstance(report, dict)
        self.assertEqual(report["title"], "AI Budget Forecast Report")
        self.assertNotIn("Content-Disposition", report)

    def test_incomplete_runs_cannot_be_exported(self):
        with patch.object(forecasting, "get_forecast_payload", return_value=None):
            with self.assertRaises(HTTPException) as missing:
                forecasting.forecast_report_preview(99)
        self.assertEqual(missing.exception.status_code, 404)
        with patch.object(
            forecasting,
            "get_forecast_payload",
            return_value=completed_payload(monthly_forecasts=[], selected_accounts=[]),
        ):
            with self.assertRaises(HTTPException) as incomplete:
                forecasting.forecast_report_excel(99)
        self.assertEqual(incomplete.exception.status_code, 409)

    def test_preview_and_export_totals_match(self):
        payload = completed_payload()
        report = build_forecast_report(payload)
        with patch.object(forecasting, "get_forecast_payload", return_value=payload):
            preview = forecasting.forecast_report_preview(3)
            excel = forecasting.forecast_report_excel(3)
            pdf = forecasting.forecast_report_pdf(3)
        workbook = load_workbook(BytesIO(excel.body))
        self.assertEqual(preview["overall_total"], payload["overall_total"])
        self.assertEqual(report["overall_total"], payload["overall_total"])
        self.assertEqual(preview["monthly_average"], 125.0)
        self.assertEqual(workbook.sheetnames, ["Budget Forecast"])
        self.assertTrue(pdf.body.startswith(b"%PDF"))

    def test_pdf_has_the_correct_content_type_and_filename(self):
        with patch.object(forecasting, "get_forecast_payload", return_value=completed_payload()):
            response = forecasting.forecast_report_pdf(3)
        self.assertEqual(response.media_type, "application/pdf")
        self.assertIn(
            'filename="SLT_Mobitel_Forecast_Report_Jul-2026_to_Dec-2026.pdf"',
            response.headers["content-disposition"],
        )
        self.assertTrue(str(response.headers["content-disposition"]).lower().startswith("attachment"))
        with patch.object(forecasting, "get_forecast_payload", return_value=completed_payload()):
            preview = forecasting.forecast_report_pdf(3, inline=True)
        self.assertTrue(str(preview.headers["content-disposition"]).lower().startswith("inline"))

    def test_excel_has_the_correct_content_type_filename_and_worksheets(self):
        with patch.object(forecasting, "get_forecast_payload", return_value=completed_payload()):
            response = forecasting.forecast_report_excel(3)
        self.assertEqual(
            response.media_type,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn(
            'filename="AI_Budget_Forecast_Jul_2026_Dec_2026.xlsx"',
            response.headers["content-disposition"],
        )
        workbook = load_workbook(BytesIO(response.body))
        self.assertEqual(workbook.sheetnames, ["Budget Forecast"])
        self.assertEqual(list(SHEET_NAMES), ["Budget Forecast"])
        self.assertEqual(workbook.active.title, "Budget Forecast")
        self.assertNotIn("Forecast Summary", workbook.sheetnames)
        self.assertNotIn("Monthly Forecast", workbook.sheetnames)
        self.assertNotIn("Budget Code Summary", workbook.sheetnames)
        self.assertNotIn("Account Monthly", workbook.sheetnames)
        self.assertNotIn("Yearly Forecast", workbook.sheetnames)
        self.assertNotIn("Historical Comparison", workbook.sheetnames)

    def test_old_forecasts_with_null_bounds_display_and_export_unavailable(self):
        payload = completed_payload()
        payload["monthly_forecasts"] = [
            {"month": "2026-07", "forecast_amount": 120.0},
            {"month": "2026-08", "forecast_amount": 130.0, "lower_bound": None, "upper_bound": None},
        ]
        report = build_forecast_report(payload)
        self.assertEqual(report["monthly_forecasts"][0]["lower_bound"], UNAVAILABLE)
        self.assertEqual(report["monthly_forecasts"][1]["upper_bound"], UNAVAILABLE)
        with patch.object(forecasting, "get_forecast_payload", return_value=payload):
            excel = forecasting.forecast_report_excel(4)
        workbook = load_workbook(BytesIO(excel.body))
        self.assertEqual(workbook.sheetnames, ["Budget Forecast"])
        self.assertNotIn("Monthly Forecast", workbook.sheetnames)

    def test_preview_and_export_do_not_invoke_model_prediction_or_training(self):
        payload = completed_payload()
        with (
            patch.object(forecasting, "get_forecast_payload", return_value=payload),
            patch.object(forecasting_service, "generate_monthly_forecast") as generate,
            patch.object(model_loader, "load_production_model") as load_model,
            patch.object(model_loader, "get_model_bundle") as bundle,
        ):
            forecasting.forecast_report_preview(8)
            forecasting.forecast_report_pdf(8)
            forecasting.forecast_report_excel(8)
        generate.assert_not_called()
        load_model.assert_not_called()
        bundle.assert_not_called()

    def test_database_failure_is_not_treated_as_missing(self):
        with patch.object(
            forecasting,
            "get_forecast_payload",
            side_effect=DatabaseError("Unable to connect to the forecast database."),
        ):
            with self.assertRaises(HTTPException) as caught:
                forecasting.forecast_report_preview(1)
        self.assertEqual(caught.exception.status_code, 503)

    def test_budget_codes_come_from_the_saved_run_only(self):
        payload = completed_payload()
        payload["account_monthly_forecasts"].append(
            {"budget_code": "R99", "account_name": "Rival", "month": "2026-07", "forecast_amount": 900.0}
        )
        report = build_forecast_report(payload)
        self.assertEqual([row["budget_code"] for row in report["budget_codes"]], ["A01", "A02"])


if __name__ == "__main__":
    unittest.main()
