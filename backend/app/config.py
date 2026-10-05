"""Backend runtime configuration."""

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
FORECAST_ARTIFACT_NAME = "all_budget_code_models.pkl"
FORECAST_ARTIFACT_PATH = (
    BACKEND_DIR / "model_training_engine" / "output" / FORECAST_ARTIFACT_NAME
)
DEPLOYED_IS_ARTIFACT_NAME = "international_settlement_top12_models.pkl"
DEPLOYED_IS_ARTIFACT_PATH = (
    BACKEND_DIR / "model_training_engine" / "output" / DEPLOYED_IS_ARTIFACT_NAME
)
ORIGINAL_ACTUALS_PATH = Path(
    os.getenv(
        "ORIGINAL_ACTUALS_PATH",
        "/app/model_training_engine/dataset/historical_actuals_master.xlsx",
    )
)
# Logical master dataset. Retraining reads this workbook until the database
# holds historical actuals, then exports a managed snapshot.
HISTORICAL_MASTER_DATASET_PATH = Path(
    os.getenv("HISTORICAL_MASTER_DATASET_PATH", str(ORIGINAL_ACTUALS_PATH))
)
MANAGED_MASTER_FILENAME = "historical_actuals_master.xlsx"
EXPECTED_CATEGORY = "Int'l Settlement"
EXPECTED_FORECAST_TYPE = "PER_BUDGET_CODE_BEST_MODELS"
LEGACY_FORECAST_TYPE = "INTERNATIONAL_SETTLEMENT_DEMO"
ALLOWED_FORECAST_TYPES = {EXPECTED_FORECAST_TYPE, LEGACY_FORECAST_TYPE}
PER_BUDGET_CODE_ALGORITHM = "PER_BUDGET_CODE"
LEGACY_EXPECTED_ACCOUNT_COUNT = 12
EXPECTED_ACCOUNT_COUNT = LEGACY_EXPECTED_ACCOUNT_COUNT
ALL_ACCOUNT_SCHEMA_VERSION = "all_account_per_budget_code_v1"
CANDIDATE_ARTIFACT_NAME = FORECAST_ARTIFACT_NAME
CANDIDATE_ARTIFACT_PATH = FORECAST_ARTIFACT_PATH
MODEL_AMOUNT_UNIT = "LKR_MILLIONS"
RESPONSE_AMOUNT_UNIT = "LKR_MILLIONS"
AMOUNT_CONVERSION_FACTOR = 1.0


def _split_origins(value: str) -> list[str]:
    return [origin.strip() for origin in value.split(",") if origin.strip()]


CORS_ORIGINS = _split_origins(
    os.getenv(
        "CORS_ORIGINS",
        "http://localhost,http://localhost:80,http://localhost:3000,http://localhost:5173",
    )
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://forecast_admin:change_me@db:5432/ai_budget_forecasting",
)

DATASET_STORAGE_DIR = Path(
    os.getenv("DATASET_STORAGE_DIR", str(BACKEND_DIR / "data" / "dataset_files"))
)
DATASET_MAX_UPLOAD_BYTES = int(os.getenv("DATASET_MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))
DATASET_PREVIEW_TTL_SECONDS = int(os.getenv("DATASET_PREVIEW_TTL_SECONDS", "7200"))

