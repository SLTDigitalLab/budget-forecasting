"""Historical data management rules. These tests do not train models or touch pickles."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.services.dataset_parser import parse_historical_dataset
from app.services.dataset_workflow import (
    analyze_upload,
    apply_changes,
    manual_edit_plan,
    plan_rollback,
)


def _sheet(rows):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Actuals"
    for row in rows:
        worksheet.append(row)
    path = Path(unittest.TestCase().id().replace(".", "_"))
    return workbook, path


def _write(rows, name="partial.xlsx"):
    workbook = Workbook()
    worksheet = workbook.active
    for row in rows:
        worksheet.append(row)
    folder = Path(tempfile.mkdtemp())
    path = folder / name
    workbook.save(path)
    return path


MASTER = [
    {"budget_code": "511101", "month": "2026-06", "amount": 80, "description": "Salaries", "category": "Staff Cost"},
    {"budget_code": "511102", "month": "2026-06", "amount": 100, "description": "Fuel", "category": "Vehicle Cost"},
]


class HistoricalWorkflowTests(unittest.TestCase):
    def test_partial_and_single_budget_code_uploads_are_accepted(self):
        path = _write(
            [
                ["Budget Code", "Description", "Category", "July26 Actuals"],
                ["511101", "Salaries", "Staff Cost", 120],
            ],
            "single.xlsx",
        )
        parsed = parse_historical_dataset(path, path.name)
        self.assertTrue(parsed["can_confirm"], parsed["errors"])
        analysis = analyze_upload(parsed["rows"], MASTER, master_version=0)
        self.assertEqual(analysis["summary"]["budget_codes_uploaded"], 1)
        self.assertTrue(analysis["can_confirm"])
        self.assertEqual(analysis["summary"]["current_budget_codes"], 2)
        self.assertEqual(analysis["summary"]["budget_codes_after_save"], 2)

    def test_invalid_text_is_rejected_and_zero_negative_and_dash_follow_the_rules(self):
        path = _write(
            [
                ["Budget Code", "Description", "Category", "July26 Actuals", "August26 Actuals", "September26 Actuals", "October26 Actuals"],
                ["700001", "New account", "Voice", "abc", 0, -4, "-"],
            ],
            "values.xlsx",
        )
        parsed = parse_historical_dataset(path, path.name)
        self.assertFalse(parsed["can_confirm"])
        analysis = analyze_upload(parsed["rows"], [], master_version=0)
        self.assertGreater(analysis["summary"]["invalid_values"], 0)
        self.assertFalse(analysis["can_confirm"])
        row = analysis["rows"][0]
        self.assertEqual(row["amounts"]["2026-08"], 0)
        self.assertEqual(row["amounts"]["2026-09"], -4)
        self.assertIsNone(row["amounts"]["2026-10"])
        self.assertIn("2026-10", row["missing_months"])

    def test_blank_is_missing_and_does_not_block_a_new_code(self):
        path = _write(
            [
                ["Budget Code", "Description", "Category", "July26 Actuals", "August26 Actuals"],
                ["700002", "Another", "Voice", "", 15],
            ],
            "blank.xlsx",
        )
        parsed = parse_historical_dataset(path, path.name)
        self.assertTrue(parsed["can_confirm"], parsed["errors"])
        analysis = analyze_upload(parsed["rows"], [], master_version=0)
        self.assertGreater(analysis["summary"]["missing_values"], 0)
        self.assertEqual(analysis["new_count"], 1)
        self.assertTrue(analysis["can_confirm"])

    def test_upload_duplicate_blocks_until_one_staged_row_is_removed(self):
        path = _write(
            [
                ["Budget Code", "Description", "Category", "July26 Actuals"],
                ["511101", "Salaries", "Staff Cost", 100],
                ["511101", "Salaries", "Staff Cost", 150],
            ],
            "duplicate.xlsx",
        )
        parsed = parse_historical_dataset(path, path.name)
        self.assertFalse(parsed["can_confirm"])
        analysis = analyze_upload(parsed["rows"], MASTER, master_version=3)
        self.assertEqual(len(analysis["duplicate_conflicts"]), 1)
        self.assertEqual(len(analysis["duplicate_conflicts"][0]["records"]), 2)
        self.assertFalse(analysis["can_confirm"])
        removed = analysis["duplicate_conflicts"][0]["records"][1]["stage_id"]
        resolved = analyze_upload(parsed["rows"], MASTER, removed_stage_ids=[removed], master_version=3)
        self.assertEqual(resolved["duplicate_conflicts"], [])
        self.assertTrue(resolved["can_confirm"])
        self.assertEqual(resolved["new_count"], 1)

    def test_new_code_metadata_conflict_and_classifications(self):
        rows = [
            {
                "stage_id": "a",
                "budget_code": "800001",
                "description": "New item",
                "category": "Voice",
                "amounts": {"2026-07": 10},
                "source_row": 2,
            },
            {
                "stage_id": "b",
                "budget_code": "511101",
                "description": "Fuel",
                "category": "Vehicle Cost",
                "amounts": {"2026-06": 95},
                "source_row": 3,
            },
        ]
        blocked = analyze_upload(rows, MASTER, master_version=1)
        self.assertTrue(blocked["metadata_conflicts"])
        self.assertFalse(blocked["can_confirm"])
        self.assertEqual(blocked["summary"]["new_budget_codes"], 1)
        accepted = analyze_upload(
            rows,
            MASTER,
            metadata_decisions=[{"budget_code": "511101", "action": "accept_upload"}],
            master_version=1,
        )
        results = {(item["budget_code"], item["month"]): item["result"] for item in accepted["merge_preview"]}
        self.assertEqual(results[("800001", "2026-07")], "New")
        self.assertEqual(results[("511101", "2026-06")], "Updated")
        self.assertTrue(accepted["can_confirm"])

    def test_missing_does_not_erase_an_existing_master_value_without_a_decision(self):
        rows = [
            {
                "stage_id": "c",
                "budget_code": "511101",
                "description": "Salaries",
                "category": "Staff Cost",
                "raw_amounts": {"2026-06": "-"},
                "source_row": 2,
            }
        ]
        blocked = analyze_upload(rows, MASTER, master_version=1)
        self.assertTrue(blocked["missing_conflicts"])
        self.assertFalse(blocked["can_confirm"])
        kept = analyze_upload(
            rows,
            MASTER,
            missing_decisions=[{"budget_code": "511101", "month": "2026-06", "action": "keep_master"}],
            master_version=1,
        )
        self.assertFalse(kept["can_confirm"])
        cleared = analyze_upload(
            rows,
            MASTER,
            missing_decisions=[{"budget_code": "511101", "month": "2026-06", "action": "clear"}],
            master_version=1,
        )
        self.assertTrue(cleared["can_confirm"])
        merged = apply_changes(MASTER, cleared["changes"])
        self.assertNotIn(("511101", "2026-06"), {(item["budget_code"], item["month"]) for item in merged})
        self.assertIn(("511102", "2026-06"), {(item["budget_code"], item["month"]) for item in merged})

    def test_confirm_merge_preserves_records_absent_from_the_upload(self):
        rows = [
            {
                "stage_id": "d",
                "budget_code": "511101",
                "description": "Salaries",
                "category": "Staff Cost",
                "amounts": {"2026-07": 125},
                "source_row": 2,
            }
        ]
        analysis = analyze_upload(rows, MASTER, master_version=4)
        merged = apply_changes(MASTER, analysis["changes"])
        keys = {(item["budget_code"], item["month"]): item["amount"] for item in merged}
        self.assertEqual(keys[("511101", "2026-06")], 80)
        self.assertEqual(keys[("511102", "2026-06")], 100)
        self.assertEqual(keys[("511101", "2026-07")], 125)
        self.assertEqual(analysis["summary"]["latest_observed_month_after_save"], "2026-07")

    def test_manual_edit_requires_valid_values_and_previews_before_confirmation(self):
        invalid = manual_edit_plan(
            [{"budget_code": "511101", "month": "2026-06", "amount": "hello", "expected_amount": 80}],
            MASTER,
            master_version=2,
        )
        self.assertFalse(invalid["can_confirm"])
        valid = manual_edit_plan(
            [{"budget_code": "511101", "month": "2026-06", "amount": 135, "expected_amount": 80, "description": "Salaries", "category": "Staff Cost"}],
            MASTER,
            master_version=2,
        )
        self.assertTrue(valid["can_confirm"])
        self.assertEqual(valid["preview"][0]["result"], "Updated")
        self.assertEqual(valid["preview"][0]["current"], 80)
        self.assertEqual(valid["preview"][0]["proposed"], 135)

    def test_rollback_restores_previous_values_and_detects_later_revisions(self):
        added = plan_rollback(
            [{"id": 1, "budget_code": "511103", "month": "2026-07", "operation": "ADD", "previous_amount": None, "new_amount": 100, "action": "UPLOAD"}],
            MASTER + [{"budget_code": "511103", "month": "2026-07", "amount": 100, "description": "Travel", "category": "Travelling"}],
        )
        self.assertEqual(added["safe_removals"], 1)
        self.assertEqual(added["conflicts"], [])
        restored = plan_rollback(
            [{"id": 2, "budget_code": "511101", "month": "2026-06", "operation": "UPDATE", "previous_amount": 80, "new_amount": 100, "action": "UPLOAD"}],
            [{**MASTER[0], "amount": 100}, MASTER[1]],
        )
        self.assertEqual(restored["previous_values_to_restore"], 1)
        self.assertEqual(restored["safe"][0]["proposed_rollback"], 80)
        conflict = plan_rollback(
            [{"id": 3, "budget_code": "511101", "month": "2026-06", "operation": "UPDATE", "previous_amount": 80, "new_amount": 100, "action": "UPLOAD"}],
            [{**MASTER[0], "amount": 130}, MASTER[1]],
        )
        self.assertEqual(conflict["later_revision_conflicts"], 1)
        self.assertEqual(conflict["safe"], [])
        self.assertIsNone(conflict["conflicts"][0]["proposed_rollback"])

    def test_summary_counts_are_calculated_and_training_is_not_referenced(self):
        rows = [
            {
                "stage_id": "e",
                "budget_code": "511102",
                "description": "Fuel",
                "category": "Vehicle Cost",
                "amounts": {"2026-06": 95},
                "source_row": 2,
            }
        ]
        analysis = analyze_upload(rows, MASTER, master_version=1)
        self.assertEqual(analysis["summary"]["current_budget_codes"], 2)
        self.assertEqual(analysis["summary"]["updated_values"], 1)
        self.assertNotEqual(analysis["summary"]["budget_codes_uploaded"], 458)
        service = Path(__file__).resolve().parents[1] / "services" / "dataset_service.py"
        text = service.read_text(encoding="utf-8")
        self.assertNotIn("model_training", text)
        self.assertNotIn("all_budget_code_models", text)


if __name__ == "__main__":
    unittest.main()
