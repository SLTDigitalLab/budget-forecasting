"""Historical dataset management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, Query, UploadFile
from fastapi.responses import Response

from app.services import dataset_service
from app.services.action_audit import record_action
from app.services.microsoft_auth import require_editor

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.get("/summary")
def dataset_summary() -> dict:
    return dataset_service.summary()


@router.get("/editor-access")
def dataset_editor_access(authorization: str | None = Header(default=None)) -> dict:
    """Authorize a non-mutating Settings action from Graph and the editor policy."""
    actor = require_editor(authorization)
    return {"authorized": True, "object_id": actor.object_id}


@router.post("/preview")
async def dataset_preview(
    file: UploadFile = File(...),
    sheet_name: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    actor = require_editor(authorization)
    data = await file.read()
    result = dataset_service.preview_upload(file.filename or "dataset.xlsx", data, sheet_name, actor)
    record_action(actor, "dataset_preview", "succeeded")
    return result


@router.post("/confirm")
def dataset_confirm(payload: dict | None = None, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    body = payload or {}
    preview_id = str(body.get("preview_id") or "")
    result = dataset_service.confirm_upload(preview_id, body, actor)
    record_action(actor, "dataset_confirm", "succeeded", revision_id=str(result.get("file_id") or preview_id))
    return result


@router.post("/previews/{preview_id}/stage")
def dataset_stage(preview_id: str, payload: dict | None = None, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    result = dataset_service.stage_upload(preview_id, payload or {})
    record_action(actor, "dataset_stage", "succeeded", revision_id=preview_id)
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
    result = dataset_service.preview_manual_edits(payload or {})
    record_action(actor, "master_preview", "succeeded")
    return result


@router.post("/master/confirm")
def dataset_master_confirm(payload: dict | None = None, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    result = dataset_service.confirm_manual_edits(payload or {}, actor)
    record_action(actor, "master_confirm", "succeeded", revision_id=str(result.get("revision_id") or ""))
    return result


@router.get("/revisions")
def dataset_revisions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
) -> dict:
    return dataset_service.list_revisions(page, page_size)


@router.get("/revisions/{revision_id}")
def dataset_revision(revision_id: str) -> dict:
    return dataset_service.view_revision(revision_id)


@router.post("/revisions/{revision_id}/rollback/preview")
def dataset_rollback_preview(revision_id: str, payload: dict | None = None, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    result = dataset_service.preview_rollback(revision_id, payload or {})
    record_action(actor, "rollback_preview", "succeeded", revision_id=revision_id)
    return result


@router.post("/revisions/{revision_id}/rollback/confirm")
def dataset_rollback_confirm(revision_id: str, payload: dict | None = None, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    result = dataset_service.confirm_rollback(revision_id, payload or {}, actor)
    record_action(actor, "rollback_confirm", "succeeded", revision_id=revision_id)
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
def dataset_download(file_id: str) -> Response:
    data, filename, content_type = dataset_service.download_file(file_id)
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/files/{file_id}/edits")
def dataset_edits(file_id: str, payload: dict, authorization: str | None = Header(default=None)) -> dict:
    actor = require_editor(authorization)
    result = dataset_service.save_edits(file_id, payload, actor)
    record_action(actor, "dataset_edit", "succeeded", revision_id=file_id)
    return result
