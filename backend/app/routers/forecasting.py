from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.db import (
    DatabaseError,
    get_forecast_payload,
    get_latest_forecast_payload,
    list_forecast_records,
    persist_forecast_run,
)
from app.services.report_builder import ReportError, build_forecast_report
from app.services.report_excel import render_forecast_excel
from app.services.report_pdf import render_forecast_pdf
from app.schemas.forecast import (
    AccountListItem,
    AccountListResponse,
    ForecastHistoryListResponse,
    MonthlyForecastRequest,
    MonthlyForecastResponse,
)
from app.services.forecasting_service import (
    ForecastingError,
    InvalidForecastWindowError,
    UnsupportedAccountError,
    generate_monthly_forecast,
    list_forecast_categories,
    selected_account_codes,
)
from app.services.model_loader import get_load_error, get_model_bundle, is_model_loaded
from app.services.system_lock import mutation_scope

router = APIRouter(prefix="/api/forecasting", tags=["forecasting"])


def _require_model() -> dict:
    if not is_model_loaded():
        detail = get_load_error() or "The forecast engine is currently unavailable."
        raise HTTPException(status_code=503, detail=detail)
    bundle = get_model_bundle()
    if bundle is None:
        raise HTTPException(
            status_code=503,
            detail="The forecast engine is currently unavailable.",
        )
    return bundle


def _validate_complete_result(payload: dict) -> None:
    monthly = payload.get("monthly_forecasts") or []
    if not monthly:
        raise ForecastingError("Forecast monthly results are missing.")
    total = float(sum(float(item["forecast_amount"]) for item in monthly))
    expected = float(payload["overall_total"])
    if abs(total - expected) > 1e-4:
        raise ForecastingError("Forecast totals do not match monthly results.")
    if int(payload["selected_account_count"]) != len(payload.get("selected_accounts") or []):
        raise ForecastingError("Selected Budget Code count does not match the result.")


@router.get("/accounts", response_model=AccountListResponse)
def list_accounts() -> AccountListResponse:
    bundle = _require_model()
    catalog = list_forecast_categories(bundle)
    codes = selected_account_codes(bundle)
    return AccountListResponse(
        selected_account_count=len(codes),
        selected_accounts=codes,
        accounts=[
            AccountListItem(
                budget_code=item["budget_code"],
                account_name=item.get("account_name"),
                category=item.get("category"),
                production_status=item.get("production_status"),
            )
            for item in catalog["accounts"]
        ],
        categories=catalog["categories"],
        missing_category_codes=catalog["missing_category_codes"],
    )


@router.post("/generate", response_model=MonthlyForecastResponse)
def generate(request: MonthlyForecastRequest) -> MonthlyForecastResponse:
    with mutation_scope("forecast"):
        return _generate_unlocked(request)


def _generate_unlocked(request: MonthlyForecastRequest) -> MonthlyForecastResponse:
    bundle = _require_model()
    try:
        payload = generate_monthly_forecast(
            bundle,
            start_month=request.start_month,
            end_month=request.end_month,
            forecast_months=request.forecast_months,
            account_codes=request.account_codes,
            category=request.category,
        )
        response = MonthlyForecastResponse(**payload)
        _validate_complete_result(response.model_dump())
        persist_forecast_run(response.model_dump(mode="json"))
    except (InvalidForecastWindowError, UnsupportedAccountError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DatabaseError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ForecastingError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return response


@router.get("/history", response_model=ForecastHistoryListResponse)
def forecast_history(
    limit: int = Query(default=50, ge=1, le=100),
) -> ForecastHistoryListResponse:
    try:
        records = list_forecast_records(limit=limit)
    except DatabaseError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return ForecastHistoryListResponse(records=records)


@router.get("/history/latest", response_model=MonthlyForecastResponse)
def forecast_history_latest() -> MonthlyForecastResponse:
    try:
        payload = get_latest_forecast_payload()
    except DatabaseError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if payload is None:
        raise HTTPException(status_code=404, detail="No completed forecast record was found.")
    try:
        return MonthlyForecastResponse(**payload)
    except Exception as error:
        raise HTTPException(status_code=500, detail="Forecast record is invalid.") from error


def _completed_report(run_id: int) -> dict:
    try:
        payload = get_forecast_payload(run_id)
    except DatabaseError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    try:
        return build_forecast_report(payload)
    except ReportError as error:
        status = 404 if "selected" in str(error).lower() or payload is None else 409
        if payload is None:
            status = 404
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.get("/reports/{run_id}")
def forecast_report_preview(run_id: int) -> dict:
    return _completed_report(run_id)


@router.get("/reports/{run_id}/pdf")
def forecast_report_pdf(run_id: int, inline: bool = False) -> Response:
    report = _completed_report(run_id)
    try:
        content = render_forecast_pdf(report)
    except Exception as error:
        raise HTTPException(status_code=500, detail="Unable to generate the PDF report.") from error
    filename = f"{report['filename_stem']}.pdf"
    inline_preview = inline is True or str(inline).lower() in {"1", "true", "yes"}
    disposition = "inline" if inline_preview else "attachment"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@router.get("/reports/{run_id}/excel")
def forecast_report_excel(run_id: int) -> Response:
    report = _completed_report(run_id)
    try:
        content = render_forecast_excel(report)
    except Exception as error:
        raise HTTPException(status_code=500, detail="Unable to generate the Excel report.") from error
    filename = f"{report.get('excel_filename_stem') or report['filename_stem']}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/history/{run_id}", response_model=MonthlyForecastResponse)
def forecast_history_detail(run_id: int) -> MonthlyForecastResponse:
    try:
        payload = get_forecast_payload(run_id)
    except DatabaseError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if payload is None:
        raise HTTPException(status_code=404, detail="Forecast record was not found.")
    try:
        return MonthlyForecastResponse(**payload)
    except Exception as error:
        raise HTTPException(status_code=500, detail="Forecast record is invalid.") from error
