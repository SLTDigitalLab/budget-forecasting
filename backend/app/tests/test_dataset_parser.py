"""Validation for historical dataset uploads."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.services.dataset_merge import classify_historical_changes, intended_edit_records
from app.services.dataset_parser import parse_historical_dataset


def _write_sheet(rows, filename="sample.xlsx", sheet="Actuals"):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    for row in rows:
        worksheet.append(row)
    path = Path(tempfile.mkdtemp()) / filename
    workbook.save(path)
    return path


class DatasetParserTests(unittest.TestCase):
    def test_duplicate_budget_codes_after_normalization_are_blocked(self):
        path = _write_sheet(
            [
                ["ACT CODE", "ACT NAME", "July25 Actuals"],
                [10, "One", 1],
                ["10.0", "One again", 2],
            ]
        )
        parsed = parse_historical_dataset(path, path.name)
        self.assertFalse(parsed["can_confirm"])
        self.assertTrue(parsed["duplicate_codes"])
        self.assertTrue(any("more than once" in item["message"] for item in parsed["errors"]))

    def test_zero_and_negative_amounts_are_valid(self):
        path = _write_sheet(
            [
                ["Budget Code", "Description", "Category", "July25 Actuals"],
                ["A1", "Alpha", "Voice", 0],
                ["A2", "Beta", "Voice", -12.5],
            ]
        )
        parsed = parse_historical_dataset(path, path.name)
        self.assertTrue(parsed["can_confirm"], parsed["errors"])
        amounts = {row["budget_code"]: row["amounts"]["2025-07"] for row in parsed["rows"]}
        self.assertEqual(amounts["A1"], 0)
        self.assertEqual(amounts["A2"], -12.5)

    def test_missing_and_invalid_amounts_and_ambiguous_headers_are_reported(self):
        path = _write_sheet(
            [
                ["ACT CODE", "ACT NAME", "Category", "July25 Actuals", "Mystery Actuals", "August25 Actuals"],
                [201, "Name", "Voice", "", 1, "not-a-number"],
            ]
        )
        parsed = parse_historical_dataset(path, path.name)
        self.assertFalse(parsed["can_confirm"])
        messages = " ".join(item["message"] for item in parsed["errors"])
        self.assertNotIn("missing an amount", messages)
        self.assertIsNone(parsed["rows"][0]["amounts"]["2025-07"])
        self.assertIn("2025-07", parsed["rows"][0]["missing_months"])
        self.assertIn("non-numeric", messages)
        self.assertIn("invalid or ambiguous", messages)

    def test_ytd_columns_are_excluded(self):
        path = _write_sheet(
            [
                ["Budget Code", "Description", "Category", "July25 Actuals", "YTD July25 Actuals"],
                ["C1", "Name", "Voice", 9, 99],
            ]
        )
        parsed = parse_historical_dataset(path, path.name)
        self.assertTrue(parsed["can_confirm"], parsed["errors"])
        self.assertEqual(parsed["months"], ["2025-07"])
        self.assertEqual(parsed["rows"][0]["amounts"]["2025-07"], 9)


class DatasetMergeTests(unittest.TestCase):
    def test_new_months_are_inserted_without_deleting_older_history(self):
        existing = [
            {"budget_code": "A1", "month": "2025-06", "amount": 10, "description": "A", "category": "Voice"},
        ]
        incoming = [
            {
                "budget_code": "A1",
                "description": "A",
                "category": "Voice",
                "amounts": {"2025-07": 11},
            }
        ]
        result = classify_historical_changes(incoming, existing)
        self.assertEqual(result["new_count"], 1)
        self.assertEqual(result["changed_count"], 0)
        self.assertEqual(result["new"][0]["month"], "2025-07")

    def test_identical_data_does_not_create_duplicate_records(self):
        existing = [
            {"budget_code": "A1", "month": "2025-07", "amount": 11, "description": "A", "category": "Voice"},
        ]
        incoming = [
            {
                "budget_code": "A1",
                "description": "A",
                "category": "Voice",
                "amounts": {"2025-07": 11},
            }
        ]
        result = classify_historical_changes(incoming, existing)
        self.assertEqual(result["new_count"], 0)
        self.assertEqual(result["changed_count"], 0)
        self.assertEqual(result["unchanged_count"], 1)

    def test_changed_values_are_flagged_for_confirmation(self):
        existing = [
            {"budget_code": "A1", "month": "2025-07", "amount": 11, "description": "A", "category": "Voice"},
        ]
        incoming = [
            {
                "budget_code": "A1",
                "description": "A",
                "category": "Voice",
                "amounts": {"2025-07": 15},
            }
        ]
        result = classify_historical_changes(incoming, existing)
        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(result["changed"][0]["existing_amount"], 11)
        self.assertEqual(result["changed"][0]["amount"], 15)

    def test_edits_only_apply_intended_records(self):
        existing = [
            {"budget_code": "A1", "month": "2025-07", "amount": 11, "description": "A", "category": "Voice"},
            {"budget_code": "A2", "month": "2025-07", "amount": 20, "description": "B", "category": "Voice"},
        ]
        result = intended_edit_records(
            existing,
            [{"budget_code": "A1", "month": "2025-07", "amount": 12, "expected_amount": 11}],
        )
        self.assertEqual(len(result["changed"]), 1)
        self.assertEqual(result["changed"][0]["budget_code"], "A1")
        self.assertEqual(result["new"], [])

    def test_stale_database_value_is_a_conflict(self):
        existing = [
            {"budget_code": "A1", "month": "2025-07", "amount": 99, "description": "A", "category": "Voice"},
        ]
        result = intended_edit_records(
            existing,
            [{"budget_code": "A1", "month": "2025-07", "amount": 12, "expected_amount": 11}],
        )
        self.assertEqual(len(result["conflicts"]), 1)


if __name__ == "__main__":
    unittest.main()
