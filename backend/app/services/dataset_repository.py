"""PostgreSQL access for historical datasets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from app.db import DatabaseError, get_connection


def _now() -> datetime:
    return datetime.now(timezone.utc)


def historical_summary() -> dict[str, Any]:
    try:
        with get_connection() as connection:
            bounds = connection.execute(
                """
                SELECT
                    MIN(month) AS earliest_month,
                    MAX(month) AS latest_month,
                    COUNT(DISTINCT category) FILTER (WHERE category <> '') AS category_count,
                    COUNT(DISTINCT budget_code) AS budget_code_count
                FROM historical_actuals
                """
            ).fetchone()
            identity = connection.execute(
                """
                SELECT earliest_month, latest_month, budget_codes
                FROM dataset_master_meta
                WHERE id = 1
                """
            ).fetchone()
            last_upload = connection.execute(
                """
                SELECT uploaded_at
                FROM dataset_files
                WHERE status = 'active' AND is_legacy = FALSE
                ORDER BY uploaded_at DESC
                LIMIT 1
                """
            ).fetchone()
        codes = []
        if identity and isinstance(identity["budget_codes"], list):
            codes = [item for item in identity["budget_codes"] if isinstance(item, dict) and item.get("budget_code")]
        earliest = (identity or {}).get("earliest_month") or (None if not bounds else bounds["earliest_month"])
        latest = (identity or {}).get("latest_month") or (None if not bounds else bounds["latest_month"])
        if codes:
            budget_code_count = len({str(item["budget_code"]) for item in codes})
            category_count = len({str(item.get("category") or "").strip() for item in codes if str(item.get("category") or "").strip()})
        else:
            budget_code_count = 0 if not bounds else int(bounds["budget_code_count"] or 0)
            category_count = 0 if not bounds else int(bounds["category_count"] or 0)
        empty = budget_code_count == 0 and not earliest
        return {
            "earliest_month": None if empty else earliest,
            "latest_month": None if empty else latest,
            "category_count": 0 if empty else category_count,
            "budget_code_count": 0 if empty else budget_code_count,
            "last_upload_at": None if not last_upload else last_upload["uploaded_at"],
            "empty": empty,
        }
    except psycopg.Error as error:
        raise DatabaseError("Unable to load the historical data summary.") from error


def load_master_identity() -> dict[str, Any]:
    """Budget-code catalog and month range stored beside the numeric actuals."""
    empty = {"budget_codes": [], "earliest_month": None, "latest_month": None}
    try:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT earliest_month, latest_month, budget_codes
                FROM dataset_master_meta
                WHERE id = 1
                """
            ).fetchone()
        if not row:
            return empty
        codes = row["budget_codes"] or []
        if isinstance(codes, str):
            codes = []
        return {
            "budget_codes": [dict(item) for item in codes if isinstance(item, dict)],
            "earliest_month": row["earliest_month"],
            "latest_month": row["latest_month"],
        }
    except psycopg.Error as error:
        raise DatabaseError("Unable to load the historical master identity.") from error


def list_historical_actuals() -> list[dict[str, Any]]:
    try:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT budget_code, month, amount, description, category, source_file_id
                FROM historical_actuals
                ORDER BY budget_code, month
                """
            ).fetchall()
        return [dict(row) for row in rows]
    except psycopg.Error as error:
        raise DatabaseError("Unable to load historical actuals.") from error


def list_dataset_files(limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    bounded = max(1, min(int(limit), 50))
    start = max(0, int(offset))
    try:
        with get_connection() as connection:
            total_row = connection.execute(
                """
                SELECT COUNT(*) AS total FROM dataset_files
                WHERE status IN ('active', 'rolled_back', 'partially_rolled_back')
                """
            ).fetchone()
            rows = connection.execute(
                """
                SELECT
                    id, original_filename, stored_filename, sheet_name, revision, content_hash,
                    status, change_kind, validation_summary, is_legacy, uploaded_at, uploaded_tid,
                    uploaded_oid, uploaded_display_name, uploaded_username, last_edited_at,
                    last_edited_tid, last_edited_oid, last_edited_display_name, last_edited_username,
                    confirmed_at, confirmed_display_name, confirmed_username, rollback_reason, rolled_back_at
                FROM dataset_files
                WHERE status IN ('active', 'rolled_back', 'partially_rolled_back')
                ORDER BY uploaded_at DESC, original_filename
                LIMIT %s OFFSET %s
                """,
                (bounded, start),
            ).fetchall()
        return [dict(row) for row in rows], int(total_row["total"] if total_row else 0)
    except psycopg.Error as error:
        raise DatabaseError("Unable to list saved dataset files.") from error


def get_dataset_file(file_id: str) -> dict[str, Any] | None:
    try:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM dataset_files
                WHERE id = %s AND status IN ('active', 'rolled_back', 'partially_rolled_back')
                """,
                (str(UUID(str(file_id))),),
            ).fetchone()
        return dict(row) if row else None
    except (psycopg.Error, ValueError) as error:
        raise DatabaseError("Unable to load the saved dataset file.") from error


def get_legacy_file_by_checksum(checksum: str) -> dict[str, Any] | None:
    try:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM dataset_files
                WHERE is_legacy = TRUE AND checksum = %s
                LIMIT 1
                """,
                (checksum,),
            ).fetchone()
        return dict(row) if row else None
    except psycopg.Error as error:
        raise DatabaseError("Unable to inspect legacy dataset files.") from error


def insert_preview(owner_tid: str, owner_oid: str, filename: str, stored_filename: str | None, sheet_name: str | None, payload: dict[str, Any], ttl_seconds: int) -> dict[str, Any]:
    ident = str(uuid4())
    created = _now()
    expires = created + timedelta(seconds=int(ttl_seconds))
    try:
        with get_connection() as connection:
            with connection.transaction():
                connection.execute("DELETE FROM dataset_previews WHERE expires_at < NOW()")
                connection.execute(
                    """
                    INSERT INTO dataset_previews (
                        id, owner_tid, owner_oid, original_filename, stored_filename, sheet_name,
                        payload, created_at, expires_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        ident,
                        owner_tid,
                        owner_oid,
                        filename,
                        stored_filename,
                        sheet_name,
                        Jsonb(payload),
                        created,
                        expires,
                    ),
                )
        return {"id": ident, "expires_at": expires}
    except psycopg.Error as error:
        raise DatabaseError("Unable to store the dataset preview.") from error


def get_preview(preview_id: str) -> dict[str, Any] | None:
    try:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM dataset_previews WHERE id = %s",
                (str(UUID(str(preview_id))),),
            ).fetchone()
        return dict(row) if row else None
    except (psycopg.Error, ValueError) as error:
        raise DatabaseError("Unable to load the dataset preview.") from error


def delete_preview(preview_id: str) -> None:
    try:
        with get_connection() as connection:
            connection.execute("DELETE FROM dataset_previews WHERE id = %s", (str(preview_id),))
    except psycopg.Error as error:
        raise DatabaseError("Unable to clear the dataset preview.") from error


def apply_historical_changes(
    file_row: dict[str, Any],
    inserts: list[dict[str, Any]],
    updates: list[dict[str, Any]],
    audit: dict[str, Any],
) -> None:
    try:
        with get_connection() as connection:
            with connection.transaction():
                if file_row.get("_insert_file"):
                    connection.execute(
                        """
                        INSERT INTO dataset_files (
                            id, original_filename, stored_filename, content_type, file_size, checksum,
                            sheet_name, revision, content_hash, status, staged_path, is_legacy,
                            uploaded_at, uploaded_tid, uploaded_oid, uploaded_display_name, uploaded_username
                        )
                        VALUES (
                            %(id)s, %(original_filename)s, %(stored_filename)s, %(content_type)s, %(file_size)s,
                            %(checksum)s, %(sheet_name)s, %(revision)s, %(content_hash)s, %(status)s, %(staged_path)s,
                            %(is_legacy)s, %(uploaded_at)s, %(uploaded_tid)s, %(uploaded_oid)s,
                            %(uploaded_display_name)s, %(uploaded_username)s
                        )
                        """,
                        file_row,
                    )
                else:
                    updated = connection.execute(
                        """
                        UPDATE dataset_files
                        SET stored_filename = %(stored_filename)s,
                            content_type = %(content_type)s,
                            file_size = %(file_size)s,
                            checksum = %(checksum)s,
                            sheet_name = %(sheet_name)s,
                            revision = %(revision)s,
                            content_hash = %(content_hash)s,
                            last_edited_at = %(last_edited_at)s,
                            last_edited_tid = %(last_edited_tid)s,
                            last_edited_oid = %(last_edited_oid)s,
                            last_edited_display_name = %(last_edited_display_name)s,
                            last_edited_username = %(last_edited_username)s
                        WHERE id = %(id)s AND revision = %(previous_revision)s
                        RETURNING id
                        """,
                        file_row,
                    ).fetchone()
                    if updated is None:
                        raise DatasetConflictError("This file was edited by someone else. Refresh and try again.")
                if inserts:
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            """
                            INSERT INTO historical_actuals (
                                budget_code, month, amount, description, category, source_file_id, updated_at
                            )
                            VALUES (%(budget_code)s, %(month)s, %(amount)s, %(description)s, %(category)s, %(source_file_id)s, NOW())
                            """,
                            [
                                {
                                    **item,
                                    "source_file_id": file_row["id"],
                                }
                                for item in inserts
                            ],
                        )
                if updates:
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            """
                            UPDATE historical_actuals
                            SET amount = %(amount)s,
                                description = %(description)s,
                                category = %(category)s,
                                source_file_id = %(source_file_id)s,
                                updated_at = NOW()
                            WHERE budget_code = %(budget_code)s AND month = %(month)s
                            """,
                            [
                                {
                                    **item,
                                    "source_file_id": file_row["id"],
                                }
                                for item in updates
                            ],
                        )
                connection.execute(
                    """
                    INSERT INTO dataset_audit_events (
                        file_id, file_name, action, actor_tid, actor_oid, actor_display_name,
                        actor_username, occurred_at, inserted_count, changed_count, changes
                    )
                    VALUES (
                        %(file_id)s, %(file_name)s, %(action)s, %(actor_tid)s, %(actor_oid)s,
                        %(actor_display_name)s, %(actor_username)s, %(occurred_at)s,
                        %(inserted_count)s, %(changed_count)s, %(changes)s
                    )
                    """,
                    {
                        **audit,
                        "changes": Jsonb(audit.get("changes") or []),
                    },
                )
    except DatasetConflictError:
        raise
    except psycopg.Error as error:
        raise DatabaseError("Unable to save historical dataset changes.") from error


class DatasetConflictError(Exception):
    pass


def update_preview_payload(preview_id: str, payload: dict[str, Any]) -> None:
    try:
        with get_connection() as connection:
            connection.execute(
                "UPDATE dataset_previews SET payload = %s WHERE id = %s",
                (Jsonb(payload), preview_id),
            )
    except psycopg.Error as error:
        raise DatabaseError("Unable to save the staged dataset preview.") from error


def master_version() -> int:
    try:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO dataset_master_meta (id, version)
                VALUES (1, 0)
                ON CONFLICT (id) DO NOTHING
                """
            )
            row = connection.execute("SELECT version FROM dataset_master_meta WHERE id = 1").fetchone()
        return int(row["version"] if row else 0)
    except psycopg.Error as error:
        raise DatabaseError("Unable to read the historical master version.") from error


def commit_master_state(
    *,
    expected_version: int,
    next_records: list[dict[str, Any]],
    file_row: dict[str, Any] | None,
    audit: dict[str, Any],
    changes: list[dict[str, Any]],
    file_status_update: dict[str, Any] | None = None,
    reversed_change_ids: list[int] | None = None,
    budget_codes: list[dict[str, Any]] | None = None,
    earliest_month: str | None = None,
    latest_month: str | None = None,
) -> dict[str, Any]:
    """Replace the master rows and write audit records in one transaction."""
    try:
        with get_connection() as connection:
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO dataset_master_meta (id, version)
                    VALUES (1, 0)
                    ON CONFLICT (id) DO NOTHING
                    """
                )
                locked = connection.execute(
                    "SELECT version FROM dataset_master_meta WHERE id = 1 FOR UPDATE"
                ).fetchone()
                current = int(locked["version"] if locked else 0)
                if current != int(expected_version):
                    raise DatasetConflictError(
                        "The historical master changed while this review was open. Validate again before saving."
                    )
                connection.execute("DELETE FROM historical_actuals")
                if next_records:
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            """
                            INSERT INTO historical_actuals (
                                budget_code, month, amount, description, category, source_file_id, updated_at
                            )
                            VALUES (
                                %(budget_code)s, %(month)s, %(amount)s, %(description)s, %(category)s,
                                %(source_file_id)s, NOW()
                            )
                            """,
                            [
                                {
                                    "budget_code": str(item["budget_code"]),
                                    "month": str(item["month"]),
                                    "amount": float(item["amount"]),
                                    "description": item.get("description") or "",
                                    "category": item.get("category") or "",
                                    "source_file_id": item.get("source_file_id"),
                                }
                                for item in next_records
                                if item.get("amount") is not None
                            ],
                        )
                if file_row:
                    connection.execute(
                        """
                        INSERT INTO dataset_files (
                            id, original_filename, stored_filename, content_type, file_size, checksum,
                            sheet_name, revision, content_hash, status, staged_path, is_legacy,
                            uploaded_at, uploaded_tid, uploaded_oid, uploaded_display_name, uploaded_username,
                            confirmed_at, confirmed_display_name, confirmed_username, validation_summary, change_kind
                        )
                        VALUES (
                            %(id)s, %(original_filename)s, %(stored_filename)s, %(content_type)s, %(file_size)s,
                            %(checksum)s, %(sheet_name)s, %(revision)s, %(content_hash)s, %(status)s, %(staged_path)s,
                            %(is_legacy)s, %(uploaded_at)s, %(uploaded_tid)s, %(uploaded_oid)s,
                            %(uploaded_display_name)s, %(uploaded_username)s,
                            %(confirmed_at)s, %(confirmed_display_name)s, %(confirmed_username)s,
                            %(validation_summary)s, %(change_kind)s
                        )
                        """,
                        {**file_row, "validation_summary": Jsonb(file_row.get("validation_summary") or {})},
                    )
                if file_status_update:
                    connection.execute(
                        """
                        UPDATE dataset_files
                        SET status = %(status)s,
                            rollback_reason = %(rollback_reason)s,
                            rolled_back_at = %(rolled_back_at)s
                        WHERE id = %(id)s
                        """,
                        file_status_update,
                    )
                audit_row = connection.execute(
                    """
                    INSERT INTO dataset_audit_events (
                        file_id, file_name, action, actor_tid, actor_oid, actor_display_name,
                        actor_username, occurred_at, inserted_count, changed_count, changes, reason, summary
                    )
                    VALUES (
                        %(file_id)s, %(file_name)s, %(action)s, %(actor_tid)s, %(actor_oid)s,
                        %(actor_display_name)s, %(actor_username)s, %(occurred_at)s,
                        %(inserted_count)s, %(changed_count)s, %(changes)s, %(reason)s, %(summary)s
                    )
                    RETURNING id
                    """,
                    {
                        **audit,
                        "changes": Jsonb(audit.get("changes") or []),
                        "reason": audit.get("reason"),
                        "summary": Jsonb(audit.get("summary") or {}),
                    },
                ).fetchone()
                audit_id = int(audit_row["id"])
                if changes:
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            """
                            INSERT INTO historical_change_records (
                                audit_event_id, file_id, action, operation, budget_code, month,
                                previous_amount, new_amount, previous_category, new_category,
                                previous_description, new_description, actor_display_name, actor_username,
                                occurred_at
                            )
                            VALUES (
                                %(audit_event_id)s, %(file_id)s, %(action)s, %(operation)s, %(budget_code)s, %(month)s,
                                %(previous_amount)s, %(new_amount)s, %(previous_category)s, %(new_category)s,
                                %(previous_description)s, %(new_description)s, %(actor_display_name)s,
                                %(actor_username)s, %(occurred_at)s
                            )
                            """,
                            [
                                {
                                    "audit_event_id": audit_id,
                                    "file_id": audit.get("file_id"),
                                    "action": audit["action"],
                                    "operation": item["operation"],
                                    "budget_code": item["budget_code"],
                                    "month": item["month"],
                                    "previous_amount": item.get("previous_amount"),
                                    "new_amount": item.get("new_amount"),
                                    "previous_category": item.get("previous_category") or "",
                                    "new_category": item.get("new_category") or "",
                                    "previous_description": item.get("previous_description") or "",
                                    "new_description": item.get("new_description") or "",
                                    "actor_display_name": audit.get("actor_display_name") or "",
                                    "actor_username": audit.get("actor_username") or "",
                                    "occurred_at": audit["occurred_at"],
                                }
                                for item in changes
                            ],
                        )
                if reversed_change_ids:
                    connection.execute(
                        "UPDATE historical_change_records SET reversed_by_audit_id = %s WHERE id = ANY(%s)",
                        (audit_id, reversed_change_ids),
                    )
                if budget_codes is not None:
                    connection.execute(
                        """
                        UPDATE dataset_master_meta
                        SET budget_codes = %s,
                            earliest_month = %s,
                            latest_month = %s
                        WHERE id = 1
                        """,
                        (
                            Jsonb(
                                [
                                    {
                                        "budget_code": str(item["budget_code"]),
                                        "description": item.get("description") or "",
                                        "category": item.get("category") or "",
                                    }
                                    for item in budget_codes
                                    if item.get("budget_code")
                                ]
                            ),
                            earliest_month,
                            latest_month,
                        ),
                    )
                connection.execute(
                    "UPDATE dataset_master_meta SET version = %s, updated_at = NOW() WHERE id = 1",
                    (current + 1,),
                )
        return {"audit_id": audit_id, "version": current + 1}
    except DatasetConflictError:
        raise
    except psycopg.Error as error:
        raise DatabaseError("Unable to update the historical master.") from error


def list_change_records_for_file(file_id: str) -> list[dict[str, Any]]:
    try:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM historical_change_records
                WHERE file_id = %s
                ORDER BY id
                """,
                (file_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    except psycopg.Error as error:
        raise DatabaseError("Unable to read historical change records.") from error


def list_change_records_for_audit(audit_id: int) -> list[dict[str, Any]]:
    try:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM historical_change_records
                WHERE audit_event_id = %s
                ORDER BY id
                """,
                (audit_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    except psycopg.Error as error:
        raise DatabaseError("Unable to read historical change records.") from error


def list_manual_audits(limit: int, offset: int) -> list[dict[str, Any]]:
    try:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM dataset_audit_events
                WHERE action = 'MANUAL_EDIT'
                ORDER BY occurred_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]
    except psycopg.Error as error:
        raise DatabaseError("Unable to list manual historical edits.") from error


def get_audit_event(audit_id: int) -> dict[str, Any] | None:
    try:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM dataset_audit_events WHERE id = %s",
                (audit_id,),
            ).fetchone()
        return dict(row) if row else None
    except psycopg.Error as error:
        raise DatabaseError("Unable to read the revision.") from error
