"""Department and job-title rules for Settings changes. Read on every check."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.config import BACKEND_DIR
from app.services.retrain_state import CoordinationError


class EditorPermissionDenied(CoordinationError):
    def __init__(self, detail: str):
        super().__init__(detail, "EDITOR_PERMISSION_DENIED", 403)


def permissions_path() -> Path:
    configured = os.getenv("EDITOR_PERMISSIONS_PATH", "").strip()
    if configured:
        return Path(configured)
    return BACKEND_DIR / "config" / "editor_permissions.json"


def load_editor_rules(path: Path | None = None) -> list[dict[str, str]]:
    target = path or permissions_path()
    if not target.is_file():
        raise EditorPermissionDenied(
            "Editor permission configuration is missing. A deployment maintainer must provide "
            f"{target} with allowed_editor_rules. Changes stay blocked until that file is valid."
        )
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EditorPermissionDenied(
            "Editor permission configuration is invalid JSON. Changes stay blocked until a deployment maintainer fixes it."
        ) from error
    rules = payload.get("allowed_editor_rules") if isinstance(payload, dict) else None
    if not isinstance(rules, list) or not rules:
        raise EditorPermissionDenied(
            "Editor permission configuration has no allowed_editor_rules. Changes stay blocked."
        )
    cleaned = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise EditorPermissionDenied("Editor permission configuration contains an invalid rule.")
        department = str(rule.get("department") or "").strip()
        job_title = str(rule.get("job_title") or "").strip()
        if not department or not job_title:
            raise EditorPermissionDenied("Each editor rule needs both a department and a job title.")
        cleaned.append({"department": department, "job_title": job_title})
    return cleaned


def _exact(value: str | None) -> str:
    return str(value or "").strip().casefold()


def editor_decision(department: str | None, job_title: str | None, rules: list[dict[str, str]] | None = None) -> tuple[bool, str]:
    """Both fields must match one complete rule. Matching is exact after trim, ignoring case."""
    try:
        loaded = rules if rules is not None else load_editor_rules()
    except EditorPermissionDenied as error:
        return False, str(error)
    department_value = _exact(department)
    title_value = _exact(job_title)
    if not department_value or not title_value:
        return False, "Microsoft did not return both a department and a job title, so this change is not allowed."
    for rule in loaded:
        if _exact(rule.get("department")) == department_value and _exact(rule.get("job_title")) == title_value:
            return True, ""
    return False, "Your Microsoft department and job title are not allowed to change historical data or models."
