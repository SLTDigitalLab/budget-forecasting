"""Settings retraining jobs. Training does not publish until Save Models."""

from __future__ import annotations

from fastapi import APIRouter, Header

from app.services.microsoft_auth import describe_caller, require_editor
from app.services.retrain_jobs import cancel_job, model_status, retry_job, save_job, start_job, status

router = APIRouter(prefix="/api/retraining", tags=["retraining"])


def _optional_identity(authorization: str | None):
    if not authorization:
        return None
    return describe_caller(authorization)


@router.get("/model-status")
def retrain_model_status() -> dict:
    return model_status()


@router.get("/status")
def retrain_status(authorization: str | None = Header(default=None)) -> dict:
    return status(_optional_identity(authorization))


@router.post("/jobs")
def retrain_start(authorization: str | None = Header(default=None)) -> dict:
    return start_job(require_editor(authorization))


@router.post("/jobs/{job_id}/save")
def retrain_save(job_id: str, authorization: str | None = Header(default=None)) -> dict:
    return save_job(job_id, require_editor(authorization))


@router.post("/jobs/{job_id}/cancel")
def retrain_cancel(job_id: str, authorization: str | None = Header(default=None)) -> dict:
    return cancel_job(job_id, require_editor(authorization))


@router.post("/jobs/{job_id}/retry")
def retrain_retry(job_id: str, authorization: str | None = Header(default=None)) -> dict:
    return retry_job(job_id, require_editor(authorization))
