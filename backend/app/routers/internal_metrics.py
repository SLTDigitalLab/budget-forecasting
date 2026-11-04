"""Internal Prometheus scrape endpoint for MonitoringPulse."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.telemetry.auth import require_metrics_bearer
from app.telemetry.metrics import metrics_payload

router = APIRouter(prefix="/internal", tags=["internal-metrics"])


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics(_: None = Depends(require_metrics_bearer)) -> Response:
    payload, content_type = metrics_payload()
    return Response(content=payload, media_type=content_type)
