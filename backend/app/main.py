import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import CORS_ORIGINS
from app.db import DatabaseError, ensure_forecast_tables
from app.routers.forecasting import router as forecasting_router
from app.routers.overview import router as overview_router
from app.routers.datasets import router as datasets_router
from app.routers.retraining import router as retraining_router
from app.services.retrain_state import CoordinationError
from app.schemas.forecast import HealthResponse
from app.services.forecasting_service import (
    InvalidForecastWindowError,
    list_forecast_categories,
    published_forecast_bounds,
)
from app.services.model_loader import (
    ModelLoadError,
    get_model_bundle,
    is_model_loaded,
    load_production_model,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        load_production_model()
    except ModelLoadError:
        pass
    try:
        ensure_forecast_tables()
        from app.services.dataset_service import ensure_book_entry_registry

        ensure_book_entry_registry()
        from app.services.dataset_storage import cleanup_temp_files, ensure_storage_dir
        from app.services.dataset_service import import_legacy_workbook, migrate_original_baseline

        ensure_storage_dir()
        cleanup_temp_files()
        migration = migrate_original_baseline()
        logger.info(
            "Original baseline migration hash=%s changed=%s declarations=%s numeric=%s codes=%s scope=%s..%s ambiguous=%s",
            migration.get("baseline_hash"),
            migration.get("changed"),
            migration.get("declaration_count"),
            migration.get("numeric_count"),
            migration.get("budget_code_count"),
            migration.get("scope_start"),
            migration.get("scope_end"),
            migration.get("ambiguous_count"),
        )
        if not migration.get("skipped_locked"):
            import_legacy_workbook()
    except DatabaseError:
        logger.exception("Forecast schema initialization failed")
    except Exception:
        logger.exception("Forecast startup initialization failed")
    try:
        from app.services.retrain_jobs import start_maintenance, sweep_persisted

        sweep_persisted()
        start_maintenance()
    except Exception:
        pass
    yield


app = FastAPI(
    title="AI Budget Forecasting API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(forecasting_router)
app.include_router(overview_router)
app.include_router(datasets_router)
app.include_router(retraining_router)


@app.exception_handler(CoordinationError)
async def coordination_error(_request, error: CoordinationError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": error.detail, "code": error.code},
    )


def _historical_actual_policy(bundle):
    from app.services.dataset_master import _import_engine

    _import_engine()
    from preprocessing import historical_actual_policy_status

    return historical_actual_policy_status(bundle)


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    loaded = is_model_loaded()
    bundle = get_model_bundle() or {}
    history_end = None
    min_forecast_month = None
    max_forecast_end = None
    forecast_bounds_error = None
    if loaded:
        try:
            bounds = published_forecast_bounds(bundle)
            history_end = bounds["history_end"]
            min_forecast_month = bounds["min_forecast_month"]
            max_forecast_end = bounds["max_forecast_end"]
        except InvalidForecastWindowError as error:
            forecast_bounds_error = str(error)
    categories = None
    selected_count = None
    if loaded:
        try:
            catalog = list_forecast_categories(bundle)
            categories = [item["name"] for item in catalog["categories"]]
            selected_count = len(catalog["accounts"])
        except Exception:
            categories = None
            selected_count = len(bundle.get("selected_accounts") or [])
    return HealthResponse(
        status="healthy",
        model_loaded=loaded,
        forecast_type=bundle.get("forecast_type") if loaded else None,
        history_end=history_end,
        min_forecast_month=min_forecast_month,
        max_forecast_end=max_forecast_end,
        forecast_bounds_error=forecast_bounds_error,
        category=bundle.get("category") if loaded else None,
        overall_best_algorithm=bundle.get("overall_best_algorithm") if loaded else None,
        selected_account_count=selected_count,
        categories=categories,
        historical_actual_policy=_historical_actual_policy(bundle if loaded else None),
    )
