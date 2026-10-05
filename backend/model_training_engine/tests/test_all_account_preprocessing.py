"""Focused tests for all-budget-code preprocessing and calendar 80/20 splits."""

from __future__ import annotations

import sys
import unittest
from calendar import month_abbr
from pathlib import Path

import numpy as np
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from preprocessing import (  # noqa: E402
    ACCOUNT_CODE_COLUMN,
    HISTORY_START_SOURCE_FALLBACK,
    HISTORY_START_SOURCE_MAPPING,
    HISTORY_START_SOURCE_METADATA,
    KIND_INVALID,
    KIND_MISSING,
    KIND_NUMBER,
    METHOD_ACCOUNT_MEAN,
    NOT_EVALUABLE,
    OUTCOME_INVALID,
    OUTCOME_NO_DATA,
    OUTCOME_READY,
    OUTCOME_UNRESOLVED,
    OUTCOME_ZERO_POLICY,
    REASON_ALL_MISSING,
    REASON_ALL_ZERO,
    REASON_INVALID_TRAIN,
    REASON_NO_USABLE_NUMERIC,
    REASON_ZERO_AND_MISSING_ONLY,
    _collect_accounts,
    account_numeric_mean,
    build_monthly_combined_series,
    calendar_split_sizes,
    complete_data_account_codes,
    detect_monthly_columns,
    fill_missing_with_mean,
    month_label,
    parse_amount_cell,
    prepare_series_window,
    preprocess_account_panel,
    run_preprocessing,
)
from evaluation import align_holdout_by_calendar  # noqa: E402


def _labels(n_months: int, start: str = "2023-01-01") -> list[str]:
    return [month_label(ts) for ts in pd.date_range(start, periods=n_months, freq="MS")]


def _dates(n_months: int, start: str = "2023-01-01"):
    return pd.date_range(start, periods=n_months, freq="MS")


def _panel(accounts: list[dict], n_months: int, default: float = 10.0):
    labels = _labels(n_months)
    codes = [item["code"] for item in accounts]
    raw = pd.DataFrame(default, index=codes, columns=labels, dtype=float)
    kinds = pd.DataFrame(KIND_NUMBER, index=codes, columns=labels)
    for item in accounts:
        code = item["code"]
        for label, spec in (item.get("cells") or {}).items():
            if spec is None:
                raw.loc[code, label] = np.nan
                kinds.loc[code, label] = KIND_MISSING
            elif spec == "invalid":
                raw.loc[code, label] = np.nan
                kinds.loc[code, label] = KIND_INVALID
            else:
                raw.loc[code, label] = float(spec)
                kinds.loc[code, label] = KIND_NUMBER
    metadata = pd.DataFrame(
        {
            "account_name": [item.get("name", item["code"]) for item in accounts],
            "category": [item.get("category", "") for item in accounts],
            "history_start": [item.get("history_start") for item in accounts],
        },
        index=codes,
    )
    return raw, kinds, _dates(n_months), metadata


def _row(result, code=None):
    frame = result["account_outcomes"].set_index("account_code")
    if code is None:
        return frame.iloc[0]
    return frame.loc[code]


class CalendarSplitTests(unittest.TestCase):
    def test_42_month_calendar_is_33_and_9(self):
        train, test = calendar_split_sizes(42)
        self.assertEqual((train, test), (33, 9))
        dates = _dates(42)
        labels = _labels(42)
        raw, kinds, _, metadata = _panel([{"code": "A1"}], 42)
        result = preprocess_account_panel(raw, kinds, dates, metadata)
        row = _row(result)
        self.assertEqual(result["n_months"], 42)
        self.assertEqual(int(row["window_n_months"]), 42)
        self.assertEqual(int(row["train_size"]), 33)
        self.assertEqual(int(row["test_size"]), 9)
        self.assertEqual(row["train_months"][0], "Jan-2023")
        self.assertEqual(row["train_months"][-1], "Sep-2025")
        self.assertEqual(row["test_months"][0], "Oct-2025")
        self.assertEqual(row["test_months"][-1], "Jun-2026")
        self.assertEqual(labels[32], "Sep-2025")
        self.assertFalse(bool(row["history_start_verified"]))
        self.assertEqual(row["history_start_source"], HISTORY_START_SOURCE_FALLBACK)
        self.assertNotIn("42", Path(__file__).resolve().parents[1].joinpath("preprocessing.py").read_text(encoding="utf-8").split("def calendar_split_sizes")[1].split("def ")[0])

    def test_42_month_calendar_with_only_30_observed_values_still_33_9(self):
        labels = _labels(42)
        cells = {label: None for label in labels[30:]}
        raw, kinds, dates, metadata = _panel([{"code": "SPARSE", "cells": cells}], 42)
        result = preprocess_account_panel(raw, kinds, dates, metadata)
        row = _row(result)
        self.assertEqual(result["n_months"], 42)
        self.assertEqual(int(row["train_size"]), 33)
        self.assertEqual(int(row["test_size"]), 9)
        self.assertEqual(int(row["full_history"]["finite_count"]), 30)
        self.assertEqual(len(result["account_matrix"].columns), 42)

    def test_genuine_30_month_calendar_is_24_and_6(self):
        train, test = calendar_split_sizes(30)
        self.assertEqual((train, test), (24, 6))
        raw, kinds, dates, metadata = _panel([{"code": "A1"}], 30)
        result = preprocess_account_panel(raw, kinds, dates, metadata)
        row = _row(result)
        self.assertEqual(row["train_months"][-1], "Dec-2024")
        self.assertEqual(row["test_months"][0], "Jan-2025")
        self.assertEqual(row["test_months"][-1], "Jun-2025")
        self.assertEqual((int(row["train_size"]), int(row["test_size"])), (24, 6))

    def test_32_month_calendar_is_25_and_7(self):
        self.assertEqual(calendar_split_sizes(32), (25, 7))
        raw, kinds, dates, metadata = _panel([{"code": "A1"}], 32)
        result = preprocess_account_panel(raw, kinds, dates, metadata)
        row = _row(result)
        self.assertEqual((int(row["train_size"]), int(row["test_size"])), (25, 7))

    def test_extending_the_source_calendar_updates_the_split(self):
        short = _row(preprocess_account_panel(*_panel([{"code": "A1"}], 30)))
        longer = _row(preprocess_account_panel(*_panel([{"code": "A1"}], 42)))
        self.assertEqual((int(short["train_size"]), int(short["test_size"])), (24, 6))
        self.assertEqual((int(longer["train_size"]), int(longer["test_size"])), (33, 9))
        self.assertNotEqual(short["test_months"][0], longer["test_months"][0])

    def test_missing_source_monthly_columns_raise(self):
        columns = ["Jan 23 Actuals", "Feb 23 Actuals", "Apr 23 Actuals"]
        with self.assertRaises(ValueError) as caught:
            detect_monthly_columns(columns)
        message = str(caught.exception)
        self.assertIn("Mar-2023", message)
        self.assertIn("Absent monthly columns", message)

    def test_insufficient_calendar_is_rejected(self):
        with self.assertRaises(ValueError):
            calendar_split_sizes(1)


class PerAccountWindowTests(unittest.TestCase):
    def test_verified_30_month_history_on_42_month_source_is_24_6(self):
        result = preprocess_account_panel(
            *_panel([{"code": "A1", "history_start": "2024-01-01"}], 42)
        )
        row = _row(result)
        self.assertEqual(result["n_months"], 42)
        self.assertEqual(int(row["window_n_months"]), 30)
        self.assertEqual((int(row["train_size"]), int(row["test_size"])), (24, 6))
        self.assertEqual(row["history_start"], "Jan-2024")
        self.assertEqual(row["history_end"], "Jun-2026")
        self.assertEqual(row["train_months"][0], "Jan-2024")
        self.assertEqual(row["train_months"][-1], "Dec-2025")
        self.assertEqual(row["test_months"][0], "Jan-2026")
        self.assertEqual(row["test_months"][-1], "Jun-2026")
        self.assertTrue(bool(row["history_start_verified"]))
        self.assertEqual(row["history_start_source"], HISTORY_START_SOURCE_METADATA)

    def test_verified_32_month_history_is_25_7(self):
        result = preprocess_account_panel(
            *_panel([{"code": "A1"}], 42),
            account_history_start={"A1": "Nov-2023"},
        )
        row = _row(result)
        self.assertEqual(int(row["window_n_months"]), 32)
        self.assertEqual((int(row["train_size"]), int(row["test_size"])), (25, 7))
        self.assertEqual(row["history_start_source"], HISTORY_START_SOURCE_MAPPING)
        self.assertEqual(row["history_start"], "Nov-2023")

    def test_42_month_window_with_12_missing_months_is_still_33_9(self):
        labels = _labels(42)
        cells = {label: None for label in labels[:12]}
        result = preprocess_account_panel(*_panel([{"code": "A1", "cells": cells}], 42))
        row = _row(result)
        self.assertEqual(int(row["window_n_months"]), 42)
        self.assertEqual((int(row["train_size"]), int(row["test_size"])), (33, 9))
        self.assertNotEqual((int(row["train_size"]), int(row["test_size"])), (24, 6))

    def test_does_not_infer_start_from_first_nonzero_or_first_observed(self):
        labels = _labels(42)
        cells = {label: None for label in labels[:12]}
        cells.update({labels[12]: 0.0})
        result = preprocess_account_panel(*_panel([{"code": "A1", "cells": cells}], 42))
        row = _row(result)
        self.assertEqual(row["history_start"], "Jan-2023")
        self.assertFalse(bool(row["history_start_verified"]))
        self.assertEqual(int(row["window_n_months"]), 42)
        self.assertEqual((int(row["train_size"]), int(row["test_size"])), (33, 9))

    def test_trailing_blanks_do_not_shorten_the_window(self):
        labels = _labels(42)
        cells = {label: None for label in labels[-6:]}
        result = preprocess_account_panel(
            *_panel([{"code": "A1", "cells": cells, "history_start": "Jan-2023"}], 42)
        )
        row = _row(result)
        self.assertEqual(row["history_end"], "Jun-2026")
        self.assertEqual(int(row["window_n_months"]), 42)
        self.assertEqual(row["test_months"][-1], "Jun-2026")

    def test_accounts_can_have_different_split_dates_and_lengths(self):
        result = preprocess_account_panel(
            *_panel(
                [
                    {"code": "LONG"},
                    {"code": "SHORT", "history_start": "2024-01-01"},
                ],
                42,
            )
        )
        by_code = result["account_outcomes"].set_index("account_code")
        self.assertEqual((int(by_code.loc["LONG", "train_size"]), int(by_code.loc["LONG", "test_size"])), (33, 9))
        self.assertEqual((int(by_code.loc["SHORT", "train_size"]), int(by_code.loc["SHORT", "test_size"])), (24, 6))
        self.assertNotEqual(by_code.loc["LONG", "test_start"], by_code.loc["SHORT", "test_start"])
        self.assertNotEqual(len(result["prepared_train_by_account"]["LONG"]), len(result["prepared_train_by_account"]["SHORT"]))

    def test_combined_holdout_aligns_by_calendar_date(self):
        holdout = {
            "LONG": {"Oct-2025": 10.0, "Nov-2025": 11.0},
            "SHORT": {"Jan-2026": 20.0, "Feb-2026": 21.0},
        }
        aligned = align_holdout_by_calendar(holdout, _labels(42))
        self.assertEqual(aligned["aligned_actuals"].loc["LONG", "Oct-2025"], 10.0)
        self.assertTrue(np.isnan(aligned["aligned_actuals"].loc["SHORT", "Oct-2025"]))
        self.assertNotEqual(aligned["aligned_actuals"].loc["LONG", "Oct-2025"], aligned["aligned_actuals"].loc["SHORT", "Jan-2026"])
        october = aligned["coverage"].set_index("Month").loc["Oct-2025"]
        self.assertEqual(int(october["observed_accounts"]), 1)
        self.assertTrue(np.isnan(aligned["combined_where_complete"]["Oct-2025"]))


class AccountRetentionTests(unittest.TestCase):
    def test_multiple_categories_and_more_than_twelve_accounts(self):
        accounts = [
            {"code": f"A{index:02d}", "category": "Int'l Settlement" if index <= 8 else "Domestic"}
            for index in range(1, 16)
        ]
        result = preprocess_account_panel(*_panel(accounts, 12))
        self.assertEqual(len(result["account_codes"]), 15)
        self.assertGreater(len(result["account_codes"]), 12)
        categories = set(result["account_outcomes"]["category"])
        self.assertEqual(categories, {"Int'l Settlement", "Domestic"})

    def test_zeros_and_negatives_are_retained(self):
        labels = _labels(12)
        accounts = [
            {
                "code": "ZERO_NEG",
                "cells": {labels[0]: 0.0, labels[1]: -12.5, labels[2]: 8.0},
            }
        ]
        result = preprocess_account_panel(*_panel(accounts, 12, default=4.0))
        series = result["account_matrix"].loc["ZERO_NEG"]
        self.assertEqual(series.iloc[0], 0.0)
        self.assertEqual(series.iloc[1], -12.5)
        self.assertEqual(result["prepared_train_matrix"].loc["ZERO_NEG"].iloc[0], 0.0)
        self.assertEqual(result["prepared_train_matrix"].loc["ZERO_NEG"].iloc[1], -12.5)
        self.assertEqual(int(result["account_outcomes"].iloc[0]["full_history"]["zero_count"]), 1)
        self.assertEqual(int(result["account_outcomes"].iloc[0]["full_history"]["negative_count"]), 1)

    def test_every_valid_account_remains_represented(self):
        accounts = [
            {"code": "READY"},
            {"code": "ZEROS", "cells": {label: 0.0 for label in _labels(12)}},
            {"code": "GAPS", "cells": {_labels(12)[3]: None, _labels(12)[4]: None}},
            {"code": "INVALID", "cells": {_labels(12)[1]: "invalid"}},
        ]
        result = preprocess_account_panel(*_panel(accounts, 12))
        self.assertEqual(result["account_codes"], ["READY", "ZEROS", "GAPS", "INVALID"])
        self.assertEqual(len(result["account_outcomes"]), 4)
        self.assertEqual(set(result["complete_data_train_matrix"].index), {"READY", "GAPS"})

    def test_accounts_without_usable_values_cannot_enter_complete_data_training(self):
        labels = _labels(12)
        accounts = [
            {"code": "READY"},
            {"code": "NO_NUMERIC", "cells": {label: None for label in labels}},
            {"code": "INVALID", "cells": {labels[0]: "invalid"}},
        ]
        result = preprocess_account_panel(*_panel(accounts, 12))
        ready = complete_data_account_codes(result["account_outcomes"])
        self.assertEqual(ready, ["READY"])
        self.assertNotIn("NO_NUMERIC", result["complete_data_train_matrix"].index)
        self.assertNotIn("INVALID", result["complete_data_train_matrix"].index)
        self.assertIn("NO_NUMERIC", result["no_data_accounts"])
        self.assertNotIn("NO_NUMERIC", result["zero_policy_accounts"])
        self.assertIn("INVALID", result["unresolved_accounts"])
        self.assertEqual(_row(result, "NO_NUMERIC")["training_outcome"], OUTCOME_NO_DATA)
        self.assertEqual(_row(result, "NO_NUMERIC")["training_reason"], REASON_ALL_MISSING)


class MeanImputationAndLeakageTests(unittest.TestCase):
    def test_complete_accounts_remain_numerically_unchanged(self):
        raw, kinds, dates, metadata = _panel([{"code": "COMPLETE"}], 12, default=7.5)
        result = preprocess_account_panel(raw, kinds, dates, metadata)
        pd.testing.assert_frame_equal(
            result["prepared_train_matrix"],
            result["raw_train_matrix"],
        )

    def test_zero_remains_zero(self):
        values = np.array([100.0, 200.0, 0.0, np.nan, 300.0], dtype=float)
        kinds = np.array([KIND_NUMBER, KIND_NUMBER, KIND_NUMBER, KIND_MISSING, KIND_NUMBER], dtype=object)
        prepared, methods, outcome, _reason = prepare_series_window(values, kinds)
        self.assertEqual(prepared[2], 0.0)
        self.assertEqual(methods[2], "")
        self.assertEqual(outcome, OUTCOME_READY)

    def test_missing_values_are_filled_with_same_code_mean(self):
        values = np.array([100.0, 200.0, 0.0, np.nan, 300.0], dtype=float)
        kinds = np.array([KIND_NUMBER, KIND_NUMBER, KIND_NUMBER, KIND_MISSING, KIND_NUMBER], dtype=object)
        prepared, methods, outcome, _reason = prepare_series_window(values, kinds)
        self.assertAlmostEqual(float(account_numeric_mean(values, kinds)), 150.0)
        self.assertAlmostEqual(prepared[3], 150.0)
        self.assertEqual(methods[3], METHOD_ACCOUNT_MEAN)
        self.assertEqual(outcome, OUTCOME_READY)
        np.testing.assert_allclose(prepared, [100.0, 200.0, 0.0, 150.0, 300.0])

    def test_zero_is_included_and_nan_is_excluded_from_mean(self):
        values = np.array([100.0, 200.0, 0.0, np.nan, 300.0], dtype=float)
        kinds = np.array([KIND_NUMBER, KIND_NUMBER, KIND_NUMBER, KIND_MISSING, KIND_NUMBER], dtype=object)
        self.assertAlmostEqual(account_numeric_mean(values, kinds), 150.0)

    def test_all_zero_with_missing_becomes_all_zero(self):
        values = np.array([0.0, 0.0, np.nan, 0.0], dtype=float)
        kinds = np.array([KIND_NUMBER, KIND_NUMBER, KIND_MISSING, KIND_NUMBER], dtype=object)
        prepared, _methods, outcome, reason = prepare_series_window(values, kinds)
        np.testing.assert_allclose(prepared, [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(outcome, OUTCOME_ZERO_POLICY)
        self.assertEqual(reason, REASON_ZERO_AND_MISSING_ONLY)

    def test_one_budget_code_mean_never_fills_another(self):
        labels = _labels(12)
        later_missing = {label: None for label in labels[3:]}
        accounts = [
            {"code": "A", "cells": {labels[0]: 10.0, labels[1]: 20.0, labels[2]: None, **later_missing}},
            {"code": "B", "cells": {labels[0]: 100.0, labels[1]: 200.0, labels[2]: None, **later_missing}},
        ]
        result = preprocess_account_panel(*_panel(accounts, 12, default=999.0))
        prepared_a = result["prepared_train_by_account"]["A"]
        prepared_b = result["prepared_train_by_account"]["B"]
        self.assertAlmostEqual(prepared_a.iloc[2], 15.0)
        self.assertAlmostEqual(prepared_b.iloc[2], 150.0)
        self.assertNotAlmostEqual(prepared_a.iloc[2], prepared_b.iloc[2])

    def test_ordinary_missing_is_no_longer_unresolved(self):
        labels = _labels(12)
        accounts = [{"code": "OTHER", "cells": {labels[2]: None}}]
        result = preprocess_account_panel(*_panel(accounts, 12, default=10.0))
        self.assertFalse(np.isnan(result["prepared_train_matrix"].loc["OTHER", labels[2]]))
        self.assertEqual(result["account_outcomes"].iloc[0]["training_outcome"], OUTCOME_READY)
        self.assertNotEqual(result["account_outcomes"].iloc[0]["training_reason"], REASON_NO_USABLE_NUMERIC)

    def test_long_gaps_are_filled_with_the_same_code_mean(self):
        labels = _labels(12)
        accounts = [{"code": "REG", "cells": {labels[2]: None, labels[3]: None, labels[4]: None}}]
        result = preprocess_account_panel(*_panel(accounts, 12, default=10.0))
        prepared = result["prepared_train_matrix"].loc["REG"]
        self.assertAlmostEqual(prepared.iloc[2], 10.0)
        self.assertAlmostEqual(prepared.iloc[3], 10.0)
        self.assertAlmostEqual(prepared.iloc[4], 10.0)
        self.assertTrue(bool(result["imputation_mask"].loc["REG"].any()))
        self.assertEqual(result["account_outcomes"].iloc[0]["training_outcome"], OUTCOME_READY)

    def test_evaluation_imputation_uses_train_mean_only(self):
        n_months = 10
        train_size, _test_size = calendar_split_sizes(n_months)
        labels = _labels(n_months)
        gap_label = labels[train_size - 1]
        test_first = labels[train_size]
        accounts = [{"code": "REG", "cells": {gap_label: None}}]
        raw, kinds, dates, metadata = _panel(accounts, n_months, default=5.0)
        raw.loc["REG", test_first] = 99.0
        result = preprocess_account_panel(raw, kinds, dates, metadata)
        train_mean = result["account_outcomes"].iloc[0]["train_imputation_mean"]
        observed_train = [5.0] * (train_size - 1)
        expected_mean = float(np.mean(observed_train))
        self.assertAlmostEqual(train_mean, expected_mean)
        self.assertAlmostEqual(result["prepared_train_matrix"].loc["REG", gap_label], expected_mean)
        self.assertEqual(result["raw_test_matrix"].loc["REG", test_first], 99.0)
        self.assertEqual(result["prepared_test_by_account"]["REG"].iloc[0], 99.0)
        self.assertNotAlmostEqual(train_mean, 99.0)
        self.assertEqual(result["account_outcomes"].iloc[0]["training_outcome"], OUTCOME_READY)

    def test_test_values_do_not_leak_into_train_mean(self):
        n_months = 12
        labels = _labels(n_months)
        split = calendar_split_sizes(n_months)[0]
        accounts = [{"code": "A1", "cells": {labels[1]: 0.0, labels[2]: None}}]
        first = preprocess_account_panel(*_panel(accounts, n_months, default=3.0))
        raw, kinds, dates, metadata = _panel(accounts, n_months, default=3.0)
        raw.loc["A1", labels[split]] = 999.0
        second = preprocess_account_panel(raw, kinds, dates, metadata)
        pd.testing.assert_frame_equal(first["prepared_train_matrix"], second["prepared_train_matrix"])
        self.assertEqual(
            first["account_outcomes"].iloc[0]["train_imputation_mean"],
            second["account_outcomes"].iloc[0]["train_imputation_mean"],
        )
        self.assertEqual(
            first["account_outcomes"].iloc[0]["training_outcome"],
            second["account_outcomes"].iloc[0]["training_outcome"],
        )

    def test_test_actuals_remain_raw_for_scoring(self):
        labels = _labels(12)
        split = calendar_split_sizes(12)[0]
        accounts = [{"code": "REG", "cells": {labels[2]: None, labels[split]: None}}]
        result = preprocess_account_panel(*_panel(accounts, 12, default=8.0))
        self.assertTrue(np.isnan(result["raw_test_matrix"].loc["REG", labels[split]]))
        self.assertFalse(bool(result["imputation_mask"].loc["REG", labels[split]]))
        self.assertTrue(np.isfinite(result["prepared_test_by_account"]["REG"].iloc[0]))

    def test_no_usable_numeric_observation_is_not_invented(self):
        values = np.array([np.nan, np.nan, np.nan], dtype=float)
        kinds = np.array([KIND_MISSING, KIND_MISSING, KIND_MISSING], dtype=object)
        prepared, _methods, outcome, reason = prepare_series_window(values, kinds)
        self.assertTrue(np.isnan(prepared).all())
        self.assertEqual(outcome, OUTCOME_NO_DATA)
        self.assertEqual(reason, REASON_ALL_MISSING)
        self.assertIsNone(account_numeric_mean(values, kinds))

    def test_full_history_mean_is_used_for_production_preparation(self):
        values = np.array([100.0, 200.0, np.nan, 400.0], dtype=float)
        kinds = np.array([KIND_NUMBER, KIND_NUMBER, KIND_MISSING, KIND_NUMBER], dtype=object)
        train_prepared, _m, _o, _r = prepare_series_window(values[:3], kinds[:3])
        full_prepared, _m2, _o2, _r2 = prepare_series_window(values, kinds)
        self.assertAlmostEqual(train_prepared[2], 150.0)
        self.assertAlmostEqual(full_prepared[2], 700.0 / 3.0)
        self.assertNotAlmostEqual(train_prepared[2], full_prepared[2])


class ZeroPolicyAndInvalidTests(unittest.TestCase):
    def test_all_zero_full_history_is_zero_policy(self):
        labels = _labels(12)
        accounts = [{"code": "ALL_ZERO", "cells": {label: 0.0 for label in labels}}]
        result = preprocess_account_panel(*_panel(accounts, 12, default=0.0))
        row = _row(result, "ALL_ZERO")
        self.assertEqual(row["training_outcome"], OUTCOME_ZERO_POLICY)
        self.assertEqual(row["training_reason"], REASON_ALL_ZERO)
        self.assertIn("ALL_ZERO", result["zero_policy_accounts"])

    def test_zero_and_missing_only_is_zero_policy_after_mean_fill(self):
        labels = _labels(12)
        cells = {label: 0.0 if index % 2 == 0 else None for index, label in enumerate(labels)}
        result = preprocess_account_panel(*_panel([{"code": "ZM", "cells": cells}], 12, default=0.0))
        prepared = result["prepared_train_by_account"]["ZM"]
        self.assertTrue(np.all(np.asarray(prepared, dtype=float) == 0.0))
        row = _row(result, "ZM")
        self.assertEqual(row["training_outcome"], OUTCOME_ZERO_POLICY)
        self.assertEqual(row["training_reason"], REASON_ZERO_AND_MISSING_ONLY)

    def test_all_missing_is_no_data_not_zero_policy(self):
        labels = _labels(12)
        accounts = [{"code": "ALL_MISSING", "cells": {label: None for label in labels}}]
        result = preprocess_account_panel(*_panel(accounts, 12))
        row = _row(result, "ALL_MISSING")
        self.assertEqual(row["training_outcome"], OUTCOME_NO_DATA)
        self.assertEqual(row["training_reason"], REASON_ALL_MISSING)
        self.assertNotIn("ALL_MISSING", result["zero_policy_accounts"])
        self.assertIn("ALL_MISSING", result["no_data_accounts"])

    def test_nonzero_actuals_remove_zero_policy_on_the_next_run(self):
        labels = _labels(12)
        zeros = preprocess_account_panel(
            *_panel([{"code": "SERIES", "cells": {label: 0.0 for label in labels}}], 12, default=0.0)
        )
        self.assertEqual(_row(zeros, "SERIES")["training_outcome"], OUTCOME_ZERO_POLICY)
        updated_cells = {label: 0.0 for label in labels}
        updated_cells[labels[-2]] = 50000.0
        updated_cells[labels[-1]] = 75000.0
        updated = preprocess_account_panel(
            *_panel([{"code": "SERIES", "cells": updated_cells}], 12, default=0.0)
        )
        self.assertEqual(_row(updated, "SERIES")["training_outcome"], OUTCOME_READY)
        self.assertNotIn("SERIES", updated["zero_policy_accounts"])
        self.assertIn("SERIES", updated["ready_accounts"])

    def test_zero_policy_is_not_hardcoded_to_budget_codes(self):
        engine = Path(__file__).resolve().parents[1]
        backend = engine.parent
        sources = [
            engine / "preprocessing.py",
            engine / "model_training.py",
            engine / "evaluation.py",
            backend / "app" / "services" / "forecasting_service.py",
        ]
        for path in sources:
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(
                text,
                r"""["']511\d{3}["']\s*:\s*["']ZERO_POLICY""",
                msg=f"{path.name} must not hardcode Budget Codes to ZERO_POLICY",
            )
            self.assertNotIn('511101 -> ZERO_POLICY', text)

    def test_mixed_zeros_and_spend_are_ready(self):
        labels = _labels(12)
        cells = {labels[0]: 0.0, labels[1]: 100.0, labels[2]: 0.0, labels[3]: 250.0, labels[4]: None, labels[5]: 400.0}
        result = preprocess_account_panel(*_panel([{"code": "MIXED", "cells": cells}], 12, default=10.0))
        self.assertEqual(_row(result, "MIXED")["training_outcome"], OUTCOME_READY)
        self.assertEqual(result["prepared_train_by_account"]["MIXED"].iloc[0], 0.0)

    def test_invalid_entries_remain_distinguishable_from_blanks(self):
        blank = parse_amount_cell(None)
        invalid = parse_amount_cell("#DIV/0!")
        text = parse_amount_cell("not-a-number")
        infinite = parse_amount_cell(np.inf)
        self.assertEqual(blank["kind"], KIND_MISSING)
        self.assertEqual(invalid["kind"], KIND_INVALID)
        self.assertEqual(text["kind"], KIND_INVALID)
        self.assertEqual(infinite["kind"], KIND_INVALID)
        labels = _labels(12)
        accounts = [
            {"code": "BLANK", "cells": {labels[1]: None}},
            {"code": "BAD", "cells": {labels[1]: "invalid"}},
        ]
        result = preprocess_account_panel(*_panel(accounts, 12))
        self.assertEqual(result["kind_matrix"].loc["BLANK", labels[1]], KIND_MISSING)
        self.assertEqual(result["kind_matrix"].loc["BAD", labels[1]], KIND_INVALID)
        self.assertEqual(
            result["account_outcomes"].set_index("account_code").loc["BAD", "training_outcome"],
            OUTCOME_INVALID,
        )
        self.assertEqual(
            result["account_outcomes"].set_index("account_code").loc["BAD", "training_reason"],
            REASON_INVALID_TRAIN,
        )

    def test_invalid_test_entries_do_not_change_training_routing(self):
        n_months = 12
        labels = _labels(n_months)
        split = calendar_split_sizes(n_months)[0]
        accounts = [{"code": "A1", "cells": {labels[split]: "invalid"}}]
        result = preprocess_account_panel(*_panel(accounts, n_months, default=4.0))
        row = result["account_outcomes"].iloc[0]
        self.assertEqual(row["training_outcome"], OUTCOME_READY)
        self.assertFalse(bool(result["observed_test_mask"].loc["A1", labels[split]]))

    def test_no_observed_test_actuals_are_not_evaluable(self):
        n_months = 12
        labels = _labels(n_months)
        split = calendar_split_sizes(n_months)[0]
        cells = {label: None for label in labels[split:]}
        result = preprocess_account_panel(*_panel([{"code": "A1", "cells": cells}], n_months))
        row = result["account_outcomes"].iloc[0]
        self.assertEqual(row["evaluation_status"], NOT_EVALUABLE)
        self.assertEqual(row["observed_test_count"], 0)


class CombinedSeriesAndCollectionTests(unittest.TestCase):
    def test_combined_series_does_not_treat_missing_as_zero(self):
        labels = _labels(6)
        frame = pd.DataFrame(
            {
                ACCOUNT_CODE_COLUMN: ["A1", "A2"],
                labels[0]: [10.0, np.nan],
                labels[1]: [5.0, 5.0],
            }
        )
        for label in labels[2:]:
            frame[label] = [1.0, 1.0]
        combined = build_monthly_combined_series(frame, ["A1", "A2"], labels)
        self.assertTrue(np.isnan(combined["monthly_history"][0]))
        self.assertEqual(combined["monthly_history"][1], 10.0)
        self.assertFalse(combined["history_coverage"][0]["complete"])
        self.assertEqual(combined["history_coverage"][0]["missing_accounts"], 1)

    def test_duplicate_normalized_codes_raise(self):
        dates = _dates(3)
        monthly_columns = [f"{month_abbr[ts.month]} {str(ts.year)[2:]} Actuals" for ts in dates]
        labels = [month_label(ts) for ts in dates]
        frame = pd.DataFrame(
            {
                "ACT CODE": ["1001", "1001.0"],
                "ACT NAME": ["One", "One duplicate"],
                "_source_row": [3, 4],
                monthly_columns[0]: [1, 2],
                monthly_columns[1]: [1, 2],
                monthly_columns[2]: [1, 2],
            }
        )
        loaded = {
            "df": frame,
            "account_code_col": "ACT CODE",
            "account_name_col": "ACT NAME",
            "monthly_columns": monthly_columns,
            "month_dates": list(dates),
            "excel_errors": {},
            "sheet_name": "Sheet1",
        }
        with self.assertRaises(ValueError) as caught:
            _collect_accounts(loaded)
        self.assertIn("Duplicate normalized account codes", str(caught.exception))

    def test_missing_category_does_not_discard_an_account(self):
        dates = _dates(4)
        monthly_columns = [f"{month_abbr[ts.month]} {str(ts.year)[2:]} Actuals" for ts in dates]
        frame = pd.DataFrame(
            {
                "ACT CODE": ["ABC-1"],
                "ACT NAME": ["No category account"],
                "_source_row": [3],
                **{column: [5.0] for column in monthly_columns},
            }
        )
        collected = _collect_accounts(
            {
                "df": frame,
                "account_code_col": "ACT CODE",
                "account_name_col": "ACT NAME",
                "monthly_columns": monthly_columns,
                "month_dates": list(dates),
                "excel_errors": {},
                "sheet_name": "Sheet1",
            }
        )
        self.assertEqual(list(collected["raw_matrix"].index), ["ABC-1"])
        self.assertEqual(collected["metadata"].loc["ABC-1", "category"], "")

    def test_mean_helper_fills_leading_and_trailing_gaps_from_same_series(self):
        values = np.array([np.nan, 2.0, np.nan, 4.0, np.nan], dtype=float)
        kinds = np.array([KIND_MISSING, KIND_NUMBER, KIND_MISSING, KIND_NUMBER, KIND_MISSING])
        mean_value = account_numeric_mean(values, kinds)
        prepared, methods, fills = fill_missing_with_mean(values, kinds, mean_value)
        self.assertAlmostEqual(mean_value, 3.0)
        self.assertAlmostEqual(prepared[0], 3.0)
        self.assertAlmostEqual(prepared[2], 3.0)
        self.assertAlmostEqual(prepared[4], 3.0)
        self.assertEqual(prepared[1], 2.0)
        self.assertEqual(methods[0], METHOD_ACCOUNT_MEAN)
        self.assertEqual(len(fills), 3)


class SourceDatasetPreprocessingCheck(unittest.TestCase):
    def test_preprocessing_only_on_available_source_dataset(self):
        source = ENGINE_DIR / "dataset" / "original_actual_data.xlsx"
        if not source.exists():
            self.skipTest(f"Source workbook is not available at {source}")
        result = run_preprocessing(str(source))
        outcomes = result["account_outcomes"]
        self.assertGreater(result["n_months"], 1)
        self.assertTrue((outcomes["window_n_months"] == outcomes["train_size"] + outcomes["test_size"]).all())
        self.assertEqual(len(result["account_codes"]), result["account_count"])
        split_sizes = outcomes[["train_size", "test_size", "window_n_months"]].drop_duplicates()
        print("SOURCE_PREPROCESS_HISTORY_START", result["history_start"])
        print("SOURCE_PREPROCESS_HISTORY_END", result["history_end"])
        print("SOURCE_PREPROCESS_N_MONTHS", result["n_months"])
        print("SOURCE_PREPROCESS_ACCOUNT_COUNT", result["account_count"])
        print("SOURCE_PREPROCESS_OUTCOMES", result["outcome_counts"])
        print("SOURCE_PREPROCESS_UNVERIFIED_STARTS", len(result["unverified_history_start_accounts"]))
        print("SOURCE_PREPROCESS_SPLIT_SIZES", split_sizes.to_dict("records"))
        print("SOURCE_PREPROCESS_START_SOURCES", outcomes["history_start_source"].value_counts().to_dict())


if __name__ == "__main__":
    unittest.main()
