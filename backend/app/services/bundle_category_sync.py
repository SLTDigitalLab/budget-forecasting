"""Sync Budget Code category metadata into the active production model bundle.

Updates only category labels on existing account records. Fitted models,
parameters, forecast series, eligibility, and Book Entry exclusions stay
unchanged. Does not retrain.
"""

from __future__ import annotations

import copy
import logging
import os
import pickle
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from app.config import FORECAST_ARTIFACT_PATH
from app.services.model_loader import (
    ModelLoadError,
    _artifact_identity,
    _refresh_lock,
    _validate_bundle,
    load_and_validate_artifact,
    resolve_forecast_artifact_path,
)

logger = logging.getLogger(__name__)

_bundle_write_lock = threading.Lock()


class BundleCategorySyncError(Exception):
    """Raised when the active model bundle cannot be updated safely."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _record_maps(bundle: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    maps: list[tuple[str, dict[str, Any]]] = []
    for key in ("account_records", "account_models"):
        value = bundle.get(key)
        if isinstance(value, dict):
            maps.append((key, value))
    return maps


def code_in_bundle(bundle: dict[str, Any], budget_code: str) -> bool:
    code = _text(budget_code)
    if not code:
        return False
    for _name, records in _record_maps(bundle):
        if records.get(code) is not None or records.get(str(code)) is not None:
            return True
    models = bundle.get("models")
    if isinstance(models, dict) and (models.get(code) is not None or models.get(str(code)) is not None):
        return True
    selected = {str(item) for item in (bundle.get("selected_accounts") or [])}
    return code in selected


def bundle_category_for_code(bundle: dict[str, Any], budget_code: str) -> str | None:
    code = _text(budget_code)
    for _name, records in _record_maps(bundle):
        record = records.get(code) or records.get(str(code))
        if isinstance(record, dict) and "category" in record:
            return _text(record.get("category"))
    return None


def apply_category_assignments(
    bundle: dict[str, Any],
    assignments: dict[str, str],
) -> dict[str, Any]:
    """Return a deep-copied bundle with category metadata updated for existing codes only."""
    next_bundle = copy.deepcopy(bundle)
    updated: list[str] = []
    skipped: list[str] = []
    previous: dict[str, str | None] = {}
    for raw_code, raw_category in (assignments or {}).items():
        code = _text(raw_code)
        category = _text(raw_category)
        if not code or not category:
            continue
        if not code_in_bundle(next_bundle, code):
            skipped.append(code)
            continue
        touched = False
        for _name, records in _record_maps(next_bundle):
            record = records.get(code) or records.get(str(code))
            if not isinstance(record, dict):
                continue
            previous.setdefault(code, _text(record.get("category")) or None)
            record["category"] = category
            if record.get("budget_code") in {None, ""}:
                record["budget_code"] = code
            touched = True
        models = next_bundle.get("models")
        if isinstance(models, dict):
            model = models.get(code) or models.get(str(code))
            if isinstance(model, dict) and "category" in model:
                previous.setdefault(code, _text(model.get("category")) or None)
                model["category"] = category
                touched = True
        if touched:
            updated.append(code)
        else:
            skipped.append(code)
    return {
        "bundle": next_bundle,
        "updated_codes": sorted(set(updated)),
        "skipped_codes": sorted(set(skipped)),
        "previous_categories": previous,
    }


def _dump_bundle(path: Path, bundle: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        pickle.dump(bundle, handle, protocol=pickle.HIGHEST_PROTOCOL)


def publish_category_metadata(
    assignments: dict[str, str],
    *,
    artifact_path: Path | None = None,
    simulate_failure: str | None = None,
) -> dict[str, Any]:
    """
    Validate and atomically publish category-only metadata updates to the active bundle.

    Codes absent from the bundle are skipped (master-only change). On failure the
    on-disk artifact and loaded cache are left unchanged.
    """
    production = Path(artifact_path or resolve_forecast_artifact_path()).resolve()
    if not production.is_file() or production.stat().st_size <= 0:
        raise BundleCategorySyncError(f"Forecast artifact not found: {production}")
    failure = simulate_failure or str(os.environ.get("BUNDLE_CATEGORY_SYNC_SIMULATE_FAILURE") or "").strip() or None

    with _bundle_write_lock:
        with _refresh_lock:
            before_identity = _artifact_identity(production)
            try:
                with open(production, "rb") as handle:
                    current = pickle.load(handle)
            except Exception as error:
                raise BundleCategorySyncError(f"Unable to read the active model bundle: {error}") from error
            try:
                _validate_bundle(current)
            except ModelLoadError as error:
                raise BundleCategorySyncError(f"The active model bundle is invalid: {error}") from error

            planned = apply_category_assignments(current, assignments)
            if not planned["updated_codes"]:
                return {
                    "updated_codes": [],
                    "skipped_codes": planned["skipped_codes"],
                    "previous_categories": planned["previous_categories"],
                    "artifact_path": str(production),
                    "reloaded": False,
                    "unchanged": True,
                }

            next_bundle = planned["bundle"]
            try:
                _validate_bundle(next_bundle)
            except ModelLoadError as error:
                raise BundleCategorySyncError(
                    f"Updated model bundle failed validation: {error}"
                ) from error

            if failure == "before_replace":
                raise BundleCategorySyncError("Simulated bundle update failure before replace.")

            temp = production.parent / f".{production.stem}.category-{uuid.uuid4().hex}.pkl.tmp"
            try:
                _dump_bundle(temp, next_bundle)
                if temp.stat().st_size <= 0:
                    raise BundleCategorySyncError("Temporary model bundle is empty.")
                if failure == "after_temp_write":
                    raise BundleCategorySyncError("Simulated bundle update failure after temp write.")
                os.replace(temp, production)
            except Exception:
                if temp.exists():
                    try:
                        temp.unlink()
                    except OSError:
                        pass
                # Ensure production identity is unchanged on failure.
                after = _artifact_identity(production)
                if before_identity is not None and after != before_identity:
                    raise BundleCategorySyncError(
                        "The model bundle changed during a failed category update and could not be verified."
                    )
                raise

            try:
                load_and_validate_artifact(production)
            except Exception as error:
                raise BundleCategorySyncError(
                    f"The model bundle was written but could not be loaded: {error}"
                ) from error

            logger.info(
                "Synced category metadata in production bundle for %s",
                ", ".join(planned["updated_codes"]),
            )
            return {
                "updated_codes": planned["updated_codes"],
                "skipped_codes": planned["skipped_codes"],
                "previous_categories": planned["previous_categories"],
                "artifact_path": str(production),
                "reloaded": True,
                "unchanged": False,
            }


def sync_categories_or_raise(assignments: dict[str, str], *, artifact_path: Path | None = None) -> dict[str, Any]:
    cleaned = {
        _text(code): _text(category)
        for code, category in (assignments or {}).items()
        if _text(code) and _text(category)
    }
    if not cleaned:
        return {"updated_codes": [], "skipped_codes": [], "unchanged": True, "reloaded": False}
    return publish_category_metadata(cleaned, artifact_path=artifact_path)
