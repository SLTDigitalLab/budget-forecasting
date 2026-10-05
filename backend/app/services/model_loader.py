"""Load the production forecast artifact once per process."""

from __future__ import annotations

import logging
import pickle
import threading
from pathlib import Path

from app.config import (
    ALL_ACCOUNT_SCHEMA_VERSION,
    ALLOWED_FORECAST_TYPES,
    CANDIDATE_ARTIFACT_PATH,
    DEPLOYED_IS_ARTIFACT_PATH,
    EXPECTED_ACCOUNT_COUNT,
    EXPECTED_FORECAST_TYPE,
    FORECAST_ARTIFACT_PATH,
    PER_BUDGET_CODE_ALGORITHM,
)

ALLOWED_NON_FITTED_STATUSES = {
    "ZERO_POLICY",
    "NO_DATA",
    "ALL_MISSING",
    "NOT_EVALUABLE",
    "NO_VALID_WINNER",
    "UNRESOLVED_MISSING",
    "INVALID_DATA",
    "INSUFFICIENT_HISTORY",
    "FIT_FAILED",
}

logger = logging.getLogger(__name__)

REQUIRED_FORECAST_METADATA = (
    "forecast_start",
    "forecast_end",
    "monthly_account_forecasts",
    "monthly_combined_forecast",
    "yearly_account_forecasts",
    "yearly_combined_forecast",
    "year_status",
)
ALL_ACCOUNT_REQUIRED_KEYS = REQUIRED_FORECAST_METADATA + (
    "account_records",
    "conformal_prediction",
    "forecast_coverage",
    "history_months",
    "historical_start",
    "historical_end",
)

_bundle: dict | None = None
_load_error: str | None = None
_loaded_path: str | None = None
_loaded_identity: tuple[int, int] | None = None
_refresh_lock = threading.Lock()


class ModelLoadError(Exception):
    """Raised when the trusted production artifact cannot be used."""


def resolve_forecast_artifact_path() -> Path:
    """Return the canonical artifact path relative to the backend directory."""
    return FORECAST_ARTIFACT_PATH.resolve()


def resolve_all_budget_artifact_path() -> Path:
    """Return the all-budget artifact path (same as the active production path)."""
    return CANDIDATE_ARTIFACT_PATH.resolve()


def resolve_deployed_is_artifact_path() -> Path:
    """Return the previous International Settlement pickle. Do not overwrite it."""
    return DEPLOYED_IS_ARTIFACT_PATH.resolve()


def reset_model_cache() -> None:
    """Clear the process-level cache so tests can reload a fixture."""
    global _bundle, _load_error, _loaded_path, _loaded_identity
    _bundle = None
    _load_error = None
    _loaded_path = None
    _loaded_identity = None


def _artifact_identity(path: Path) -> tuple[int, int] | None:
    try:
        if not path.is_file():
            return None
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size <= 0:
        return None
    modified = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))
    return (int(stat.st_size), modified)


def get_model_bundle() -> dict | None:
    _refresh_production_artifact_if_replaced()
    return _bundle


def _refresh_production_artifact_if_replaced() -> None:
    """Load a complete replacement of the production pickle without discarding an in-use bundle."""
    global _bundle, _load_error, _loaded_identity
    if _bundle is None or _loaded_path is None:
        return
    production = resolve_forecast_artifact_path()
    if Path(_loaded_path).resolve() != production:
        return
    identity = _artifact_identity(production)
    if identity is None or identity == _loaded_identity:
        return
    with _refresh_lock:
        identity = _artifact_identity(production)
        if identity is None or identity == _loaded_identity:
            return
        previous = _bundle
        previous_identity = _loaded_identity
        try:
            with open(production, "rb") as file:
                bundle = pickle.load(file)
            _validate_bundle(bundle)
        except Exception as error:
            logger.error("Updated production artifact was not loaded: %s", error)
            _bundle = previous
            _loaded_identity = previous_identity
            return
        _bundle = bundle
        _loaded_identity = identity
        _load_error = None
        logger.info("Reloaded replaced production artifact from %s", production)


def is_model_loaded() -> bool:
    return _bundle is not None


def get_load_error() -> str | None:
    return _load_error


def _is_international_settlement(category) -> bool:
    text = str(category or "").casefold()
    return "int" in text and "settlement" in text


def _validate_bundle(bundle: dict) -> None:
    if not isinstance(bundle, dict):
        raise ModelLoadError("The forecast artifact is not a dictionary.")
    forecast_type = bundle.get("forecast_type")
    if forecast_type not in ALLOWED_FORECAST_TYPES:
        raise ModelLoadError(
            f"forecast_type must be {EXPECTED_FORECAST_TYPE}."
        )
    schema = str(bundle.get("artifact_schema_version") or "")
    all_account = schema == ALL_ACCOUNT_SCHEMA_VERSION
    if not all_account and not _is_international_settlement(bundle.get("category")):
        raise ModelLoadError("Category must be Int'l Settlement.")
    selected = [str(code) for code in bundle.get("selected_accounts") or []]
    if not selected or len(set(selected)) != len(selected):
        raise ModelLoadError("selected_accounts must contain unique Budget Codes.")
    if not all_account:
        if len(selected) != EXPECTED_ACCOUNT_COUNT:
            raise ModelLoadError(
                f"selected_accounts must contain exactly {EXPECTED_ACCOUNT_COUNT} unique Budget Codes."
            )
    models = bundle.get("models")
    if not isinstance(models, dict):
        raise ModelLoadError("models must be a dictionary of fitted account models.")
    records = bundle.get("account_records") or bundle.get("account_models") or models
    overall = bundle.get("overall_best_algorithm")
    per_code = forecast_type == EXPECTED_FORECAST_TYPE or overall in {None, "", PER_BUDGET_CODE_ALGORITHM}
    if not per_code and not overall:
        raise ModelLoadError("overall_best_algorithm is missing.")
    if overall not in {None, "", PER_BUDGET_CODE_ALGORITHM} and all_account:
        raise ModelLoadError("overall_best_algorithm must not override per-budget-code selections.")
    if not bundle.get("historical_start") or not bundle.get("historical_end"):
        raise ModelLoadError("Historical start and end are required.")
    metrics = [str(item).upper() for item in bundle.get("evaluation_metrics") or []]
    if "MASE" not in metrics:
        raise ModelLoadError("Evaluation metrics must include MASE.")
    missing_meta = [key for key in REQUIRED_FORECAST_METADATA if key not in bundle]
    if missing_meta:
        raise ModelLoadError("Required forecast metadata is missing: " + ", ".join(missing_meta))
    if all_account:
        missing_all = [key for key in ALL_ACCOUNT_REQUIRED_KEYS if key not in bundle]
        if missing_all:
            raise ModelLoadError(
                "All-budget artifact is missing required runtime fields: " + ", ".join(missing_all)
            )
        if not isinstance(bundle.get("conformal_prediction"), dict):
            raise ModelLoadError("conformal_prediction is invalid.")
        if not isinstance(bundle.get("forecast_coverage"), dict):
            raise ModelLoadError("forecast_coverage is invalid.")
        if not isinstance(records, dict) or not records:
            raise ModelLoadError("account_records are required for the all-budget artifact.")
        if "feature_selection_results" in bundle:
            raise ModelLoadError("feature_selection_results must not be stored in the all-budget artifact.")
    if not all_account:
        if len(models) != EXPECTED_ACCOUNT_COUNT:
            raise ModelLoadError(
                f"Model count is {len(models)}, expected {EXPECTED_ACCOUNT_COUNT}."
            )
        if set(str(code) for code in models) != set(selected):
            raise ModelLoadError("Model keys must exactly match selected_accounts.")
    fitted_codes = []
    for code in selected:
        record = (records.get(code) or records.get(str(code)) or models.get(code) or models.get(str(code)))
        if not isinstance(record, dict):
            if all_account:
                raise ModelLoadError(f"Account record for {code} is invalid.")
            raise ModelLoadError(f"Model entry for {code} is invalid.")
        status = str(record.get("production_status") or "")
        algorithm = str(record.get("algorithm") or record.get("model_name") or "").strip()
        fitted = record.get("model") if record.get("model") is not None else record.get("trained_model")
        if all_account and status and status != "FITTED_MODEL":
            if status not in ALLOWED_NON_FITTED_STATUSES:
                raise ModelLoadError(f"{code} production_status {status!r} is not recognized.")
            if status in {"ZERO_POLICY", "NO_DATA", "ALL_MISSING", "NOT_EVALUABLE", "NO_VALID_WINNER"} and fitted is not None:
                raise ModelLoadError(
                    f"{code} {status} record must not include a fitted model."
                )
            continue
        if not algorithm:
            raise ModelLoadError(f"{code} selected model name is missing.")
        if not per_code and algorithm != overall:
            raise ModelLoadError(
                f"{code} uses {record.get('algorithm')}, expected {overall}."
            )
        if fitted is None:
            raise ModelLoadError(f"{code} fitted model is missing.")
        fitted_codes.append(code)
    if all_account and set(str(code) for code in models) != set(fitted_codes):
        raise ModelLoadError("Model keys must match fitted-model accounts.")
    conformal = bundle.get("conformal_prediction")
    if conformal is not None:
        if not isinstance(conformal, dict):
            raise ModelLoadError("conformal_prediction is invalid.")
        conformal_algo = conformal.get("overall_best_algorithm")
        if (
            not per_code
            and conformal_algo
            and conformal_algo != overall
        ):
            raise ModelLoadError(
                "conformal_prediction algorithm does not match overall_best_algorithm."
            )


def load_and_validate_artifact(path: str | Path | None = None) -> dict:
    """Load and validate the production forecast artifact, then cache it."""
    global _bundle, _load_error, _loaded_path, _loaded_identity
    reset_model_cache()
    model_path = Path(path).resolve() if path else resolve_forecast_artifact_path()
    try:
        if not model_path.exists() or not model_path.is_file():
            raise ModelLoadError(f"Forecast artifact not found: {model_path}")
        if model_path.stat().st_size <= 0:
            raise ModelLoadError(f"Forecast artifact is empty: {model_path}")
        with open(model_path, "rb") as file:
            bundle = pickle.load(file)
        _validate_bundle(bundle)
        _bundle = bundle
        _loaded_path = str(model_path.resolve())
        _loaded_identity = _artifact_identity(model_path)
        _load_error = None
        logger.info(
            "Forecast artifact loaded\n"
            "Category: %s\n"
            "Historical period: %s – %s\n"
            "Selected algorithms: per budget code\n"
            "Model count: %s",
            bundle.get("category"),
            bundle.get("historical_start"),
            bundle.get("historical_end"),
            len(bundle.get("models") or {}),
        )
        return bundle
    except ModelLoadError as error:
        _bundle = None
        _load_error = str(error)
        logger.error("Failed to load forecast artifact from %s: %s", model_path, error)
        raise
    except Exception as error:
        message = f"Forecast artifact is invalid or corrupt: {error}"
        _bundle = None
        _load_error = message
        logger.error("Failed to load forecast artifact from %s: %s", model_path, error)
        raise ModelLoadError(message) from error


def load_production_model() -> dict:
    """Load the canonical production artifact. Raises ModelLoadError on failure."""
    return load_and_validate_artifact()


def load_all_budget_artifact() -> dict:
    """Load the all-budget production artifact."""
    return load_and_validate_artifact(resolve_all_budget_artifact_path())
