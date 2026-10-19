"""Historical submission state and coverage.

Applicability policy
---------------------
A budget code is applicable in a month when it is in the master catalog and
the month is inside its effective bounds.

- ``effective_from`` null means the code is applicable from the start of the
  preserved master span. The first numeric month is not used as a start date.
- ``effective_to`` null means the code is still applicable. Omission from an
  upload never sets this bound.
- A code added later receives ``effective_from`` equal to the earliest month
  in the confirming change, unless the edit sets the bound explicitly.
- Retirement happens only when an edit sets ``effective_to`` explicitly.
- Codes with no numeric rows stay in the catalog and stay applicable inside
  their bounds.

Submission policy
-----------------
- A numeric amount, including 0, is submitted.
- An explicit missing declaration is submitted and is not a numeric actual.
- No numeric row and no declaration means not submitted.
- Legacy blanks and absent pairs are not declarations. Nothing in this module
  invents a declaration from a null, a blank, or a missing row.
"""

from __future__ import annotations

import sys
from typing import Any

from app.config import BACKEND_DIR

ENGINE_DIR = BACKEND_DIR / "model_training_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from preprocessing import KIND_INVALID, KIND_NUMBER, effective_historical_actual, parse_amount_cell  # noqa: E402

SUBMISSION_NUMERIC = "numeric"
SUBMISSION_DECLARED_MISSING = "declared_missing"
SUBMISSION_ABSENT = "absent"

EXPLICIT_MISSING_TOKENS = frozenset(
    {
        "declared missing",
        "declared_missing",
        "explicit missing",
        "explicit_missing",
    }
)

BLANK_DECISION_EFFECTS = {
    "keep_master": (
        "Keeps the stored number or missing declaration. The blank cell is ignored."
    ),
    "clear": (
        "Removes the stored number and any missing declaration. "
        "The code/month becomes not submitted. It does not declare the value missing."
    ),
    "remove_staged": (
        "Drops this blank cell from the upload. Existing master state stays unchanged."
    ),
    "declare_missing": (
        "Records an explicit missing declaration and removes any numeric amount. "
        "The code/month counts as submitted, not as a numeric actual."
    ),
}


class MasterTransitionError(ValueError):
    """A workbook-to-database save would drop master values that were not changed."""


def explicit_missing_text(value: Any) -> bool:
    """True for explicit missing markers, including a trimmed standalone hyphen.

    Numeric amounts, including negatives and zero, are not markers. Other dash
    characters, blanks, and NaN stay undeclared.
    """
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return False
    if isinstance(value, float) and value != value:
        return False
    text = str(value).strip()
    if text == "-":
        return True
    return text.lower() in EXPLICIT_MISSING_TOKENS


SUPPLIED_CELL_MESSAGE = "Enter a numeric value or '-' to declare missing."
BASELINE_MIGRATION_REASON = "user-approved legacy baseline interpretation"


def classify_supplied_upload_cell(value: Any) -> dict[str, Any]:
    """Classify a cell that a new upload actually supplied.

    Genuine blanks stay unresolved (not declared missing and not numeric).
    A trimmed standalone '-' is an explicit missing declaration. Numbers,
    including zero and negatives, stay numeric. Other nonempty tokens are
    invalid. This is not used for the original baseline workbook, training
    matrices, or a manual clear.
    """
    if _genuine_blank(value):
        return {"kind": "blank", "value": None, "message": SUPPLIED_CELL_MESSAGE}
    parsed = classify_upload_cell(value)
    if parsed["kind"] in {SUBMISSION_NUMERIC, SUBMISSION_DECLARED_MISSING}:
        return parsed
    return {"kind": "invalid", "value": None, "message": SUPPLIED_CELL_MESSAGE}


def _genuine_blank(value: Any) -> bool:
    """True only for an empty cell. n/a and other tokens are not genuine blanks."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, float) and value != value:
        return True
    return False


def classify_baseline_stored_cell(stored: Any, cached: Any = None) -> dict[str, Any]:
    """Classify one original-baseline cell from its stored Excel value.

    Only a genuine blank becomes a declaration. Numeric zeros, negatives, and
    cached numeric formula results stay numeric. This is not an upload rule.
    """
    if isinstance(stored, str) and stored.startswith("="):
        cached_kind = classify_upload_cell(cached)
        if cached_kind["kind"] == SUBMISSION_NUMERIC:
            return {"kind": SUBMISSION_NUMERIC, "value": cached_kind["value"]}
        return {"kind": "ambiguous", "value": None, "reason": "formula has no cached number"}
    parsed = classify_upload_cell(stored)
    if parsed["kind"] == SUBMISSION_NUMERIC:
        return parsed
    if parsed["kind"] == SUBMISSION_DECLARED_MISSING:
        return parsed
    if _genuine_blank(stored):
        return {"kind": SUBMISSION_DECLARED_MISSING, "value": None, "from_blank": True}
    return {"kind": "ambiguous", "value": None, "reason": "unsupported baseline token"}


def classify_upload_cell(value: Any) -> dict[str, Any]:
    """Classify one uploaded or edited cell.

    A trimmed standalone "-" and the existing declared-missing tokens are
    explicit missing. Blank, NaN, n/a, and other dash characters are not.
    Numeric 0 and negative amounts stay numeric.
    """
    if explicit_missing_text(value):
        return {"kind": SUBMISSION_DECLARED_MISSING, "value": None}
    parsed = parse_amount_cell(value)
    if parsed["kind"] == KIND_NUMBER:
        return {"kind": SUBMISSION_NUMERIC, "value": float(parsed["value"])}
    if parsed["kind"] == KIND_INVALID:
        return {"kind": "invalid", "value": None}
    return {"kind": "blank", "value": None}


def pair_submission(has_numeric: bool, has_declaration: bool) -> str:
    if has_numeric:
        return SUBMISSION_NUMERIC
    if has_declaration:
        return SUBMISSION_DECLARED_MISSING
    return SUBMISSION_ABSENT


def plan_legacy_hyphen_backfill(
    hyphen_pairs: list[tuple[str, str]],
    *,
    records: list[dict[str, Any]] | None = None,
    declarations: list[dict[str, Any]] | None = None,
    touched_pairs: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Choose untouched legacy "-" cells. Never replace a stored number or a later edit."""
    numeric = {
        (str(item.get("budget_code") or "").strip(), str(item.get("month") or "").strip())
        for item in (records or [])
        if item.get("budget_code") and item.get("month") and item.get("amount") is not None
    }
    declared = declaration_index(declarations)
    touched = touched_pairs or set()
    recover: list[dict[str, str]] = []
    already: list[dict[str, str]] = []
    ambiguous: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for code, month in hyphen_pairs:
        pair = (str(code).strip(), str(month).strip())
        if not pair[0] or not pair[1] or pair in seen:
            continue
        seen.add(pair)
        if pair in numeric or pair in touched:
            reason = "numeric amount already stored" if pair in numeric else "later revision exists"
            if pair in numeric and pair in touched:
                reason = "numeric amount and later revision exist"
            ambiguous.append({"budget_code": pair[0], "month": pair[1], "reason": reason})
            continue
        item = {"budget_code": pair[0], "month": pair[1]}
        if pair in declared:
            already.append(item)
        else:
            recover.append(item)
    return {
        "recover": recover,
        "already_declared": already,
        "ambiguous": ambiguous,
    }


def declaration_index(declarations: list[dict[str, Any]] | None) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for item in declarations or []:
        code = str(item.get("budget_code") or "").strip()
        month = str(item.get("month") or "").strip()
        if code and month:
            found.add((code, month))
    return found


def _same_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return float(left) == float(right)


def plan_original_baseline(
    baseline: dict[str, Any],
    state: dict[str, Any],
    touched_pairs: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Merge the approved baseline into the master without overwriting later edits.

    Blank cells are declarations only when this baseline says so. A later clear
    or amount stays in place and is reported instead of being guessed over.
    """
    touched = touched_pairs or set()
    numeric_now = {
        (str(item.get("budget_code") or "").strip(), str(item.get("month") or "").strip()): item
        for item in (state.get("records") or [])
        if item.get("budget_code") and item.get("month") and item.get("amount") is not None
    }
    declared_now = declaration_index(state.get("declarations"))
    database = state.get("source") == "database"
    next_records = [dict(item) for item in numeric_now.values()] if database else []
    next_declarations = [
        {"budget_code": code, "month": month} for code, month in sorted(declared_now)
    ] if database else []
    changes: list[dict[str, Any]] = []
    ambiguous: list[dict[str, str]] = []
    if not database:
        for item in baseline.get("records") or []:
            next_records.append(dict(item))
            changes.append(
                {
                    "operation": "ADD",
                    "budget_code": item["budget_code"],
                    "month": item["month"],
                    "previous_amount": None,
                    "new_amount": float(item["amount"]),
                    "previous_submission": SUBMISSION_ABSENT,
                    "new_submission": SUBMISSION_NUMERIC,
                    "previous_description": "",
                    "new_description": item.get("description") or "",
                    "previous_category": "",
                    "new_category": item.get("category") or "",
                }
            )
        for item in baseline.get("declarations") or []:
            next_declarations.append({"budget_code": item["budget_code"], "month": item["month"]})
            changes.append(
                {
                    "operation": "DECLARE_MISSING",
                    "budget_code": item["budget_code"],
                    "month": item["month"],
                    "previous_amount": None,
                    "new_amount": None,
                    "previous_submission": SUBMISSION_ABSENT,
                    "new_submission": SUBMISSION_DECLARED_MISSING,
                    "previous_description": "",
                    "new_description": "",
                    "previous_category": "",
                    "new_category": "",
                }
            )
    else:
        for item in baseline.get("records") or []:
            pair = (str(item["budget_code"]), str(item["month"]))
            current = numeric_now.get(pair)
            if pair in touched or current is not None:
                if pair in touched or current is None or not _same_number(current.get("amount"), item.get("amount")):
                    ambiguous.append(
                        {
                            "budget_code": pair[0],
                            "month": pair[1],
                            "reason": "later revision exists" if pair in touched else "numeric amount already stored",
                        }
                    )
                continue
            next_records.append(dict(item))
            changes.append(
                {
                    "operation": "ADD",
                    "budget_code": pair[0],
                    "month": pair[1],
                    "previous_amount": None,
                    "new_amount": float(item["amount"]),
                    "previous_submission": SUBMISSION_ABSENT,
                    "new_submission": SUBMISSION_NUMERIC,
                    "previous_description": "",
                    "new_description": item.get("description") or "",
                    "previous_category": "",
                    "new_category": item.get("category") or "",
                }
            )
        for item in baseline.get("declarations") or []:
            pair = (str(item["budget_code"]), str(item["month"]))
            if pair in touched or pair in numeric_now:
                ambiguous.append(
                    {
                        "budget_code": pair[0],
                        "month": pair[1],
                        "reason": "later revision exists" if pair in touched else "numeric amount already stored",
                    }
                )
                continue
            if pair in declared_now:
                continue
            next_declarations.append({"budget_code": pair[0], "month": pair[1]})
            changes.append(
                {
                    "operation": "DECLARE_MISSING",
                    "budget_code": pair[0],
                    "month": pair[1],
                    "previous_amount": None,
                    "new_amount": None,
                    "previous_submission": SUBMISSION_ABSENT,
                    "new_submission": SUBMISSION_DECLARED_MISSING,
                    "previous_description": "",
                    "new_description": "",
                    "previous_category": "",
                    "new_category": "",
                }
            )
    for item in baseline.get("ambiguous") or []:
        ambiguous.append(dict(item))
    codes = {str(item.get("budget_code")): dict(item) for item in (state.get("budget_codes") or []) if item.get("budget_code")}
    for item in baseline.get("budget_codes") or []:
        code = str(item.get("budget_code") or "")
        if code and code not in codes:
            codes[code] = {
                "budget_code": code,
                "description": item.get("description") or "",
                "category": item.get("category") or "",
                "effective_from": None,
                "effective_to": None,
            }
    months = [str(item.get("month")) for item in next_records] + [item["month"] for item in next_declarations]
    earliest = state.get("earliest_month") or baseline.get("earliest_month")
    latest = state.get("latest_month") or baseline.get("latest_month")
    if months:
        earliest = min([value for value in [earliest, min(months)] if value])
        latest = max([value for value in [latest, max(months)] if value])
    return {
        "records": next_records,
        "declarations": next_declarations,
        "budget_codes": list(codes.values()) or list(baseline.get("budget_codes") or []),
        "earliest_month": earliest or baseline.get("earliest_month"),
        "latest_month": latest or baseline.get("latest_month"),
        "changes": changes,
        "ambiguous": ambiguous,
    }


def code_is_applicable(meta: dict[str, Any], month: str) -> bool:
    start = meta.get("effective_from") or None
    end = meta.get("effective_to") or None
    if start and month < str(start):
        return False
    if end and month > str(end):
        return False
    return True


def _months_between(earliest: str | None, latest: str | None, extra: list[str] | None = None) -> list[str]:
    from app.services.dataset_workflow import inclusive_months

    return inclusive_months(earliest, latest, extra)


def numeric_month_total(records: list[dict[str, Any]], month: str) -> float | None:
    """Sum normalized historical actuals. An all-missing month stays null, not zero."""
    values = []
    for item in records:
        if str(item.get("month") or "") != month:
            continue
        amount = item.get("amount")
        if amount is None:
            continue
        number = effective_historical_actual(amount)
        if number == number:
            values.append(float(number))
    if not values:
        return None
    return float(sum(values))


def coverage_report(state: dict[str, Any]) -> dict[str, Any]:
    """Monthly submitted/applicable counts from confirmed master submission state.

    Scope is the full master catalog with each code's effective_from/effective_to
    bounds. Book Entry codes stay in this completeness scope; forecasting
    excludes them separately. A month is complete only when every applicable
    code has a finite numeric actual or an explicit missing declaration.
    A year is complete only when Jan–Dec are all complete. Training imputation
    must not be passed in. Undeclared gaps do not count as complete.
    """
    catalog = []
    for item in state.get("budget_codes") or []:
        code = str(item.get("budget_code") or "").strip()
        if not code:
            continue
        catalog.append(
            {
                "budget_code": code,
                "description": item.get("description") or "",
                "category": item.get("category") or "",
                "effective_from": item.get("effective_from") or None,
                "effective_to": item.get("effective_to") or None,
            }
        )
    numeric: set[tuple[str, str]] = set()
    declared = declaration_index(state.get("declarations"))
    extra_months: list[str] = []
    for item in state.get("records") or []:
        code = str(item.get("budget_code") or "").strip()
        month = str(item.get("month") or "").strip()
        if not code or not month or item.get("amount") is None:
            continue
        numeric.add((code, month))
        extra_months.append(month)
    extra_months.extend(month for _code, month in declared)
    months = _months_between(state.get("earliest_month"), state.get("latest_month"), extra_months)
    by_code = {item["budget_code"]: item for item in catalog}
    month_rows = []
    for month in months:
        applicable = [code for code, meta in by_code.items() if code_is_applicable(meta, month)]
        numeric_count = 0
        declared_count = 0
        for code in applicable:
            kind = pair_submission((code, month) in numeric, (code, month) in declared)
            if kind == SUBMISSION_NUMERIC:
                numeric_count += 1
            elif kind == SUBMISSION_DECLARED_MISSING:
                declared_count += 1
        applicable_count = len(applicable)
        submitted_count = numeric_count + declared_count
        status = "complete" if applicable_count > 0 and submitted_count == applicable_count else "partial"
        month_rows.append(
            {
                "month": month,
                "applicable_count": applicable_count,
                "submitted_count": submitted_count,
                "numeric_count": numeric_count,
                "declared_missing_count": declared_count,
                "status": status,
                "numeric_total": numeric_month_total(list(state.get("records") or []), month),
            }
        )
    by_month = {row["month"]: row for row in month_rows}
    complete_years = []
    years = sorted({int(month[:4]) for month in months}) if months else []
    for year in years:
        year_months = [f"{year:04d}-{month:02d}" for month in range(1, 13)]
        if all(by_month.get(month, {}).get("status") == "complete" for month in year_months):
            complete_years.append(year)
    available = [row for row in month_rows if row["submitted_count"] > 0]
    latest = available[-1] if available else None
    return {
        "months": month_rows,
        "complete_years": complete_years,
        "latest_complete_year": complete_years[-1] if complete_years else None,
        "latest_available_month": None if latest is None else latest["month"],
        "latest_available_status": None if latest is None else latest["status"],
        "latest_available_submitted": 0 if latest is None else latest["submitted_count"],
        "latest_available_applicable": 0 if latest is None else latest["applicable_count"],
        "latest_available_declared_missing": 0 if latest is None else latest["declared_missing_count"],
        "latest_available_numeric": 0 if latest is None else latest["numeric_count"],
    }


def coverage_fields(report: dict[str, Any] | None) -> dict[str, Any]:
    report = report or {}
    return {
        "latest_complete_year": report.get("latest_complete_year"),
        "latest_available_month": report.get("latest_available_month"),
        "latest_available_status": report.get("latest_available_status"),
        "latest_available_submitted": int(report.get("latest_available_submitted") or 0),
        "latest_available_applicable": int(report.get("latest_available_applicable") or 0),
        "latest_available_declared_missing": int(report.get("latest_available_declared_missing") or 0),
        "latest_available_numeric": int(report.get("latest_available_numeric") or 0),
        "complete_years": list(report.get("complete_years") or []),
    }


def apply_declarations(declarations: list[dict[str, Any]] | None, changes: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    indexed = declaration_index(declarations)
    for change in changes or []:
        operation = str(change.get("operation") or "")
        if operation == "BOUNDARY":
            continue
        code = str(change.get("budget_code") or "").strip()
        month = str(change.get("month") or "").strip()
        if not code or not month:
            continue
        key = (code, month)
        new_submission = change.get("new_submission")
        if new_submission == SUBMISSION_DECLARED_MISSING or operation == "DECLARE_MISSING":
            indexed.add(key)
            continue
        if new_submission in {SUBMISSION_NUMERIC, SUBMISSION_ABSENT}:
            indexed.discard(key)
            continue
        if operation in {"ADD", "UPDATE", "CLEAR", "REMOVE"}:
            indexed.discard(key)
    return [{"budget_code": code, "month": month} for code, month in sorted(indexed)]


def apply_code_boundaries(
    previous: list[dict[str, Any]] | None,
    merged: list[dict[str, Any]] | None,
    changes: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Keep existing bounds. New codes start at their first confirming month."""
    previous_map = {
        str(item.get("budget_code") or "").strip(): item
        for item in (previous or [])
        if str(item.get("budget_code") or "").strip()
    }
    explicit_from: dict[str, Any] = {}
    explicit_to: dict[str, Any] = {}
    change_months: dict[str, list[str]] = {}
    for change in changes or []:
        code = str(change.get("budget_code") or "").strip()
        if not code:
            continue
        if "effective_from" in change:
            explicit_from[code] = change.get("effective_from") or None
        if "effective_to" in change:
            explicit_to[code] = change.get("effective_to") or None
        month = str(change.get("month") or "").strip()
        if month and change.get("operation") in {"ADD", "UPDATE", "DECLARE_MISSING"}:
            change_months.setdefault(code, []).append(month)
    result = []
    for item in merged or []:
        code = str(item.get("budget_code") or "").strip()
        if not code:
            continue
        prior = previous_map.get(code)
        entry = {
            "budget_code": code,
            "description": item.get("description") or "",
            "category": item.get("category") or "",
            "effective_from": None if prior is None else (prior.get("effective_from") or None),
            "effective_to": None if prior is None else (prior.get("effective_to") or None),
        }
        if prior is None and change_months.get(code):
            entry["effective_from"] = min(change_months[code])
        if code in explicit_from:
            entry["effective_from"] = explicit_from[code]
        if code in explicit_to:
            entry["effective_to"] = explicit_to[code]
        result.append(entry)
    return result


def assert_transition_preserves_master(
    before: dict[str, Any],
    after: dict[str, Any],
    changes: list[dict[str, Any]] | None,
) -> None:
    """The first database save must keep every untouched workbook value and code."""
    if before.get("source") != "production_workbook":
        return
    removed = {
        (str(item.get("budget_code")), str(item.get("month")))
        for item in (changes or [])
        if item.get("operation") in {"CLEAR", "DECLARE_MISSING", "REMOVE"}
        or item.get("new_submission") in {SUBMISSION_ABSENT, SUBMISSION_DECLARED_MISSING}
    }
    before_keys = {
        (str(item.get("budget_code")), str(item.get("month")))
        for item in (before.get("records") or [])
        if item.get("budget_code") and item.get("month") and item.get("amount") is not None
    }
    after_keys = {
        (str(item.get("budget_code")), str(item.get("month")))
        for item in (after.get("records") or [])
        if item.get("budget_code") and item.get("month") and item.get("amount") is not None
    }
    missing = before_keys - after_keys - removed
    if missing:
        sample = ", ".join(f"{code} {month}" for code, month in sorted(missing)[:5])
        raise MasterTransitionError(
            "The first save would drop historical master values that this upload did not change "
            f"({sample}). Nothing was saved."
        )
    before_codes = {
        str(item.get("budget_code"))
        for item in (before.get("budget_codes") or [])
        if item.get("budget_code")
    }
    after_codes = {
        str(item.get("budget_code"))
        for item in (after.get("budget_codes") or [])
        if item.get("budget_code")
    }
    dropped = before_codes - after_codes
    if dropped:
        sample = ", ".join(sorted(dropped)[:5])
        raise MasterTransitionError(
            "The first save would drop budget codes that this upload did not change "
            f"({sample}). Nothing was saved."
        )
    if before.get("earliest_month") and after.get("earliest_month") and after["earliest_month"] > before["earliest_month"]:
        raise MasterTransitionError("The first save would shrink the historical span. Nothing was saved.")
    if before.get("latest_month") and after.get("latest_month") and after["latest_month"] < before["latest_month"]:
        raise MasterTransitionError("The first save would shrink the historical span. Nothing was saved.")


def infer_legacy_submission(change: dict[str, Any], field: str) -> str:
    """Old revisions have no submission column. Do not invent a declaration."""
    stored = change.get(field)
    if stored in {SUBMISSION_NUMERIC, SUBMISSION_DECLARED_MISSING, SUBMISSION_ABSENT}:
        return str(stored)
    amount_field = "new_amount" if field == "new_submission" else "previous_amount"
    if change.get(amount_field) is None:
        return SUBMISSION_ABSENT
    return SUBMISSION_NUMERIC
