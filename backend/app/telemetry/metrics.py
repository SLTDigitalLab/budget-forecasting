"""Prometheus metric definitions and route normalization for Next Budget."""

from __future__ import annotations

import re
import time
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

# Dedicated registry so default process collectors stay optional and labels stay bounded.
REGISTRY = CollectorRegistry()

HTTP_REQUESTS_TOTAL = Counter(
    "next_budget_http_requests_total",
    "Total HTTP requests handled by the Next Budget backend.",
    ["method", "route", "status_code"],
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "next_budget_http_request_duration_seconds",
    "HTTP request duration in seconds measured through response completion.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
    registry=REGISTRY,
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "next_budget_http_requests_in_progress",
    "HTTP requests currently in progress on this backend process.",
    registry=REGISTRY,
)
HTTP_LAST_REQUEST_TIMESTAMP_SECONDS = Gauge(
    "next_budget_http_last_request_timestamp_seconds",
    "Unix timestamp of the most recent completed non-excluded HTTP request.",
    registry=REGISTRY,
)

_ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_NUMERIC_RE = re.compile(r"^\d+$")

# Paths that must never appear in application request telemetry.
_EXCLUDED_EXACT = frozenset(
    {
        "/internal/metrics",
        "/api/health",
        "/api/ready",
        "/health",
        "/ready",
        "/readiness",
        "/api/readiness",
    }
)


def should_exclude_path(path: str) -> bool:
    cleaned = (path or "").split("?", 1)[0].rstrip("/") or "/"
    if cleaned in _EXCLUDED_EXACT:
        return True
    # Defensive: any metrics scrape path under /internal/
    if cleaned == "/internal/metrics" or cleaned.startswith("/internal/metrics/"):
        return True
    return False


def normalize_method(method: str | None) -> str:
    text = str(method or "OTHER").upper()
    if text in _ALLOWED_METHODS:
        return text
    return "OTHER"


def normalize_status_code(status_code: int | None) -> str:
    if status_code is None:
        return "unknown"
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return "unknown"
    if 100 <= code <= 599:
        return str(code)
    return "unknown"


def _sanitize_template_segment(segment: str) -> str:
    if segment.startswith("{") and segment.endswith("}"):
        return segment
    if _UUID_RE.match(segment) or _NUMERIC_RE.match(segment):
        return "{id}"
    return segment


def normalize_route(path: str, route_template: str | None = None) -> str:
    """Return a low-cardinality route template. Unknown paths map to unmatched."""
    if route_template:
        template = str(route_template).split("?", 1)[0]
        if not template.startswith("/"):
            template = "/" + template
        # Starlette mounts may leave path params already templated.
        parts = [part for part in template.split("/") if part != ""]
        cleaned = "/" + "/".join(_sanitize_template_segment(part) for part in parts)
        if cleaned.startswith("/api/") or cleaned.startswith("/internal/"):
            return cleaned
        # Non-API templates still recorded if the framework matched them.
        return cleaned if cleaned != "/" else "unmatched"

    raw = (path or "").split("?", 1)[0]
    if not raw.startswith("/"):
        raw = "/" + raw
    if raw != "/" and raw.endswith("/"):
        raw = raw.rstrip("/")
    if not raw.startswith("/api/") and not raw.startswith("/internal/"):
        return "unmatched"
    parts = []
    for part in raw.split("/"):
        if part == "":
            continue
        if _UUID_RE.match(part) or _NUMERIC_RE.match(part):
            parts.append("{id}")
        else:
            # Drop residual query-like fragments and keep path tokens only.
            parts.append(part.split(";", 1)[0])
    if not parts:
        return "unmatched"
    return "/" + "/".join(parts)


def observe_request(
    *,
    method: str,
    route: str,
    status_code: int | None,
    duration_seconds: float,
) -> None:
    method_label = normalize_method(method)
    route_label = route or "unmatched"
    status_label = normalize_status_code(status_code)
    HTTP_REQUESTS_TOTAL.labels(
        method=method_label,
        route=route_label,
        status_code=status_label,
    ).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(
        method=method_label,
        route=route_label,
    ).observe(max(duration_seconds, 0.0))
    HTTP_LAST_REQUEST_TIMESTAMP_SECONDS.set(time.time())


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def success_failure_from_status(status_code: int | None) -> str | None:
    """Classify observed statuses. 2xx/3xx success; 4xx/5xx failure."""
    if status_code is None:
        return None
    if 200 <= status_code < 400:
        return "success"
    if 400 <= status_code < 600:
        return "failure"
    return None


def registry_snapshot_hints() -> dict[str, Any]:
    """Small non-secret diagnostic helper for local verification scripts."""
    return {
        "metric_names": [
            "next_budget_http_requests_total",
            "next_budget_http_request_duration_seconds",
            "next_budget_http_requests_in_progress",
            "next_budget_http_last_request_timestamp_seconds",
        ],
        "success_rule": "2xx/3xx from next_budget_http_requests_total",
        "failure_rule": "4xx/5xx from next_budget_http_requests_total",
    }
