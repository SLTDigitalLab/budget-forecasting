"""ASGI middleware that records HTTP telemetry without buffering response bodies."""

from __future__ import annotations

import time
from typing import Any, Callable

from app.telemetry.metrics import (
    HTTP_REQUESTS_IN_PROGRESS,
    normalize_route,
    observe_request,
    should_exclude_path,
)


def _matched_route_template(scope: dict[str, Any]) -> str | None:
    route = scope.get("route")
    if route is None:
        return None
    template = getattr(route, "path", None)
    if not template:
        return None
    text = str(template)
    # Prefer the framework template (already uses {param} placeholders).
    return text if text.startswith("/") else f"/{text}"


class HttpMetricsMiddleware:
    """Measure requests through final response body / disconnect without buffering."""

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        method = scope.get("method") or "OTHER"
        if should_exclude_path(path):
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code: int | None = None
        finished = False
        HTTP_REQUESTS_IN_PROGRESS.inc()

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code, finished
            if message["type"] == "http.response.start":
                try:
                    status_code = int(message.get("status"))
                except (TypeError, ValueError):
                    status_code = None
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                if not finished:
                    finished = True
                    _record(method, path, _matched_route_template(scope), status_code, start)

        try:
            await self.app(scope, receive, send_wrapper)
            if not finished:
                finished = True
                _record(method, path, _matched_route_template(scope), status_code, start)
        except Exception:
            if not finished:
                finished = True
                if status_code is None:
                    status_code = 500
                _record(method, path, _matched_route_template(scope), status_code, start)
            raise
        finally:
            # Always release the gauge, including client cancel / incomplete streams.
            HTTP_REQUESTS_IN_PROGRESS.dec()


def _record(
    method: str,
    path: str,
    route_template: str | None,
    status_code: int | None,
    start: float,
) -> None:
    if route_template:
        route = normalize_route(path, route_template)
    else:
        # No Starlette route match (typical 404) — do not emit raw URLs.
        route = "unmatched"
    observe_request(
        method=method,
        route=route,
        status_code=status_code,
        duration_seconds=time.perf_counter() - start,
    )
