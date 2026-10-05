from __future__ import annotations

from calendar import month_abbr, month_name
from math import floor
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, skew

AMOUNT_UNIT = "LKR_MILLIONS"
ACCOUNT_CODE_COLUMN = "Account Code"
DEFAULT_CATEGORY = "Int'l Settlement"
HISTORY_START = pd.Timestamp("2023-01-01")
DEFAULT_MAX_INTERNAL_GAP = 2
TRAIN_FRACTION = 0.80
HISTORY_START_SOURCE_METADATA = "verified_metadata"
HISTORY_START_SOURCE_MAPPING = "account_history_start"
HISTORY_START_SOURCE_FALLBACK = "source_calendar_unverified"
HISTORY_START_COLUMN_KEYS = {
    "ACTIVATION DATE",
    "ACTIVATION",
    "HISTORY START",
    "ACCOUNT HISTORY START",
    "START DATE",
}

KIND_NUMBER = "number"
KIND_MISSING = "missing"
KIND_INVALID = "invalid"

OUTCOME_READY = "READY"
OUTCOME_ZERO_POLICY = "ZERO_POLICY"
OUTCOME_NO_DATA = "NO_DATA"
OUTCOME_UNRESOLVED = "NEEDS_MISSING_AWARE_MODEL_OR_REVIEW"
OUTCOME_INVALID = "INVALID_ENTRIES"
NOT_EVALUABLE = "NOT_EVALUABLE"

STATUS_FITTED_MODEL = "FITTED_MODEL"
STATUS_ZERO_POLICY = "ZERO_POLICY"
STATUS_NO_DATA = "NO_DATA"
STATUS_UNRESOLVED_MISSING = "UNRESOLVED_MISSING"
STATUS_INVALID_DATA = "INVALID_DATA"
STATUS_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
STATUS_NOT_EVALUABLE = "NOT_EVALUABLE"
STATUS_NO_VALID_WINNER = "NO_VALID_WINNER"
STATUS_FIT_FAILED = "FIT_FAILED"

MAX_FORECAST_END = pd.Timestamp(year=2030, month=12, day=1)

REASON_ALL_MISSING = "ALL_MISSING"
REASON_ALL_ZERO = "ALL_ZERO"
REASON_ZERO_AND_MISSING_ONLY = "ZERO_AND_MISSING_ONLY"
REASON_SHORT_GAP_INTERPOLATED = "SHORT_INTERNAL_GAP_INTERPOLATED"
REASON_UNRESOLVED_GAPS = "UNRESOLVED_MISSING_GAPS"
REASON_NO_USABLE_NUMERIC = "NO_USABLE_NUMERIC_OBSERVATION"
REASON_INVALID_TRAIN = "INVALID_TRAINING_ENTRIES"
METHOD_LINEAR_INTERNAL = "linear_internal_gap"
METHOD_ACCOUNT_MEAN = "account_mean"

MISSING_TOKENS = {"", "nan", "none", "null", "n/a", "na", "#n/a", "<na>"}
BLANK_PLACEHOLDERS = {"-", "–", "—", "−", ".", ".."}
EXCEL_ERROR_PATTERN = re.compile(
    r"^#(DIV/0!|N/A|NAME\?|NULL!|NUM!|REF!|VALUE!|GETTING_DATA|SPILL!|CALC!)",
    re.IGNORECASE,
)

MONTH_LOOKUP = {}
for month_number in range(1, 13):
    MONTH_LOOKUP[month_name[month_number].lower()] = month_number
    MONTH_LOOKUP[month_abbr[month_number].lower()] = month_number
MONTH_LOOKUP["sept"] = 9

MONTH_NAME_PATTERN = (
    "january|february|march|april|june|july|august|september|october|november|december|"
    "sept|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
)


def normalize_label(value):
    """Collapse extra spaces in Excel headers."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def label_key(value):
    return normalize_label(value).upper()


def parse_month_header(column_name):
    """Convert a monthly Actuals header into a month-start Timestamp."""
    if is_excluded_actuals_column(column_name):
        return pd.NaT
    text = normalize_label(column_name).lower()
    spaced = re.sub(
        rf"({MONTH_NAME_PATTERN})(\d{{2,4}})",
        r"\1 \2",
        text,
        flags=re.IGNORECASE,
    )
    match = re.search(
        rf"\b({MONTH_NAME_PATTERN})\s+(20\d{{2}}|\d{{2}})\b",
        spaced,
        flags=re.IGNORECASE,
    )
    if not match:
        return pd.NaT
    month_number = MONTH_LOOKUP[match.group(1).lower()]
    year = int(match.group(2))
    if year < 100:
        year += 2000
    return pd.Timestamp(year=year, month=month_number, day=1)


def is_excluded_actuals_column(column_name) -> bool:
    """Exclude annual Actuals and all YTD columns from model inputs."""
    text = normalize_label(column_name).lower()
    if not text:
        return True
    if re.search(r"\bytd\b", text):
        return True
    has_month = bool(re.search(rf"\b({MONTH_NAME_PATTERN})\b", text, flags=re.IGNORECASE))
    has_actuals = bool(re.search(r"\bactuals?\b", text))
    has_year_only = bool(re.search(r"\b(20\d{2}|\d{2})\b", text))
    if has_actuals and has_year_only and not has_month:
        return True
    return False


def month_label(timestamp):
    """Display label such as Jan-2023, independent of system locale."""
    ts = pd.Timestamp(timestamp)
    return f"{month_abbr[ts.month]}-{ts.year}"


def convert_to_numeric(value):
    """Convert one cell to float. Blanks stay NaN. Zeros stay 0. No imputation."""
    parsed = parse_amount_cell(value)
    if parsed["kind"] == KIND_NUMBER:
        return parsed["value"]
        return np.nan


def parse_amount_cell(value) -> dict[str, Any]:
    """Distinguish genuine missing, valid numbers, and invalid entries."""
    if value is None:
        return {"kind": KIND_MISSING, "value": np.nan, "detail": "blank"}
    if isinstance(value, (float, np.floating)) and np.isnan(value):
        return {"kind": KIND_MISSING, "value": np.nan, "detail": "blank"}
    if isinstance(value, (int, np.integer, float, np.floating)):
        number = float(value)
        if not np.isfinite(number):
            return {"kind": KIND_INVALID, "value": np.nan, "detail": "non-finite numeric"}
        return {"kind": KIND_NUMBER, "value": number, "detail": ""}
    if isinstance(value, pd.Timestamp):
        return {"kind": KIND_INVALID, "value": np.nan, "detail": "timestamp in amount cell"}

    text = str(value).strip()
    if text == "" or text.lower() in MISSING_TOKENS or text in BLANK_PLACEHOLDERS:
        return {"kind": KIND_MISSING, "value": np.nan, "detail": "blank"}
    if EXCEL_ERROR_PATTERN.match(text):
        return {"kind": KIND_INVALID, "value": np.nan, "detail": f"excel_error:{text}"}

    negative = False
    working = text
    if working.startswith("(") and working.endswith(")"):
        negative = True
        working = working[1:-1].strip()
    working = (
        working.replace(",", "")
        .replace(" ", "")
        .replace("LKR", "")
        .replace("lkr", "")
        .replace("Rs.", "")
        .replace("Rs", "")
        .replace("$", "")
    )
    if working == "":
        return {"kind": KIND_MISSING, "value": np.nan, "detail": "blank"}
    try:
        number = float(working)
    except ValueError:
        return {"kind": KIND_INVALID, "value": np.nan, "detail": f"non-numeric:{text}"}
    if not np.isfinite(number):
        return {"kind": KIND_INVALID, "value": np.nan, "detail": f"non-finite:{text}"}
    return {"kind": KIND_NUMBER, "value": (-number if negative else number), "detail": ""}


def clean_account_code(value):
    """Keep account codes as strings and strip Excel's trailing .0."""
    if pd.isna(value):
        return np.nan
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "nat"}:
        return np.nan
    return re.sub(r"\.0+$", "", text)


def expected_month_range(start, end):
    start_ts = pd.Timestamp(start).replace(day=1)
    end_ts = pd.Timestamp(end).replace(day=1)
    return pd.date_range(start_ts, end_ts, freq="MS")


def validate_contiguous_month_range(month_dates, history_start=HISTORY_START):
    dates = pd.to_datetime(list(month_dates))
    if dates.empty:
        raise ValueError("No monthly Actuals columns were detected from January 2023 onward.")
    if dates.duplicated().any():
        duplicates = [month_label(ts) for ts in dates[dates.duplicated()]]
        raise ValueError(
            "Duplicate monthly timestamps were detected: " + ", ".join(sorted(set(duplicates)))
        )
    ordered = dates.sort_values()
    latest = ordered.max()
    expected = expected_month_range(history_start, latest)
    missing = [month_label(ts) for ts in expected if ts not in set(ordered)]
    extra_before = [month_label(ts) for ts in ordered if ts < pd.Timestamp(history_start)]
    if extra_before:
        raise ValueError(
            "Monthly columns before January 2023 were included unexpectedly: "
            + ", ".join(extra_before)
        )
    if missing:
        raise ValueError(
            "The monthly Actuals timeline is missing expected monthly columns from "
            f"{month_label(history_start)} to {month_label(latest)}. "
            "Absent monthly columns: " + ", ".join(missing)
        )
    if not np.array_equal(ordered.to_numpy(), expected.to_numpy()):
        raise ValueError("Detected monthly columns are not a contiguous chronological sequence.")
    return expected


def detect_monthly_columns(column_names, history_start=HISTORY_START):
    detected = []
    seen_dates = {}
    for column in column_names:
        timestamp = parse_month_header(column)
        if pd.isna(timestamp) or timestamp < pd.Timestamp(history_start):
            continue
        timestamp = pd.Timestamp(timestamp).replace(day=1)
        if timestamp in seen_dates:
            raise ValueError(
                "Duplicate monthly date "
                f"{month_label(timestamp)} from columns "
                f"{seen_dates[timestamp]!r} and {column!r}."
            )
        seen_dates[timestamp] = column
        detected.append((column, timestamp))
    if not detected:
        raise ValueError("No monthly Actuals columns from January 2023 onward were detected.")
    detected.sort(key=lambda item: item[1])
    month_dates = [item[1] for item in detected]
    expected = validate_contiguous_month_range(month_dates, history_start=history_start)
    monthly_columns = [item[0] for item in detected]
    if [item[1] for item in detected] != list(expected):
        raise ValueError("Detected monthly columns are not sorted from January 2023 to the latest month.")
    return monthly_columns, expected


def assign_categories(df, account_code_col, account_name_col):
    cleaned_code = df[account_code_col].map(clean_account_code)
    numeric_account_code = pd.to_numeric(df[account_code_col], errors="coerce")
    account_name = df[account_name_col].astype("string")
    is_category_row = cleaned_code.isna() & account_name.notna()
    category = account_name.where(is_category_row).str.strip().ffill()
    assigned = df.copy()
    assigned["category"] = category
    assigned["numeric_account_code"] = numeric_account_code
    assigned["is_category_row"] = is_category_row
    assigned["cleaned_account_code"] = cleaned_code
    return assigned


def calendar_split_sizes(n_months: int) -> tuple[int, int]:
    """80/20 split on calendar length, including missing positions. Never shuffles."""
    count = int(n_months)
    if count < 2:
        raise ValueError(
            f"The calendar window has {count} month(s). Both a nonempty training window "
            "and a nonempty test window are required."
        )
    train_size = int(floor(count * TRAIN_FRACTION))
    test_size = count - train_size
    if train_size < 1 or test_size < 1:
        raise ValueError(
            f"The calendar window of {count} months cannot produce both nonempty train "
            f"and test windows under an 80/20 chronological split "
            f"(train_size={train_size}, test_size={test_size})."
        )
    return train_size, test_size


def parse_history_start_value(value) -> pd.Timestamp:
    """Parse a month-start timestamp from metadata or an explicit mapping."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return pd.NaT
        return pd.Timestamp(value).replace(day=1)
    if isinstance(value, (int, np.integer)):
        raise ValueError(f"History start {value!r} is not a calendar date.")
    text = str(value).strip()
    if not text or text.lower() in MISSING_TOKENS:
        return pd.NaT
    month_match = re.match(r"^([A-Za-z]{3,9})[- ](\d{4})$", text)
    if month_match:
        month_number = MONTH_LOOKUP.get(month_match.group(1).lower())
        if month_number:
            return pd.Timestamp(year=int(month_match.group(2)), month=month_number, day=1)
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"Could not parse history start {value!r} as a month.")
    return pd.Timestamp(parsed).replace(day=1)


def resolve_account_history_start(
    account_code: str,
    metadata: pd.DataFrame,
    account_history_start: dict[str, Any] | None,
    source_start: pd.Timestamp,
    source_end: pd.Timestamp,
    month_dates: Sequence[pd.Timestamp],
) -> tuple[pd.Timestamp, str, bool]:
    """Return (start, source, verified). Never infers from the first nonzero value."""
    mapping = {str(key): value for key, value in (account_history_start or {}).items()}
    mapped_raw = mapping.get(str(account_code))
    mapped_start = parse_history_start_value(mapped_raw) if mapped_raw is not None else pd.NaT
    meta_start = pd.NaT
    if str(account_code) in metadata.index:
        meta_row = metadata.loc[str(account_code)]
        if isinstance(meta_row, pd.DataFrame):
            meta_row = meta_row.iloc[0]
        for key in ("history_start", "activation_date", "account_history_start"):
            if key in meta_row.index and pd.notna(meta_row.get(key)):
                meta_start = parse_history_start_value(meta_row.get(key))
                break
    if pd.notna(mapped_start) and pd.notna(meta_start) and mapped_start != meta_start:
        raise ValueError(
            f"Account {account_code} has conflicting history starts: "
            f"metadata {month_label(meta_start)} vs mapping {month_label(mapped_start)}."
        )
    if pd.notna(mapped_start):
        start = mapped_start
        source = HISTORY_START_SOURCE_MAPPING
        verified = True
    elif pd.notna(meta_start):
        start = meta_start
        source = HISTORY_START_SOURCE_METADATA
        verified = True
    else:
        start = pd.Timestamp(source_start).replace(day=1)
        source = HISTORY_START_SOURCE_FALLBACK
        verified = False
    start = pd.Timestamp(start).replace(day=1)
    source_months = [pd.Timestamp(item).replace(day=1) for item in month_dates]
    if start < pd.Timestamp(source_start).replace(day=1) or start > pd.Timestamp(source_end).replace(day=1):
        raise ValueError(
            f"Account {account_code} history start {month_label(start)} is outside the source "
            f"calendar {month_label(source_start)} to {month_label(source_end)}."
        )
    if start not in set(source_months):
        raise ValueError(
            f"Account {account_code} history start {month_label(start)} is not a month in the "
            "validated source calendar."
        )
    return start, source, verified


def _collect_excel_error_cells(excel_path: Path, sheet_name: str, header_row_idx: int, monthly_columns: Sequence[str]) -> dict[tuple[int, str], str]:
    errors: dict[tuple[int, str], str] = {}
    try:
        from openpyxl import load_workbook
    except ImportError:
        return errors
    workbook = load_workbook(excel_path, read_only=True, data_only=False)
    try:
        if sheet_name not in workbook.sheetnames:
            return errors
        worksheet = workbook[sheet_name]
        header_cells = next(worksheet.iter_rows(min_row=header_row_idx + 1, max_row=header_row_idx + 1))
        header = [normalize_label(cell.value) for cell in header_cells]
        col_index = {label: idx for idx, label in enumerate(header) if label}
        for excel_row, row in enumerate(worksheet.iter_rows(min_row=header_row_idx + 2), start=header_row_idx + 2):
            for column in monthly_columns:
                idx = col_index.get(column)
                if idx is None or idx >= len(row):
                    continue
                cell = row[idx]
                data_type = str(getattr(cell, "data_type", "") or "")
                raw = cell.value
                if data_type == "e" or (isinstance(raw, str) and EXCEL_ERROR_PATTERN.match(raw.strip())):
                    errors[(excel_row, column)] = str(raw)
    finally:
        workbook.close()
    return errors


def _load_original_excel(input_file: str):
    excel_path = Path(input_file)
    if not excel_path.exists():
        raise FileNotFoundError(f"Original Excel file was not found: {excel_path}")

    print(f"Source file: {excel_path.name}")
    xls = pd.ExcelFile(excel_path)
    try:
        print("Available sheets:")
        for sheet in xls.sheet_names:
            print(f" - {sheet}")

        sheet_name = None
        header_row_idx = None
        for candidate_sheet in xls.sheet_names:
            preview = pd.read_excel(
                xls,
                sheet_name=candidate_sheet,
                header=None,
                dtype=object,
                nrows=100,
            )
            for idx, row in preview.iterrows():
                if "ACT CODE" in [label_key(value) for value in row.tolist()]:
                    sheet_name = candidate_sheet
                    header_row_idx = int(idx)
                    break
            if sheet_name is not None:
                break

        if sheet_name is None:
            raise KeyError("No worksheet contained ACT CODE in the first 100 rows.")

        print(f"Selected worksheet: {sheet_name}")
        print(f"Detected header row (0-based): {header_row_idx}")
        print("Sheet name is not used to infer the latest Actuals month.")
    finally:
        xls.close()

    raw = pd.read_excel(excel_path, sheet_name=sheet_name, header=None, dtype=object)
    headers = [normalize_label(value) for value in raw.iloc[header_row_idx].tolist()]
    df = raw.iloc[header_row_idx + 1 :].copy()
    df.columns = headers
    df = df.reset_index(drop=True)
    df["_source_row"] = np.arange(header_row_idx + 2, header_row_idx + 2 + len(df))

    column_lookup = {label_key(column): column for column in df.columns}
    if "ACT CODE" not in column_lookup:
        raise KeyError(f"ACT CODE was not found. Columns: {df.columns.tolist()}")
    if "ACT NAME" not in column_lookup:
        raise KeyError(f"ACT NAME was not found. Columns: {df.columns.tolist()}")

    account_code_col = column_lookup["ACT CODE"]
    account_name_col = column_lookup["ACT NAME"]
    monthly_columns, month_dates = detect_monthly_columns(df.columns.tolist())
    excel_errors = _collect_excel_error_cells(excel_path, sheet_name, header_row_idx, monthly_columns)

    df[account_code_col] = df[account_code_col].map(clean_account_code)
    df[account_name_col] = df[account_name_col].map(
        lambda value: np.nan if pd.isna(value) or str(value).strip() == "" else str(value).strip()
    )
    return {
        "df": df,
        "account_code_col": account_code_col,
        "account_name_col": account_name_col,
        "monthly_columns": monthly_columns,
        "month_dates": month_dates,
        "sheet_name": sheet_name,
        "header_row_idx": header_row_idx,
        "excel_errors": excel_errors,
    }


def _series_counts(values: np.ndarray, kinds: np.ndarray) -> dict[str, Any]:
    numbers = kinds == KIND_NUMBER
    missing = kinds == KIND_MISSING
    invalid = kinds == KIND_INVALID
    finite = numbers & np.isfinite(values)
    zeros = finite & (values == 0)
    negatives = finite & (values < 0)
    observed = finite & ~zeros
    longest = _longest_missing_run(missing)
    return {
        "observed_count": int(np.sum(finite & (values != 0))),
        "finite_count": int(np.sum(finite)),
        "missing_count": int(np.sum(missing)),
        "zero_count": int(np.sum(zeros)),
        "negative_count": int(np.sum(negatives)),
        "invalid_count": int(np.sum(invalid)),
        "nonzero_count": int(np.sum(observed)),
        "longest_missing_run": int(longest),
    }


def _longest_missing_run(missing_mask: np.ndarray) -> int:
    longest = 0
    current = 0
    for flag in missing_mask.tolist():
        if flag:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _safe_descriptive_stats(values: np.ndarray) -> dict[str, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {
            "Mean": np.nan,
            "Median": np.nan,
            "Standard Deviation": np.nan,
            "Variance": np.nan,
            "CV": np.nan,
            "Minimum": np.nan,
            "Maximum": np.nan,
            "Range": np.nan,
            "Skewness": np.nan,
            "Excess Kurtosis": np.nan,
        }
    mean_value = float(np.mean(finite))
    std_value = float(np.std(finite, ddof=1)) if finite.size > 1 else 0.0
    variance = float(np.var(finite, ddof=1)) if finite.size > 1 else 0.0
    cv = std_value / abs(mean_value) if abs(mean_value) > 1e-12 else np.nan
    spread = std_value > 1e-12
    return {
        "Mean": mean_value,
        "Median": float(np.median(finite)),
        "Standard Deviation": std_value,
        "Variance": variance,
        "CV": cv,
        "Minimum": float(np.min(finite)),
        "Maximum": float(np.max(finite)),
        "Range": float(np.max(finite) - np.min(finite)),
        "Skewness": float(skew(finite, bias=False)) if finite.size > 2 and spread else np.nan,
        "Excess Kurtosis": float(kurtosis(finite, fisher=True, bias=False)) if finite.size > 3 and spread else np.nan,
    }


def account_numeric_mean(
    values: np.ndarray,
    kinds: np.ndarray | None = None,
) -> float | None:
    """Arithmetic mean of one Budget Code's usable numeric values.

    Zeros are included. Missing and invalid values are excluded. Returns None
    when the series has no usable numeric observation.
    """
    array = np.asarray(values, dtype=float).reshape(-1)
    if kinds is None:
        usable = array[np.isfinite(array)]
    else:
        kind_row = np.asarray(kinds, dtype=object).reshape(-1)
        if kind_row.size != array.size:
            raise ValueError("Observation-kind length does not match values.")
        usable = array[(kind_row == KIND_NUMBER) & np.isfinite(array)]
    if usable.size == 0:
        return None
    return float(np.mean(usable))


def fill_missing_with_mean(
    values: np.ndarray,
    kinds: np.ndarray,
    mean_value: float | None,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    """Fill genuinely missing months with the supplied same-Budget-Code mean.

    Zeros stay zero. Invalid entries are not invented. If ``mean_value`` is
    None the series is left unchanged.
    """
    prepared = np.array(values, dtype=float, copy=True)
    kind_row = np.asarray(kinds, dtype=object).reshape(-1)
    methods = np.full(prepared.shape, "", dtype=object)
    fills: list[dict[str, Any]] = []
    if mean_value is None or not np.isfinite(float(mean_value)):
        return prepared, methods, fills
    fill_value = float(mean_value)
    for index in range(prepared.size):
        kind = kind_row[index] if index < kind_row.size else KIND_MISSING
        if kind == KIND_INVALID:
            continue
        missing = kind == KIND_MISSING or (kind != KIND_NUMBER and not np.isfinite(prepared[index]))
        if not missing and kind == KIND_NUMBER:
            continue
        if missing:
            prepared[index] = fill_value
            methods[index] = METHOD_ACCOUNT_MEAN
            fills.append(
                {
                    "index": int(index),
                    "method": METHOD_ACCOUNT_MEAN,
                    "reason": METHOD_ACCOUNT_MEAN,
                    "mean": fill_value,
                }
            )
    return prepared, methods, fills


def interpolate_short_internal_gaps(
    values: np.ndarray,
    kinds: np.ndarray,
    max_internal_gap: int = DEFAULT_MAX_INTERNAL_GAP,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    """Deprecated helper kept for compatibility. The training pipeline no longer interpolates."""
    prepared = np.array(values, dtype=float, copy=True)
    methods = np.full(values.shape, "", dtype=object)
    fills: list[dict[str, Any]] = []
    n_values = prepared.size
    index = 0
    while index < n_values:
        if not (kinds[index] == KIND_MISSING and not np.isfinite(prepared[index])):
            index += 1
            continue
        start = index
        while index < n_values and kinds[index] == KIND_MISSING and not np.isfinite(prepared[index]):
            index += 1
        end = index
        gap_len = end - start
        left = start - 1
        right = end
        internal = left >= 0 and right < n_values
        bounds_ok = (
            internal
            and kinds[left] == KIND_NUMBER
            and kinds[right] == KIND_NUMBER
            and np.isfinite(prepared[left])
            and np.isfinite(prepared[right])
        )
        if not bounds_ok or gap_len > int(max_internal_gap):
            continue
        left_value = float(prepared[left])
        right_value = float(prepared[right])
        for offset, position in enumerate(range(start, end), start=1):
            prepared[position] = left_value + (right_value - left_value) * offset / (gap_len + 1)
            methods[position] = METHOD_LINEAR_INTERNAL
            fills.append(
                {
                    "index": int(position),
                    "method": METHOD_LINEAR_INTERNAL,
                    "reason": REASON_SHORT_GAP_INTERPOLATED,
                }
            )
    return prepared, methods, fills


def prepare_series_window(
    values: np.ndarray,
    kinds: np.ndarray,
    interpolate: bool = False,
    max_internal_gap: int = DEFAULT_MAX_INTERNAL_GAP,
    imputation_mean: float | None = None,
) -> tuple[np.ndarray, np.ndarray, str, str]:
    """Prepare one chronological window independently. Never uses later observations.

    Missing months are filled with this window's own mean unless
    ``imputation_mean`` is supplied (evaluation uses the TRAIN mean).
    ``interpolate`` and ``max_internal_gap`` are unused compatibility arguments.
    """
    del interpolate, max_internal_gap
    raw = np.array(values, dtype=float, copy=True)
    kind_row = np.array(kinds, copy=True)
    mean_value = imputation_mean
    if mean_value is None:
        mean_value = account_numeric_mean(raw, kind_row)
    prepared, methods, _fills = fill_missing_with_mean(raw, kind_row, mean_value)
    outcome, reason = classify_training_outcome(np.array(values, dtype=float), kind_row, prepared)
    return prepared, methods, outcome, reason


def classify_training_outcome(
    train_values: np.ndarray,
    train_kinds: np.ndarray,
    prepared_train: np.ndarray,
) -> tuple[str, str]:
    if np.any(train_kinds == KIND_INVALID):
        return OUTCOME_INVALID, REASON_INVALID_TRAIN
    original_finite = train_values[(train_kinds == KIND_NUMBER) & np.isfinite(train_values)]
    if original_finite.size == 0:
        return OUTCOME_NO_DATA, REASON_ALL_MISSING
    if np.all(original_finite == 0):
        if np.any(train_kinds == KIND_MISSING):
            return OUTCOME_ZERO_POLICY, REASON_ZERO_AND_MISSING_ONLY
        return OUTCOME_ZERO_POLICY, REASON_ALL_ZERO
    if np.any(~np.isfinite(prepared_train)):
        return OUTCOME_UNRESOLVED, REASON_NO_USABLE_NUMERIC
    return OUTCOME_READY, OUTCOME_READY


def preprocess_account_panel(
    raw_matrix: pd.DataFrame,
    kind_matrix: pd.DataFrame,
    month_dates: Sequence[pd.Timestamp],
    metadata: pd.DataFrame,
    regular_expense_accounts: Iterable[str] | None = None,
    max_internal_gap: int = DEFAULT_MAX_INTERNAL_GAP,
    invalid_reports: list[dict[str, Any]] | None = None,
    account_history_start: dict[str, Any] | None = None,
) -> dict[str, Any]:
    month_dates = [pd.Timestamp(item).replace(day=1) for item in month_dates]
    source_n_months = len(month_dates)
    source_start = month_dates[0]
    source_end = month_dates[-1]
    history_labels = [month_label(ts) for ts in month_dates]
    allowed = {str(code) for code in (regular_expense_accounts or [])}
    codes = [str(code) for code in raw_matrix.index.tolist()]
    raw_values = raw_matrix.loc[codes, history_labels].to_numpy(dtype=float)
    kinds = kind_matrix.loc[codes, history_labels].to_numpy()
    prepared_train_full = np.full((len(codes), source_n_months), np.nan, dtype=float)
    raw_train_full = np.full((len(codes), source_n_months), np.nan, dtype=float)
    raw_test_full = np.full((len(codes), source_n_months), np.nan, dtype=float)
    imputation_mask_full = np.zeros((len(codes), source_n_months), dtype=bool)
    imputation_methods_full = np.full((len(codes), source_n_months), "", dtype=object)
    observed_test_mask_full = np.zeros((len(codes), source_n_months), dtype=bool)
    prepared_train_by_account: dict[str, pd.Series] = {}
    prepared_test_by_account: dict[str, pd.Series] = {}
    raw_test_by_account: dict[str, pd.Series] = {}
    account_splits: dict[str, dict[str, Any]] = {}
    outcomes = []
    test_fill_count = 0
    unable_to_impute = 0
    for row_index, code in enumerate(codes):
        start_ts, start_source, start_verified = resolve_account_history_start(
            code,
            metadata,
            account_history_start,
            source_start,
            source_end,
            month_dates,
        )
        window_indices = [index for index, ts in enumerate(month_dates) if ts >= start_ts]
        window_dates = [month_dates[index] for index in window_indices]
        window_labels = [history_labels[index] for index in window_indices]
        window_n = len(window_indices)
        train_size, test_size = calendar_split_sizes(window_n)
        train_indices = window_indices[:train_size]
        test_indices = window_indices[train_size:]
        train_labels = [history_labels[index] for index in train_indices]
        test_labels = [history_labels[index] for index in test_indices]
        train_raw = raw_values[row_index, train_indices]
        train_kind = kinds[row_index, train_indices]
        test_raw = raw_values[row_index, test_indices]
        test_kind = kinds[row_index, test_indices]
        window_raw = raw_values[row_index, window_indices]
        window_kind = kinds[row_index, window_indices]
        train_mean = account_numeric_mean(train_raw, train_kind)
        filled, methods, _fills = fill_missing_with_mean(train_raw, train_kind, train_mean)
        test_filled, _test_methods, test_fills = fill_missing_with_mean(test_raw, test_kind, train_mean)
        test_fill_count += len(test_fills)
        if train_mean is None:
            unable_to_impute += 1
        for offset, source_index in enumerate(train_indices):
            raw_train_full[row_index, source_index] = train_raw[offset]
            prepared_train_full[row_index, source_index] = filled[offset]
            imputation_methods_full[row_index, source_index] = methods[offset]
            imputation_mask_full[row_index, source_index] = methods[offset] != ""
        for offset, source_index in enumerate(test_indices):
            raw_test_full[row_index, source_index] = test_raw[offset]
            observed_test_mask_full[row_index, source_index] = (
                test_kind[offset] == KIND_NUMBER and np.isfinite(test_raw[offset])
            )
        prepared_train_by_account[code] = pd.Series(filled, index=train_labels, dtype=float)
        prepared_test_by_account[code] = pd.Series(test_filled, index=test_labels, dtype=float)
        raw_test_by_account[code] = pd.Series(test_raw, index=test_labels, dtype=float)
        if np.any(train_kind == KIND_INVALID):
            outcome, reason = classify_training_outcome(train_raw, train_kind, filled)
        else:
            policy_kinds = np.where(window_kind == KIND_INVALID, KIND_MISSING, window_kind)
            _full_prepared, _full_methods, outcome, reason = prepare_series_window(
                window_raw, policy_kinds
            )
            del _full_prepared, _full_methods
        source_stats = _series_counts(raw_values[row_index], kinds[row_index])
        window_stats = _series_counts(window_raw, window_kind)
        train_stats = _series_counts(train_raw, train_kind)
        test_stats = _series_counts(test_raw, test_kind)
        observed_test_count = int(np.sum(observed_test_mask_full[row_index, test_indices]))
        evaluation_status = NOT_EVALUABLE if observed_test_count == 0 else "EVALUABLE"
        descriptive = _safe_descriptive_stats(window_raw)
        account_name = ""
        category = ""
        if code in metadata.index:
            meta_row = metadata.loc[code]
            if isinstance(meta_row, pd.DataFrame):
                meta_row = meta_row.iloc[0]
            raw_name = meta_row.get("account_name")
            raw_category = meta_row.get("category")
            account_name = "" if pd.isna(raw_name) else str(raw_name)
            category = "" if pd.isna(raw_category) else str(raw_category)
        split_payload = {
            "history_start": month_label(start_ts),
            "history_end": month_label(source_end),
            "history_start_date": start_ts,
            "history_end_date": source_end,
            "history_start_source": start_source,
            "history_start_verified": bool(start_verified),
            "window_n_months": int(window_n),
            "train_size": int(train_size),
            "test_size": int(test_size),
            "split_index": int(train_size),
            "train_months": train_labels,
            "test_months": test_labels,
            "train_dates": [month_dates[index] for index in train_indices],
            "test_dates": [month_dates[index] for index in test_indices],
            "history_months": window_labels,
            "train_imputation_mean": train_mean,
        }
        account_splits[code] = split_payload
        outcomes.append(
            {
                "account_code": code,
                "account_name": account_name,
                "category": category,
                "training_outcome": outcome,
                "training_reason": reason,
                "zero_policy": outcome == OUTCOME_ZERO_POLICY,
                "no_data": outcome == OUTCOME_NO_DATA,
                "ready_for_complete_data_model": (
                    outcome == OUTCOME_READY and bool(np.isfinite(filled).all())
                ),
                "evaluation_status": evaluation_status,
                "observed_test_count": observed_test_count,
                "history_start": split_payload["history_start"],
                "history_end": split_payload["history_end"],
                "history_start_source": start_source,
                "history_start_verified": bool(start_verified),
                "window_n_months": int(window_n),
                "train_size": int(train_size),
                "test_size": int(test_size),
                "split_index": int(train_size),
                "train_months": train_labels,
                "test_months": test_labels,
                "train_start": train_labels[0],
                "train_end": train_labels[-1],
                "test_start": test_labels[0],
                "test_end": test_labels[-1],
                "source_history": source_stats,
                "full_history": window_stats,
                "training": train_stats,
                "test": test_stats,
                "train_imputation_mean": train_mean,
                **{f"full_{key.lower().replace(' ', '_')}": value for key, value in descriptive.items()},
            }
        )

    outcome_frame = pd.DataFrame(outcomes)
    raw_train = pd.DataFrame(raw_train_full, index=codes, columns=history_labels)
    raw_test = pd.DataFrame(raw_test_full, index=codes, columns=history_labels)
    prepared_train_df = pd.DataFrame(prepared_train_full, index=codes, columns=history_labels)
    kind_train = pd.DataFrame(np.full((len(codes), source_n_months), "", dtype=object), index=codes, columns=history_labels)
    kind_test = pd.DataFrame(np.full((len(codes), source_n_months), "", dtype=object), index=codes, columns=history_labels)
    for row_index, code in enumerate(codes):
        split = account_splits[code]
        for month in split["train_months"]:
            kind_train.loc[code, month] = kind_matrix.loc[code, month]
        for month in split["test_months"]:
            kind_test.loc[code, month] = kind_matrix.loc[code, month]
    observed_test_mask = pd.DataFrame(observed_test_mask_full, index=codes, columns=history_labels)
    accounts_df = raw_matrix.loc[codes, history_labels].copy()
    accounts_df.insert(0, ACCOUNT_CODE_COLUMN, codes)
    accounts_df = accounts_df.reset_index(drop=True)
    zero_policy_codes = outcome_frame.loc[outcome_frame["zero_policy"], "account_code"].tolist()
    no_data_codes = outcome_frame.loc[
        outcome_frame["training_outcome"] == OUTCOME_NO_DATA, "account_code"
    ].tolist()
    ready_codes = outcome_frame.loc[outcome_frame["ready_for_complete_data_model"], "account_code"].tolist()
    unresolved_codes = outcome_frame.loc[
        outcome_frame["training_outcome"].isin([OUTCOME_UNRESOLVED, OUTCOME_INVALID, OUTCOME_NO_DATA]),
        "account_code",
    ].tolist()
    counts = outcome_frame["training_outcome"].value_counts().to_dict()
    zero_preserved = int(np.sum((kinds == KIND_NUMBER) & np.isfinite(raw_values) & (raw_values == 0)))
    missing_detected = int(np.sum(kinds == KIND_MISSING))
    missing_filled = int(np.sum(imputation_mask_full)) + int(test_fill_count)
    imputation_summary = {
        "total_budget_codes": int(len(codes)),
        "calendar_months": int(source_n_months),
        "zero_values_preserved": zero_preserved,
        "missing_values_detected": missing_detected,
        "missing_values_filled": missing_filled,
        "unable_to_impute_or_train": int(unable_to_impute),
    }
    combined = build_monthly_combined_series(accounts_df, codes, history_labels)
    complete_prepared = prepared_train_df.loc[ready_codes] if ready_codes else prepared_train_df.iloc[0:0]
    return {
        "account_codes": codes,
        "selected_codes": codes,
        "accounts_df": accounts_df,
        "selected_12": accounts_df,
        "account_matrix": raw_matrix.loc[codes, history_labels],
        "selected_matrix": raw_matrix.loc[codes, history_labels],
        "kind_matrix": kind_matrix.loc[codes, history_labels],
        "month_columns": history_labels,
        "month_dates": month_dates,
        "history_months": history_labels,
        "n_months": source_n_months,
        "source_n_months": source_n_months,
        "source_history_start": month_label(source_start),
        "source_history_end": month_label(source_end),
        "account_splits": account_splits,
        "account_outcomes": outcome_frame,
        "raw_train_matrix": raw_train,
        "raw_test_matrix": raw_test,
        "prepared_train_matrix": prepared_train_df,
        "prepared_train_by_account": prepared_train_by_account,
        "prepared_test_by_account": prepared_test_by_account,
        "raw_test_by_account": raw_test_by_account,
        "imputation_summary": imputation_summary,
        "imputation_mask": pd.DataFrame(imputation_mask_full, index=codes, columns=history_labels),
        "imputation_methods": pd.DataFrame(imputation_methods_full, index=codes, columns=history_labels),
        "observed_test_mask": observed_test_mask,
        "zero_policy_accounts": zero_policy_codes,
        "no_data_accounts": no_data_codes,
        "ready_accounts": ready_codes,
        "unresolved_accounts": unresolved_codes,
        "complete_data_train_matrix": complete_prepared,
        "complete_data_train_by_account": {code: prepared_train_by_account[code] for code in ready_codes},
        "kind_train_matrix": kind_train,
        "kind_test_matrix": kind_test,
        "outcome_counts": counts,
        "invalid_reports": list(invalid_reports or []),
        "combined_df": combined["combined_df"],
        "monthly_history": combined["monthly_history"],
        "history_coverage": combined["history_coverage"],
        "amount_unit": AMOUNT_UNIT,
        "regular_expense_accounts": sorted(allowed),
        "max_internal_gap": int(max_internal_gap),
        "unverified_history_start_accounts": outcome_frame.loc[
            ~outcome_frame["history_start_verified"], "account_code"
        ].tolist(),
    }


def _collect_accounts(loaded) -> dict[str, Any]:
    df = assign_categories(loaded["df"], loaded["account_code_col"], loaded["account_name_col"])
    account_code_col = loaded["account_code_col"]
    account_name_col = loaded["account_name_col"]
    monthly_columns = loaded["monthly_columns"]
    month_dates = list(loaded["month_dates"])
    history_labels = [month_label(ts) for ts in month_dates]
    excel_errors = loaded.get("excel_errors") or {}
    df[account_code_col] = df[account_code_col].map(clean_account_code)
    history_start_col = None
    for column in df.columns:
        if label_key(column) in HISTORY_START_COLUMN_KEYS:
            history_start_col = column
            break

    accounts = df[(~df["is_category_row"]) & df[account_code_col].notna()].copy()
    if accounts.empty:
        raise ValueError("No account rows with a Budget Code were found.")

    duplicate_rows = accounts[accounts.duplicated(subset=[account_code_col], keep=False)]
    if not duplicate_rows.empty:
        raise ValueError(
            "Duplicate normalized account codes exist in the dataset. "
            "Codes were not summed or dropped. "
            f"Codes: {sorted(duplicate_rows[account_code_col].astype(str).unique().tolist())}"
        )

    invalid_reports: list[dict[str, Any]] = []
    raw_rows = []
    kind_rows = []
    meta_rows = []
    for _, record in accounts.iterrows():
        code = str(record[account_code_col])
        source_row = int(record["_source_row"]) if pd.notna(record.get("_source_row")) else None
        values = []
        kinds = []
        for column, label in zip(monthly_columns, history_labels):
            excel_error = excel_errors.get((source_row, column)) if source_row is not None else None
            if excel_error:
                parsed = {"kind": KIND_INVALID, "value": np.nan, "detail": f"excel_error:{excel_error}"}
            else:
                parsed = parse_amount_cell(record[column])
            values.append(parsed["value"])
            kinds.append(parsed["kind"])
            if parsed["kind"] == KIND_INVALID:
                invalid_reports.append(
                    {
                        "account_code": code,
                        "month": label,
                        "column": column,
                        "row": source_row,
                        "sheet": loaded.get("sheet_name"),
                        "detail": parsed["detail"],
                        "raw": None if excel_error is None else excel_error,
                    }
                )
        raw_rows.append(values)
        kind_rows.append(kinds)
        category = record.get("category")
        history_start = pd.NaT
        if history_start_col is not None:
            try:
                history_start = parse_history_start_value(record.get(history_start_col))
            except ValueError:
                invalid_reports.append(
                    {
                        "account_code": code,
                        "month": None,
                        "column": history_start_col,
                        "row": source_row,
                        "sheet": loaded.get("sheet_name"),
                        "detail": f"invalid_history_start:{record.get(history_start_col)!r}",
                        "raw": record.get(history_start_col),
                    }
                )
        meta_rows.append(
            {
                "account_code": code,
                "account_name": "" if pd.isna(record[account_name_col]) else str(record[account_name_col]),
                "category": "" if pd.isna(category) else str(category).strip(),
                "source_row": source_row,
                "history_start": history_start,
                "activation_date": history_start,
            }
        )

    raw_matrix = pd.DataFrame(raw_rows, index=[row["account_code"] for row in meta_rows], columns=history_labels)
    kind_matrix = pd.DataFrame(kind_rows, index=raw_matrix.index, columns=history_labels)
    metadata = pd.DataFrame(meta_rows).set_index("account_code")
    return {
        "raw_matrix": raw_matrix,
        "kind_matrix": kind_matrix,
        "metadata": metadata,
        "month_dates": month_dates,
        "monthly_source_columns": list(monthly_columns),
        "invalid_reports": invalid_reports,
        "account_count": int(len(raw_matrix)),
        "history_labels": history_labels,
    }


def complete_data_account_codes(account_outcomes: pd.DataFrame) -> list[str]:
    """Accounts that may enter complete-numeric training paths."""
    ready = account_outcomes.loc[account_outcomes["ready_for_complete_data_model"], "account_code"]
    return [str(code) for code in ready.tolist()]


def split_from_preprocessing(preprocessing_result: dict[str, Any]) -> dict[str, Any]:
    """Per-account calendar 80/20 splits produced before imputation."""
    outcomes = preprocessing_result["account_outcomes"]
    account_splits = preprocessing_result.get("account_splits") or {}
    if not account_splits:
        account_splits = {
            str(row["account_code"]): {
                "history_start": row["history_start"],
                "history_end": row["history_end"],
                "history_start_source": row["history_start_source"],
                "history_start_verified": bool(row["history_start_verified"]),
                "window_n_months": int(row["window_n_months"]),
                "train_size": int(row["train_size"]),
                "test_size": int(row["test_size"]),
                "split_index": int(row["split_index"]),
                "train_months": list(row["train_months"]),
                "test_months": list(row["test_months"]),
            }
            for _, row in outcomes.iterrows()
        }
    return {
        "source_calendar_months": list(preprocessing_result["history_months"]),
        "source_n_months": int(preprocessing_result["n_months"]),
        "account_splits": account_splits,
    }


def build_monthly_combined_series(accounts_df, account_codes, month_columns):
    ordered_codes = [str(code) for code in account_codes]
    working = accounts_df.copy()
    if ACCOUNT_CODE_COLUMN in working.columns:
        matrix = working.set_index(ACCOUNT_CODE_COLUMN)
    else:
        matrix = working
    matrix = matrix.loc[ordered_codes, month_columns].apply(pd.to_numeric, errors="coerce")
    missing_by_month = matrix.isna().sum(axis=0)
    observed_by_month = matrix.notna().sum(axis=0)
    complete = missing_by_month.eq(0)
    summed = matrix.sum(axis=0, skipna=False)
    totals = []
    coverage = []
    for month in month_columns:
        observed = int(observed_by_month[month])
        missing = int(missing_by_month[month])
        is_complete = bool(complete[month])
        total = float(summed[month]) if is_complete and np.isfinite(summed[month]) else np.nan
        totals.append(total)
        coverage.append(
            {
                "Month": month,
                "observed_accounts": observed,
                "missing_accounts": missing,
                "complete": is_complete,
                "combined_monthly_total": total,
            }
        )
    combined_df = pd.DataFrame(
        {
            "Month": list(month_columns),
            "combined_monthly_total": totals,
            "observed_accounts": [item["observed_accounts"] for item in coverage],
            "missing_accounts": [item["missing_accounts"] for item in coverage],
            "complete": [item["complete"] for item in coverage],
        }
    )
    return {
        "selected_matrix": matrix,
        "combined_df": combined_df,
        "history_months": list(month_columns),
        "monthly_history": totals,
        "selected_account_codes": ordered_codes,
        "history_coverage": coverage,
        "history_complete": bool(all(item["complete"] for item in coverage)),
    }


def category_matches(value: str | None, requested: str | None) -> bool:
    left = str(value or "").strip().casefold()
    right = str(requested or "").strip().casefold()
    if not right or right in {"all", "*"}:
        return True
    if left == right:
        return True
    if "int" in right and "settlement" in right:
        return "int" in left and "settlement" in left
    return False


def select_codes_for_category(metadata: pd.DataFrame, category: str | None) -> list[str]:
    codes = [str(code) for code in metadata.index.tolist()]
    if category is None or str(category).strip().casefold() in {"", "all", "*"}:
        return codes
    selected = [
        code
        for code in codes
        if category_matches(metadata.loc[code, "category"] if "category" in metadata.columns else "", category)
    ]
    if not selected:
        raise ValueError(
            f"No budget-code accounts were found for requested category {category!r}."
        )
    return selected


def map_training_outcome_to_status(outcome: str) -> str:
    if outcome == OUTCOME_READY:
        return STATUS_FITTED_MODEL
    if outcome == OUTCOME_ZERO_POLICY:
        return STATUS_ZERO_POLICY
    if outcome == OUTCOME_NO_DATA:
        return STATUS_NO_DATA
    if outcome == OUTCOME_INVALID:
        return STATUS_INVALID_DATA
    if outcome == OUTCOME_UNRESOLVED:
        return STATUS_UNRESOLVED_MISSING
    return str(outcome)


def run_preprocessing(
    input_file: str,
    category: str | None = None,
    top_n: int | None = None,
    regular_expense_accounts: Iterable[str] | None = None,
    max_internal_gap: int = DEFAULT_MAX_INTERNAL_GAP,
    account_history_start: dict[str, Any] | None = None,
):
    """Preprocess budget codes, optionally limited to one requested category.

    ``top_n`` is accepted only for caller compatibility and is not used as a
    selection filter. When ``category`` is omitted or ``all``, every valid
    account is processed. A requested category uses that category's accounts
    only. ``selected_codes`` aliases ``account_codes``. ``selected_12`` aliases
    ``accounts_df``. Each account is split on its own historical window.
    """
    del top_n
    print("[1/6] Loading original Actuals Excel and detecting monthly columns")
    loaded = _load_original_excel(input_file)
    print("[2/6] Collecting budget-code accounts")
    collected = _collect_accounts(loaded)
    selected_codes = select_codes_for_category(collected["metadata"], category)
    if selected_codes != list(collected["raw_matrix"].index):
        collected["raw_matrix"] = collected["raw_matrix"].loc[selected_codes]
        collected["kind_matrix"] = collected["kind_matrix"].loc[selected_codes]
        collected["metadata"] = collected["metadata"].loc[selected_codes]
        collected["account_count"] = int(len(selected_codes))
        collected["invalid_reports"] = [
            row for row in collected["invalid_reports"] if str(row.get("account_code")) in set(selected_codes)
        ]
    print("[3/6] Building per-account historical windows and chronological 80/20 splits")
    print("[4/6] Preserving zeros as valid actuals")
    print("[5/6] Filling missing months with each Budget Code's own mean")
    result = preprocess_account_panel(
        collected["raw_matrix"],
        collected["kind_matrix"],
        collected["month_dates"],
        collected["metadata"],
        regular_expense_accounts=regular_expense_accounts,
        max_internal_gap=max_internal_gap,
        invalid_reports=collected["invalid_reports"],
        account_history_start=account_history_start,
    )
    result.update(
        {
            "monthly_source_columns": collected["monthly_source_columns"],
            "detected_month_count": result["n_months"],
            "history_start": result["history_months"][0],
            "history_end": result["history_months"][-1],
            "sheet_name": loaded["sheet_name"],
            "account_count": collected["account_count"],
            "category_account_count": collected["account_count"],
            "eligible_account_count": collected["account_count"],
            "requested_category": None if category is None or str(category).strip().casefold() in {"", "all", "*"} else str(category),
            "filtering_summary": pd.DataFrame(
                [
                    {"stage": "accounts_with_budget_code", "count": collected["account_count"]},
                    {"stage": "detected_monthly_columns", "count": result["n_months"]},
                    {"stage": "ready_accounts", "count": len(result["ready_accounts"])},
                    {"stage": "zero_policy_accounts", "count": len(result["zero_policy_accounts"])},
                    {"stage": "unresolved_accounts", "count": len(result["unresolved_accounts"])},
                ]
            ),
        }
    )
    stats_rows = []
    for _, row in result["account_outcomes"].iterrows():
        stats_rows.append(
            {
                "Account Code": row["account_code"],
                "Account Name": row["account_name"],
                "Category": row["category"],
                "Training Outcome": row["training_outcome"],
                "Training Reason": row["training_reason"],
                "Original Available Months": row["full_history"]["finite_count"],
                "Original Missing Count": row["full_history"]["missing_count"],
                "Original Zero Count": row["full_history"]["zero_count"],
                "Original Negative Count": row["full_history"]["negative_count"],
                "Longest Missing Run": row["full_history"]["longest_missing_run"],
                "History Start": row["history_start"],
                "History End": row["history_end"],
                "History Start Source": row["history_start_source"],
                "History Start Verified": row["history_start_verified"],
                "Window Month Count": row["window_n_months"],
                "Train Size": row["train_size"],
                "Test Size": row["test_size"],
                "Train Start": row["train_start"],
                "Train End": row["train_end"],
                "Test Start": row["test_start"],
                "Test End": row["test_end"],
                "Source Month Count": result["n_months"],
            }
        )
    result["account_statistics"] = pd.DataFrame(stats_rows)
    result["eligible_accounts"] = result["account_statistics"]
    quality_report = pd.DataFrame(
        {
            "Account_Code": result["account_outcomes"]["account_code"],
            "Missing_Count": [item["missing_count"] for item in result["account_outcomes"]["full_history"]],
            "Zero_Count": [item["zero_count"] for item in result["account_outcomes"]["full_history"]],
            "Non_Zero_Count": [item["nonzero_count"] for item in result["account_outcomes"]["full_history"]],
            "Training_Outcome": result["account_outcomes"]["training_outcome"],
            "History_Start": result["account_outcomes"]["history_start"],
            "Window_Months": result["account_outcomes"]["window_n_months"],
            "Train_Size": result["account_outcomes"]["train_size"],
            "Test_Size": result["account_outcomes"]["test_size"],
        }
    )
    result["quality_report"] = quality_report
    split_counts = (
        result["account_outcomes"][["train_size", "test_size", "window_n_months"]]
        .value_counts()
        .to_dict()
    )
    print("[6/6] Preprocessing complete")
    summary = result.get("imputation_summary") or {}
    print(f"Total Budget Codes: {summary.get('total_budget_codes', result['account_count'])}")
    print(f"Calendar Months: {summary.get('calendar_months', result['n_months'])}")
    print(f"Zero values preserved: {summary.get('zero_values_preserved', 0)}")
    print(f"Missing values detected: {summary.get('missing_values_detected', 0)}")
    print(f"Missing values filled using same-Budget-Code mean: {summary.get('missing_values_filled', 0)}")
    print(f"Unable to impute/train: {summary.get('unable_to_impute_or_train', 0)}")
    print(f"Source calendar: {result['history_start']} to {result['history_end']}")
    print(f"Unverified history starts: {len(result['unverified_history_start_accounts'])}")
    print(f"Per-account split sizes: {split_counts}")
    print(f"Outcomes: {result['outcome_counts']}")
    return result
