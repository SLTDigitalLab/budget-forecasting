"""Shared retraining lock and job state.

The decision functions mutate plain dictionaries. Postgres and the in-memory
backend both persist those dictionaries under one exclusive transaction, so
API workers and the retraining command observe the same lock.
"""

from __future__ import annotations

import os
import re
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.db import Jsonb, get_connection

HEARTBEAT_STALE = timedelta(seconds=90)
ABANDON_AFTER = timedelta(hours=6)
COUNTDOWN = timedelta(seconds=5)
TRAINING_STATES = {"QUEUED", "TRAINING", "VALIDATING", "SAVING"}
LOCK_STATES = {
    "QUEUED",
    "TRAINING",
    "VALIDATING",
    "READY_TO_SAVE",
    "SAVING",
    "SAVE_FAILED",
    "SAVED",
}
TERMINAL_STATES = {"FAILED", "INTERRUPTED", "CANCELLED", "ABANDONED", "CLOSED"}
LOCK_MESSAGE = "Model training in progress. Please wait."
BUSY_MESSAGE = (
    "A forecast or historical update is already running. "
    "Wait for it to finish, then try retraining again."
)
DUPLICATE_MESSAGE = "Model training is already in progress."
MISMATCH_MESSAGE = (
    "The historical master changed after this training snapshot. "
    "Retraining is required before the models can be saved."
)


class CoordinationError(Exception):
    def __init__(self, detail: str, code: str, status_code: int):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code


class SystemLocked(CoordinationError):
    def __init__(self, detail: str = LOCK_MESSAGE):
        super().__init__(detail, "SYSTEM_LOCKED", 423)


class OperationInProgress(CoordinationError):
    def __init__(self, detail: str = BUSY_MESSAGE):
        super().__init__(detail, "OPERATION_IN_PROGRESS", 409)


class RetrainAlreadyActive(CoordinationError):
    def __init__(self, detail: str = DUPLICATE_MESSAGE):
        super().__init__(detail, "RETRAIN_IN_PROGRESS", 409)


class SnapshotMismatch(CoordinationError):
    def __init__(self, detail: str = MISMATCH_MESSAGE):
        super().__init__(detail, "SNAPSHOT_MISMATCH", 409)


class RetrainForbidden(CoordinationError):
    def __init__(self, detail: str = "This browser session is not authorized to manage the training job."):
        super().__init__(detail, "RETRAIN_FORBIDDEN", 403)


class RetrainStateError(CoordinationError):
    def __init__(self, detail: str):
        super().__init__(detail, "RETRAIN_STATE", 409)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def empty_state() -> dict[str, Any]:
    return {
        "coordination": {
            "retrain_job_id": None,
            "active_operations": 0,
            "updated_at": None,
        },
        "operations": [],
        "jobs": {},
        "publication": None,
    }


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def worker_id_for_process() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if handle:
            kernel.CloseHandle(handle)
            return True
        return kernel.GetLastError() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def worker_process_alive(worker_id: str | None) -> bool | None:
    """True/False for this host. None when the worker is on another machine."""
    if not worker_id or ":" not in str(worker_id):
        return None
    host, _, pid_text = str(worker_id).rpartition(":")
    if host != socket.gethostname():
        return None
    try:
        pid = int(pid_text)
    except ValueError:
        return None
    return _pid_exists(pid)


def _release_if_holder(state: dict[str, Any], job_id: str) -> None:
    coordination = state["coordination"]
    if coordination.get("retrain_job_id") == job_id:
        coordination["retrain_job_id"] = None
        coordination["updated_at"] = utcnow()


def discard_candidate_file(path: str | None, unlink) -> None:
    if not path or unlink is None:
        return
    unlink(path)


def _job_is_dead(job: dict[str, Any], now: datetime) -> bool:
    alive = worker_process_alive(job.get("worker_id"))
    if alive is False:
        return True
    heartbeat = _aware(job.get("heartbeat_at") or job.get("created_at"))
    if heartbeat is None:
        return True
    return now - heartbeat > HEARTBEAT_STALE


def sweep(state: dict[str, Any], now: datetime, publication_matches, candidate_exists, unlink) -> None:
    """Recover dead workers, expired countdowns, and abandoned save prompts."""
    now = _aware(now) or utcnow()
    live_operations = []
    for operation in state.get("operations") or []:
        heartbeat = _aware(_as_datetime(operation.get("heartbeat_at")))
        alive = worker_process_alive(operation.get("worker_id"))
        if alive is False or heartbeat is None or now - heartbeat > HEARTBEAT_STALE:
            continue
        live_operations.append(operation)
    state["operations"] = live_operations
    state["coordination"]["active_operations"] = len(live_operations)
    for job in list(state["jobs"].values()):
        state_name = job.get("state")
        if state_name in {"READY_TO_SAVE", "SAVE_FAILED"}:
            abandon_at = _aware(job.get("abandon_at"))
            if abandon_at is not None and abandon_at <= now:
                discard_candidate_file(job.get("candidate_path"), unlink)
                job["candidate_path"] = None
                job["state"] = "ABANDONED"
                job["ended_at"] = now
                job["error_detail"] = (
                    "The validated candidate was discarded because Save Models was not completed "
                    "before the recovery deadline. The previous production model is still active."
                )
                job["error_code"] = "ABANDONED"
                job["updated_at"] = now
                _release_if_holder(state, job["id"])
            continue
        if state_name == "SAVED":
            unlock_at = _aware(job.get("unlock_at"))
            if unlock_at is not None and unlock_at <= now:
                discard_candidate_file(job.get("candidate_path"), unlink)
                job["candidate_path"] = None
                job["state"] = "CLOSED"
                job["ended_at"] = job.get("ended_at") or now
                job["updated_at"] = now
                _release_if_holder(state, job["id"])
            continue
        if state_name in TRAINING_STATES and _job_is_dead(job, now):
            _recover_dead_job(state, job, now, publication_matches, candidate_exists, unlink)


def _recover_dead_job(state, job, now, publication_matches, candidate_exists, unlink) -> None:
    if job.get("state") == "SAVING" and publication_matches(job):
        job["state"] = "SAVED"
        job["publication_status"] = "published"
        job["unlock_at"] = now + COUNTDOWN
        job["ended_at"] = now
        job["updated_at"] = now
        job["error_detail"] = None
        job["cache_warning"] = (
            "The production model was replaced, but the worker stopped before the status was saved."
        )
        return
    if job.get("state") == "SAVING":
        job["state"] = "SAVE_FAILED"
        job["error_detail"] = (
            "Saving was interrupted before the production model could be replaced. "
            "The previous production model is still active."
        )
        job["error_code"] = "SAVE_INTERRUPTED"
        job["abandon_at"] = now + ABANDON_AFTER
        job["heartbeat_at"] = now
        job["updated_at"] = now
        return
    if job.get("candidate_validated") and candidate_exists(job):
        job["state"] = "READY_TO_SAVE"
        job["error_detail"] = (
            "Training was interrupted after the candidate was validated. Save Models is still available."
        )
        job["error_code"] = "INTERRUPTED_READY"
        job["abandon_at"] = now + ABANDON_AFTER
        job["heartbeat_at"] = now
        job["updated_at"] = now
        return
    discard_candidate_file(job.get("candidate_path"), unlink)
    job["candidate_path"] = None
    job["candidate_validated"] = False
    job["state"] = "INTERRUPTED"
    job["ended_at"] = now
    job["updated_at"] = now
    job["error_code"] = "INTERRUPTED"
    job["error_detail"] = (
        "Training was interrupted before a validated candidate was ready. "
        "The previous production model is still active."
    )
    _release_if_holder(state, job["id"])


def lock_held(state: dict[str, Any], now: datetime | None = None) -> bool:
    now = _aware(now) or utcnow()
    job_id = state["coordination"].get("retrain_job_id")
    if not job_id:
        return False
    job = state["jobs"].get(job_id)
    if not job or job.get("state") not in LOCK_STATES:
        return False
    if job.get("state") == "SAVED":
        unlock_at = _aware(job.get("unlock_at"))
        if unlock_at is not None and unlock_at <= now:
            return False
    return True


def begin_operation(state: dict[str, Any], kind: str = "operation", now: datetime | None = None) -> str:
    now = _aware(now) or utcnow()
    if lock_held(state, now):
        raise SystemLocked()
    operation_id = uuid.uuid4().hex
    state.setdefault("operations", []).append(
        {
            "id": operation_id,
            "kind": kind,
            "worker_id": worker_id_for_process(),
            "heartbeat_at": now,
        }
    )
    coordination = state["coordination"]
    coordination["active_operations"] = len(state["operations"])
    coordination["updated_at"] = now
    return operation_id


def touch_operation(state: dict[str, Any], operation_id: str, now: datetime | None = None) -> None:
    moment = _aware(now) or utcnow()
    for operation in state.get("operations") or []:
        if operation.get("id") == operation_id:
            operation["heartbeat_at"] = moment
    state["coordination"]["updated_at"] = moment


def end_operation(state: dict[str, Any], operation_id: str | None = None, now: datetime | None = None) -> None:
    if operation_id is None:
        operations = list(state.get("operations") or [])
        state["operations"] = operations[:-1]
    else:
        state["operations"] = [item for item in (state.get("operations") or []) if item.get("id") != operation_id]
    coordination = state["coordination"]
    coordination["active_operations"] = len(state["operations"])
    coordination["updated_at"] = _aware(now) or utcnow()


def _as_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return None


def _reject_if_busy(state: dict[str, Any], now: datetime) -> None:
    if lock_held(state, now):
        raise RetrainAlreadyActive()
    if int(state["coordination"].get("active_operations") or 0) > 0:
        raise OperationInProgress()


def create_job(state: dict[str, Any], job: dict[str, Any], now: datetime) -> dict[str, Any]:
    now = _aware(now) or utcnow()
    _reject_if_busy(state, now)
    state["jobs"][job["id"]] = job
    state["coordination"]["retrain_job_id"] = job["id"]
    state["coordination"]["updated_at"] = now
    return job


def _identity_value(identity, name: str):
    if identity is None:
        return None
    if isinstance(identity, dict):
        return identity.get(name)
    return getattr(identity, name, None)


_SECRET_TEXT = re.compile(
    r"(Bearer\s+\S+|access_token=\S+|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_PRIVATE_PATH = re.compile(r"(?i)([^\s]*retrain_candidates[^\s]*|[A-Za-z]:\\[^\s]+)")


def redact_public_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = _SECRET_TEXT.sub("[redacted]", str(value))
    return _PRIVATE_PATH.sub("[redacted-path]", text)


def can_manage_job(job: dict[str, Any] | None, identity) -> bool:
    """Current editor permission from Graph. A matching id string from another identity source is not ownership."""
    if job is None or identity is None:
        return False
    return bool(_identity_value(identity, "is_editor"))


def authorize(state: dict[str, Any], job_id: str, identity) -> dict[str, Any]:
    job = state["jobs"].get(job_id)
    if not can_manage_job(job, identity):
        raise RetrainForbidden()
    return job


def append_progress(
    job: dict[str, Any],
    message: str,
    stage: str | None,
    current: int | None,
    total: int | None,
    now: datetime,
    budget_code: str | None = None,
) -> None:
    if message:
        lines = [line for line in str(job.get("logs") or "").splitlines() if line != ""]
        lines.append(message)
        job["logs"] = "\n".join(lines[-400:])
    if stage:
        job["progress_stage"] = stage
    if current is not None:
        job["progress_current"] = int(current)
    if total is not None:
        job["progress_total"] = int(total)
    if budget_code:
        job["progress_budget_code"] = str(budget_code)
    job["heartbeat_at"] = now
    job["updated_at"] = now


def fail_job(state: dict[str, Any], job: dict[str, Any], detail: str, code: str, now: datetime, unlink) -> None:
    discard_candidate_file(job.get("candidate_path"), unlink)
    job["candidate_path"] = None
    job["candidate_validated"] = False
    job["state"] = "FAILED"
    job["error_detail"] = detail
    job["error_code"] = code
    job["publication_status"] = "not_published"
    job["ended_at"] = now
    job["updated_at"] = now
    _release_if_holder(state, job["id"])


def mark_ready(job: dict[str, Any], candidate_path: str, digest: str, now: datetime) -> None:
    job["state"] = "READY_TO_SAVE"
    job["candidate_path"] = candidate_path
    job["candidate_sha256"] = digest
    job["candidate_validated"] = True
    job["progress_stage"] = "ready_to_save"
    job["error_detail"] = None
    job["error_code"] = None
    job["publication_status"] = "not_published"
    job["abandon_at"] = now + ABANDON_AFTER
    job["heartbeat_at"] = now
    job["updated_at"] = now


def mark_saved(job: dict[str, Any], now: datetime, cache_warning: str | None = None) -> None:
    job["state"] = "SAVED"
    job["publication_status"] = "published"
    job["cache_warning"] = cache_warning
    job["error_detail"] = None
    job["error_code"] = None
    job["unlock_at"] = now + COUNTDOWN
    job["ended_at"] = now
    job["updated_at"] = now
    job["progress_stage"] = "saved"


def cancel_save(state: dict[str, Any], job: dict[str, Any], now: datetime, unlink) -> None:
    if job.get("state") != "SAVE_FAILED":
        raise RetrainStateError("Cancel is available after a save failure.")
    discard_candidate_file(job.get("candidate_path"), unlink)
    job["candidate_path"] = None
    job["candidate_validated"] = False
    job["state"] = "CANCELLED"
    job["error_detail"] = "The candidate was discarded. The previous production model is still active."
    job["ended_at"] = now
    job["updated_at"] = now
    _release_if_holder(state, job["id"])


def public_view(state: dict[str, Any], identity, now: datetime) -> dict[str, Any]:
    now = _aware(now) or utcnow()
    active = state["jobs"].get(state["coordination"].get("retrain_job_id"))
    manageable = [item for item in state["jobs"].values() if can_manage_job(item, identity)]
    manageable.sort(key=lambda item: _aware(item.get("created_at")) or now, reverse=True)
    if active is not None and can_manage_job(active, identity):
        managed = active
    else:
        managed = manageable[0] if manageable else None
    locked = lock_held(state, now)
    if managed is not None and active is not None and managed.get("id") == active.get("id"):
        job = managed
        can_manage = True
    elif locked and active is not None:
        job = active
        can_manage = False
    else:
        job = managed
        can_manage = managed is not None
    payload = {
        "locked": locked,
        "message": LOCK_MESSAGE if locked else None,
        "server_now": now.isoformat(),
        "job_id": job.get("id") if job else None,
        "state": job.get("state") if job else None,
        "can_manage": can_manage,
        "progress_stage": job.get("progress_stage") if job else None,
        "progress_budget_code": job.get("progress_budget_code") if job else None,
        "progress_current": job.get("progress_current") if job else None,
        "progress_total": job.get("progress_total") if job else None,
        "legacy_unverified": bool(job and locked and not job.get("initiator_oid")),
        "unlock_at": _iso(job.get("unlock_at")) if job else None,
        "abandon_at": _iso(job.get("abandon_at")) if job and can_manage else None,
        "publication_status": job.get("publication_status") if job else None,
        "cache_warning": redact_public_text(job.get("cache_warning")) if job else None,
        "error_detail": redact_public_text(job.get("error_detail")) if job else None,
        "error_code": job.get("error_code") if job else None,
        "logs": redact_public_text(job.get("logs") or "") if job else None,
        "initiator_display_name": job.get("initiator_display_name") if job else None,
    }
    return payload


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    if value is None:
        return None
    return value.isoformat()


_JOB_COLUMNS = (
    "id",
    "state",
    "initiator_display_name",
    "initiator_username",
    "initiator_tid",
    "initiator_oid",
    "initiator_identity_source",
    "manage_token_hash",
    "snapshot_revision",
    "snapshot_fingerprint",
    "snapshot_source",
    "snapshot_path",
    "earliest_month",
    "latest_month",
    "budget_code_count",
    "candidate_path",
    "candidate_sha256",
    "candidate_validated",
    "progress_stage",
    "progress_budget_code",
    "progress_current",
    "progress_total",
    "logs",
    "error_detail",
    "error_code",
    "publication_status",
    "cache_warning",
    "started_at",
    "ended_at",
    "heartbeat_at",
    "unlock_at",
    "abandon_at",
    "worker_id",
    "created_at",
    "updated_at",
)


class MemoryBackend:
    def __init__(self):
        self._lock = threading.Lock()
        self.state = empty_state()

    def mutate(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        with self._lock:
            return fn(self.state)


class PostgresBackend:
    def mutate(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        with get_connection() as connection:
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO system_coordination (id, active_operations)
                    VALUES (1, 0)
                    ON CONFLICT (id) DO NOTHING
                    """
                )
                connection.execute("SELECT id FROM system_coordination WHERE id = 1 FOR UPDATE")
                state = _load_state(connection)
                result = fn(state)
                _save_state(connection, state)
                return result


def _dump_operations(operations: list[dict]) -> list[dict]:
    dumped = []
    for operation in operations:
        heartbeat = operation.get("heartbeat_at")
        dumped.append(
            {
                "id": operation.get("id"),
                "kind": operation.get("kind"),
                "worker_id": operation.get("worker_id"),
                "heartbeat_at": heartbeat.isoformat() if isinstance(heartbeat, datetime) else heartbeat,
            }
        )
    return dumped


def _load_operations(raw) -> list[dict]:
    if isinstance(raw, str):
        import json

        raw = json.loads(raw or "[]")
    operations = []
    for operation in raw or []:
        item = dict(operation)
        item["heartbeat_at"] = _as_datetime(item.get("heartbeat_at"))
        operations.append(item)
    return operations


def _load_publication(connection) -> dict | None:
    row = connection.execute(
        """
        SELECT published_at, snapshot_fingerprint, snapshot_revision, job_id, publisher_tid, publisher_oid
        FROM model_publication_meta
        WHERE id = 1
        """
    ).fetchone()
    if not row or not row.get("published_at"):
        return None
    return dict(row)


def _save_publication(connection, publication: dict | None) -> None:
    if not publication or not publication.get("published_at"):
        return
    connection.execute(
        """
        INSERT INTO model_publication_meta (
            id, published_at, snapshot_fingerprint, snapshot_revision, job_id, publisher_tid, publisher_oid
        )
        VALUES (1, %(published_at)s, %(snapshot_fingerprint)s, %(snapshot_revision)s, %(job_id)s, %(publisher_tid)s, %(publisher_oid)s)
        ON CONFLICT (id) DO UPDATE SET
            published_at = EXCLUDED.published_at,
            snapshot_fingerprint = EXCLUDED.snapshot_fingerprint,
            snapshot_revision = EXCLUDED.snapshot_revision,
            job_id = EXCLUDED.job_id,
            publisher_tid = EXCLUDED.publisher_tid,
            publisher_oid = EXCLUDED.publisher_oid
        """,
        {
            "published_at": publication.get("published_at"),
            "snapshot_fingerprint": publication.get("snapshot_fingerprint"),
            "snapshot_revision": publication.get("snapshot_revision"),
            "job_id": publication.get("job_id"),
            "publisher_tid": publication.get("publisher_tid"),
            "publisher_oid": publication.get("publisher_oid"),
        },
    )


def _load_state(connection) -> dict[str, Any]:
    coordination = connection.execute(
        """
        SELECT retrain_job_id, active_operations, operations, updated_at
        FROM system_coordination
        WHERE id = 1
        """
    ).fetchone()
    rows = connection.execute("SELECT * FROM retrain_jobs FOR UPDATE").fetchall()
    jobs = {}
    for row in rows:
        job = {column: row[column] for column in _JOB_COLUMNS}
        job["candidate_validated"] = bool(job.get("candidate_validated"))
        job["logs"] = job.get("logs") or ""
        jobs[job["id"]] = job
    return {
        "coordination": {
            "retrain_job_id": coordination["retrain_job_id"] if coordination else None,
            "active_operations": int(coordination["active_operations"]) if coordination else 0,
            "updated_at": coordination["updated_at"] if coordination else None,
        },
        "operations": _load_operations(coordination.get("operations") if coordination else []),
        "jobs": jobs,
        "publication": _load_publication(connection),
    }


def _save_state(connection, state: dict[str, Any]) -> None:
    coordination = state["coordination"]
    connection.execute(
        """
        UPDATE system_coordination
        SET retrain_job_id = %(retrain_job_id)s,
            active_operations = %(active_operations)s,
            operations = %(operations)s,
            updated_at = %(updated_at)s
        WHERE id = 1
        """,
        {
            "retrain_job_id": coordination.get("retrain_job_id"),
            "active_operations": int(coordination.get("active_operations") or 0),
            "operations": Jsonb(_dump_operations(state.get("operations") or [])),
            "updated_at": coordination.get("updated_at") or utcnow(),
        },
    )
    columns = ", ".join(_JOB_COLUMNS)
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in _JOB_COLUMNS if column != "id")
    for job in state["jobs"].values():
        payload = {column: job.get(column) for column in _JOB_COLUMNS}
        payload["logs"] = payload.get("logs") or ""
        payload["candidate_validated"] = bool(payload.get("candidate_validated"))
        payload["publication_status"] = payload.get("publication_status") or "not_published"
        payload["manage_token_hash"] = payload.get("manage_token_hash") or ""
        connection.execute(
            f"""
            INSERT INTO retrain_jobs ({columns})
            VALUES ({", ".join(f"%({column})s" for column in _JOB_COLUMNS)})
            ON CONFLICT (id) DO UPDATE SET {updates}
            """,
            payload,
        )
    _save_publication(connection, state.get("publication"))


_backend: MemoryBackend | PostgresBackend | None = None


def get_backend():
    global _backend
    if _backend is None:
        _backend = PostgresBackend()
    return _backend


def use_memory_backend() -> MemoryBackend:
    global _backend
    _backend = MemoryBackend()
    return _backend


def reset_backend() -> None:
    global _backend
    _backend = None


def mutate(fn: Callable[[dict[str, Any]], Any]) -> Any:
    return get_backend().mutate(fn)
