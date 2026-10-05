"""Pure historical merge, staging, and rollback decisions.

No database access and no model training.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from typing import Any

from app.config import BACKEND_DIR

ENGINE_DIR = BACKEND_DIR / "model_training_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from preprocessing import KIND_INVALID, KIND_NUMBER, parse_amount_cell  # noqa: E402

def same_amount(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        return abs(float(left) - float(right)) < 1e-9
    except (TypeError, ValueError):
        return False


def _text(value: Any) -> str:
    return str(value or "").strip()


def master_index(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for item in records:
        indexed[(str(item["budget_code"]), str(item["month"]))] = item
    return indexed


def inclusive_months(earliest: str | None, latest: str | None, extra: list[str] | None = None) -> list[str]:
    """Return every YYYY-MM from the earliest bound through the latest, plus any extra months."""
    months = [str(month) for month in (earliest, latest) if month]
    months.extend(str(month) for month in (extra or []) if month)
    ordered = sorted({month for month in months if month})
    if not ordered:
        return []
    start_year, start_month = (int(part) for part in ordered[0].split("-"))
    end_year, end_month = (int(part) for part in ordered[-1].split("-"))
    columns: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        columns.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            month = 1
            year += 1
    return columns


def merge_budget_codes(
    existing: list[dict[str, Any]] | None,
    changes: list[dict[str, Any]] | None,
    records: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """Keep every budget code. Clearing its last numeric actual does not remove it."""
    codes: dict[str, dict[str, str]] = {}

    def remember(code: Any, description: Any = "", category: Any = "") -> None:
        key = _text(code)
        if not key:
            return
        entry = codes.setdefault(key, {"budget_code": key, "description": "", "category": ""})
        if _text(description):
            entry["description"] = _text(description)
        if _text(category):
            entry["category"] = _text(category)

    for item in existing or []:
        remember(item.get("budget_code"), item.get("description"), item.get("category"))
    for change in changes or []:
        remember(change.get("budget_code"), change.get("new_description"), change.get("new_category"))
    for item in records or []:
        remember(item.get("budget_code"), item.get("description"), item.get("category"))
    return [codes[code] for code in sorted(codes)]


def extend_month_span(
    earliest: str | None,
    latest: str | None,
    records: list[dict[str, Any]] | None,
) -> tuple[str | None, str | None]:
    """Preserve the master month range and extend it when a later or earlier month is added."""
    months = inclusive_months(
        earliest,
        latest,
        [str(item.get("month")) for item in (records or []) if item.get("month")],
    )
    if not months:
        return None, None
    return months[0], months[-1]


def next_master_state(state: dict[str, Any], changes: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Apply numeric changes without dropping budget codes or shrinking the month range."""
    applied = list(changes or [])
    next_records = apply_changes(list(state.get("records") or []), applied)
    earliest, latest = extend_month_span(state.get("earliest_month"), state.get("latest_month"), next_records)
    return {
        "records": next_records,
        "budget_codes": merge_budget_codes(state.get("budget_codes"), applied, next_records),
        "earliest_month": earliest,
        "latest_month": latest,
    }


def code_metadata(records: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    meta: dict[str, dict[str, str]] = {}
    for item in records:
        code = str(item["budget_code"])
        meta.setdefault(
            code,
            {
                "description": _text(item.get("description")),
                "category": _text(item.get("category")),
            },
        )
    return meta


def observed_period(records: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    months = sorted({str(item["month"]) for item in records if item.get("month")})
    if not months:
        return None, None
    return months[0], months[-1]


def normalize_staged_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for source in rows:
        amounts = dict(source.get("raw_amounts") or source.get("amounts") or {})
        parsed_amounts: dict[str, float | None] = {}
        invalid_months = []
        missing_months = []
        for month, raw in amounts.items():
            parsed = parse_amount_cell(raw if raw != "" else None)
            if parsed["kind"] == KIND_NUMBER:
                parsed_amounts[str(month)] = float(parsed["value"])
            else:
                parsed_amounts[str(month)] = None
                if parsed["kind"] == KIND_INVALID:
                    invalid_months.append(str(month))
                else:
                    missing_months.append(str(month))
        normalized.append(
            {
                **source,
                "budget_code": _text(source.get("budget_code")),
                "description": _text(source.get("description")),
                "category": _text(source.get("category")),
                "stage_id": str(source.get("stage_id") or ""),
                "amounts": parsed_amounts,
                "raw_amounts": amounts,
                "invalid_months": invalid_months,
                "missing_months": missing_months,
            }
        )
    return normalized


def duplicate_conflicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for month, amount in (row.get("amounts") or {}).items():
            if amount is None or month in (row.get("invalid_months") or []):
                continue
            hits[(row["budget_code"], str(month))].append(
                {
                    "stage_id": row.get("stage_id"),
                    "source_row": row.get("source_row"),
                    "amount": amount,
                    "description": row.get("description") or "",
                    "category": row.get("category") or "",
                }
            )
    conflicts = []
    for (code, month), records in sorted(hits.items()):
        if len(records) > 1:
            conflicts.append({"budget_code": code, "month": month, "records": records})
    return conflicts


def _decision_map(items: list[dict[str, Any]] | None, key_fields: tuple[str, ...]) -> dict[tuple, str]:
    found: dict[tuple, str] = {}
    for item in items or []:
        key = tuple(str(item.get(field) or "") for field in key_fields)
        action = str(item.get("action") or "").strip()
        if action:
            found[key] = action
    return found


def analyze_upload(
    rows: list[dict[str, Any]],
    master_records: list[dict[str, Any]],
    *,
    removed_stage_ids: list[str] | None = None,
    missing_decisions: list[dict[str, Any]] | None = None,
    metadata_decisions: list[dict[str, Any]] | None = None,
    master_version: int = 0,
    budget_codes: list[dict[str, Any]] | None = None,
    earliest_month: str | None = None,
    latest_month: str | None = None,
) -> dict[str, Any]:
    removed = {str(item) for item in (removed_stage_ids or [])}
    staged = [row for row in normalize_staged_rows(rows) if str(row.get("stage_id") or "") not in removed]
    existing = master_index(master_records)
    metadata = code_metadata(master_records)
    for item in budget_codes or []:
        code = _text(item.get("budget_code"))
        if not code:
            continue
        entry = metadata.setdefault(code, {"description": "", "category": ""})
        if not entry["description"]:
            entry["description"] = _text(item.get("description"))
        if not entry["category"]:
            entry["category"] = _text(item.get("category"))
    duplicates = duplicate_conflicts(staged)
    duplicate_keys = {(item["budget_code"], item["month"]) for item in duplicates}
    missing_choice = _decision_map(missing_decisions, ("budget_code", "month"))
    metadata_choice = _decision_map(metadata_decisions, ("budget_code",))

    preview: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    metadata_conflicts: list[dict[str, Any]] = []
    missing_conflicts: list[dict[str, Any]] = []
    invalid_count = 0
    missing_count = 0
    valid_count = 0
    codes = set()
    categories = set()
    seen_meta: set[str] = set()

    for row in staged:
        code = row["budget_code"]
        if not code:
            continue
        codes.add(code)
        if row["category"]:
            categories.add(row["category"])
        prior_meta = metadata.get(code)
        if prior_meta and code not in seen_meta:
            seen_meta.add(code)
            description_differs = row["description"] != prior_meta["description"] and row["description"] != ""
            category_differs = row["category"] != prior_meta["category"] and row["category"] != ""
            if description_differs or category_differs:
                action = metadata_choice.get((code,))
                conflict = {
                    "budget_code": code,
                    "current_category": prior_meta["category"],
                    "uploaded_category": row["category"],
                    "current_description": prior_meta["description"],
                    "uploaded_description": row["description"],
                    "decision": action,
                }
                if action not in {"keep_master", "accept_upload"}:
                    metadata_conflicts.append(conflict)
        apply_metadata = metadata_choice.get((code,)) == "accept_upload"
        for month, amount in row["amounts"].items():
            key = (code, month)
            prior = existing.get(key)
            current = None if prior is None else prior.get("amount")
            if month in row["invalid_months"]:
                invalid_count += 1
                preview.append(
                    {
                        "budget_code": code,
                        "month": month,
                        "current": current,
                        "uploaded": row.get("raw_amounts", {}).get(month),
                        "result": "Invalid",
                        "stage_id": row["stage_id"],
                    }
                )
                continue
            if amount is None:
                missing_count += 1
                if prior is not None and current is not None:
                    action = missing_choice.get(key)
                    entry = {
                        "budget_code": code,
                        "month": month,
                        "current": current,
                        "uploaded": None,
                        "result": "Missing",
                        "stage_id": row["stage_id"],
                        "decision": action,
                    }
                    preview.append(entry)
                    if action == "clear":
                        changes.append(_change("CLEAR", month, prior, None, row, apply_metadata))
                    elif action == "keep_master":
                        entry["result"] = "Unchanged"
                    elif action == "remove_staged":
                        entry["result"] = "Removed"
                    else:
                        missing_conflicts.append(entry)
                continue
            valid_count += 1
            if key in duplicate_keys:
                preview.append(
                    {
                        "budget_code": code,
                        "month": month,
                        "current": current,
                        "uploaded": amount,
                        "result": "Duplicate",
                        "stage_id": row["stage_id"],
                    }
                )
                continue
            if prior is None:
                preview.append(_preview_row(code, month, None, amount, "New", row["stage_id"]))
                changes.append(_change("ADD", month, None, amount, row, True))
            elif same_amount(current, amount):
                preview.append(_preview_row(code, month, current, amount, "Unchanged", row["stage_id"]))
                if apply_metadata and (
                    row["category"] != _text(prior.get("category")) or row["description"] != _text(prior.get("description"))
                ):
                    changes.append(_change("UPDATE", month, prior, amount, row, True))
            else:
                preview.append(_preview_row(code, month, current, amount, "Updated", row["stage_id"]))
                changes.append(_change("UPDATE", month, prior, amount, row, apply_metadata))

    new_codes = sorted(code for code in codes if code not in metadata)
    matched_codes = sorted(code for code in codes if code in metadata)
    master_codes = set(metadata)
    after_codes = set(master_codes) | set(new_codes)
    current_start, current_end = observed_period(master_records)
    current_start = earliest_month or current_start
    current_end = latest_month or current_end
    uploaded_months = sorted(
        month
        for row in staged
        for month, amount in row["amounts"].items()
        if amount is not None and month not in row["invalid_months"]
    )
    observed_months = sorted(
        {*( [current_start] if current_start else []), *( [current_end] if current_end else []), *uploaded_months}
    )
    # Rebuild the full observed span from actual month keys rather than only the endpoints.
    all_months = sorted(
        {
            *[str(item["month"]) for item in master_records],
            *uploaded_months,
            *([earliest_month] if earliest_month else []),
            *([latest_month] if latest_month else []),
        }
    )
    blocking = bool(duplicates or metadata_conflicts or missing_conflicts or invalid_count or any(not row["budget_code"] for row in staged))
    structure_errors = [
        row
        for row in staged
        if row["budget_code"] and row["budget_code"] not in metadata and (not row["description"] or not row["category"])
    ]
    new_value_count = sum(1 for item in changes if item["operation"] == "ADD")
    updated_value_count = sum(1 for item in changes if item["operation"] == "UPDATE")
    clear_count = sum(1 for item in changes if item["operation"] == "CLEAR")
    unchanged_count = sum(1 for item in preview if item["result"] == "Unchanged")
    can_confirm = not blocking and not structure_errors and (new_value_count + updated_value_count + clear_count) > 0
    return {
        "rows": staged,
        "can_confirm": can_confirm,
        "duplicate_conflicts": duplicates,
        "metadata_conflicts": metadata_conflicts,
        "missing_conflicts": missing_conflicts,
        "merge_preview": preview,
        "changes": changes,
        "master_version": int(master_version),
        "summary": {
            "file_structure": "Valid" if not structure_errors and invalid_count == 0 and not duplicates else "Needs correction",
            "period_detected": _span(uploaded_months),
            "budget_codes_uploaded": len(codes),
            "existing_codes": len(matched_codes),
            "new_budget_codes": len(new_codes),
            "new_budget_code_list": new_codes,
            "categories_represented": len(categories),
            "valid_numeric_values": valid_count,
            "missing_values": missing_count,
            "invalid_values": invalid_count,
            "duplicate_conflicts": len(duplicates),
            "current_budget_codes": len(master_codes),
            "budget_codes_after_save": len(after_codes),
            "current_observed_period": _span([month for month in (current_start, current_end) if month]),
            "latest_observed_month_after_save": all_months[-1] if all_months else None,
            "new_values": new_value_count,
            "updated_values": updated_value_count,
            "unchanged_values": unchanged_count,
            "clears": clear_count,
        },
        "new_count": new_value_count,
        "changed_count": updated_value_count,
        "unchanged_count": unchanged_count,
        "invalid_count": invalid_count,
    }


def apply_changes(master_records: list[dict[str, Any]], changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = {key: dict(value) for key, value in master_index(master_records).items()}
    for change in changes:
        key = (str(change["budget_code"]), str(change["month"]))
        operation = change["operation"]
        if operation in {"CLEAR", "REMOVE"} or change.get("new_amount") is None:
            indexed.pop(key, None)
            continue
        prior = indexed.get(key) or {}
        indexed[key] = {
            "budget_code": key[0],
            "month": key[1],
            "amount": float(change["new_amount"]),
            "description": _text(change.get("new_description")) or _text(prior.get("description")),
            "category": _text(change.get("new_category")) or _text(prior.get("category")),
        }
    return [indexed[key] for key in sorted(indexed)]


def plan_rollback(changes: list[dict[str, Any]], master_records: list[dict[str, Any]]) -> dict[str, Any]:
    current = master_index(master_records)
    safe = []
    conflicts = []
    for change in changes:
        if change.get("reversed_by_audit_id"):
            continue
        key = (str(change["budget_code"]), str(change["month"]))
        prior = current.get(key)
        current_amount = None if prior is None else prior.get("amount")
        introduced = change.get("new_amount")
        before = change.get("previous_amount")
        safe_to_reverse = same_amount(current_amount, introduced)
        entry = {
            "budget_code": key[0],
            "month": key[1],
            "before_upload": before,
            "value_introduced": introduced,
            "current_master_value": current_amount,
            "proposed_rollback": before if safe_to_reverse else None,
            "conflict_status": "safe" if safe_to_reverse else "later_revision_conflict",
            "operation": "REMOVE" if before is None else "RESTORE",
            "change_id": change.get("id"),
        }
        if safe_to_reverse:
            safe.append(entry)
        else:
            conflicts.append(entry)
    removals = [item for item in safe if item["operation"] == "REMOVE"]
    restores = [item for item in safe if item["operation"] == "RESTORE"]
    return {
        "safe": safe,
        "conflicts": conflicts,
        "safe_removals": len(removals),
        "previous_values_to_restore": len(restores),
        "later_revision_conflicts": len(conflicts),
        "rollback_changes": [
            {
                "operation": item["operation"],
                "budget_code": item["budget_code"],
                "month": item["month"],
                "previous_amount": item["current_master_value"],
                "new_amount": item["proposed_rollback"],
                "previous_description": "",
                "new_description": "",
                "previous_category": "",
                "new_category": "",
                "source_change_id": item["change_id"],
            }
            for item in safe
        ],
    }


def _preview_row(code: str, month: str, current: Any, uploaded: Any, result: str, stage_id: str) -> dict[str, Any]:
    return {
        "budget_code": code,
        "month": month,
        "current": current,
        "uploaded": uploaded,
        "result": result,
        "stage_id": stage_id,
    }


def _change(
    operation: str,
    month: str,
    prior: dict[str, Any] | None,
    new_amount: Any,
    row: dict[str, Any],
    apply_metadata: bool,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "budget_code": row["budget_code"],
        "month": month,
        "previous_amount": None if prior is None else prior.get("amount"),
        "new_amount": new_amount,
        "previous_description": "" if prior is None else _text(prior.get("description")),
        "previous_category": "" if prior is None else _text(prior.get("category")),
        "new_description": row["description"] if apply_metadata or prior is None else _text((prior or {}).get("description")),
        "new_category": row["category"] if apply_metadata or prior is None else _text((prior or {}).get("category")),
    }


def _span(months: list[str]) -> str | None:
    ordered = sorted({month for month in months if month})
    if not ordered:
        return None
    if ordered[0] == ordered[-1]:
        return ordered[0]
    return f"{ordered[0]} – {ordered[-1]}"


def manual_edit_plan(
    edits: list[dict[str, Any]],
    master_records: list[dict[str, Any]],
    *,
    master_version: int = 0,
    budget_codes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    existing = master_index(master_records)
    known = {
        _text(item.get("budget_code")): item
        for item in (budget_codes or [])
        if _text(item.get("budget_code"))
    }
    changes = []
    preview = []
    errors = []
    for edit in edits:
        code = _text(edit.get("budget_code"))
        month = _text(edit.get("month"))
        if not code or not month:
            errors.append("Each edit needs a Budget Code and month.")
            continue
        parsed = parse_amount_cell(edit.get("amount"))
        prior = existing.get((code, month))
        current = None if prior is None else prior.get("amount")
        expected = edit.get("expected_amount", current)
        if not same_amount(expected, current):
            errors.append(f"{code} {month} changed since it was loaded. Refresh and review again.")
            continue
        if parsed["kind"] == KIND_INVALID:
            errors.append(f"{code} {month} has an invalid amount.")
            continue
        known_meta = known.get(code) or {}
        description = _text(edit.get("description") or (prior or {}).get("description") or known_meta.get("description"))
        category = _text(edit.get("category") or (prior or {}).get("category") or known_meta.get("category"))
        if parsed["kind"] != KIND_NUMBER:
            if prior is None:
                continue
            preview.append({"budget_code": code, "month": month, "current": current, "proposed": None, "result": "Clear"})
            changes.append(
                {
                    "operation": "CLEAR",
                    "budget_code": code,
                    "month": month,
                    "previous_amount": current,
                    "new_amount": None,
                    "previous_description": _text((prior or {}).get("description")),
                    "new_description": description,
                    "previous_category": _text((prior or {}).get("category")),
                    "new_category": category,
                }
            )
            continue
        amount = float(parsed["value"])
        if prior is not None and same_amount(current, amount) and description == _text(prior.get("description")) and category == _text(prior.get("category")):
            preview.append({"budget_code": code, "month": month, "current": current, "proposed": amount, "result": "Unchanged"})
            continue
        operation = "ADD" if prior is None else "UPDATE"
        if operation == "ADD" and (not description or not category):
            errors.append(f"New Budget Code {code} needs a description and category.")
            continue
        preview.append({"budget_code": code, "month": month, "current": current, "proposed": amount, "result": "New" if operation == "ADD" else "Updated"})
        changes.append(
            {
                "operation": operation,
                "budget_code": code,
                "month": month,
                "previous_amount": current,
                "new_amount": amount,
                "previous_description": "" if prior is None else _text(prior.get("description")),
                "new_description": description,
                "previous_category": "" if prior is None else _text(prior.get("category")),
                "new_category": category,
            }
        )
    return {
        "can_confirm": not errors and bool(changes),
        "errors": errors,
        "preview": preview,
        "changes": changes,
        "master_version": int(master_version),
        "new_count": sum(1 for item in changes if item["operation"] == "ADD"),
        "changed_count": sum(1 for item in changes if item["operation"] in {"UPDATE", "CLEAR"}),
    }


def display_month(month: str | None) -> str:
    if not month:
        return "—"
    try:
        year, number = str(month).split("-")
        timestamp_month = int(number)
    except (TypeError, ValueError):
        return str(month)
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if not 1 <= timestamp_month <= 12:
        return str(month)
    return f"{names[timestamp_month - 1]} {year}"
