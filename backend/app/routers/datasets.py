"""Historical dataset management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, Query, UploadFile
from fastapi.responses import Response

from app.services import dataset_service
from app.services.action_audit import list_activity_events, record_action
from app.services.microsoft_auth import describe_caller, require_editor

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


def _edit_details(payload: dict | None, result: dict | None = None) -> dict:
    body = payload or {}
    edits = list(body.get("edits") or [])
    compact = []
    for item in edits[:40]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "budget_code": item.get("budget_code"),
                "month": item.get("month"),
                "before": item.get("previous_amount", item.get("existing_amount", item.get("previous"))),
                "after": item.get("new_amount", item.get("amount", item.get("new"))),
                "operation": item.get("operation"),
            }
        )
    details = {"edits": compact, "edit_count": len(edits)}
    if isinstance(result, dict):
        for key in ("inserted", "changed", "revision_id", "file_id", "revision", "message"):
            if key in result and result.get(key) is not None:
                details[key] = result.get(key)
    return details


def _book_entry_audit(actor, action: str, result: dict) -> None:
    account = result.get("account") or {}
    previous = result.get("previous")
    new = result.get("new") or account
    code = str((account or previous or new or {}).get("budget_code") or "")
    record_action(
        actor,
        action,
        "succeeded",
        revision_id=code or None,
        target=code or "book-entry",
        details={"before": previous, "after": new, "budget_code": code},
    )


def _optional_actor(authorization: str | None):
    if not authorization:
        return None
    try:
        return describe_caller(authorization)
    except Exception:
        return None


@router.get("/summary")
def dataset_summary() -> dict:
    return dataset_service.summary()


@router.get("/editor-access")
def dataset_editor_access(authorization: str | None = Header(default=None)) -> dict:
    """Authorize a non-mutating Settings action from Graph and the editor policy."""
    actor = require_editor(authorization)
    return {"authorized": True, "object_id": actor.object_id}


@router.get("/activity")
def dataset_activity(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    day: str | None = Query(default=None),
) -> dict:
    """Append-only Settings activity log. Newest first; day uses Asia/Colombo boundaries."""
    return list_activity_events(page=page, page_size=page_size, day=day)


@router.post("/preview")
async def dataset_preview(
    file: UploadFile = File(...),
    sheet_name: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    actor = require_editor(authorization)
    data = await file.read()
    filename = file.filename or "dataset.xlsx"
    result = dataset_service.preview_upload(filename, data, sheet_name, actor)
    record_action(
        actor,
        "dataset_preview",
        "succeeded" if result.get("can_confirm") or result.get("needs_sheet_selection") else "failed",
        target=filename,
        details={
            "filename": filename,
            "sheet_name": sheet_name or result.get("selected_sheet"),
            "needs_sheet_selection": bool(result.get("needs_sheet_selection")),
            "can_confirm": bool(result.get("can_confirm")),
            "preview_id": result.get("preview_id"),
        },
    )
    return result


@router.post("/confirm")
def dataset_confirm(payload: dict | None = None, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    body = payload or {}
    preview_id = str(body.get("preview_id") or "")
    result = dataset_service.confirm_upload(preview_id, body, actor)
    file_id = str(result.get("file_id") or preview_id)
    record_action(
        actor,
        "dataset_confirm",
        "succeeded",
        revision_id=file_id,
        target=file_id,
        details={
            "preview_id": preview_id,
            "inserted": result.get("inserted"),
            "changed": result.get("changed"),
            "message": result.get("message"),
        },
    )
    return result


@router.get("/previews/{preview_id}/confirmation")
def dataset_preview_confirmation(preview_id: str, authorization: str | None = Header(default=None)) -> dict:
    require_editor(authorization)
    return dataset_service.preview_confirmation_status(preview_id)


@router.post("/previews/{preview_id}/stage")
def dataset_stage(preview_id: str, payload: dict | None = None, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    result = dataset_service.stage_upload(preview_id, payload or {})
    record_action(
        actor,
        "dataset_stage",
        "succeeded",
        revision_id=preview_id,
        target=preview_id,
        details={"preview_id": preview_id},
    )
    return result


@router.get("/master")
def dataset_master(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    search: str | None = Query(default=None),
    revision_filter: str | None = Query(default="all"),
    highlight: str | None = Query(default=None),
) -> dict:
    return dataset_service.master_dataset(page, page_size, search, revision_filter, highlight)


@router.post("/master/preview")
def dataset_master_preview(payload: dict | None = None, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    body = payload or {}
    result = dataset_service.preview_manual_edits(body)
    record_action(
        actor,
        "master_preview",
        "succeeded",
        target="historical-master",
        details=_edit_details(body, result),
    )
    return result


@router.post("/master/confirm")
def dataset_master_confirm(payload: dict | None = None, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    body = payload or {}
    result = dataset_service.confirm_manual_edits(body, actor)
    revision_id = str(result.get("revision_id") or "")
    record_action(
        actor,
        "master_confirm",
        "succeeded",
        revision_id=revision_id,
        target=revision_id or "historical-master",
        details=_edit_details(body, result),
    )
    return result


@router.get("/revisions")
def dataset_revisions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
) -> dict:
    return dataset_service.list_revisions(page, page_size)


@router.get("/revisions/{revision_id}")
def dataset_revision(revision_id: str, authorization: str | None = Header(default=None)) -> dict:
    result = dataset_service.view_revision(revision_id)
    actor = _optional_actor(authorization)
    if actor is not None:
        record_action(
            actor,
            "revision_view",
            "succeeded",
            revision_id=revision_id,
            target=revision_id,
            details={"file_name": (result.get("revision") or {}).get("file_name")},
        )
    return result


@router.post("/revisions/{revision_id}/rollback/preview")
def dataset_rollback_preview(revision_id: str, payload: dict | None = None, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    result = dataset_service.preview_rollback(revision_id, payload or {})
    record_action(
        actor,
        "rollback_preview",
        "succeeded",
        revision_id=revision_id,
        target=revision_id,
        details={"reason": (payload or {}).get("reason")},
    )
    return result


@router.post("/revisions/{revision_id}/rollback/confirm")
def dataset_rollback_confirm(revision_id: str, payload: dict | None = None, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    body = payload or {}
    result = dataset_service.confirm_rollback(revision_id, body, actor)
    record_action(
        actor,
        "rollback_confirm",
        "succeeded",
        revision_id=revision_id,
        target=revision_id,
        details={"reason": body.get("reason"), "message": result.get("message") if isinstance(result, dict) else None},
    )
    return result


@router.get("/files")
def dataset_files(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
) -> dict:
    return dataset_service.list_files(page, page_size)


@router.get("/files/{file_id}")
def dataset_view(file_id: str, search: str | None = Query(default=None)) -> dict:
    return dataset_service.view_file(file_id, search)


@router.get("/files/{file_id}/download")
def dataset_download(file_id: str, authorization: str | None = Header(default=None)) -> Response:
    data, filename, content_type = dataset_service.download_file(file_id)
    actor = _optional_actor(authorization)
    if actor is not None:
        record_action(
            actor,
            "revision_download",
            "succeeded",
            revision_id=file_id,
            target=file_id,
            details={"filename": filename, "bytes": len(data)},
        )
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/files/{file_id}/edits")
def dataset_edits(file_id: str, payload: dict, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    result = dataset_service.save_edits(file_id, payload, actor)
    record_action(
        actor,
        "dataset_edit",
        "succeeded",
        revision_id=file_id,
        target=file_id,
        details=_edit_details(payload, result),
    )
    return result


@router.get("/book-entries")
def book_entries(
    search: str = Query(default=""),
    category: str = Query(default=""),
    name: str = Query(default=""),
) -> dict:
    return dataset_service.list_book_entries(search, category, name)


@router.get("/book-entries/lookup")
def book_entry_lookup(budget_code: str = Query(...)) -> dict:
    return dataset_service.lookup_book_entry_code(budget_code)


@router.get("/book-entries/workbook")
def book_entry_workbook() -> Response:
    data = dataset_service.book_entry_workbook_bytes()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Book entry accounts.xlsx"'},
    )


@router.post("/book-entries")
def create_book_entry(payload: dict, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    result = dataset_service.add_book_entry(payload, actor)
    _book_entry_audit(actor, "BOOK_ENTRY_ADD", result)
    return result["account"]


@router.put("/book-entries/{budget_code}")
def edit_book_entry(budget_code: str, payload: dict, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    result = dataset_service.update_book_entry(budget_code, payload, actor)
    _book_entry_audit(actor, "BOOK_ENTRY_EDIT", result)
    return result["account"]


@router.delete("/book-entries/{budget_code}")
def remove_book_entry(budget_code: str, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    result = dataset_service.delete_book_entry(budget_code)
    _book_entry_audit(actor, "BOOK_ENTRY_DELETE", result)
    return {"removed": budget_code}
