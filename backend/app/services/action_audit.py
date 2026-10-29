"""Audit Settings changes with the verified Microsoft actor. Tokens are never stored."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.db import DatabaseError, get_connection

_LOCK = threading.Lock()
_MEMORY: list[dict] = []
COLOMBO = ZoneInfo("Asia/Colombo")

ACTION_LABELS = {
    "dataset_preview": "Upload validation",
    "dataset_confirm": "Dataset confirmation",
    "dataset_stage": "Upload staging",
    "master_preview": "Historical edit preview",
    "master_confirm": "Historical edits saved",
    "dataset_edit": "Historical edit",
    "rollback_preview": "Rollback preview",
    "rollback_confirm": "Rollback",
    "BOOK_ENTRY_ADD": "Book Entry added",
    "BOOK_ENTRY_EDIT": "Book Entry updated",
    "BOOK_ENTRY_DELETE": "Book Entry removed",
    "retrain_start": "Training started",
    "retrain_complete": "Training completed",
    "retrain_failed": "Training failed",
    "retrain_save": "Models published",
    "retrain_discard": "Training discarded",
    "retrain_retry": "Training retry",
    "revision_view": "Revision viewed",
    "revision_download": "Revision downloaded",
}


def reset_action_audit() -> None:
    with _LOCK:
        _MEMORY.clear()


def recent_actions() -> list[dict]:
    with _LOCK:
        return list(_MEMORY)


def record_action(
    actor,
    action: str,
    result: str,
    *,
    job_id: str | None = None,
    revision_id: str | None = None,
    target: str | None = None,
    details: dict[str, Any] | list | str | None = None,
    now: datetime | None = None,
) -> dict:
    """Append one immutable Settings activity event. Never stores tokens or file bytes."""
    occurred_at = now or datetime.now(timezone.utc)
    safe_details = _sanitize_details(details)
    event = {
        "id": uuid.uuid4().hex,
        "occurred_at": occurred_at,
        "action": str(action),
        "result": str(result),
        "actor_tid": str(_value(actor, "tenant_id") or ""),
        "actor_oid": str(_value(actor, "object_id") or ""),
        "actor_display_name": str(_value(actor, "name") or "")[:200],
        "actor_email": str(_value(actor, "email") or "")[:200],
        "actor_identity_source": str(_value(actor, "identity_source") or ""),
        "job_id": job_id,
        "revision_id": revision_id,
        "target": str(target or revision_id or job_id or "")[:500],
        "details": safe_details,
    }
    with _LOCK:
        _MEMORY.append(event)
        if len(_MEMORY) > 500:
            del _MEMORY[:-500]
    _persist(event)
    return event


def list_activity_events(
    *,
    page: int = 1,
    page_size: int = 20,
    day: str | None = None,
) -> dict[str, Any]:
    """Newest-first activity events, optionally filtered to an Asia/Colombo calendar day."""
    page = max(1, int(page or 1))
    page_size = min(50, max(1, int(page_size or 20)))
    offset = (page - 1) * page_size
    start_utc, end_utc, day_label = _colombo_day_bounds(day)
    try:
        with get_connection() as connection:
            params: dict[str, Any] = {"limit": page_size, "offset": offset}
            where = ""
            if start_utc is not None and end_utc is not None:
                where = "WHERE occurred_at >= %(start)s AND occurred_at < %(end)s"
                params["start"] = start_utc
                params["end"] = end_utc
            count_params = {k: v for k, v in params.items() if k in {"start", "end"}}
            total_row = connection.execute(
                f"SELECT COUNT(*) AS total FROM settings_action_audit {where}",
                count_params,
            ).fetchone()
            rows = connection.execute(
                f"""
                SELECT id, occurred_at, action, result, actor_tid, actor_oid,
                       actor_display_name, actor_email, actor_identity_source,
                       job_id, revision_id, target, details
                FROM settings_action_audit
                {where}
                ORDER BY occurred_at DESC, id DESC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            ).fetchall()
            total = int((total_row or {}).get("total") or 0)
    except DatabaseError:
        return {
            "items": [],
            "page": page,
            "page_size": page_size,
            "total": 0,
            "day": day_label,
            "timezone": "Asia/Colombo",
        }
    items = [_public_event(row) for row in rows or []]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "day": day_label,
        "timezone": "Asia/Colombo",
    }


def _public_event(row: dict[str, Any]) -> dict[str, Any]:
    action = str(row.get("action") or "")
    details = row.get("details")
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            details = {"note": details[:500]}
    if details is None:
        details = {}
    occurred = row.get("occurred_at")
    if hasattr(occurred, "isoformat"):
        occurred_text = occurred.isoformat()
    else:
        occurred_text = str(occurred or "")
    actor_name = str(row.get("actor_display_name") or "").strip()
    actor_email = str(row.get("actor_email") or "").strip()
    user_label = actor_name or actor_email or "Unknown user"
    if actor_name and actor_email:
        user_label = f"{actor_name} ({actor_email})"
    return {
        "id": row.get("id"),
        "occurred_at": occurred_text,
        "action": action,
        "activity": ACTION_LABELS.get(action, action.replace("_", " ").title()),
        "status": str(row.get("result") or ""),
        "target": str(row.get("target") or row.get("revision_id") or row.get("job_id") or "—"),
        "user": user_label,
        "actor_display_name": actor_name,
        "actor_email": actor_email,
        "details": details if isinstance(details, (dict, list)) else {"note": str(details)},
        "job_id": row.get("job_id"),
        "revision_id": row.get("revision_id"),
    }


def _colombo_day_bounds(day: str | None) -> tuple[datetime | None, datetime | None, str]:
    raw = str(day or "").strip()
    if not raw:
        today = datetime.now(COLOMBO).date()
    else:
        try:
            today = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            today = datetime.now(COLOMBO).date()
    start_local = datetime.combine(today, time.min, tzinfo=COLOMBO)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), today.isoformat()


def _sanitize_details(details: dict[str, Any] | list | str | None) -> dict[str, Any]:
    if details is None:
        return {}
    if isinstance(details, str):
        text = details.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"note": text[:1000]}
        return _sanitize_details(parsed)
    if isinstance(details, list):
        return {"items": [_clip_value(item) for item in details[:50]]}
    if not isinstance(details, dict):
        return {"note": str(details)[:1000]}
    blocked = {
        "authorization",
        "access_token",
        "token",
        "password",
        "secret",
        "refresh_token",
        "content",
        "file_bytes",
        "bytes",
        "payload_bytes",
    }
    cleaned: dict[str, Any] = {}
    for key, value in list(details.items())[:40]:
        key_text = str(key)
        if key_text.lower() in blocked or "token" in key_text.lower():
            continue
        cleaned[key_text] = _clip_value(value)
    return cleaned


def _clip_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, dict):
        return {str(k)[:80]: _clip_value(v) for k, v in list(value.items())[:30]}
    if isinstance(value, list):
        return [_clip_value(item) for item in value[:40]]
    return str(value)[:1000]


def _value(actor, name: str):
    if actor is None:
        return None
    if isinstance(actor, dict):
        return actor.get(name)
    return getattr(actor, name, None)


def _persist(event: dict) -> None:
    if os.getenv("RETRAIN_DISABLE_MAINTENANCE") == "1":
        return
    try:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO settings_action_audit (
                    id, occurred_at, action, result, actor_tid, actor_oid,
                    actor_display_name, actor_email, actor_identity_source, job_id, revision_id,
                    target, details
                )
                VALUES (
                    %(id)s, %(occurred_at)s, %(action)s, %(result)s, %(actor_tid)s, %(actor_oid)s,
                    %(actor_display_name)s, %(actor_email)s, %(actor_identity_source)s, %(job_id)s, %(revision_id)s,
                    %(target)s, %(details)s::jsonb
                )
                ON CONFLICT (id) DO NOTHING
                """,
                {
                    **event,
                    "details": json.dumps(event.get("details") or {}, default=str),
                },
            )
            connection.commit()
    except DatabaseError:
        return
