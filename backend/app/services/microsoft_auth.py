"""Authorize Settings changes from a Microsoft Graph profile.

The browser sends the selected account's existing User.Read access token.
This module calls Graph /me with that token. It does not validate the token as
an access token issued for this API, and it does not treat decoded claims as
identity. Tenant and audience checks from the separate API/on-behalf-of design
are not performed here.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.services.editor_permissions import EditorPermissionDenied, editor_decision
from app.services.retrain_state import CoordinationError

GRAPH_ME = (
    "https://graph.microsoft.com/v1.0/me"
    "?$select=id,displayName,mail,userPrincipalName,department,jobTitle"
)
GRAPH_IDENTITY_SOURCE = "microsoft_graph"
GRAPH_TIMEOUT_SECONDS = 5
_TEST_GRAPH_CALL = None


class MicrosoftAuthRejected(CoordinationError):
    def __init__(self, detail: str, status_code: int = 401):
        super().__init__(detail, "MICROSOFT_AUTH_REJECTED", status_code)


class GraphUnavailable(CoordinationError):
    def __init__(self, detail: str):
        super().__init__(detail, "GRAPH_UNAVAILABLE", 503)


class GraphProbeError(Exception):
    """Test stand-in for a Graph HTTP failure. Not used in production."""

    def __init__(self, status_code: int | None, kind: str = "http"):
        super().__init__(kind)
        self.status_code = status_code
        self.kind = kind


@dataclass(frozen=True)
class VerifiedIdentity:
    tenant_id: str
    object_id: str
    name: str
    roles: tuple[str, ...] = ()
    is_admin: bool = False
    email: str = ""
    department: str = ""
    job_title: str = ""
    is_editor: bool = False
    identity_source: str = GRAPH_IDENTITY_SOURCE


def use_test_graph_profile(profile) -> None:
    """Install a local Graph /me result. profile may be a dict or callable(token)."""
    global _TEST_GRAPH_CALL
    _TEST_GRAPH_CALL = profile


def reset_test_auth() -> None:
    global _TEST_GRAPH_CALL
    _TEST_GRAPH_CALL = None


def require_editor(header: str | None) -> VerifiedIdentity:
    identity = describe_caller(header)
    if identity.is_editor:
        return identity
    _allowed, message = editor_decision(identity.department, identity.job_title)
    raise EditorPermissionDenied(message)


def describe_caller(header: str | None) -> VerifiedIdentity:
    token = _bearer_token(header)
    profile = fetch_graph_profile(token)
    user_id = str(profile.get("id") or "").strip()
    department = profile.get("department")
    job_title = profile.get("jobTitle")
    if not user_id:
        raise EditorPermissionDenied("Microsoft Graph did not return a user id, so this change is not allowed.")
    allowed, _message = editor_decision(department, job_title)
    email = str(profile.get("mail") or profile.get("userPrincipalName") or "")
    return VerifiedIdentity(
        tenant_id="",
        object_id=user_id,
        name=str(profile.get("displayName") or ""),
        email=email,
        department=str(department or ""),
        job_title=str(job_title or ""),
        is_editor=allowed,
        identity_source=GRAPH_IDENTITY_SOURCE,
    )


def fetch_graph_profile(token: str) -> dict:
    if _TEST_GRAPH_CALL is not None:
        try:
            profile = _TEST_GRAPH_CALL(token) if callable(_TEST_GRAPH_CALL) else _TEST_GRAPH_CALL
        except GraphProbeError as error:
            _raise_graph_probe(error)
        if not isinstance(profile, dict):
            raise GraphUnavailable("Microsoft Graph could not return the selected user's profile.")
        return profile
    request = urllib.request.Request(
        GRAPH_ME,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=GRAPH_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        _raise_http_status(getattr(error, "code", None))
    except TimeoutError as error:
        raise GraphUnavailable("Microsoft Graph did not respond in time.") from error
    except urllib.error.URLError as error:
        if isinstance(getattr(error, "reason", None), TimeoutError):
            raise GraphUnavailable("Microsoft Graph did not respond in time.") from error
        raise GraphUnavailable("Microsoft Graph could not be reached for the selected account.") from error
    except Exception as error:
        raise GraphUnavailable("Microsoft Graph could not return the selected user's profile.") from error
    if not isinstance(payload, dict):
        raise GraphUnavailable("Microsoft Graph could not return the selected user's profile.")
    return payload


def _bearer_token(header: str | None) -> str:
    if not header or not header.lower().startswith("bearer "):
        raise MicrosoftAuthRejected("A Microsoft Graph access token is required.")
    token = header.split(" ", 1)[1].strip()
    if not token:
        raise MicrosoftAuthRejected("A Microsoft Graph access token is required.")
    return token


def _raise_graph_probe(error: GraphProbeError) -> None:
    if error.kind == "timeout":
        raise GraphUnavailable("Microsoft Graph did not respond in time.")
    if error.kind == "failure":
        raise GraphUnavailable("Microsoft Graph could not return the selected user's profile.")
    _raise_http_status(error.status_code)


def _raise_http_status(status_code: int | None) -> None:
    if status_code in {401, 403}:
        raise MicrosoftAuthRejected(
            "Microsoft Graph rejected the access token.",
            401 if status_code == 401 else 403,
        )
    raise GraphUnavailable("Microsoft Graph could not return the selected user's profile.")
