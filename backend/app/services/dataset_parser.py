"""Wide-format historical dataset parsing and validation."""

from __future__ import annotations

import re
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from openpyxl import load_workbook

from app.config import BACKEND_DIR

ENGINE_DIR = BACKEND_DIR / "model_training_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from app.services.submission_state import (  # noqa: E402
    SUPPLIED_CELL_MESSAGE,
    classify_supplied_upload_cell,
    classify_upload_cell,
)
from preprocessing import (  # noqa: E402
    KIND_INVALID,
    KIND_MISSING,
    KIND_NUMBER,
    assign_categories,
    category_order_from_headings,
    clean_account_code,
    detect_category_headings_from_workbook,
    is_excluded_actuals_column,
    label_key,
    normalize_label,
    parse_amount_cell,
    parse_month_header,
)

CODE_ALIASES = ("ACT CODE", "BUDGET CODE", "ACCOUNT CODE")
NAME_ALIASES = ("ACT NAME", "DESCRIPTION", "ACCOUNT NAME")
CATEGORY_ALIASES = ("CATEGORY",)
MAX_HEADER_SCAN_ROWS = 100
MAX_SHEETS = 40
MAX_ROWS = 20000
MAX_COLUMNS = 250


class DatasetParseError(Exception):
    pass


def iso_month(timestamp: pd.Timestamp) -> str:
    value = pd.Timestamp(timestamp).replace(day=1)
    return f"{int(value.year):04d}-{int(value.month):02d}"


def actuals_header_for_month(month: str) -> str:
    year, month_number = str(month).split("-")
    name = pd.Timestamp(year=int(year), month=int(month_number), day=1).strftime("%B")
    return f"{name}{year[2:]} Actuals"


def _lookup_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    lookup = {label_key(column): column for column in columns}
    for alias in aliases:
        if alias in lookup:
            return lookup[alias]
    return None


def detect_upload_month_columns(column_names: list[str]) -> tuple[list[str], list[pd.Timestamp], list[dict]]:
    detected: list[tuple[str, pd.Timestamp]] = []
    seen: dict[pd.Timestamp, str] = {}
    errors: list[dict] = []
    for column in column_names:
        label = normalize_label(column)
        if not label:
            continue
        excluded = is_excluded_actuals_column(label)
        timestamp = parse_month_header(label)
        looks_monthly = "actual" in label.lower() or bool(pd.notna(timestamp))
        if excluded:
            continue
        if looks_monthly and pd.isna(timestamp):
            errors.append(
                {
                    "sheet": "",
                    "row": 1,
                    "column": label,
                    "message": f"Monthly header {label!r} is invalid or ambiguous.",
                    "budget_code": "",
                }
            )
            continue
        if pd.isna(timestamp):
            continue
        month = pd.Timestamp(timestamp).replace(day=1)
        if month in seen:
            errors.append(
                {
                    "sheet": "",
                    "row": 1,
                    "column": label,
                    "message": (
                        f"Multiple columns resolve to {iso_month(month)}: "
                        f"{seen[month]!r} and {label!r}."
                    ),
                    "budget_code": "",
                }
            )
            continue
        seen[month] = label
        detected.append((label, month))
    detected.sort(key=lambda item: item[1])
    return [item[0] for item in detected], [item[1] for item in detected], errors


def _excel_row_number(header_row_idx: int, data_index: int) -> int:
    return header_row_idx + 2 + int(data_index)


def _is_blank(value: Any) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    return str(value).strip() == ""


def _is_formula(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("=")


# Blank-code workbook rows that are totals/notes — not category headings and not accounts.
# Do not treat every blank ACT CODE as skippable; genuine accounts still fail validation.
_SUMMARY_OR_NOTE_LABEL = re.compile(
    r"(?ix)"
    r"(?:^|\b)(?:"
    r"grand\s*totals?"
    r"|sub[\s-]*totals?"
    r"|totals?"
    r"|exclusions?"
    r")(?:\b|$)"
)
_CELL_REF = re.compile(r"\$?[A-Za-z]{1,3}\$?\d+")
_ACCOUNT_LOOKUP_FORMULA = re.compile(r"(?i)\b(?:VLOOKUP|XLOOKUP|INDEX|MATCH|HLOOKUP)\b")


def is_summary_or_note_label(description: Any) -> bool:
    """True for total/subtotal/exclusion labels that are not budget accounts."""
    text = "" if description is None else str(description).strip()
    if not text:
        return False
    return bool(_SUMMARY_OR_NOTE_LABEL.search(text))


def _formula_is_cross_row_aggregation(formula: str) -> bool:
    """Detect SUM/additive rollups; account VLOOKUPs are not aggregations."""
    text = str(formula or "").strip()
    if not text.startswith("="):
        return False
    body = text[1:]
    if _ACCOUNT_LOOKUP_FORMULA.search(body):
        return False
    if re.search(r"(?i)\b(?:SUM|AVERAGE|SUBTOTAL|AGGREGATE)\s*\(", body):
        return True
    refs = _CELL_REF.findall(body)
    return len(refs) >= 2 and ("+" in body or "-" in body)


def _month_values_look_like_non_account_rollups(month_values: list[Any]) -> bool:
    """True when blank-code month cells are dominated by cross-row rollup formulas.

    Some workbook subtotals paste a few months as literals while leaving the rest
    as SUM/additive formulas. Require aggregations to outnumber literals and reject
    account-style lookup formulas so genuine missing Budget Codes still fail.
    """
    nonempty = [value for value in month_values if not _is_blank(value)]
    if not nonempty:
        return False
    aggregations = 0
    lookups = 0
    other = 0
    for value in nonempty:
        if _is_formula(value):
            text = str(value)
            if _ACCOUNT_LOOKUP_FORMULA.search(text):
                lookups += 1
            elif _formula_is_cross_row_aggregation(text):
                aggregations += 1
            else:
                other += 1
        else:
            other += 1
    if lookups or aggregations == 0:
        return False
    return aggregations >= max(2, (aggregations + other + 1) // 2)


def discover_non_account_blank_code_rows(
    path: Path,
    sheet_name: str,
    header_row_idx: int,
    code_column: str,
    name_column: str,
    month_columns: list[str],
    *,
    end_excel_row: int | None = None,
) -> set[int]:
    """Excel rows with a blank Budget Code that are summary/note lines, not accounts.

    Category headings are handled separately (bold name + empty actuals) and must
    not be reclassified here — notes/summaries must not interrupt category ffill.
    Genuine account rows that simply omit their Budget Code are not returned.
    """
    workbook = load_workbook(path, read_only=False, data_only=False)
    try:
        if sheet_name not in workbook.sheetnames:
            return set()
        worksheet = workbook[sheet_name]
        header_row = int(header_row_idx) + 1
        max_column = int(worksheet.max_column or 0)
        headers = [
            normalize_label(worksheet.cell(header_row, column).value)
            for column in range(1, max_column + 1)
        ]
        try:
            code_idx = headers.index(normalize_label(code_column))
            name_idx = headers.index(normalize_label(name_column))
        except ValueError:
            return set()
        month_indices = [
            headers.index(normalize_label(column))
            for column in month_columns
            if normalize_label(column) in headers
        ]
        first_data_row = header_row + 1
        last_data_row = int(end_excel_row or worksheet.max_row or 0)
        if last_data_row < first_data_row:
            return set()
        skipped: set[int] = set()
        for excel_row in range(first_data_row, last_data_row + 1):
            code_value = worksheet.cell(excel_row, code_idx + 1).value
            name_value = worksheet.cell(excel_row, name_idx + 1).value
            if not _is_blank(code_value) or _is_formula(code_value):
                continue
            description = "" if name_value is None else str(name_value).strip()
            if not description or _is_formula(name_value):
                continue
            if is_summary_or_note_label(description):
                skipped.add(int(excel_row))
                continue
            month_values = [worksheet.cell(excel_row, index + 1).value for index in month_indices]
            if _month_values_look_like_non_account_rollups(month_values):
                skipped.add(int(excel_row))
        return skipped
    finally:
        workbook.close()


def _cached_financial_value(value: Any) -> Any:
    """Return a formula cache the upload validator can classify.

    A finite number, including zero and negatives, and '-' are kept exactly.
    An empty or whitespace-only result is kept as a blank for strict validation.
    Excel errors (#N/A, #VALUE!, …) and a missing cache are omitted so they
    cannot replace a stored actual or be mistaken for an unresolved blank cell.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in {float("inf"), float("-inf")}:
            return None
        return number
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return ""
        if text == "-":
            return "-"
        if text.startswith("#"):
            return None
    return None


def _resolve_month_formulas(
    path: Path,
    sheet_name: str,
    header_row_idx: int,
    month_columns: list[str],
) -> tuple[dict[tuple[int, str], Any], set[tuple[int, str]]]:
    """Read calculated values stored beside monthly formulas.

    A finite number, including zero and negatives, and a standalone '-' are kept
    exactly. An empty formula result is kept as a blank. Excel errors and a
    formula with no usable cache are omitted so they cannot replace a stored
    actual.
    """
    resolved: dict[tuple[int, str], Any] = {}
    omitted: set[tuple[int, str]] = set()
    if not month_columns:
        return resolved, omitted
    formula_book = load_workbook(path, read_only=True, data_only=False)
    value_book = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet_name not in formula_book.sheetnames or sheet_name not in value_book.sheetnames:
            return resolved, omitted
        formula_rows = formula_book[sheet_name].iter_rows(min_row=header_row_idx + 1)
        value_rows = value_book[sheet_name].iter_rows(min_row=header_row_idx + 1)
        header = [normalize_label(cell.value) for cell in next(formula_rows)]
        next(value_rows)
        col_index = {label: idx for idx, label in enumerate(header) if label}
        targets = [column for column in month_columns if column in col_index]
        for excel_row, (formula_row, value_row) in enumerate(
            zip(formula_rows, value_rows),
            start=header_row_idx + 2,
        ):
            for column in targets:
                idx = col_index[column]
                if idx >= len(formula_row) or not _is_formula(formula_row[idx].value):
                    continue
                cached = value_row[idx].value if idx < len(value_row) else None
                usable = _cached_financial_value(cached)
                if usable is None:
                    omitted.add((excel_row, column))
                else:
                    resolved[(excel_row, column)] = usable
    finally:
        formula_book.close()
        value_book.close()
    return resolved, omitted


def _workbook_formula_errors(path: Path, sheet_name: str, header_row_idx: int, columns: list[str]) -> list[dict]:
    errors: list[dict] = []
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        if sheet_name not in workbook.sheetnames:
            return errors
        worksheet = workbook[sheet_name]
        header = [normalize_label(cell.value) for cell in next(worksheet.iter_rows(min_row=header_row_idx + 1, max_row=header_row_idx + 1))]
        col_index = {label: idx for idx, label in enumerate(header) if label}
        for excel_row, row in enumerate(
            worksheet.iter_rows(min_row=header_row_idx + 2, max_row=header_row_idx + 1 + MAX_ROWS),
            start=header_row_idx + 2,
        ):
            for column in columns:
                idx = col_index.get(column)
                if idx is None or idx >= len(row):
                    continue
                cell = row[idx]
                if cell.value is None:
                    continue
                if getattr(cell, "data_type", "") == "f" or (
                    isinstance(cell.value, str) and str(cell.value).startswith("=")
                ):
                    errors.append(
                        {
                            "sheet": sheet_name,
                            "row": excel_row,
                            "column": column,
                            "message": (
                                f"Cell {column} on row {excel_row} contains a formula or unsupported workbook feature. "
                                "Replace it with a numeric actual before saving."
                            ),
                            "budget_code": "",
                        }
                    )
    finally:
        workbook.close()
    return errors


def list_candidate_sheets(frame_by_sheet: dict[str, pd.DataFrame]) -> list[dict]:
    candidates = []
    for sheet_name, preview in frame_by_sheet.items():
        for idx, row in preview.iterrows():
            labels = [label_key(value) for value in row.tolist()]
            if any(alias in labels for alias in CODE_ALIASES):
                candidates.append({"sheet": sheet_name, "header_row": int(idx) + 1})
                break
    return candidates


def _read_tabular(path: Path, filename: str, sheet_name: str | None) -> dict[str, Any]:
    suffix = path.suffix.lower()
    original_name = filename.lower()
    if suffix in {".csv"} or original_name.endswith(".csv"):
        raw = pd.read_csv(path, header=None, dtype=object, nrows=MAX_ROWS + MAX_HEADER_SCAN_ROWS)
        return {
            "kind": "csv",
            "sheets": ["Sheet1"],
            "selected_sheet": "Sheet1",
            "raw": raw,
            "header_row_idx": 0,
            "needs_sheet_selection": False,
            "candidates": [{"sheet": "Sheet1", "header_row": 1}],
        }
    if suffix not in {".xlsx", ".xlsm"} and not original_name.endswith((".xlsx", ".xlsm")):
        raise DatasetParseError("Only .xlsx and .csv files are supported.")

    xls = pd.ExcelFile(path)
    sheets = list(xls.sheet_names)[:MAX_SHEETS]
    previews = {}
    candidates = []
    for candidate in sheets:
        preview = pd.read_excel(xls, sheet_name=candidate, header=None, dtype=object, nrows=MAX_HEADER_SCAN_ROWS)
        previews[candidate] = preview
        found = False
        for idx, row in preview.iterrows():
            labels = [label_key(value) for value in row.tolist()]
            if any(alias in labels for alias in CODE_ALIASES):
                candidates.append({"sheet": candidate, "header_row": int(idx) + 1})
                found = True
                break
        if not found:
            continue
    if not candidates:
        raise DatasetParseError("No worksheet contained a Budget Code / ACT CODE header in the first 100 rows.")
    if sheet_name:
        match = next((item for item in candidates if item["sheet"] == sheet_name), None)
        if match is None:
            raise DatasetParseError(f"Sheet {sheet_name!r} is not a valid historical dataset sheet.")
        selected = match["sheet"]
    elif len(candidates) > 1:
        return {
            "kind": "xlsx",
            "sheets": sheets,
            "candidates": candidates,
            "needs_sheet_selection": True,
            "selected_sheet": None,
            "raw": None,
            "header_row_idx": None,
        }
    else:
        selected = candidates[0]["sheet"]

    raw = pd.read_excel(path, sheet_name=selected, header=None, dtype=object, nrows=MAX_ROWS + MAX_HEADER_SCAN_ROWS)
    header_row_idx = next(item["header_row"] for item in candidates if item["sheet"] == selected) - 1
    return {
        "kind": "xlsx",
        "sheets": sheets,
        "candidates": candidates,
        "needs_sheet_selection": False,
        "selected_sheet": selected,
        "raw": raw,
        "header_row_idx": header_row_idx,
    }


def parse_historical_dataset(
    path: Path,
    filename: str,
    sheet_name: str | None = None,
    *,
    strict_supplied_cells: bool = True,
) -> dict[str, Any]:
    loaded = _read_tabular(path, filename, sheet_name)
    if loaded.get("needs_sheet_selection"):
        return {
            "ok": False,
            "needs_sheet_selection": True,
            "sheets": loaded["candidates"],
            "errors": [
                {
                    "sheet": "",
                    "row": None,
                    "column": "",
                    "message": "Multiple sheets look like historical datasets. Select one sheet to validate.",
                    "budget_code": "",
                }
            ],
            "rows": [],
            "months": [],
            "duplicate_codes": [],
            "can_confirm": False,
            "selected_sheet": None,
            "filename": filename,
        }

    raw = loaded["raw"]
    header_row_idx = int(loaded["header_row_idx"])
    sheet = loaded["selected_sheet"]
    headers = [normalize_label(value) for value in raw.iloc[header_row_idx].tolist()]
    if len(headers) > MAX_COLUMNS:
        headers = headers[:MAX_COLUMNS]
    df = raw.iloc[header_row_idx + 1 : header_row_idx + 1 + MAX_ROWS].copy()
    df.columns = headers
    df = df.loc[:, [column for column in df.columns if str(column).strip() != ""]]
    df = df.reset_index(drop=True)

    errors: list[dict] = []
    code_col = _lookup_column(df.columns.tolist(), CODE_ALIASES)
    name_col = _lookup_column(df.columns.tolist(), NAME_ALIASES)
    category_col = _lookup_column(df.columns.tolist(), CATEGORY_ALIASES)
    if not code_col:
        errors.append(
            {
                "sheet": sheet,
                "row": header_row_idx + 1,
                "column": "Budget Code",
                "message": "Required column Budget Code / ACT CODE is missing.",
                "budget_code": "",
            }
        )
    if not name_col:
        errors.append(
            {
                "sheet": sheet,
                "row": header_row_idx + 1,
                "column": "Description",
                "message": "Required column Description / ACT NAME is missing.",
                "budget_code": "",
            }
        )
    month_columns, month_dates, month_errors = detect_upload_month_columns(df.columns.tolist())
    for item in month_errors:
        item["sheet"] = sheet
        errors.append(item)
    if not month_columns and not month_errors:
        errors.append(
            {
                "sheet": sheet,
                "row": header_row_idx + 1,
                "column": "",
                "message": "No monthly actual columns were detected. Use headers such as July25 Actuals.",
                "budget_code": "",
            }
        )
    if loaded["kind"] == "xlsx":
        inspect_columns = [column for column in [code_col, name_col] if column]
        errors.extend(_workbook_formula_errors(path, sheet, header_row_idx, inspect_columns))

    if errors and (not code_col or not name_col or not month_columns):
        return {
            "ok": False,
            "needs_sheet_selection": False,
            "sheets": loaded["candidates"],
            "errors": errors,
            "rows": [],
            "months": [iso_month(item) for item in month_dates],
            "duplicate_codes": [],
            "can_confirm": False,
            "selected_sheet": sheet,
            "filename": filename,
        }

    formula_values: dict[tuple[int, str], Any] = {}
    omitted_formulas: set[tuple[int, str]] = set()
    if loaded["kind"] == "xlsx":
        formula_values, omitted_formulas = _resolve_month_formulas(path, sheet, header_row_idx, month_columns)
    omitted_formula_cells = 0

    working = df.copy()
    working["_source_row"] = np.arange(header_row_idx + 2, header_row_idx + 2 + len(working))
    working[code_col] = working[code_col].map(clean_account_code)
    working[name_col] = working[name_col].map(
        lambda value: "" if _is_blank(value) else str(value).strip()
    )
    category_headings: list[dict] = []
    category_order: list[str] = []
    non_account_rows: set[int] = set()
    end_excel_row = int(header_row_idx) + 1 + len(working)
    if loaded["kind"] == "xlsx":
        category_headings = detect_category_headings_from_workbook(
            path,
            sheet,
            header_row_idx,
            header_values=raw.iloc[header_row_idx].tolist(),
            end_excel_row=end_excel_row,
        )
        category_order = category_order_from_headings(category_headings)
        assigned = assign_categories(
            working,
            code_col,
            name_col,
            category_heading_excel_rows=[int(item["excel_row"]) for item in category_headings],
        )
        working = assigned
        working["category"] = working["category"].fillna("").astype(str).str.strip()
        # Summaries/notes (blank code + total/exclusion label or aggregation formulas).
        # Never treat these as category headings — Bad debt ffill must continue past them.
        non_account_rows = discover_non_account_blank_code_rows(
            path,
            sheet,
            header_row_idx,
            code_col,
            name_col,
            month_columns,
            end_excel_row=end_excel_row,
        )
        heading_rows = {int(item["excel_row"]) for item in category_headings}
        non_account_rows -= heading_rows
    elif category_col:
        # CSV/text uploads cannot expose bold headings; use an explicit Category column only.
        working["category"] = working[category_col].map(
            lambda value: "" if _is_blank(value) else str(value).strip()
        )
        working["is_category_row"] = False
        category_order = category_order_from_headings(
            [value for value in working["category"].tolist() if str(value or "").strip()]
        )
    else:
        working["category"] = ""
        working["is_category_row"] = False

    parsed_rows: list[dict] = []
    codes_to_rows: dict[str, list[int]] = defaultdict(list)
    for index, record in working.iterrows():
        excel_row = _excel_row_number(header_row_idx, int(index))
        if record.get("is_category_row"):
            continue
        if excel_row in non_account_rows:
            # Skip totals/notes: do not import amounts and do not raise missing-code errors.
            continue
        code = record.get(code_col)
        description = str(record.get(name_col) or "").strip()
        if _is_blank(code) and not description:
            continue
        # CSV/text path: label-only non-account rows (no openpyxl formula inspection).
        if loaded["kind"] != "xlsx" and _is_blank(code) and is_summary_or_note_label(description):
            continue
        category = str(record.get("category") or "").strip()
        amounts: dict[str, Any] = {}
        raw_amounts: dict[str, Any] = {}
        missing_months: list[str] = []
        declared_months: list[str] = []
        invalid_months: list[str] = []
        invalid_amounts = []
        all_amounts_blank = True
        classifier = classify_supplied_upload_cell if strict_supplied_cells else classify_upload_cell
        for column, timestamp in zip(month_columns, month_dates):
            raw_value = record.get(column)
            month = iso_month(timestamp)
            formula_key = (excel_row, column)
            if formula_key in omitted_formulas or (_is_formula(raw_value) and formula_key not in formula_values):
                omitted_formula_cells += 1
                continue
            if formula_key in formula_values:
                raw_value = formula_values[formula_key]
            raw_amounts[month] = None if _is_blank(raw_value) else raw_value
            parsed_cell = classifier(raw_value)
            if parsed_cell["kind"] == "declared_missing":
                amounts[month] = None
                declared_months.append(month)
                all_amounts_blank = False
                continue
            if parsed_cell["kind"] == "blank":
                amounts[month] = None
                if strict_supplied_cells:
                    # Strict uploads: blanks are unresolved (invalid), not declared-missing.
                    invalid_months.append(month)
                    invalid_amounts.append(
                        (excel_row, column, month, raw_value, parsed_cell.get("message") or SUPPLIED_CELL_MESSAGE)
                    )
                else:
                    missing_months.append(month)
                continue
            if parsed_cell["kind"] == "invalid":
                amounts[month] = None
                invalid_months.append(month)
                invalid_amounts.append((excel_row, column, month, raw_value, parsed_cell.get("message")))
                continue
            all_amounts_blank = False
            amounts[month] = float(parsed_cell["value"])
        blank_row = _is_blank(code) and not description and not category and all_amounts_blank
        if blank_row:
            continue
        if _is_blank(code):
            errors.append(
                {
                    "sheet": sheet,
                    "row": excel_row,
                    "column": code_col,
                    "message": f"Row {excel_row} is missing a Budget Code.",
                    "budget_code": "",
                }
            )
            continue
        code_text = str(code).strip()
        codes_to_rows[code_text].append(excel_row)
        if not description:
            errors.append(
                {
                    "sheet": sheet,
                    "row": excel_row,
                    "column": name_col,
                    "message": f"Budget code {code_text} is missing a description on row {excel_row}.",
                    "budget_code": code_text,
                }
            )
        if not category:
            errors.append(
                {
                    "sheet": sheet,
                    "row": excel_row,
                    "column": category_col or "Category",
                    "message": f"Budget code {code_text} is missing a category on row {excel_row}.",
                    "budget_code": code_text,
                }
            )
        for row_number, column, month, raw_value, supplied_message in invalid_amounts:
            message = supplied_message or (
                f"Budget code {code_text} has a non-numeric amount for {month} "
                f"in column {column} on row {row_number}."
            )
            if supplied_message:
                message = (
                    f"Budget code {code_text}, {month}, row {row_number}, column {column}: {SUPPLIED_CELL_MESSAGE}"
                )
            errors.append(
                {
                    "sheet": sheet,
                    "row": row_number,
                    "column": column,
                    "month": month,
                    "message": message,
                    "budget_code": code_text,
                }
            )
        parsed_rows.append(
            {
                "stage_id": uuid.uuid4().hex,
                "budget_code": code_text,
                "description": description,
                "category": category,
                "source_row": excel_row,
                "amounts": amounts,
                "raw_amounts": raw_amounts,
                "missing_months": missing_months,
                "declared_months": declared_months,
                "invalid_months": invalid_months,
            }
        )

    month_hits: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in parsed_rows:
        for month, amount in (row.get("amounts") or {}).items():
            if amount is None or month in (row.get("invalid_months") or []):
                continue
            month_hits[(row["budget_code"], month)].append(
                {
                    "stage_id": row["stage_id"],
                    "source_row": row["source_row"],
                    "amount": amount,
                    "description": row.get("description") or "",
                    "category": row.get("category") or "",
                }
            )
    duplicate_conflicts = []
    for (code, month), hits in month_hits.items():
        if len(hits) < 2:
            continue
        duplicate_conflicts.append({"budget_code": code, "month": month, "records": hits})
        errors.append(
            {
                "sheet": sheet,
                "row": hits[0]["source_row"],
                "column": code_col,
                "message": (
                    f"Budget code {code} appears more than once for {month} "
                    f"(rows {', '.join(str(item['source_row']) for item in hits)}). "
                    "Correct the duplicates before confirming. Rows were not combined."
                ),
                "budget_code": code,
            }
        )
    duplicate_codes = [
        {"budget_code": code, "rows": rows}
        for code, rows in codes_to_rows.items()
        if len(rows) > 1 and any(item["budget_code"] == code for item in duplicate_conflicts)
    ]

    blocking = [item for item in errors if item.get("message")]
    if not category_order:
        category_order = category_order_from_headings(
            [str(row.get("category") or "").strip() for row in parsed_rows]
        )
    return {
        "ok": not blocking,
        "needs_sheet_selection": False,
        "sheets": loaded["candidates"],
        "errors": errors,
        "rows": parsed_rows,
        "months": [iso_month(item) for item in month_dates],
        "duplicate_codes": duplicate_codes,
        "duplicate_conflicts": duplicate_conflicts,
        "can_confirm": not blocking and bool(parsed_rows),
        "selected_sheet": sheet,
        "filename": filename,
        "omitted_formula_cells": omitted_formula_cells,
        "header_row_idx": header_row_idx,
        "code_column": code_col,
        "name_column": name_col,
        "category_column": category_col,
        "month_columns": month_columns,
        "category_headings": [
            {"name": item.get("name"), "excel_row": item.get("excel_row")}
            for item in category_headings
        ],
        "category_order": category_order,
    }
