"""Historical dataset upload, confirm, view, edit, and download."""

from __future__ import annotations

import csv
import functools
import io
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from openpyxl import load_workbook

from app.config import DATASET_MAX_UPLOAD_BYTES, DATASET_PREVIEW_TTL_SECONDS, ORIGINAL_ACTUALS_PATH
from app.db import DatabaseError, ensure_forecast_tables, get_connection
from app.services.dataset_merge import classify_historical_changes, intended_edit_records
from app.services.dataset_parser import parse_historical_dataset
from app.services.dataset_workflow import (
    analyze_upload,
    inclusive_months,
    manual_edit_plan,
    next_master_state,
    plan_rollback,
)
from app.services.dataset_repository import (
    DatasetConflictError,
    apply_historical_changes,
    delete_preview,
    get_dataset_file,
    get_legacy_file_by_checksum,
    get_preview,
    get_preview_confirmation,
    commit_master_state,
    get_audit_event,
    historical_summary,
    insert_preview,
    list_change_records_for_audit,
    list_change_records_for_file,
    list_dataset_files,
    list_historical_actuals,
    list_manual_audits,
    master_version,
    update_preview_payload,
)
from app.services.dataset_master import load_master_records, load_master_state, prepare_managed_master, publish_managed_master
from app.services.dataset_storage import (
    DatasetStorageError,
    cleanup_temp_files,
    file_checksum,
    read_stored_file,
    replace_current_file,
    restore_from_backup,
    stored_name,
    write_bytes_atomically,
)
from app.services.dataset_storage import resolve_stored_path
from app.services.system_lock import mutation_scope

logger = logging.getLogger(__name__)


def _guard_dataset_mutation(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        with mutation_scope("dataset"):
            return func(*args, **kwargs)

    return wrapped

CONTENT_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".csv": "text/csv",
}
CLIENT_ACTIVITY_TID = "client"
CLIENT_ACTIVITY_OID = "unverified"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_filename(name: str) -> str:
    text = Path(name or "dataset.xlsx").name.strip() or "dataset.xlsx"
    return text.replace("\\", "_").replace("/", "_")[:180]


def _suffix(filename: str) -> str:
    return Path(filename).suffix.lower()


def _guard_upload(filename: str, data: bytes) -> None:
    if len(data) > DATASET_MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The dataset is larger than the 20 MB upload limit.")
    suffix = _suffix(filename)
    if suffix not in {".xlsx", ".xlsm", ".csv"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only .xlsx and .csv files are supported.")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty.")


def _write_temp_upload(data: bytes, filename: str) -> Path:
    from app.services.dataset_storage import ensure_storage_dir

    ident = uuid.uuid4().hex
    suffix = _suffix(filename) or ".xlsx"
    path = ensure_storage_dir() / f".tmp-upload-{ident}{suffix}"
    path.write_bytes(data)
    return path


def _preview_payload(parsed: dict[str, Any], classification: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "filename": parsed.get("filename"),
        "selected_sheet": parsed.get("selected_sheet"),
        "needs_sheet_selection": parsed.get("needs_sheet_selection"),
        "sheets": parsed.get("sheets") or [],
        "errors": parsed.get("errors") or [],
        "duplicate_codes": parsed.get("duplicate_codes") or [],
        "months": parsed.get("months") or [],
        "rows": parsed.get("rows") or [],
        "can_confirm": bool(parsed.get("can_confirm")),
        "new_count": 0,
        "changed_count": 0,
        "unchanged_count": 0,
        "invalid_count": len(parsed.get("errors") or []),
        "new_records": [],
        "changed_records": [],
        "forecast_note": "Saving historical data does not retrain models or update generated forecasts.",
    }
    if classification:
        payload.update(
            {
                "new_count": classification["new_count"],
                "changed_count": classification["changed_count"],
                "unchanged_count": classification["unchanged_count"],
                "new_records": classification["new"][:200],
                "changed_records": classification["changed"][:200],
                "can_confirm": payload["can_confirm"] and not payload["errors"],
            }
        )
    return payload


def read_activity_metadata(body: dict[str, Any] | None) -> tuple[str, str]:
    """Return client-supplied name/email. Do not invent a missing email."""
    payload = body or {}
    name = str(payload.get("name") or "").strip()
    email = str(payload.get("email") or "").strip()
    return name, email


def format_activity_label(display_name: str | None, username: str | None, is_legacy: bool = False) -> str:
    if is_legacy:
        return "Legacy import"
    name = (display_name or "").strip()
    email = (username or "").strip()
    if name and email:
        return f"{name} ({email})"
    return name or email or "Unknown"


@_guard_dataset_mutation
def preview_upload(filename: str, data: bytes, sheet_name: str | None, actor=None) -> dict[str, Any]:
    filename = _safe_filename(filename)
    _guard_upload(filename, data)
    temp = _write_temp_upload(data, filename)
    try:
        parsed = parse_historical_dataset(temp, filename, sheet_name)
        classification = None
        analysis = None
        if not parsed.get("needs_sheet_selection"):
            state = load_master_state()
            master_records, version, master_source = state["records"], state["version"], state["source"]
            analysis = analyze_upload(
                parsed.get("rows") or [],
                master_records,
                master_version=version,
                budget_codes=state["budget_codes"],
                earliest_month=state["earliest_month"],
                latest_month=state["latest_month"],
                declarations=state.get("declarations") or [],
            )
            analysis["master_source"] = master_source
            classification = _classification_from_analysis(analysis)
        payload = _preview_payload(parsed, classification)
        if analysis is not None:
            payload.update(_public_analysis(analysis, parsed.get("errors") or []))
            omitted_formulas = int(parsed.get("omitted_formula_cells") or 0)
            if omitted_formulas and isinstance(payload.get("summary"), dict):
                payload["summary"]["unresolved_formula_cells"] = omitted_formulas
        stored_preview_name = stored_name(str(uuid.uuid4()), filename)
        preview_file = resolve_stored_path(stored_preview_name).with_name(f".preview-{stored_preview_name}")
        write_bytes_atomically(preview_file, data)
        owner_tid = _verified_value(actor, "tenant_id") if actor is not None else CLIENT_ACTIVITY_TID
        owner_oid = _verified_value(actor, "object_id") if actor is not None else CLIENT_ACTIVITY_OID
        stored = insert_preview(
            owner_tid,
            owner_oid,
            filename,
            str(preview_file.name),
            parsed.get("selected_sheet"),
            {
                **payload,
                "parsed_rows": parsed.get("rows") or [],
                "checksum": file_checksum(data),
                "content_type": CONTENT_TYPES.get(_suffix(filename), "application/octet-stream"),
                "file_size": len(data),
            },
            DATASET_PREVIEW_TTL_SECONDS,
        )
        return {
            "preview_id": stored["id"],
            "expires_at": stored["expires_at"],
            "rows": parsed.get("rows") or [],
            **payload,
        }
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error) or "The dataset could not be validated.") from error
    finally:
        if temp.exists():
            temp.unlink()


def committed_upload_result(preview_id: str) -> dict[str, Any] | None:
    row = get_preview_confirmation(preview_id)
    if not row or row.get("status") != "committed":
        return None
    result = dict(row.get("result") or {})
    result["file_id"] = str(row.get("file_id") or result.get("file_id") or "")
    result["idempotent"] = True
    return result


def preview_confirmation_status(preview_id: str) -> dict[str, Any]:
    preview_id = str(preview_id or "").strip()
    if not preview_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A validation preview is required.")
    committed = committed_upload_result(preview_id)
    if committed:
        return {"status": "committed", "file_id": committed.get("file_id"), "result": committed}
    try:
        _load_preview(preview_id)
    except HTTPException as error:
        if error.status_code == status.HTTP_404_NOT_FOUND:
            return {"status": "unknown", "file_id": None, "result": None}
        raise
    return {"status": "not_committed", "file_id": None, "result": None}


def _load_preview(preview_id: str) -> dict[str, Any]:
    preview = get_preview(preview_id)
    if preview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The validation preview was not found or has expired.")
    expires = preview.get("expires_at")
    if expires is not None and getattr(expires, "tzinfo", None) and expires < _utc_now():
        delete_preview(preview_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The validation preview has expired. Validate the file again.")
    return preview


@_guard_dataset_mutation
def confirm_upload(preview_id: str, body: dict[str, Any] | None = None, actor=None) -> dict[str, Any]:
    """Save a validated dataset. Does not reset or extend the Book Entry registry."""
    preview_id = str(preview_id or "").strip()
    if not preview_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A validation preview is required.")
    already_saved = committed_upload_result(preview_id)
    if already_saved:
        return already_saved
    name, email, actor_tid, actor_oid = _actor(body, actor)
    preview = _load_preview(preview_id)
    payload = dict(preview.get("payload") or {})
    from app.services.dataset_storage import ensure_storage_dir

    preview_name = preview.get("stored_filename")
    if not preview_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The preview file is no longer available.")
    source = ensure_storage_dir() / Path(preview_name).name
    if not source.exists():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The preview file is no longer available. Validate again.")
    original_bytes = source.read_bytes()
    staged_rows = payload.get("parsed_rows") or []
    state = load_master_state()
    master_records, version = state["records"], state["version"]
    analysis = analyze_upload(
        staged_rows,
        master_records,
        removed_stage_ids=payload.get("removed_stage_ids") or [],
        missing_decisions=payload.get("missing_decisions") or [],
        metadata_decisions=payload.get("metadata_decisions") or [],
        master_version=version,
        budget_codes=state["budget_codes"],
        earliest_month=state["earliest_month"],
        latest_month=state["latest_month"],
        declarations=state.get("declarations") or [],
    )
    structural = _structural_errors(payload.get("structural_errors") or [])
    if structural or not analysis["can_confirm"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Blocking validation issues must be resolved before saving.")
    if int(payload.get("master_version") or 0) != version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The historical master changed while this review was open. Validate again before saving.",
        )
    file_id = str(uuid.uuid4())
    stored_filename = stored_name(file_id, preview["original_filename"])
    now = _utc_now()
    next_state = next_master_state(state, analysis["changes"])
    from app.services.submission_state import MasterTransitionError, assert_transition_preserves_master

    try:
        assert_transition_preserves_master(state, next_state, analysis["changes"])
    except MasterTransitionError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    next_state["records"] = _stamp_source(next_state["records"], analysis["changes"], file_id, master_records)
    next_records = next_state["records"]
    file_row = _upload_file_row(
        file_id, preview, payload, original_bytes, stored_filename, name, email, now, analysis, actor_tid, actor_oid
    )
    audit = _revision_audit(
        file_id,
        preview["original_filename"],
        "UPLOAD",
        name,
        email,
        now,
        analysis,
        reason=None,
        tid=actor_tid,
        oid=actor_oid,
    )
    summary = analysis["summary"]
    result = {
        "file_id": file_id,
        "inserted": summary["new_values"],
        "changed": summary["updated_values"],
        "unchanged": summary["unchanged_values"],
        "message": "Historical dataset updated successfully. Trained forecast models were not updated.",
        "models_retrained": False,
        "idempotent": False,
    }
    archive_written = False
    prepared_master = None
    try:
        prepared_master = _prepare_from_state(next_state)
        replace_current_file(stored_filename, original_bytes)
        archive_written = True
        commit_master_state(
            expected_version=version,
            next_records=next_records,
            file_row=file_row,
            audit=audit,
            changes=analysis["changes"],
            confirmation={"preview_id": preview_id, "file_id": file_id, "message": result["message"], "result": result},
            **_identity_kwargs(next_state),
        )
    except DatasetConflictError as error:
        if archive_written:
            restore_from_backup(stored_filename)
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except HTTPException:
        if archive_written:
            restore_from_backup(stored_filename)
        raise
    except (DatabaseError, DatasetStorageError) as error:
        if archive_written:
            restore_from_backup(stored_filename)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(error)) from error
    try:
        if prepared_master is not None:
            publish_managed_master(prepared_master)
            prepared_master = None
    except Exception as error:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Historical records were saved, but the managed master workbook could not be replaced.",
        ) from error
    finally:
        if prepared_master is not None and prepared_master.exists():
            prepared_master.unlink()
        if source.exists():
            source.unlink()
        delete_preview(preview_id)
        cleanup_temp_files()
        _invalidate_overview()
    return result


def _invalidate_overview() -> None:
    try:
        from app.routers import overview as overview_router

        overview_router.invalidate_overview_cache()
    except Exception:
        pass


def _structural_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.services.submission_state import SUPPLIED_CELL_MESSAGE

    kept = []
    for error in errors:
        message = str(error.get("message") or "")
        if SUPPLIED_CELL_MESSAGE in message:
            continue
        lowered = message.lower()
        if any(token in lowered for token in ("formula", "header", "column", "sheet", "ambiguous", "missing a budget code")):
            kept.append(error)
    return kept


def _classification_from_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "new_count": analysis["new_count"],
        "changed_count": analysis["changed_count"],
        "unchanged_count": analysis["unchanged_count"],
        "new": [item for item in analysis["changes"] if item["operation"] == "ADD"][:200],
        "changed": [item for item in analysis["changes"] if item["operation"] == "UPDATE"][:200],
    }


def _public_analysis(analysis: dict[str, Any], errors: list[dict[str, Any]]) -> dict[str, Any]:
    structural = _structural_errors(errors)
    actionable = [item for item in analysis["merge_preview"] if item["result"] != "Unchanged"]
    return {
        "can_confirm": bool(analysis["can_confirm"]) and not structural,
        "summary": analysis["summary"],
        "duplicate_conflicts": analysis["duplicate_conflicts"],
        "metadata_conflicts": analysis["metadata_conflicts"],
        "missing_conflicts": analysis["missing_conflicts"],
        "merge_preview": actionable[:400],
        "merge_preview_total": len(actionable),
        "master_version": analysis["master_version"],
        "master_source": analysis.get("master_source"),
        "structural_errors": structural,
        "errors": analysis.get("cell_errors") or [],
        "new_count": analysis["new_count"],
        "changed_count": analysis["changed_count"],
        "unchanged_count": analysis["unchanged_count"],
        "invalid_count": analysis["invalid_count"],
        "new_records": [item for item in analysis["changes"] if item["operation"] == "ADD"][:200],
        "changed_records": [
            {**item, "existing_amount": item.get("previous_amount"), "amount": item.get("new_amount")}
            for item in analysis["changes"]
            if item["operation"] == "UPDATE"
        ][:200],
        "forecast_note": "Saving historical data does not retrain models or update generated forecasts.",
    }


def _identity_kwargs(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "budget_codes": state.get("budget_codes") or [],
        "earliest_month": state.get("earliest_month"),
        "latest_month": state.get("latest_month"),
        "declarations": list(state.get("declarations") or []),
    }


def _prepare_from_state(state: dict[str, Any]):
    return prepare_managed_master(
        state.get("records") or [],
        budget_codes=state.get("budget_codes") or [],
        earliest_month=state.get("earliest_month"),
        latest_month=state.get("latest_month"),
        declarations=state.get("declarations") or [],
    )


def _stamp_source(records: list[dict[str, Any]], changes: list[dict[str, Any]], file_id: str, previous: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changed = {
        (str(item["budget_code"]), str(item["month"]))
        for item in changes
        if item["operation"] != "CLEAR"
    }
    previous_source = {
        (str(item["budget_code"]), str(item["month"])): item.get("source_file_id")
        for item in previous
    }
    stamped = []
    for item in records:
        key = (str(item["budget_code"]), str(item["month"]))
        stamped.append(
            {
                **item,
                "source_file_id": file_id if key in changed else previous_source.get(key),
            }
        )
    return stamped


def _upload_file_row(file_id, preview, payload, data, stored_filename, name, email, now, analysis, tid=None, oid=None) -> dict[str, Any]:
    return {
        "id": file_id,
        "original_filename": preview["original_filename"],
        "stored_filename": stored_filename,
        "content_type": payload.get("content_type"),
        "file_size": int(payload.get("file_size") or len(data)),
        "checksum": file_checksum(data),
        "sheet_name": preview.get("sheet_name") or payload.get("selected_sheet"),
        "revision": 1,
        "content_hash": file_checksum(data),
        "status": "active",
        "staged_path": None,
        "is_legacy": False,
        "uploaded_at": now,
        "uploaded_tid": tid or CLIENT_ACTIVITY_TID,
        "uploaded_oid": oid or CLIENT_ACTIVITY_OID,
        "uploaded_display_name": name,
        "uploaded_username": email,
        "confirmed_at": now,
        "confirmed_display_name": name,
        "confirmed_username": email,
        "validation_summary": analysis["summary"],
        "change_kind": "UPLOAD",
    }


def _revision_audit(file_id, file_name, action, name, email, now, analysis, reason, tid=None, oid=None) -> dict[str, Any]:
    summary = analysis.get("summary") or {}
    return {
        "file_id": file_id,
        "file_name": file_name,
        "action": action,
        "actor_tid": tid or CLIENT_ACTIVITY_TID,
        "actor_oid": oid or CLIENT_ACTIVITY_OID,
        "actor_display_name": name,
        "actor_username": email,
        "occurred_at": now,
        "inserted_count": int(summary.get("new_values") or analysis.get("new_count") or 0),
        "changed_count": int(summary.get("updated_values") or analysis.get("changed_count") or 0),
        "changes": [
            {
                "budget_code": item["budget_code"],
                "month": item["month"],
                "operation": item["operation"],
                "previous": item.get("previous_amount"),
                "new": item.get("new_amount"),
            }
            for item in analysis.get("changes") or []
        ],
        "reason": reason,
        "summary": summary,
    }


def _verified_value(actor, name: str) -> str:
    if isinstance(actor, dict):
        return str(actor.get(name) or "").strip()
    return str(getattr(actor, name, "") or "").strip()


def _actor(body: dict[str, Any] | None, verified=None) -> tuple[str, str, str, str]:
    """Prefer the backend-verified Graph actor. Body labels are not authorization."""
    if verified is not None:
        return (
            _verified_value(verified, "name"),
            _verified_value(verified, "email"),
            _verified_value(verified, "tenant_id"),
            _verified_value(verified, "object_id"),
        )
    name, email = read_activity_metadata(body)
    if not name and not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The signed-in name or email is required for this historical change.")
    return name, email, CLIENT_ACTIVITY_TID, CLIENT_ACTIVITY_OID


@_guard_dataset_mutation
def stage_upload(preview_id: str, body: dict[str, Any]) -> dict[str, Any]:
    preview = _load_preview(preview_id)
    payload = dict(preview.get("payload") or {})
    rows = body.get("rows") if body.get("rows") is not None else payload.get("parsed_rows") or []
    removed = body.get("removed_stage_ids") or []
    missing_decisions = body.get("missing_decisions") or []
    metadata_decisions = body.get("metadata_decisions") or []
    state = load_master_state()
    master_records, version, master_source = state["records"], state["version"], state["source"]
    analysis = analyze_upload(
        rows,
        master_records,
        removed_stage_ids=removed,
        missing_decisions=missing_decisions,
        metadata_decisions=metadata_decisions,
        master_version=version,
        budget_codes=state["budget_codes"],
        earliest_month=state["earliest_month"],
        latest_month=state["latest_month"],
        declarations=state.get("declarations") or [],
    )
    analysis["master_source"] = master_source
    public = _public_analysis(analysis, payload.get("structural_errors") or payload.get("errors") or [])
    stored_rows = analysis["rows"]
    payload.update(public)
    payload["parsed_rows"] = stored_rows
    payload["removed_stage_ids"] = removed
    payload["missing_decisions"] = missing_decisions
    payload["metadata_decisions"] = metadata_decisions
    update_preview_payload(str(preview["id"]), payload)
    return {"preview_id": str(preview["id"]), "expires_at": preview.get("expires_at"), **public, "rows": stored_rows}


def _display_amount(value):
    from app.services.dataset_master import _import_engine

    _import_engine()
    from preprocessing import display_historical_actual

    return display_historical_actual(value)


def master_dataset(page: int, page_size: int, search: str | None, revision_filter: str | None, highlight: str | None) -> dict[str, Any]:
    from app.services.dataset_master import _import_engine

    _import_engine()
    from preprocessing import ACTUALS_DISPLAY_LABEL

    state = load_master_state()
    records, version, source = state["records"], state["version"], state["source"]
    highlights = _highlight_map(highlight)
    grouped: dict[str, dict[str, Any]] = {}
    months: set[str] = set()
    if state.get("earliest_month"):
        months.add(str(state["earliest_month"]))
    if state.get("latest_month"):
        months.add(str(state["latest_month"]))
    for item in state.get("budget_codes") or []:
        code = str(item.get("budget_code") or "")
        if not code:
            continue
        grouped.setdefault(
            code,
            {
                "budget_code": code,
                "description": item.get("description") or "",
                "category": item.get("category") or "",
                "amounts": {},
                "display_amounts": {},
                "raw_amounts": {},
                "highlights": {},
                "submissions": {},
            },
        )
    for item in records:
        code = str(item["budget_code"])
        month = str(item["month"])
        months.add(month)
        row = grouped.setdefault(
            code,
            {
                "budget_code": code,
                "description": item.get("description") or "",
                "category": item.get("category") or "",
                "amounts": {},
                "highlights": {},
                "submissions": {},
            },
        )
        row["amounts"][month] = item.get("amount")
        row.setdefault("display_amounts", {})[month] = _display_amount(item.get("amount"))
        row.setdefault("raw_amounts", {})[month] = item.get("amount")
        if item.get("description"):
            row["description"] = item["description"]
        if item.get("category"):
            row["category"] = item["category"]
        if (code, month) in highlights:
            row["highlights"][month] = highlights[(code, month)]
    declared = {(str(item.get("budget_code")), str(item.get("month"))) for item in (state.get("declarations") or [])}
    for code, row in grouped.items():
        submissions = {}
        for item_code, month in declared:
            if item_code != code or row["amounts"].get(month) is not None:
                continue
            submissions[month] = "declared_missing"
            months.add(month)
        row["submissions"] = submissions
    ordered_months = inclusive_months(state.get("earliest_month"), state.get("latest_month"), sorted(months))
    rows = list(grouped.values())
    needle = (search or "").strip().casefold()
    if needle:
        rows = [row for row in rows if needle in row["budget_code"].casefold() or needle in row["category"].casefold()]
    mode = (revision_filter or "all").strip().lower()
    if mode == "changes":
        rows = [row for row in rows if row["highlights"]]
    elif mode == "new":
        rows = [row for row in rows if any(value == "new" for value in row["highlights"].values())]
    elif mode == "updated":
        rows = [row for row in rows if any(value == "updated" for value in row["highlights"].values())]
    elif mode == "missing":
        rows = [
            row
            for row in rows
            if any(
                row["amounts"].get(month) is None and row.get("submissions", {}).get(month) != "declared_missing"
                for month in ordered_months
            )
        ]
    rows.sort(key=lambda item: item["budget_code"])
    size = max(1, min(int(page_size), 50))
    current = max(1, int(page))
    start = (current - 1) * size
    earliest, latest = (ordered_months[0], ordered_months[-1]) if ordered_months else (None, None)
    return {
        "page": current,
        "page_size": size,
        "total_codes": len(rows),
        "months": ordered_months,
        "rows": rows[start : start + size],
        "master_version": version,
        "data_source": source,
        "earliest_month": earliest,
        "latest_month": latest,
        "models_retrained": False,
        "actuals_display_label": ACTUALS_DISPLAY_LABEL,
    }


def _highlight_map(highlight: str | None) -> dict[tuple[str, str], str]:
    if not highlight:
        return {}
    if highlight.startswith("manual-"):
        records = list_change_records_for_audit(int(highlight.split("-", 1)[1]))
    else:
        records = list_change_records_for_file(highlight)
    flags = {}
    for item in records:
        if item.get("reversed_by_audit_id"):
            continue
        operation = str(item.get("operation") or "")
        if operation == "ADD":
            flags[(str(item["budget_code"]), str(item["month"]))] = "new"
        elif operation in {"UPDATE", "RESTORE"}:
            flags[(str(item["budget_code"]), str(item["month"]))] = "updated"
    return flags


@_guard_dataset_mutation
def preview_manual_edits(body: dict[str, Any]) -> dict[str, Any]:
    state = load_master_state()
    records, version, source = state["records"], state["version"], state["source"]
    plan = manual_edit_plan(
        list(body.get("edits") or []),
        records,
        master_version=version,
        budget_codes=state["budget_codes"],
        declarations=state.get("declarations") or [],
    )
    plan["data_source"] = source
    plan["models_retrained"] = False
    return plan


@_guard_dataset_mutation
def confirm_manual_edits(body: dict[str, Any], actor=None) -> dict[str, Any]:
    name, email, actor_tid, actor_oid = _actor(body, actor)
    state = load_master_state()
    records, version = state["records"], state["version"]
    if int(body.get("master_version") or -1) != version:
        raise HTTPException(status.HTTP_409_CONFLICT, "The historical master changed. Review the edits again.")
    plan = manual_edit_plan(
        list(body.get("edits") or []),
        records,
        master_version=version,
        budget_codes=state["budget_codes"],
        declarations=state.get("declarations") or [],
    )
    if not plan["can_confirm"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, plan["errors"][0] if plan["errors"] else "No valid master changes were submitted.")
    now = _utc_now()
    next_state = next_master_state(state, plan["changes"])
    next_state["records"] = _stamp_source(next_state["records"], plan["changes"], None, records)
    next_records = next_state["records"]
    prepared = _prepare_from_state(next_state)
    try:
        result = commit_master_state(
            expected_version=version,
            next_records=next_records,
            file_row=None,
            audit=_revision_audit(None, "Manual Master Edit", "MANUAL_EDIT", name, email, now, plan, reason=None, tid=actor_tid, oid=actor_oid),
            changes=plan["changes"],
            **_identity_kwargs(next_state),
        )
        publish_managed_master(prepared)
    except DatasetConflictError as error:
        if prepared.exists():
            prepared.unlink()
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except DatabaseError as error:
        if prepared.exists():
            prepared.unlink()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(error)) from error
    _invalidate_overview()
    return {
        "audit_id": result["audit_id"],
        "revision_id": f"manual-{result['audit_id']}",
        "inserted": plan["new_count"],
        "changed": plan["changed_count"],
        "message": "Historical dataset updated successfully. Trained forecast models were not updated.",
        "models_retrained": False,
    }


def list_revisions(page: int, page_size: int) -> dict[str, Any]:
    size = max(1, min(int(page_size), 50))
    current = max(1, int(page))
    files, total_files = list_dataset_files(size, (current - 1) * size)
    manuals = list_manual_audits(size, (current - 1) * size)
    revisions = [_public_file(row) for row in files]
    revisions.extend(_public_manual(row) for row in manuals)
    revisions.sort(key=lambda item: str(item.get("uploaded_at") or ""), reverse=True)
    return {"page": current, "page_size": size, "total": total_files + len(manuals), "revisions": revisions[:size]}


def _public_manual(row: dict[str, Any]) -> dict[str, Any]:
    summary = row.get("summary") or {}
    return {
        "id": f"manual-{row['id']}",
        "file_name": row.get("file_name") or "Manual Master Edit",
        "change_kind": "MANUAL_EDIT",
        "status": "active",
        "uploaded_at": row.get("occurred_at"),
        "uploaded_by": format_activity_label(row.get("actor_display_name"), row.get("actor_username"), False),
        "last_edited_at": None,
        "last_edited_by": None,
        "new_values": summary.get("new_values") or row.get("inserted_count") or 0,
        "updated_values": summary.get("updated_values") or row.get("changed_count") or 0,
        "rollback_allowed": False,
    }


def view_revision(revision_id: str) -> dict[str, Any]:
    if revision_id.startswith("manual-"):
        audit = get_audit_event(int(revision_id.split("-", 1)[1]))
        if audit is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "The revision was not found.")
        return {"revision": _public_manual(audit), "changes": list_change_records_for_audit(int(audit["id"])), "validation_summary": audit.get("summary") or {}}
    record = get_dataset_file(revision_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The revision was not found.")
    return {
        "revision": _public_file(record),
        "changes": list_change_records_for_file(str(record["id"])),
        "validation_summary": record.get("validation_summary") or {},
    }


@_guard_dataset_mutation
def preview_rollback(revision_id: str, body: dict[str, Any]) -> dict[str, Any]:
    reason = str((body or {}).get("reason") or "").strip()
    if not reason:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A rollback reason is required.")
    if revision_id.startswith("manual-"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Manual master edits are viewed from revision history. Roll back the upload that introduced the values.")
    record = get_dataset_file(revision_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The upload revision was not found.")
    if record.get("status") == "rolled_back":
        raise HTTPException(status.HTTP_409_CONFLICT, "This upload has already been rolled back.")
    changes = [item for item in list_change_records_for_file(str(record["id"])) if str(item.get("action")) == "UPLOAD" and not item.get("reversed_by_audit_id")]
    state = load_master_state()
    records, version = state["records"], state["version"]
    plan = plan_rollback(changes, records, state.get("declarations") or [])
    plan["master_version"] = version
    plan["reason"] = reason
    plan["file_name"] = record["original_filename"]
    return plan


@_guard_dataset_mutation
def confirm_rollback(revision_id: str, body: dict[str, Any], actor=None) -> dict[str, Any]:
    name, email, actor_tid, actor_oid = _actor(body, actor)
    reason = str((body or {}).get("reason") or "").strip()
    if not reason:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A rollback reason is required.")
    preview = preview_rollback(revision_id, {"reason": reason})
    if not preview["safe"]:
        message = (
            "Every change from this upload has a later revision. Nothing was rolled back."
            if preview["conflicts"]
            else "This upload has no remaining master changes to roll back."
        )
        raise HTTPException(status.HTTP_409_CONFLICT, message)
    state = load_master_state()
    records, version = state["records"], state["version"]
    if int(body.get("master_version") or preview["master_version"]) != version:
        raise HTTPException(status.HTTP_409_CONFLICT, "The historical master changed. Review the rollback again.")
    now = _utc_now()
    next_state = next_master_state(state, preview["rollback_changes"])
    next_records = next_state["records"]
    status_name = "rolled_back" if not preview["conflicts"] else "partially_rolled_back"
    prepared = _prepare_from_state(next_state)
    record = get_dataset_file(revision_id)
    try:
        result = commit_master_state(
            expected_version=version,
            next_records=next_records,
            file_row=None,
            audit=_revision_audit(
                str(record["id"]),
                record["original_filename"],
                "ROLLBACK",
                name,
                email,
                now,
                {"changes": preview["rollback_changes"], "summary": {"new_values": 0, "updated_values": len(preview["rollback_changes"])}},
                reason=reason,
                tid=actor_tid,
                oid=actor_oid,
            ),
            changes=preview["rollback_changes"],
            **_identity_kwargs(next_state),
            file_status_update={
                "id": str(record["id"]),
                "status": status_name,
                "rollback_reason": reason,
                "rolled_back_at": now,
            },
            reversed_change_ids=[int(item["change_id"]) for item in preview["safe"] if item.get("change_id")],
        )
        publish_managed_master(prepared)
    except DatasetConflictError as error:
        if prepared.exists():
            prepared.unlink()
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except DatabaseError as error:
        if prepared.exists():
            prepared.unlink()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(error)) from error
    _invalidate_overview()
    return {
        "audit_id": result["audit_id"],
        "status": status_name,
        "reversed": len(preview["safe"]),
        "conflicts": len(preview["conflicts"]),
        "message": "The upload was rolled back. The original file and audit history were retained. Trained forecast models were not updated.",
        "models_retrained": False,
    }


def _summary_from_loaded_workbook(workbook: dict[str, Any]) -> dict[str, Any] | None:
    """Read-only counts from a workbook already parsed for Overview."""
    import pandas as pd

    assigned = workbook.get("assigned")
    month_dates = list(workbook.get("month_dates") or [])
    if assigned is None or len(assigned) == 0 or not month_dates:
        return None
    accounts = assigned.loc[
        ~assigned["is_category_row"].fillna(False) & assigned["numeric_account_code"].notna()
    ]
    codes = accounts["cleaned_account_code"].dropna().astype(str).str.strip()
    codes = codes[codes != ""]
    categories = accounts["category"].dropna().astype(str).str.strip()
    categories = categories[(categories != "") & (categories.str.lower() != "nan")]
    if codes.empty or categories.empty:
        return None
    ordered = sorted(pd.Timestamp(item).replace(day=1) for item in month_dates)
    earliest = ordered[0]
    latest = ordered[-1]
    from_database = workbook.get("sheet_name") == "historical_actuals"
    payload = {
        "earliest_month": f"{int(earliest.year):04d}-{int(earliest.month):02d}",
        "latest_month": f"{int(latest.year):04d}-{int(latest.month):02d}",
        "category_count": int(categories.nunique()),
        "budget_code_count": int(codes.nunique()),
        "last_upload_at": None,
        "empty": False,
        "data_source": "database" if from_database else "production_workbook",
    }
    if not from_database:
        payload["data_source_label"] = "Production Historical Dataset"
    return payload


def _coverage_summary(payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.dataset_master import load_master_state
    from app.services.submission_state import coverage_fields, coverage_report

    report = coverage_report(load_master_state())
    return {**payload, **coverage_fields(report)}


def summary() -> dict[str, Any]:
    stored = historical_summary()
    if not stored.get("empty"):
        return _coverage_summary({**stored, "data_source": "database"})
    try:
        from app.routers.overview import OverviewDataError, _load_workbook

        workbook = _load_workbook()
    except OverviewDataError:
        return {**stored, "data_source": None}
    derived = _summary_from_loaded_workbook(workbook)
    if derived is None:
        return {**stored, "data_source": None}
    if derived["data_source"] == "database":
        derived["last_upload_at"] = stored.get("last_upload_at")
    return _coverage_summary(derived)


def list_files(page: int, page_size: int) -> dict[str, Any]:
    size = max(1, min(int(page_size), 50))
    current = max(1, int(page))
    rows, total = list_dataset_files(size, (current - 1) * size)
    return {
        "page": current,
        "page_size": size,
        "total": total,
        "files": [_public_file(row) for row in rows],
    }


def _public_file(row: dict[str, Any]) -> dict[str, Any]:
    legacy = bool(row.get("is_legacy"))
    return {
        "id": str(row["id"]),
        "file_name": row["original_filename"],
        "uploaded_at": row["uploaded_at"],
        "uploaded_by": format_activity_label(
            row.get("uploaded_display_name"),
            row.get("uploaded_username"),
            legacy,
        ),
        "last_edited_at": row.get("last_edited_at"),
        "last_edited_by": None
        if not row.get("last_edited_at")
        else format_activity_label(
            row.get("last_edited_display_name"),
            row.get("last_edited_username"),
            False,
        ),
        "revision": row.get("revision"),
        "sheet_name": row.get("sheet_name"),
        "status": row.get("status") or "active",
        "change_kind": row.get("change_kind") or "UPLOAD",
        "new_values": (row.get("validation_summary") or {}).get("new_values") or 0,
        "updated_values": (row.get("validation_summary") or {}).get("updated_values") or 0,
        "rollback_allowed": (row.get("change_kind") or "UPLOAD") == "UPLOAD" and row.get("status") == "active",
    }


def view_file(file_id: str, search: str | None = None) -> dict[str, Any]:
    record = get_dataset_file(file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The saved dataset was not found.")
    data = read_stored_file(record["stored_filename"])
    from app.services.dataset_storage import ensure_storage_dir

    temp = ensure_storage_dir() / f".tmp-view-{uuid.uuid4().hex}{Path(record['stored_filename']).suffix}"
    temp.write_bytes(data)
    try:
        parsed = parse_historical_dataset(temp, record["original_filename"], record.get("sheet_name"))
    finally:
        if temp.exists():
            temp.unlink()
    rows = parsed.get("rows") or []
    if search:
        needle = search.strip().casefold()
        rows = [row for row in rows if needle in str(row.get("budget_code") or "").casefold()]
    return {
        "file": _public_file(record),
        "months": parsed.get("months") or [],
        "rows": rows,
        "errors": parsed.get("errors") or [],
    }


def download_file(file_id: str) -> tuple[bytes, str, str]:
    record = get_dataset_file(file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The saved dataset was not found.")
    data = read_stored_file(record["stored_filename"])
    filename = record["original_filename"]
    if _suffix(filename) == ".csv":
        data = sanitize_csv_bytes(data)
    content_type = record.get("content_type") or CONTENT_TYPES.get(_suffix(filename), "application/octet-stream")
    return data, filename, content_type


def sanitize_csv_bytes(data: bytes) -> bytes:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for row in reader:
        writer.writerow([_sanitize_csv_cell(item) for item in row])
    return output.getvalue().encode("utf-8")


def _sanitize_csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return f"'{text}"
    return text


@_guard_dataset_mutation
def save_edits(file_id: str, body: dict[str, Any], actor=None) -> dict[str, Any]:
    name, email, actor_tid, actor_oid = _actor(body, actor)
    record = get_dataset_file(file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The saved dataset was not found.")
    expected_revision = int(body.get("revision") or 0)
    if expected_revision != int(record["revision"]):
        raise HTTPException(status.HTTP_409_CONFLICT, "This file was edited by someone else. Refresh and try again.")
    edits = list(body.get("edits") or [])
    if not edits:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No changes were submitted.")
    classified = intended_edit_records(list_historical_actuals(), edits)
    if classified["conflicts"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            classified["conflicts"][0]["message"],
        )
    data = bytearray(read_stored_file(record["stored_filename"]))
    updated_bytes = _apply_edits_to_file(bytes(data), record, edits)
    from app.services.dataset_storage import ensure_storage_dir

    temp = ensure_storage_dir() / f".tmp-edit-{uuid.uuid4().hex}{Path(record['stored_filename']).suffix}"
    temp.write_bytes(updated_bytes)
    try:
        parsed = parse_historical_dataset(temp, record["original_filename"], record.get("sheet_name"))
        if not parsed.get("can_confirm"):
            detail = parsed.get("errors") or []
            message = detail[0]["message"] if detail else "Edited values failed validation."
            raise HTTPException(status.HTTP_400_BAD_REQUEST, message)
        now = _utc_now()
        file_row = {
            "_insert_file": False,
            "id": str(record["id"]),
            "stored_filename": record["stored_filename"],
            "content_type": record.get("content_type"),
            "file_size": len(updated_bytes),
            "checksum": file_checksum(updated_bytes),
            "sheet_name": record.get("sheet_name"),
            "revision": int(record["revision"]) + 1,
            "previous_revision": int(record["revision"]),
            "content_hash": file_checksum(updated_bytes),
            "last_edited_at": now,
            "last_edited_tid": actor_tid,
            "last_edited_oid": actor_oid,
            "last_edited_display_name": name,
            "last_edited_username": email,
        }
        audit = {
            "file_id": str(record["id"]),
            "file_name": record["original_filename"],
            "action": "edit",
            "actor_tid": actor_tid,
            "actor_oid": actor_oid,
            "actor_display_name": name,
            "actor_username": email,
            "occurred_at": now,
            "inserted_count": len(classified["new"]),
            "changed_count": len(classified["changed"]),
            "changes": [
                {
                    "budget_code": item["budget_code"],
                    "month": item["month"],
                    "field": "amount",
                    "previous": item.get("existing_amount"),
                    "new": item["amount"],
                }
                for item in classified["changed"] + classified["new"]
            ],
        }
        apply_historical_changes(file_row, classified["new"], classified["changed"], audit)
        try:
            replace_current_file(record["stored_filename"], updated_bytes)
        except DatasetStorageError:
            restore_from_backup(record["stored_filename"])
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "The file could not be saved. Database changes were not left without the file.")
    except DatasetConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    finally:
        if temp.exists():
            temp.unlink()
        cleanup_temp_files()
        _invalidate_overview()
    return {
        "file_id": str(record["id"]),
        "inserted": len(classified["new"]),
        "changed": len(classified["changed"]),
        "message": "The saved file and historical records were updated. Trained forecast models were not updated.",
        "revision": int(record["revision"]) + 1,
    }


def _apply_edits_to_file(data: bytes, record: dict[str, Any], edits: list[dict[str, Any]]) -> bytes:
    suffix = Path(record["stored_filename"]).suffix.lower()
    from app.services.dataset_storage import ensure_storage_dir

    temp = ensure_storage_dir() / f".tmp-mutate-{uuid.uuid4().hex}{suffix}"
    temp.write_bytes(data)
    try:
        parsed = parse_historical_dataset(temp, record["original_filename"], record.get("sheet_name"))
        row_map = {str(item["budget_code"]): item for item in parsed.get("rows") or []}
        if suffix == ".csv":
            return _apply_csv_edits(data, parsed, edits)
        workbook = load_workbook(temp)
        sheet = workbook[parsed.get("selected_sheet") or workbook.sheetnames[0]]
        month_columns = {month: column for month, column in zip(parsed.get("months") or [], parsed.get("month_columns") or [])}
        headers = [cell.value for cell in next(sheet.iter_rows(min_row=(parsed.get("header_row_idx") or 0) + 1, max_row=(parsed.get("header_row_idx") or 0) + 1))]
        header_index = {str(value).strip(): idx + 1 for idx, value in enumerate(headers) if value is not None}
        for edit in edits:
            code = str(edit["budget_code"])
            source = row_map.get(code)
            if source is None:
                continue
            excel_row = int(source["source_row"])
            if "amount" in edit and edit.get("month") in month_columns:
                column_name = month_columns[edit["month"]]
                col = header_index.get(column_name)
                if col:
                    sheet.cell(excel_row, col).value = float(edit["amount"])
            if "description" in edit and parsed.get("name_column") in header_index:
                sheet.cell(excel_row, header_index[parsed["name_column"]]).value = edit["description"]
            if "category" in edit and parsed.get("category_column") and parsed["category_column"] in header_index:
                sheet.cell(excel_row, header_index[parsed["category_column"]]).value = edit["category"]
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()
    finally:
        if temp.exists():
            temp.unlink()


def _apply_csv_edits(data: bytes, parsed: dict[str, Any], edits: list[dict[str, Any]]) -> bytes:
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return data
    header = rows[0]
    header_index = {str(value).strip(): idx for idx, value in enumerate(header)}
    month_columns = {month: column for month, column in zip(parsed.get("months") or [], parsed.get("month_columns") or [])}
    by_code = {str(item["budget_code"]): item for item in parsed.get("rows") or []}
    code_col = parsed.get("code_column")
    for edit in edits:
        source = by_code.get(str(edit["budget_code"]))
        if source is None:
            continue
        excel_row = int(source["source_row"]) - 1
        if excel_row < 0 or excel_row >= len(rows):
            continue
        if "amount" in edit and edit.get("month") in month_columns:
            idx = header_index.get(month_columns[edit["month"]])
            if idx is not None:
                rows[excel_row][idx] = str(edit["amount"])
        if "description" in edit and parsed.get("name_column") in header_index:
            rows[excel_row][header_index[parsed["name_column"]]] = str(edit["description"])
        if "category" in edit and parsed.get("category_column") in header_index:
            rows[excel_row][header_index[parsed["category_column"]]] = str(edit["category"])
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for row in rows:
        writer.writerow([_sanitize_csv_cell(item) for item in row])
    return output.getvalue().encode("utf-8")


def import_legacy_workbook() -> dict[str, Any]:
    from app.services.dataset_repository import has_baseline_migration

    ensure_forecast_tables()
    if has_baseline_migration():
        return {"imported": False, "reason": "The original baseline migration is already recorded."}
    path = Path(ORIGINAL_ACTUALS_PATH)
    if not path.exists():
        return {"imported": False, "reason": "No existing historical workbook was found at ORIGINAL_ACTUALS_PATH."}
    data = path.read_bytes()
    checksum = file_checksum(data)
    existing = get_legacy_file_by_checksum(checksum)
    if existing:
        return {"imported": False, "reason": "The existing historical workbook is already registered.", "file_id": str(existing["id"])}
    parsed = parse_historical_dataset(path, path.name, None, strict_supplied_cells=False)
    if parsed.get("needs_sheet_selection"):
        first = (parsed.get("sheets") or [{}])[0].get("sheet")
        parsed = parse_historical_dataset(path, path.name, first, strict_supplied_cells=False)
    if not parsed.get("can_confirm"):
        return {"imported": False, "reason": "The existing workbook could not be validated for import."}
    classification = classify_historical_changes(parsed["rows"], list_historical_actuals())
    file_id = str(uuid.uuid4())
    stored_filename = stored_name(file_id, path.name)
    now = _utc_now()
    file_row = {
        "_insert_file": True,
        "id": file_id,
        "original_filename": path.name,
        "stored_filename": stored_filename,
        "content_type": CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        "file_size": len(data),
        "checksum": checksum,
        "sheet_name": parsed.get("selected_sheet"),
        "revision": 1,
        "content_hash": checksum,
        "status": "active",
        "staged_path": None,
        "is_legacy": True,
        "uploaded_at": now,
        "uploaded_tid": "legacy",
        "uploaded_oid": "unknown",
        "uploaded_display_name": "Legacy import",
        "uploaded_username": "",
    }
    audit = {
        "file_id": file_id,
        "file_name": path.name,
        "action": "legacy_import",
        "actor_tid": "legacy",
        "actor_oid": "unknown",
        "actor_display_name": "Legacy import",
        "actor_username": "",
        "occurred_at": now,
        "inserted_count": classification["new_count"],
        "changed_count": classification["changed_count"],
        "changes": [],
    }
    apply_historical_changes(file_row, classification["new"], classification["changed"], audit)
    replace_current_file(stored_filename, data)
    _invalidate_overview()
    return {"imported": True, "file_id": file_id, "inserted": classification["new_count"]}


def migrate_original_baseline() -> dict[str, Any]:
    """Apply the one-time blank-as-declared rule to the original baseline workbook only."""
    from app.services.dataset_master import (
        authoritative_workbook_path,
        extract_original_baseline,
        prepare_managed_master,
        publish_managed_master,
    )
    from app.services.dataset_repository import commit_master_state, get_baseline_migration, list_touched_submission_pairs
    from app.services.retrain_state import SystemLocked
    from app.services.submission_state import (
        BASELINE_MIGRATION_REASON,
        MasterTransitionError,
        assert_transition_preserves_master,
        plan_original_baseline,
    )

    path = authoritative_workbook_path()
    data = path.read_bytes()
    digest = file_checksum(data)
    ensure_forecast_tables()
    existing = get_baseline_migration(digest)
    if existing:
        return {
            "already_applied": True,
            "changed": False,
            "baseline_hash": digest,
            "declaration_count": existing["declaration_count"],
            "numeric_count": existing["numeric_count"],
            "budget_code_count": existing["budget_code_count"],
            "scope_start": existing["scope_start"],
            "scope_end": existing["scope_end"],
            "reason": existing["reason"],
        }
    try:
        with mutation_scope("dataset"):
            existing = get_baseline_migration(digest)
            if existing:
                return {
                    "already_applied": True,
                    "changed": False,
                    "baseline_hash": digest,
                    "declaration_count": existing["declaration_count"],
                    "numeric_count": existing["numeric_count"],
                    "budget_code_count": existing["budget_code_count"],
                    "scope_start": existing["scope_start"],
                    "scope_end": existing["scope_end"],
                    "reason": existing["reason"],
                }
            state = load_master_state()
            baseline = extract_original_baseline(path)
            touched = list_touched_submission_pairs() if state.get("source") == "database" else set()
            plan = plan_original_baseline(baseline, state, touched)
            after = {
                "source": "database",
                "records": plan["records"],
                "declarations": plan["declarations"],
                "budget_codes": plan["budget_codes"],
                "earliest_month": plan["earliest_month"],
                "latest_month": plan["latest_month"],
            }
            if state.get("source") != "database":
                assert_transition_preserves_master(state, after, plan["changes"])
            from app.services.dataset_workflow import same_amount

            before_numbers = {
                (str(item["budget_code"]), str(item["month"])): float(item["amount"])
                for item in state.get("records") or []
                if item.get("budget_code") and item.get("month") and item.get("amount") is not None
            }
            after_numbers = {
                (str(item["budget_code"]), str(item["month"])): float(item["amount"])
                for item in plan["records"]
                if item.get("budget_code") and item.get("month") and item.get("amount") is not None
            }
            dropped_numbers = [
                f"{code} {month}"
                for (code, month), amount in before_numbers.items()
                if (code, month) not in after_numbers or not same_amount(after_numbers[(code, month)], amount)
            ]
            if dropped_numbers:
                raise MasterTransitionError(
                    "Baseline migration would change or drop a stored numeric amount "
                    f"({', '.join(dropped_numbers[:5])})."
                )
            file_id = str(uuid.uuid4())
            now = _utc_now()
            for item in plan["records"]:
                item["source_file_id"] = file_id
            declaration_count = sum(1 for item in plan["changes"] if item["operation"] == "DECLARE_MISSING")
            stored_filename = stored_name(file_id, path.name)
            file_row = {
                "id": file_id,
                "original_filename": path.name,
                "stored_filename": stored_filename,
                "content_type": CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"),
                "file_size": len(data),
                "checksum": digest,
                "sheet_name": "baseline",
                "revision": 1,
                "content_hash": digest,
                "status": "active",
                "staged_path": None,
                "is_legacy": True,
                "uploaded_at": now,
                "uploaded_tid": "legacy-baseline",
                "uploaded_oid": "legacy-baseline",
                "uploaded_display_name": "Legacy baseline migration",
                "uploaded_username": "",
                "confirmed_at": now,
                "confirmed_display_name": "Legacy baseline migration",
                "confirmed_username": "",
                "validation_summary": {"reason": BASELINE_MIGRATION_REASON},
                "change_kind": "legacy_baseline",
            }
            audit = {
                "file_id": file_id,
                "file_name": path.name,
                "action": "legacy_baseline",
                "actor_tid": "legacy-baseline",
                "actor_oid": "legacy-baseline",
                "actor_display_name": "Legacy baseline migration",
                "actor_username": "",
                "occurred_at": now,
                "inserted_count": declaration_count,
                "changed_count": len(plan["changes"]),
                "changes": [],
                "reason": BASELINE_MIGRATION_REASON,
                "summary": {
                    "baseline_hash": digest,
                    "declaration_count": declaration_count,
                    "numeric_count": len(plan["records"]),
                    "budget_code_count": len(plan["budget_codes"]),
                    "ambiguous_count": len(plan["ambiguous"]),
                },
            }
            prepared = prepare_managed_master(
                plan["records"],
                budget_codes=plan["budget_codes"],
                earliest_month=plan["earliest_month"],
                latest_month=plan["latest_month"],
                declarations=plan["declarations"],
            )
            try:
                replace_current_file(stored_filename, data)
                result = commit_master_state(
                    expected_version=int(state.get("version") or 0),
                    next_records=plan["records"],
                    file_row=file_row,
                    audit=audit,
                    changes=plan["changes"],
                    budget_codes=plan["budget_codes"],
                    earliest_month=plan["earliest_month"],
                    latest_month=plan["latest_month"],
                    declarations=plan["declarations"],
                    baseline_migration={
                        "baseline_hash": digest,
                        "scope_start": plan["earliest_month"],
                        "scope_end": plan["latest_month"],
                        "declaration_count": declaration_count,
                        "numeric_count": len(plan["records"]),
                        "budget_code_count": len(plan["budget_codes"]),
                        "reason": BASELINE_MIGRATION_REASON,
                        "applied_at": now,
                    },
                )
                try:
                    publish_managed_master(prepared)
                    prepared = None
                except Exception:
                    logger.exception("Managed workbook publish failed after the baseline migration was saved")
            finally:
                if prepared is not None and prepared.exists():
                    prepared.unlink()
            _invalidate_overview()
            return {
                "already_applied": False,
                "changed": True,
                "baseline_hash": digest,
                "declaration_count": declaration_count,
                "numeric_count": len(plan["records"]),
                "budget_code_count": len(plan["budget_codes"]),
                "scope_start": plan["earliest_month"],
                "scope_end": plan["latest_month"],
                "ambiguous_count": len(plan["ambiguous"]),
                "ambiguous_sample": plan["ambiguous"][:20],
                "reason": BASELINE_MIGRATION_REASON,
                "version": result["version"],
            }
    except SystemLocked:
        return {"already_applied": False, "changed": False, "skipped_locked": True, "baseline_hash": digest}


def backfill_legacy_hyphen_declarations() -> dict[str, Any]:
    """Recover stored "-" cells that no later edit has touched.

    Numeric amounts, including accounting-format zeros, are left unchanged.
    A second call inserts nothing.
    """
    from app.services.dataset_master import authoritative_workbook_path, scan_workbook_explicit_hyphens
    from app.services.dataset_repository import insert_legacy_declarations, list_touched_submission_pairs
    from app.services.submission_state import plan_legacy_hyphen_backfill

    with mutation_scope("dataset"):
        state = load_master_state()
        scanned = scan_workbook_explicit_hyphens(authoritative_workbook_path())
        plan = plan_legacy_hyphen_backfill(
            scanned["hyphen_pairs"],
            records=state.get("records") or [],
            declarations=state.get("declarations") or [],
            touched_pairs=list_touched_submission_pairs() if state.get("source") == "database" else set(),
        )
        report = {
            "recovered": [],
            "already_declared": plan["already_declared"],
            "ambiguous": plan["ambiguous"],
            "numeric_zero_dash_format": scanned["numeric_zero_dash_format"],
            "version": state.get("version"),
        }
        if not plan["recover"] or state.get("source") != "database":
            report["recovered_count"] = 0
            return report
        now = _utc_now()
        result = insert_legacy_declarations(
            expected_version=int(state.get("version") or 0),
            declarations=plan["recover"],
            audit={
                "file_id": None,
                "file_name": "historical_actuals_master.xlsx",
                "action": "legacy_missing_backfill",
                "actor_tid": "legacy-backfill",
                "actor_oid": "legacy-backfill",
                "actor_display_name": "Legacy declaration backfill",
                "actor_username": "",
                "occurred_at": now,
                "reason": "Recover explicit hyphen cells that were not submitted as missing.",
                "summary": {
                    "ambiguous_count": len(plan["ambiguous"]),
                    "already_declared_count": len(plan["already_declared"]),
                },
            },
            changes=[
                {
                    "operation": "DECLARE_MISSING",
                    "budget_code": item["budget_code"],
                    "month": item["month"],
                    "previous_amount": None,
                    "new_amount": None,
                    "previous_submission": "absent",
                    "new_submission": "declared_missing",
                }
                for item in plan["recover"]
            ],
        )
        _invalidate_overview()
        report["recovered"] = result["inserted"]
        report["recovered_count"] = len(result["inserted"])
        report["version"] = result["version"]
        return report


def normalize_budget_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if not text.isdigit() or len(text) > 12:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a numeric Budget Code.")
    return text


def resolve_book_entry_fields(name: Any, category: Any, master: dict[str, str] | None) -> tuple[str, str]:
    """Use the master identity when the code exists. Otherwise keep the entered values."""
    requested_name = str(name or "").strip()
    requested_category = str(category or "").strip()
    if master:
        authoritative_name = str(master.get("budget_name") or "").strip()
        authoritative_category = str(master.get("category") or "").strip()
        if requested_name and requested_name != authoritative_name:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Budget Code Name must match the master dataset for this Budget Code.",
            )
        if requested_category and requested_category != authoritative_category:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Category must match the master dataset for this Budget Code.",
            )
        return authoritative_name, authoritative_category
    if not requested_name or not requested_category:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Enter a Budget Code Name and Category. This Budget Code is not in the master dataset.",
        )
    return requested_name, requested_category


def _master_identity(connection, budget_code: str) -> dict[str, str] | None:
    row = connection.execute(
        """
        SELECT description AS budget_name, category
        FROM historical_actuals
        WHERE budget_code = %s
          AND description IS NOT NULL
          AND description <> ''
        ORDER BY month DESC
        LIMIT 1
        """,
        (budget_code,),
    ).fetchone()
    if not row:
        return None
    return {"budget_name": str(row["budget_name"] or ""), "category": str(row["category"] or "")}


def _book_entry_record(row) -> dict[str, Any]:
    updated_at = row["updated_at"]
    return {
        "budget_code": row["budget_code"],
        "budget_name": row["budget_name"],
        "category": row["category"],
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
        "updated_by": row["updated_by"],
    }


def _registry_rows(connection) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT budget_code, budget_name
        FROM book_entry_accounts
        ORDER BY budget_code
        """
    ).fetchall()
    return [{"budget_code": row["budget_code"], "budget_name": row["budget_name"]} for row in rows]


def _import_preprocessing():
    import sys

    from app.config import BACKEND_DIR

    engine = BACKEND_DIR / "model_training_engine"
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))


def _run_book_entry_change(mutator):
    """Apply a registry change and roll it back when the workbook cannot be saved."""
    _import_preprocessing()
    from preprocessing import book_entry_workbook_path

    path = book_entry_workbook_path()
    snapshot = path.read_bytes() if path is not None and path.is_file() else None
    try:
        with get_connection() as connection:
            result = mutator(connection)
            synchronize_book_entry_workbook(connection)
            return result
    except Exception:
        if path is not None and snapshot is not None:
            try:
                path.write_bytes(snapshot)
            except OSError:
                logger.exception("Book Entry workbook could not be restored after a failed update")
        raise


def synchronize_book_entry_workbook(connection) -> None:
    """Write the registry to the existing workbook. Raise if the file cannot be replaced."""
    from preprocessing import BookEntryConfigurationError, read_book_entry_rows, write_book_entry_workbook

    rows = _registry_rows(connection)
    try:
        current = read_book_entry_rows()
    except BookEntryConfigurationError:
        current = None

    def identity(row):
        return (str(row["budget_code"]), str(row.get("budget_name") or ""))

    if current is not None and sorted(map(identity, current)) == sorted(map(identity, rows)):
        return
    try:
        write_book_entry_workbook(rows)
    except Exception as error:
        logger.exception("Book Entry workbook synchronization failed")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "The Book Entry workbook could not be updated. The registry was not changed.",
        ) from error


def book_entry_codes_from_registry() -> list[str]:
    with get_connection() as connection:
        rows = connection.execute("SELECT budget_code FROM book_entry_accounts ORDER BY budget_code").fetchall()
    return [row["budget_code"] for row in rows]


def _import_book_entries_if_needed(connection) -> None:
    from preprocessing import read_book_entry_rows

    state = connection.execute(
        "SELECT initialized_at FROM book_entry_registry_state WHERE id = 1"
    ).fetchone()
    if state is not None:
        return
    for row in read_book_entry_rows():
        master = _master_identity(connection, row["budget_code"])
        connection.execute(
            """
            INSERT INTO book_entry_accounts (budget_code, budget_name, category, updated_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (budget_code) DO NOTHING
            """,
            (
                row["budget_code"],
                row["budget_name"],
                master["category"] if master else "",
                "workbook-import",
            ),
        )
    connection.execute(
        "INSERT INTO book_entry_registry_state (id, initialized_at) VALUES (1, NOW())"
    )


def ensure_book_entry_registry() -> None:
    """Import the workbook once. Later startups keep the saved registry, including an empty one."""
    _import_preprocessing()
    from preprocessing import set_book_entry_code_provider

    ensure_forecast_tables()
    try:
        _run_book_entry_change(_import_book_entries_if_needed)
    except Exception:
        logger.exception("Book Entry workbook synchronization failed during startup")
        try:
            with get_connection() as connection:
                state = connection.execute(
                    "SELECT initialized_at FROM book_entry_registry_state WHERE id = 1"
                ).fetchone()
            if state is not None:
                set_book_entry_code_provider(book_entry_codes_from_registry)
        except Exception:
            logger.exception("Book Entry registry could not be read")
        return
    set_book_entry_code_provider(book_entry_codes_from_registry)


def list_master_categories() -> list[str]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT category
            FROM historical_actuals
            WHERE category IS NOT NULL AND category <> ''
            ORDER BY category
            """
        ).fetchall()
    return [row["category"] for row in rows]


def list_book_entries(search: str = "", category: str = "", name: str = "") -> dict[str, Any]:
    term = str(search or "").strip()
    name_term = str(name or "").strip()
    selected = str(category or "").strip()
    like = f"%{term}%"
    name_like = f"%{name_term}%"
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT budget_code, budget_name, category, updated_at, updated_by
            FROM book_entry_accounts
            WHERE (%s = '' OR budget_code ILIKE %s)
              AND (%s = '' OR budget_name ILIKE %s)
              AND (%s = '' OR category = %s)
            ORDER BY category, budget_code
            """,
            (term, like, name_term, name_like, selected, selected),
        ).fetchall()
        total = connection.execute("SELECT COUNT(*) AS total FROM book_entry_accounts").fetchone()["total"]
        registered = connection.execute(
            """
            SELECT DISTINCT category
            FROM book_entry_accounts
            WHERE category IS NOT NULL AND category <> ''
            """
        ).fetchall()
    categories = list(dict.fromkeys([*list_master_categories(), *[row["category"] for row in registered]]))
    categories.sort()
    return {
        "count": int(total),
        "accounts": [_book_entry_record(row) for row in rows],
        "categories": categories,
    }


def lookup_book_entry_code(budget_code: str) -> dict[str, Any]:
    code = normalize_budget_code(budget_code)
    with get_connection() as connection:
        master = _master_identity(connection, code)
        existing = connection.execute(
            "SELECT budget_code FROM book_entry_accounts WHERE budget_code = %s",
            (code,),
        ).fetchone()
    if not master:
        return {"budget_code": code, "found": False, "already_registered": bool(existing)}
    return {
        "budget_code": code,
        "found": True,
        "budget_name": master["budget_name"],
        "category": master["category"],
        "already_registered": bool(existing),
    }


def _actor_label(actor) -> str:
    if actor is None:
        return ""
    return str(getattr(actor, "name", "") or getattr(actor, "email", "") or getattr(actor, "object_id", ""))


def add_book_entry(body: dict[str, Any], actor=None) -> dict[str, Any]:
    code = normalize_budget_code(body.get("budget_code"))

    def mutate(connection):
        existing = connection.execute(
            "SELECT budget_code, budget_name, category FROM book_entry_accounts WHERE budget_code = %s",
            (code,),
        ).fetchone()
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, "This Budget Code is already a Book Entry account.")
        master = _master_identity(connection, code)
        name, category = resolve_book_entry_fields(body.get("budget_name"), body.get("category"), master)
        connection.execute(
            """
            INSERT INTO book_entry_accounts (budget_code, budget_name, category, updated_by)
            VALUES (%s, %s, %s, %s)
            """,
            (code, name, category, _actor_label(actor)),
        )
        return {"budget_code": code, "budget_name": name, "category": category}

    record = _run_book_entry_change(mutate)
    _import_preprocessing()
    from preprocessing import clear_book_entry_cache

    clear_book_entry_cache()
    return {"account": record, "previous": None, "new": record}


def update_book_entry(budget_code: str, body: dict[str, Any], actor=None) -> dict[str, Any]:
    code = normalize_budget_code(budget_code)
    if "budget_code" in body and str(body.get("budget_code") or "").strip() not in ("", code):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Budget Code cannot be changed. Remove this account and add the correct Budget Code.",
        )

    def mutate(connection):
        existing = connection.execute(
            """
            SELECT budget_code, budget_name, category
            FROM book_entry_accounts
            WHERE budget_code = %s
            """,
            (code,),
        ).fetchone()
        if not existing:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "This Book Entry account was not found.")
        master = _master_identity(connection, code)
        name, category = resolve_book_entry_fields(body.get("budget_name"), body.get("category"), master)
        previous = {
            "budget_code": existing["budget_code"],
            "budget_name": existing["budget_name"],
            "category": existing["category"],
        }
        connection.execute(
            """
            UPDATE book_entry_accounts
            SET budget_name = %s, category = %s, updated_at = NOW(), updated_by = %s
            WHERE budget_code = %s
            """,
            (name, category, _actor_label(actor), code),
        )
        return previous, {"budget_code": code, "budget_name": name, "category": category}

    previous, record = _run_book_entry_change(mutate)
    _import_preprocessing()
    from preprocessing import clear_book_entry_cache

    clear_book_entry_cache()
    return {"account": record, "previous": previous, "new": record}


def delete_book_entry(budget_code: str) -> dict[str, Any]:
    code = normalize_budget_code(budget_code)

    def mutate(connection):
        existing = connection.execute(
            """
            SELECT budget_code, budget_name, category
            FROM book_entry_accounts
            WHERE budget_code = %s
            """,
            (code,),
        ).fetchone()
        if not existing:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "This Book Entry account was not found.")
        previous = {
            "budget_code": existing["budget_code"],
            "budget_name": existing["budget_name"],
            "category": existing["category"],
        }
        connection.execute("DELETE FROM book_entry_accounts WHERE budget_code = %s", (code,))
        return previous

    previous = _run_book_entry_change(mutate)
    _import_preprocessing()
    from preprocessing import clear_book_entry_cache

    clear_book_entry_cache()
    return {"account": previous, "previous": previous, "new": None}


def book_entry_workbook_bytes() -> bytes:
    _import_preprocessing()
    from preprocessing import book_entry_workbook_path

    _run_book_entry_change(lambda connection: None)
    path = book_entry_workbook_path()
    if path is None or not path.is_file():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "The Book Entry workbook is not available.")
    return path.read_bytes()
