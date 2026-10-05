"""Durable Settings retraining jobs. The command-line retraining path shares this lock."""

from __future__ import annotations

import hashlib
import hmac
import os
import pickle
import secrets
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.config import DEPLOYED_IS_ARTIFACT_NAME, FORECAST_ARTIFACT_NAME, FORECAST_ARTIFACT_PATH
from app.services.action_audit import record_action
from app.services.retrain_state import (
    ABANDON_AFTER,
    OperationInProgress,
    RetrainAlreadyActive,
    RetrainForbidden,
    RetrainStateError,
    SnapshotMismatch,
    append_progress,
    authorize,
    cancel_save,
    create_job,
    fail_job,
    mark_ready,
    mark_saved,
    mutate,
    public_view,
    sweep,
    utcnow,
    worker_id_for_process,
)

CANDIDATE_DIR_NAME = "retrain_candidates"
_runner = None
_maintenance_started = False
_local_jobs: set[str] = set()
_local_guard = threading.Lock()


def candidate_directory() -> Path:
    return FORECAST_ARTIFACT_PATH.parent / CANDIDATE_DIR_NAME


def production_artifact_path() -> Path:
    return FORECAST_ARTIFACT_PATH.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _protected(path: Path) -> bool:
    name = path.name
    return name in {FORECAST_ARTIFACT_NAME, DEPLOYED_IS_ARTIFACT_NAME}


def safe_unlink(path: str | None) -> None:
    if not path:
        return
    target = Path(path)
    try:
        resolved = target.resolve()
    except OSError:
        resolved = target
    if _protected(resolved) or resolved == production_artifact_path():
        return
    try:
        if resolved.is_file() and CANDIDATE_DIR_NAME in resolved.parts:
            resolved.unlink()
    except OSError:
        return


def candidate_exists(job: dict) -> bool:
    path = job.get("candidate_path")
    if not path:
        return False
    file_path = Path(path)
    return file_path.is_file() and file_path.stat().st_size > 0


def publication_matches(job: dict) -> bool:
    expected = job.get("candidate_sha256")
    production = production_artifact_path()
    if not expected or not production.is_file():
        return False
    try:
        return hmac.compare_digest(_sha256(production), str(expected))
    except OSError:
        return False


def _sweep_now(state, now: datetime | None = None) -> None:
    sweep(state, now or utcnow(), publication_matches, candidate_exists, safe_unlink)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _shell_job(identity, now: datetime) -> dict:
    from app.services.retrain_state import _identity_value

    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "state": "QUEUED",
        "initiator_display_name": "",
        "initiator_username": "",
        "initiator_tid": None,
        "initiator_oid": None,
        "initiator_identity_source": None,
        "manage_token_hash": "",
        "snapshot_revision": None,
        "snapshot_fingerprint": None,
        "snapshot_source": None,
        "snapshot_path": None,
        "earliest_month": None,
        "latest_month": None,
        "budget_code_count": None,
        "candidate_path": str(candidate_directory() / f"{job_id}.pkl"),
        "candidate_sha256": None,
        "candidate_validated": False,
        "progress_stage": "queued",
        "progress_current": None,
        "progress_total": None,
        "logs": "Retraining queued.",
        "error_detail": None,
        "error_code": None,
        "publication_status": "not_published",
        "cache_warning": None,
        "started_at": now,
        "ended_at": None,
        "heartbeat_at": now,
        "unlock_at": None,
        "abandon_at": None,
        "worker_id": worker_id_for_process(),
        "created_at": now,
        "updated_at": now,
        "progress_budget_code": None,
    }
    job["initiator_display_name"] = str(_identity_value(identity, "name") or "")[:200]
    job["initiator_tid"] = ""
    job["initiator_oid"] = str(_identity_value(identity, "object_id") or "")
    job["initiator_identity_source"] = str(_identity_value(identity, "identity_source") or "microsoft_graph")
    return job


def _apply_snapshot(job: dict, snapshot: dict) -> None:
    job["snapshot_revision"] = snapshot.get("revision")
    job["snapshot_fingerprint"] = snapshot.get("fingerprint")
    job["snapshot_source"] = snapshot.get("source")
    job["snapshot_path"] = snapshot.get("path")
    job["earliest_month"] = snapshot.get("earliest_month")
    job["latest_month"] = snapshot.get("latest_month")
    job["budget_code_count"] = snapshot.get("budget_code_count")


def capture_snapshot(job_id: str) -> dict:
    """Freeze the current master so training does not follow a later edit."""
    from app.services.dataset_master import fingerprint_master_state, load_master_state, resolve_training_snapshot

    snapshot = resolve_training_snapshot()
    state = load_master_state()
    destination = candidate_directory() / f"{job_id}-snapshot.xlsx"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(snapshot["path"], destination)
    return {
        "path": str(destination),
        "fingerprint": fingerprint_master_state(state),
        "revision": int(state.get("version") or snapshot.get("master_version") or 0),
        "source": snapshot.get("source"),
        "earliest_month": snapshot.get("earliest_month"),
        "latest_month": snapshot.get("latest_month"),
        "budget_code_count": int(snapshot.get("budget_code_count") or 0),
    }


def current_fingerprint() -> tuple[str, int]:
    from app.services.dataset_master import fingerprint_master_state, load_master_state

    state = load_master_state()
    return fingerprint_master_state(state), int(state.get("version") or 0)


def execute_training(**kwargs):
    import sys

    engine_dir = FORECAST_ARTIFACT_PATH.parent.parent
    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))
    import model_training

    return model_training.run_training(**kwargs)


def reload_published_model() -> str | None:
    from app.services.model_loader import get_model_bundle, is_model_loaded, load_production_model

    try:
        if is_model_loaded():
            get_model_bundle()
        else:
            load_production_model()
    except Exception as error:
        return f"The production model was replaced, but the running forecast cache could not be refreshed: {error}"
    return None


def publish_candidate(candidate_path: Path, production_path: Path | None = None) -> dict:
    """Validate the candidate again and atomically replace only the fixed production pickle."""
    import sys

    production = Path(production_path or production_artifact_path()).resolve()
    candidate = Path(candidate_path).resolve()
    if production.name != FORECAST_ARTIFACT_NAME:
        raise AssertionError(f"Save Models can replace only {FORECAST_ARTIFACT_NAME}.")
    if production.name == DEPLOYED_IS_ARTIFACT_NAME or candidate.name == DEPLOYED_IS_ARTIFACT_NAME:
        raise AssertionError(f"Refusing to modify {DEPLOYED_IS_ARTIFACT_NAME}.")
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        raise AssertionError("The validated candidate pickle is missing.")
    before = None
    if production.is_file():
        stat = production.stat()
        before = (stat.st_size, int(getattr(stat, "st_mtime_ns", stat.st_mtime)))
    engine_dir = production.parent.parent
    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))
    import model_training

    with open(candidate, "rb") as file:
        bundle = pickle.load(file)
    temporary = production.parent / f".{production.stem}.{uuid.uuid4().hex}.pkl.tmp"
    shutil.copyfile(candidate, temporary)
    try:
        model_training.publish_validated_production_pickle(
            temporary,
            production,
            bundle,
            expected_account_count=len(bundle.get("selected_accounts") or []),
        )
    except Exception:
        if before is not None and production.is_file():
            stat = production.stat()
            identity = (stat.st_size, int(getattr(stat, "st_mtime_ns", stat.st_mtime)))
            if identity != before:
                raise AssertionError(
                    "The production artifact changed during a failed save and could not be verified. "
                    "The candidate was kept."
                ) from None
        raise
    warning = reload_published_model()
    return {
        "publication_status": "published",
        "cache_warning": warning,
        "sha256": _sha256(production),
    }


def _spawn(job_id: str) -> None:
    runner = _runner or _thread_runner
    runner(job_id)


def _thread_runner(job_id: str) -> None:
    thread = threading.Thread(target=run_job_body, args=(job_id,), name=f"retrain-{job_id}", daemon=True)
    thread.start()


def set_runner(runner) -> None:
    global _runner
    _runner = runner


def start_job(identity, snapshot: dict | None = None, now: datetime | None = None) -> dict:
    """Queue a background training job. Publication waits for Save Models."""
    from app.services.retrain_state import RetrainForbidden, _identity_value

    if not str(_identity_value(identity, "object_id") or ""):
        raise RetrainForbidden("A verified Microsoft Graph user is required to retrain models.")
    if not _identity_value(identity, "is_editor"):
        raise RetrainForbidden("Your Microsoft department and job title are not allowed to change models.")
    now = now or utcnow()
    job = _shell_job(identity, now)

    def apply(state):
        _sweep_now(state, now)
        create_job(state, job, now)
        return job

    mutate(apply)
    try:
        frozen = snapshot if snapshot is not None else capture_snapshot(job["id"])
    except Exception as error:
        def fail(state):
            current = state["jobs"].get(job["id"])
            if current is not None:
                fail_job(state, current, str(error), "SNAPSHOT_FAILED", utcnow(), safe_unlink)

        mutate(fail)
        raise

    def store(state):
        current = state["jobs"][job["id"]]
        _apply_snapshot(current, frozen)
        return current

    mutate(store)
    try:
        _spawn(job["id"])
    except Exception as error:
        def fail(state):
            current = state["jobs"].get(job["id"])
            if current is not None:
                fail_job(state, current, f"Retraining could not be started: {error}", "START_FAILED", utcnow(), safe_unlink)

        mutate(fail)
        raise
    record_action(identity, "retrain_start", "succeeded", job_id=job["id"])
    return {"job_id": job["id"], "state": "QUEUED"}


def run_job_body(job_id: str) -> None:
    with _local_guard:
        _local_jobs.add(job_id)
    stop = threading.Event()

    def beat():
        while not stop.wait(10):
            def touch(state):
                job = state["jobs"].get(job_id)
                if job is None or job.get("state") not in {"QUEUED", "TRAINING", "VALIDATING", "SAVING"}:
                    return
                job["heartbeat_at"] = utcnow()
                job["updated_at"] = job["heartbeat_at"]
            try:
                mutate(touch)
            except Exception:
                return

    beater = threading.Thread(target=beat, name=f"retrain-heartbeat-{job_id}", daemon=True)
    beater.start()
    try:
        def mark_training(state):
            job = state["jobs"][job_id]
            job["state"] = "TRAINING"
            job["progress_stage"] = "loading"
            job["heartbeat_at"] = utcnow()
            job["updated_at"] = job["heartbeat_at"]
            return job

        job = mutate(mark_training)
        candidate = Path(job["candidate_path"])
        candidate.parent.mkdir(parents=True, exist_ok=True)

        def progress(event: dict) -> None:
            def apply(state):
                current = state["jobs"].get(job_id)
                if current is None:
                    return
                if event.get("stage") == "validating" and current.get("state") == "TRAINING":
                    current["state"] = "VALIDATING"
                append_progress(
                    current,
                    str(event.get("message") or ""),
                    event.get("stage"),
                    event.get("current"),
                    event.get("total"),
                    utcnow(),
                    event.get("budget_code"),
                )

            mutate(apply)

        result = execute_training(
            input_file=job["snapshot_path"],
            output_dir=str(candidate.parent),
            output_file=candidate.name,
            publish_production=False,
            progress_callback=progress,
            expected_account_count=job.get("budget_code_count"),
            category=None,
        )
        saved = Path(result["pickle_path"]).resolve()
        if saved.name == FORECAST_ARTIFACT_NAME or saved == production_artifact_path():
            raise AssertionError("Training published the production pickle before Save Models.")
        digest = _sha256(saved)

        def ready(state):
            current = state["jobs"][job_id]
            current["state"] = "VALIDATING"
            mark_ready(current, str(saved), digest, utcnow())

        mutate(ready)
    except Exception as error:
        def fail(state):
            current = state["jobs"].get(job_id)
            if current is None or current.get("state") in {"READY_TO_SAVE", "SAVED", "SAVE_FAILED"}:
                return
            fail_job(state, current, str(error), "TRAINING_FAILED", utcnow(), safe_unlink)

        mutate(fail)
    finally:
        stop.set()
        with _local_guard:
            _local_jobs.discard(job_id)


def model_status() -> dict:
    """Compare the last recorded publication with the current master. Missing metadata stays unknown."""
    publication = mutate(lambda state: state.get("publication"))
    unavailable = {
        "status": "unknown",
        "published_at": None,
        "message": "Model publication metadata is unavailable.",
        "retraining_required": False,
        "comparison_available": False,
    }
    if not publication or not publication.get("published_at") or not publication.get("snapshot_fingerprint"):
        return unavailable
    published_at = publication.get("published_at")
    stamp = published_at.isoformat() if hasattr(published_at, "isoformat") else str(published_at)
    try:
        fingerprint, revision = current_fingerprint()
    except Exception:
        return {
            "status": "unknown",
            "published_at": stamp,
            "message": "The current historical master could not be compared with the published model.",
            "retraining_required": False,
            "comparison_available": False,
        }
    stored_revision = publication.get("snapshot_revision")
    changed = fingerprint != publication.get("snapshot_fingerprint") or stored_revision is None or int(revision) != int(stored_revision)
    if changed:
        return {
            "status": "retraining_required",
            "published_at": stamp,
            "message": "Data updated — retraining required",
            "retraining_required": True,
            "comparison_available": True,
        }
    return {
        "status": "current",
        "published_at": stamp,
        "message": "Models match the published historical snapshot.",
        "retraining_required": False,
        "comparison_available": True,
    }


def status(identity=None, now: datetime | None = None) -> dict:
    now = now or utcnow()

    def apply(state):
        _sweep_now(state, now)
        return public_view(state, identity, now)

    return mutate(apply)


def _require(state, job_id: str, identity) -> dict:
    return authorize(state, job_id, identity)


def save_job(job_id: str, identity, now: datetime | None = None) -> dict:
    now = now or utcnow()

    def begin(state):
        _sweep_now(state, now)
        job = _require(state, job_id, identity)
        if job.get("state") == "SAVED" and job.get("publication_status") == "published":
            return {"idempotent": True, "job": job}
        if job.get("state") == "SAVING":
            raise RetrainStateError("Save Models is already in progress.")
        if job.get("state") != "READY_TO_SAVE" and job.get("state") != "SAVE_FAILED":
            raise RetrainStateError("Save Models is available after training has been validated.")
        fingerprint, revision = current_fingerprint()
        stored_revision = job.get("snapshot_revision")
        revision_changed = stored_revision is None or int(revision) != int(stored_revision)
        if fingerprint != job.get("snapshot_fingerprint") or revision_changed:
            fail_job(state, job, SnapshotMismatch().detail, "SNAPSHOT_MISMATCH", now, safe_unlink)
            return {"mismatch": True}
        job["state"] = "SAVING"
        job["progress_stage"] = "saving"
        job["error_detail"] = None
        job["updated_at"] = now
        job["heartbeat_at"] = now
        return {"idempotent": False, "candidate_path": job.get("candidate_path"), "sha": job.get("candidate_sha256")}

    started = mutate(begin)
    if started.get("mismatch"):
        raise SnapshotMismatch()
    if started.get("idempotent"):
        job = started["job"]
        return _save_payload(job, idempotent=True)
    try:
        published = publish_candidate(Path(started["candidate_path"]))
    except Exception as error:
        def fail(state):
            job = state["jobs"].get(job_id)
            if job is None:
                return
            if publication_matches(job):
                mark_saved(job, utcnow(), cache_warning=str(error))
                return
            job["state"] = "SAVE_FAILED"
            job["error_detail"] = (
                f"Save Models failed. The previous production model is still active. {error}"
            )
            job["error_code"] = "SAVE_FAILED"
            job["publication_status"] = "not_published"
            job["abandon_at"] = utcnow() + ABANDON_AFTER
            job["updated_at"] = utcnow()

        mutate(fail)
        view = status(identity)
        if view.get("publication_status") == "published":
            return {
                "job_id": job_id,
                "state": view.get("state"),
                "publication_status": "published",
                "message": "Models saved successfully",
                "unlock_at": view.get("unlock_at"),
                "cache_warning": view.get("cache_warning"),
                "idempotent": False,
            }
        raise RetrainStateError(view.get("error_detail") or str(error))

    def finish(state):
        job = state["jobs"][job_id]
        job["candidate_sha256"] = published.get("sha256") or job.get("candidate_sha256")
        candidate_path = job.get("candidate_path")
        saved_at = utcnow()
        mark_saved(job, saved_at, cache_warning=published.get("cache_warning"))
        state["publication"] = {
            "published_at": saved_at,
            "snapshot_fingerprint": job.get("snapshot_fingerprint"),
            "snapshot_revision": job.get("snapshot_revision"),
            "job_id": job.get("id"),
            "publisher_tid": job.get("initiator_tid"),
            "publisher_oid": job.get("initiator_oid"),
        }
        job["candidate_path"] = None
        return job, candidate_path

    job, candidate_path = mutate(finish)
    safe_unlink(candidate_path)
    record_action(identity, "retrain_save", "succeeded", job_id=job_id)
    return _save_payload(job, idempotent=False)


def _save_payload(job: dict, idempotent: bool) -> dict:
    return {
        "job_id": job["id"],
        "state": job.get("state"),
        "publication_status": job.get("publication_status"),
        "message": "Models saved successfully",
        "unlock_at": job.get("unlock_at").isoformat() if job.get("unlock_at") else None,
        "cache_warning": job.get("cache_warning"),
        "idempotent": idempotent,
    }


def cancel_job(job_id: str, identity, now: datetime | None = None) -> dict:
    now = now or utcnow()

    def apply(state):
        _sweep_now(state, now)
        job = _require(state, job_id, identity)
        cancel_save(state, job, now, safe_unlink)
        return public_view(state, identity, now)

    view = mutate(apply)
    record_action(identity, "retrain_discard", "succeeded", job_id=job_id)
    return view


def retry_job(job_id: str, identity) -> dict:
    now = utcnow()

    def apply(state):
        _sweep_now(state, now)
        job = _require(state, job_id, identity)
        if job.get("state") not in {"FAILED", "INTERRUPTED", "ABANDONED", "CANCELLED"}:
            raise RetrainStateError("Retry is available after training fails or is interrupted.")
        return True

    mutate(apply)
    record_action(identity, "retrain_retry", "succeeded", job_id=job_id)
    return start_job(identity)


def touch_local_heartbeats() -> None:
    with _local_guard:
        job_ids = list(_local_jobs)
    if not job_ids:
        return

    def apply(state):
        now = utcnow()
        for job_id in job_ids:
            job = state["jobs"].get(job_id)
            if job is not None and job.get("state") in {"QUEUED", "TRAINING", "VALIDATING", "SAVING"}:
                job["heartbeat_at"] = now
                job["updated_at"] = now

    mutate(apply)


def sweep_persisted(now: datetime | None = None) -> None:
    moment = now or utcnow()

    def apply(state):
        _sweep_now(state, moment)

    mutate(apply)


def start_maintenance() -> None:
    global _maintenance_started
    if os.getenv("RETRAIN_DISABLE_MAINTENANCE") == "1" or _maintenance_started:
        return
    _maintenance_started = True

    def loop():
        while True:
            time.sleep(1)
            try:
                sweep_persisted()
                touch_local_heartbeats()
            except Exception:
                continue

    threading.Thread(target=loop, name="retrain-maintenance", daemon=True).start()


@contextmanager
def cli_retrain_scope():
    """Hold the same global lock as Settings retraining, then publish from the command."""
    from app.db import ensure_forecast_tables

    ensure_forecast_tables()
    now = utcnow()
    job_id = f"cli-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    job = {
        "id": job_id,
        "state": "TRAINING",
        "initiator_display_name": "command",
        "initiator_username": "",
        "initiator_tid": None,
        "initiator_oid": None,
        "initiator_identity_source": None,
        "manage_token_hash": "",
        "snapshot_revision": None,
        "snapshot_fingerprint": None,
        "snapshot_source": "command",
        "snapshot_path": None,
        "earliest_month": None,
        "latest_month": None,
        "budget_code_count": None,
        "candidate_path": None,
        "candidate_sha256": None,
        "candidate_validated": False,
        "progress_stage": "training",
        "progress_current": None,
        "progress_total": None,
        "logs": "Command retraining started.",
        "error_detail": None,
        "error_code": None,
        "publication_status": "not_published",
        "cache_warning": None,
        "started_at": now,
        "ended_at": None,
        "heartbeat_at": now,
        "unlock_at": None,
        "abandon_at": None,
        "worker_id": worker_id_for_process(),
        "created_at": now,
        "updated_at": now,
    }

    def acquire(state):
        _sweep_now(state, now)
        create_job(state, job, now)

    mutate(acquire)
    stop = threading.Event()

    def beat():
        while not stop.wait(10):
            def touch(state):
                current = state["jobs"].get(job_id)
                if current is not None and current.get("state") == "TRAINING":
                    current["heartbeat_at"] = utcnow()
            try:
                mutate(touch)
            except Exception:
                return

    threading.Thread(target=beat, name=f"cli-retrain-{job_id}", daemon=True).start()
    published = False
    try:
        yield job_id
        published = True
    finally:
        stop.set()

        def finish(state):
            current = state["jobs"].get(job_id)
            if current is None:
                return
            if published:
                current["state"] = "CLOSED"
                current["publication_status"] = "published"
                current["ended_at"] = utcnow()
            elif current.get("state") == "TRAINING":
                current["state"] = "FAILED"
                current["error_detail"] = "Command retraining failed. The previous production model was retained if publication did not finish."
                current["error_code"] = "CLI_FAILED"
                current["ended_at"] = utcnow()
            if state["coordination"].get("retrain_job_id") == job_id:
                state["coordination"]["retrain_job_id"] = None
                state["coordination"]["updated_at"] = utcnow()

        mutate(finish)
