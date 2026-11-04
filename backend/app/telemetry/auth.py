"""Bearer token gate for the internal metrics scrape endpoint."""

from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import Header, HTTPException, status


def configured_metrics_token() -> str:
    return str(os.environ.get("METRICS_API_TOKEN") or "").strip()


def require_metrics_bearer(authorization: str | None = Header(default=None)) -> None:
    """Reject scrapes when the dedicated token is unset, missing, or invalid."""
    expected = configured_metrics_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Metrics token is not configured.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Metrics authorization required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    provided = authorization[7:].strip()
    # Hash both sides so unequal lengths never throw and comparison stays constant-time.
    provided_digest = hashlib.sha256(provided.encode("utf-8")).digest()
    expected_digest = hashlib.sha256(expected.encode("utf-8")).digest()
    if not provided or not hmac.compare_digest(provided_digest, expected_digest):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Metrics authorization failed.",
            headers={"WWW-Authenticate": "Bearer"},
        )
