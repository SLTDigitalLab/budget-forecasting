"""Audit Settings changes with the verified Microsoft actor. Tokens are never stored."""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone

from app.db import DatabaseError, get_connection

_LOCK = threading.Lock()
_MEMORY: list[dict] = []


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
    now: datetime | None = None,
) -> dict:
    occurred_at = now or datetime.now(timezone.utc)
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
    }
    with _LOCK:
        _MEMORY.append(event)
    _persist(event)
    return event


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
                    actor_display_name, actor_email, actor_identity_source, job_id, revision_id
                )
                VALUES (
                    %(id)s, %(occurred_at)s, %(action)s, %(result)s, %(actor_tid)s, %(actor_oid)s,
                    %(actor_display_name)s, %(actor_email)s, %(actor_identity_source)s, %(job_id)s, %(revision_id)s
                )
                ON CONFLICT (id) DO NOTHING
                """,
                event,
            )
            connection.commit()
    except DatabaseError:
        return
