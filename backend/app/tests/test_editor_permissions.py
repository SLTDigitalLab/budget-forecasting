"""Editor policy and mutation authorization. Graph responses are injected locally."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ["RETRAIN_DISABLE_MAINTENANCE"] = "1"

from fastapi.testclient import TestClient

from app.main import app
from app.services.action_audit import recent_actions, reset_action_audit
from app.services.editor_permissions import editor_decision, load_editor_rules
from app.services.microsoft_auth import reset_test_auth, use_test_graph_profile
from app.services.retrain_state import redact_public_text, reset_backend, use_memory_backend


def _profile(department="Digital Lab", job_title="Research Executive", user_id="oid-ada"):
    return {
        "id": user_id,
        "displayName": "Ada",
        "mail": "ada@example.com",
        "userPrincipalName": "ada@example.com",
        "department": department,
        "jobTitle": job_title,
    }


MUTATION_ROUTES = (
    ("/api/datasets/preview", {}),
    ("/api/datasets/confirm", {"preview_id": "x"}),
    ("/api/datasets/previews/preview-1/stage", {}),
    ("/api/datasets/master/preview", {}),
    ("/api/datasets/master/confirm", {}),
    ("/api/datasets/revisions/1/rollback/preview", {}),
    ("/api/datasets/revisions/1/rollback/confirm", {}),
    ("/api/datasets/files/file-1/edits", {}),
    ("/api/retraining/jobs", {}),
    ("/api/retraining/jobs/job-1/save", {}),
    ("/api/retraining/jobs/job-1/cancel", {}),
    ("/api/retraining/jobs/job-1/retry", {}),
)


class EditorRuleTests(unittest.TestCase):
    def test_both_fields_must_match_exactly_after_trim_and_case(self):
        rules = [{"department": "Digital Lab", "job_title": "Research Executive"}]
        self.assertTrue(editor_decision(" digital lab ", " research executive ", rules)[0])
        self.assertFalse(editor_decision("Digital", "Research Executive", rules)[0])
        self.assertFalse(editor_decision("Digital Lab", "Research", rules)[0])
        self.assertFalse(editor_decision("Digital Laboratory", "Research Executive", rules)[0])
        self.assertFalse(editor_decision("", "Research Executive", rules)[0])
        self.assertFalse(editor_decision("Digital Lab", None, rules)[0])

    def test_any_complete_rule_can_match_and_file_changes_apply_immediately(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "editor_permissions.json"
            path.write_text(json.dumps({"allowed_editor_rules": [{"department": "Finance", "job_title": "Analyst"}]}), encoding="utf-8")
            self.assertFalse(editor_decision("Digital Lab", "Research Executive", load_editor_rules(path))[0])
            path.write_text(
                json.dumps(
                    {
                        "allowed_editor_rules": [
                            {"department": "Finance", "job_title": "Analyst"},
                            {"department": "Digital Lab", "job_title": "Research Executive"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(editor_decision("Digital Lab", "Research Executive", load_editor_rules(path))[0])

    def test_missing_configuration_denies_changes(self):
        with patch("app.services.editor_permissions.permissions_path", return_value=Path("missing-editor-permissions.json")):
            allowed, message = editor_decision("Digital Lab", "Research Executive")
        self.assertFalse(allowed)
        self.assertIn("missing", message.lower())


class MutationAuthorizationTests(unittest.TestCase):
    def setUp(self):
        reset_action_audit()
        use_memory_backend()
        use_test_graph_profile(lambda _token: _profile())
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        reset_test_auth()
        reset_backend()

    def test_reads_stay_open_and_mutations_require_a_verified_editor(self):
        with patch("app.services.dataset_service.summary", return_value={"empty": True}):
            summary = self.client.get("/api/datasets/summary")
        self.assertEqual(summary.status_code, 200)
        with patch("app.services.dataset_service.list_revisions", return_value={"revisions": [], "total": 0}):
            revisions = self.client.get("/api/datasets/revisions")
        self.assertEqual(revisions.status_code, 200)
        model_status = self.client.get("/api/retraining/model-status")
        self.assertEqual(model_status.status_code, 200)
        status = self.client.get("/api/retraining/status")
        self.assertEqual(status.status_code, 200)
        denied = self.client.post("/api/datasets/confirm", json={"preview_id": "x", "department": "Digital Lab", "jobTitle": "Research Executive"})
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(recent_actions(), [])
        missing_access = self.client.get("/api/datasets/editor-access")
        self.assertEqual(missing_access.status_code, 401)

    def test_every_mutation_route_rejects_a_non_editor(self):
        use_test_graph_profile(lambda _token: _profile(department="Finance", job_title="Analyst"))
        preview = {"file": ("sample.xlsx", b"not-a-workbook", "application/octet-stream")}
        self.assertEqual(self.client.post("/api/datasets/preview", files=preview).status_code, 401)
        self.assertEqual(
            self.client.post("/api/datasets/preview", files=preview, headers={"Authorization": "Bearer viewer"}).status_code,
            403,
        )
        for path, body in MUTATION_ROUTES:
            if path == "/api/datasets/preview":
                continue
            missing = self.client.post(path, json=body)
            self.assertEqual(missing.status_code, 401, path)
            denied = self.client.post(
                path,
                json={**body, "department": "Digital Lab", "jobTitle": "Research Executive"},
                headers={"Authorization": "Bearer viewer"},
            )
            self.assertEqual(denied.status_code, 403, path)
        self.assertEqual(recent_actions(), [])

    def test_forged_profile_fields_do_not_grant_permission(self):
        use_test_graph_profile(lambda _token: _profile(department="Finance", job_title="Analyst"))
        with patch("app.services.dataset_service.confirm_upload") as confirm:
            response = self.client.post(
                "/api/datasets/confirm",
                json={"preview_id": "x", "name": "Administrator", "email": "admin@example.com", "department": "Digital Lab", "jobTitle": "Research Executive"},
                headers={"Authorization": "Bearer opaque-graph-token"},
            )
        self.assertEqual(response.status_code, 403)
        confirm.assert_not_called()
        self.assertIn("not allowed", response.json()["detail"])

    def test_matching_graph_profile_is_the_actor(self):
        with patch("app.services.dataset_service.confirm_upload", return_value={"file_id": "file-1"}) as confirm:
            response = self.client.post(
                "/api/datasets/confirm",
                json={"preview_id": "preview-1", "department": "Finance", "jobTitle": "Analyst", "name": "Wrong"},
                headers={"Authorization": "Bearer opaque-graph-token"},
            )
        self.assertEqual(response.status_code, 200)
        actor = confirm.call_args.args[2]
        self.assertEqual(actor.object_id, "oid-ada")
        self.assertEqual(actor.tenant_id, "")
        self.assertEqual(actor.identity_source, "microsoft_graph")
        self.assertEqual(actor.department, "Digital Lab")
        self.assertTrue(actor.is_editor)
        self.assertEqual(recent_actions()[-1]["actor_oid"], "oid-ada")
        self.assertEqual(recent_actions()[-1]["actor_identity_source"], "microsoft_graph")
        self.assertEqual(recent_actions()[-1]["action"], "dataset_confirm")
        access = self.client.get("/api/datasets/editor-access", headers={"Authorization": "Bearer opaque-graph-token"})
        self.assertEqual(access.status_code, 200)
        self.assertEqual(access.json()["object_id"], "oid-ada")
        self.assertTrue(access.json()["authorized"])
        use_test_graph_profile(lambda _token: _profile(department="Finance", job_title="Analyst"))
        forged = self.client.get(
            "/api/datasets/editor-access",
            headers={"Authorization": "Bearer opaque-graph-token"},
        )
        self.assertEqual(forged.status_code, 403)
        self.assertNotIn("access_token", forged.json())

    def test_logs_do_not_include_secrets_or_candidate_paths(self):
        redacted = redact_public_text("Bearer secret-token C:\\data\\retrain_candidates\\job.pkl")
        self.assertNotIn("secret-token", redacted)
        self.assertNotIn("retrain_candidates", redacted)
        self.assertIn("[redacted]", redacted)
