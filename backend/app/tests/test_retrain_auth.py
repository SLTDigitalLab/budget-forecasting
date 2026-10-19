"""Graph User.Read authorization. Graph responses are injected and Microsoft is not called."""

from __future__ import annotations

import os
import unittest
from datetime import timedelta
from unittest.mock import patch

os.environ["RETRAIN_DISABLE_MAINTENANCE"] = "1"

from fastapi.testclient import TestClient

from app.main import app
from app.services.action_audit import recent_actions, reset_action_audit
from app.services.microsoft_auth import (
    GRAPH_ME,
    EditorPermissionDenied,
    GraphProbeError,
    GraphUnavailable,
    MicrosoftAuthRejected,
    describe_caller,
    reset_test_auth,
    use_test_graph_profile,
)
from app.services.retrain_jobs import cancel_job, save_job, set_runner, start_job, status
from app.services.retrain_state import (
    RetrainForbidden,
    RetrainStateError,
    mutate,
    reset_backend,
    use_memory_backend,
    utcnow,
    worker_id_for_process,
)

OTHER_USER = {"tenant_id": "", "object_id": "oid-other", "name": "Other Person", "email": "other@example.com", "is_editor": False, "identity_source": "microsoft_graph"}
ADMIN = {"tenant_id": "", "object_id": "oid-editor", "name": "Current Editor", "email": "editor@example.com", "is_editor": True, "identity_source": "microsoft_graph"}
INITIATOR = {"tenant_id": "", "object_id": "oid-ada", "name": "Ada", "email": "ada@example.com", "is_editor": True, "identity_source": "microsoft_graph"}
OTHER_BROWSER = {"tenant_id": "", "object_id": "oid-ada", "name": "Ada Lovelace", "email": "ada@example.com", "is_editor": True, "identity_source": "microsoft_graph"}


def _profile_for(token):
    profiles = {
        "ada-token": ("oid-ada", "Digital Lab", "Research Executive", "Ada"),
        "editor-token": ("oid-editor", "Digital Lab", "Research Executive", "Editor"),
        "viewer-token": ("oid-other", "Finance", "Analyst", "Other"),
    }
    if token not in profiles:
        raise GraphProbeError(401, "http")
    oid, department, title, name = profiles[token]
    return {
        "id": oid,
        "displayName": name,
        "mail": f"{oid}@example.com",
        "userPrincipalName": f"{oid}@example.com",
        "department": department,
        "jobTitle": title,
    }


def _probe(status_code, kind="http"):
    def call(_token):
        raise GraphProbeError(status_code, kind)

    return call


class GraphRejectionTests(unittest.TestCase):
    def tearDown(self):
        reset_test_auth()

    def test_graph_failures_and_missing_fields_deny_changes(self):
        use_test_graph_profile(_probe(401))
        with self.assertRaises(MicrosoftAuthRejected) as expired:
            describe_caller("Bearer opaque-token")
        self.assertEqual(expired.exception.status_code, 401)
        use_test_graph_profile(_probe(403))
        with self.assertRaises(MicrosoftAuthRejected) as forbidden:
            describe_caller("Bearer opaque-token")
        self.assertEqual(forbidden.exception.status_code, 403)
        use_test_graph_profile(_probe(None, "timeout"))
        with self.assertRaises(GraphUnavailable):
            describe_caller("Bearer opaque-token")
        use_test_graph_profile(_probe(503, "failure"))
        with self.assertRaises(GraphUnavailable):
            describe_caller("Bearer opaque-token")
        use_test_graph_profile({"displayName": "Ada", "department": "Digital Lab", "jobTitle": "Research Executive"})
        with self.assertRaises(EditorPermissionDenied):
            describe_caller("Bearer missing-id")
        use_test_graph_profile({"id": "graph-user", "department": "Digital Lab"})
        denied = describe_caller("Bearer missing-title")
        self.assertFalse(denied.is_editor)

    def test_graph_profile_is_authoritative_and_the_endpoint_is_fixed(self):
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"id":"graph-user","displayName":"Ada","mail":"ada@example.com","department":"Digital Lab","jobTitle":"Research Executive"}'

        def urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return Response()

        with patch("app.services.microsoft_auth.urllib.request.urlopen", urlopen):
            identity = describe_caller("Bearer selected-graph-token")
        self.assertEqual(captured["url"], GRAPH_ME)
        self.assertNotIn("selected-graph-token", captured["url"])
        self.assertEqual(captured["authorization"], "Bearer selected-graph-token")
        self.assertEqual(captured["timeout"], 5)
        self.assertEqual(identity.object_id, "graph-user")
        self.assertEqual(identity.tenant_id, "")
        self.assertEqual(identity.identity_source, "microsoft_graph")
        self.assertTrue(identity.is_editor)
        use_test_graph_profile({"id": "graph-user", "department": "Finance", "jobTitle": "Analyst", "displayName": "Forged"})
        denied = describe_caller("Bearer token-with-editor-claims")
        self.assertFalse(denied.is_editor)
        from app.services.microsoft_auth import require_editor
        with self.assertRaises(EditorPermissionDenied):
            require_editor("Bearer token-with-editor-claims")


class IdentityRecoveryTests(unittest.TestCase):
    def setUp(self):
        reset_action_audit()
        self.backend = use_memory_backend()

    def tearDown(self):
        reset_backend()

    def _ready(self, job_id="ready", oid="", tid="", state="READY_TO_SAVE"):
        now = utcnow()

        def seed(store):
            store["jobs"][job_id] = {
                "id": job_id,
                "state": state,
                "initiator_tid": tid,
                "initiator_oid": oid,
                "initiator_display_name": "Legacy label",
                "manage_token_hash": "legacy-secret-hash",
                "snapshot_fingerprint": "fingerprint-1",
                "snapshot_revision": 4,
                "candidate_path": "candidate.pkl",
                "candidate_validated": True,
                "publication_status": "not_published",
                "heartbeat_at": now,
                "created_at": now,
                "logs": "ready log",
                "worker_id": worker_id_for_process(),
            }
            store["coordination"]["retrain_job_id"] = job_id

        mutate(seed)

    def test_same_object_id_recovers_from_another_browser_without_a_manage_token(self):
        self._ready(oid="oid-ada", tid="")
        view = status(OTHER_BROWSER)
        self.assertTrue(view["can_manage"])
        self.assertEqual(view["logs"], "ready log")
        outsider = status(OTHER_USER)
        self.assertFalse(outsider["can_manage"])
        self.assertEqual(outsider["logs"], "ready log")
        with self.assertRaises(RetrainForbidden):
            save_job("ready", OTHER_USER)
        with self.assertRaises(RetrainForbidden):
            cancel_job("ready", OTHER_USER)

    def test_legacy_manage_token_jobs_are_not_public_and_admins_can_recover_them(self):
        self._ready(state="SAVE_FAILED")
        self.assertTrue(status(None)["legacy_unverified"])
        self.assertFalse(status(OTHER_USER)["can_manage"])
        with self.assertRaises(RetrainForbidden):
            cancel_job("ready", OTHER_USER)
        cancelled = cancel_job("ready", ADMIN)
        self.assertEqual(cancelled["state"], "CANCELLED")
        self.assertFalse(cancelled["locked"])
        audit = recent_actions()
        self.assertEqual(audit[-1]["action"], "retrain_discard")
        self.assertEqual(audit[-1]["actor_oid"], "oid-editor")
        self.assertEqual(audit[-1]["result"], "succeeded")
        self.assertNotIn("access_token", audit[-1])
        self.assertEqual(audit[-1]["actor_email"], "editor@example.com")

    def test_training_and_publication_cannot_be_discarded(self):
        self._ready(oid="oid-ada", tid="", state="TRAINING")
        with self.assertRaises(RetrainStateError):
            cancel_job("ready", INITIATOR)
        with self.assertRaises(RetrainStateError):
            cancel_job("ready", ADMIN)

        def saving(store):
            store["jobs"]["ready"]["state"] = "SAVING"

        mutate(saving)
        with self.assertRaises(RetrainStateError):
            cancel_job("ready", ADMIN)

    def test_abandoned_deadline_still_releases_a_legacy_job(self):
        now = utcnow()
        self._ready(state="READY_TO_SAVE")

        def age(store):
            store["jobs"]["ready"]["abandon_at"] = now - timedelta(hours=1)

        mutate(age)
        from app.services.retrain_jobs import sweep_persisted

        sweep_persisted(now)
        self.assertEqual(self.backend.state["jobs"]["ready"]["state"], "ABANDONED")
        self.assertIsNone(self.backend.state["coordination"]["retrain_job_id"])


class AuthRouteTests(unittest.TestCase):
    def setUp(self):
        reset_action_audit()
        self.backend = use_memory_backend()
        use_test_graph_profile(_profile_for)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        reset_test_auth()
        reset_backend()

    def _seed_lock(self, state_name="TRAINING", oid="", tid=""):
        now = utcnow()

        def seed(store):
            store["jobs"]["held"] = {
                "id": "held",
                "state": state_name,
                "initiator_tid": tid,
                "initiator_oid": oid,
                "manage_token_hash": "legacy-secret-hash",
                "heartbeat_at": now,
                "created_at": now,
                "worker_id": worker_id_for_process(),
                "candidate_validated": state_name == "SAVE_FAILED",
                "candidate_path": "candidate.pkl" if state_name == "SAVE_FAILED" else None,
                "publication_status": "not_published",
                "logs": "locked log",
                "snapshot_fingerprint": "fingerprint-1",
                "snapshot_revision": 4,
            }
            store["coordination"]["retrain_job_id"] = "held"

        mutate(seed)

    def test_health_and_authorized_recovery_stay_available_during_the_lock(self):
        self._seed_lock(oid="legacy-oid", tid="legacy-tenant", state_name="SAVE_FAILED")
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        anonymous = self.client.get("/api/retraining/status")
        self.assertEqual(anonymous.status_code, 200)
        self.assertTrue(anonymous.json()["locked"])
        self.assertEqual(anonymous.json()["logs"], "locked log")
        self.assertNotIn("candidate_path", anonymous.json())
        holder = self.client.get("/api/retraining/status", headers={"Authorization": "Bearer editor-token"})
        self.assertEqual(holder.status_code, 200)
        self.assertTrue(holder.json()["can_manage"])
        self.assertEqual(holder.json()["logs"], "locked log")
        other = self.client.post(
            "/api/retraining/jobs/held/cancel",
            headers={"Authorization": "Bearer viewer-token"},
        )
        self.assertEqual(other.status_code, 403)
        forged = self.client.get("/api/retraining/status", headers={"Authorization": "Bearer forged-token"})
        self.assertEqual(forged.status_code, 401)
        legacy = self.client.post(
            "/api/retraining/jobs/held/cancel",
            headers={"X-Retrain-Token": "legacy-secret-hash"},
        )
        self.assertEqual(legacy.status_code, 401)
        self.assertEqual(self.backend.state["jobs"]["held"]["state"], "SAVE_FAILED")
        cancelled = self.client.post(
            "/api/retraining/jobs/held/cancel",
            headers={"Authorization": "Bearer editor-token"},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["state"], "CANCELLED")
        self.assertEqual(recent_actions()[-1]["actor_oid"], "oid-editor")
        self.assertEqual(recent_actions()[-1]["actor_identity_source"], "microsoft_graph")
        self.assertEqual(recent_actions()[-1]["actor_tid"], "")
        self.assertEqual(recent_actions()[-1]["action"], "retrain_discard")

    def test_graph_authorization_does_not_require_an_api_secret(self):
        set_runner(lambda _job_id: None)
        try:
            with patch.dict(os.environ, {"AZURE_API_AUDIENCE": "", "AZURE_CLIENT_SECRET": "", "AZURE_TENANT_ID": ""}, clear=False):
                with patch("app.services.retrain_jobs.capture_snapshot", return_value={
                    "path": "snapshot.xlsx",
                    "fingerprint": "fingerprint-1",
                    "revision": 4,
                    "source": "database",
                    "earliest_month": "2023-01",
                    "latest_month": "2026-06",
                    "budget_code_count": 1,
                }):
                    response = self.client.post("/api/retraining/jobs", headers={"Authorization": "Bearer ada-token"})
        finally:
            set_runner(None)
        self.assertEqual(response.status_code, 200)
        stored = self.backend.state["jobs"][response.json()["job_id"]]
        self.assertEqual(stored["initiator_oid"], "oid-ada")
        self.assertEqual(stored["initiator_identity_source"], "microsoft_graph")
        self.assertEqual(stored["initiator_tid"], "")
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_start_persists_the_verified_initiator(self):
        set_runner(lambda _job_id: None)
        try:
            started = start_job(INITIATOR, snapshot={
                "path": "snapshot.xlsx",
                "fingerprint": "fingerprint-1",
                "revision": 4,
                "source": "database",
                "earliest_month": "2023-01",
                "latest_month": "2026-06",
                "budget_code_count": 1,
            })
        finally:
            set_runner(None)
        stored = self.backend.state["jobs"][started["job_id"]]
        self.assertEqual(stored["initiator_oid"], "oid-ada")
        self.assertEqual(stored["initiator_tid"], "")
        self.assertEqual(stored["initiator_identity_source"], "microsoft_graph")
        self.assertEqual(stored["manage_token_hash"], "")
        self.assertNotIn("manage_token", started)
