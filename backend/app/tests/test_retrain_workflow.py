"""Settings retraining workflow. Training is mocked and production pickles are not replaced."""

from __future__ import annotations

import os
import pickle
import socket
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

os.environ["RETRAIN_DISABLE_MAINTENANCE"] = "1"
ENGINE_DIR = Path(__file__).resolve().parents[2] / "model_training_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from fastapi.testclient import TestClient

from app.config import FORECAST_ARTIFACT_PATH
from app.main import app
from app.services import model_loader
from app.services.model_loader import get_model_bundle, load_production_model, reset_model_cache
from app.services.retrain_jobs import (
    cancel_job,
    cli_retrain_scope,
    production_artifact_path,
    publish_candidate,
    retry_job,
    run_job_body,
    save_job,
    set_runner,
    model_status,
    start_job,
    status,
    sweep_persisted,
)
from app.services.retrain_state import (
    OperationInProgress,
    RetrainAlreadyActive,
    SnapshotMismatch,
    begin_operation,
    end_operation,
    mutate,
    reset_backend,
    use_memory_backend,
    utcnow,
    worker_id_for_process,
)
import model_training

PRODUCTION_STAT = FORECAST_ARTIFACT_PATH.stat()
IS_ARTIFACT = ENGINE_DIR / "output" / "international_settlement_top12_models.pkl"
IS_STAT = IS_ARTIFACT.stat()
USER = {"tenant_id": "tenant-1", "object_id": "oid-ada", "name": "Ada", "email": "ada@example.com", "is_editor": True}
OTHER = {"tenant_id": "tenant-1", "object_id": "oid-other", "name": "Other", "email": "other@example.com", "is_editor": False}


def _bundle(marker: str) -> dict:
    code = "511101"
    return {
        "artifact_schema_version": "all_account_per_budget_code_v1",
        "forecast_type": "PER_BUDGET_CODE_BEST_MODELS",
        "category": "ALL",
        "selected_accounts": [code],
        "overall_best_algorithm": "PER_BUDGET_CODE",
        "historical_start": "Jan-2023",
        "historical_end": marker,
        "evaluation_metrics": ["MASE"],
        "forecast_start": "Jul-2026",
        "forecast_end": "Dec-2027",
        "monthly_account_forecasts": [],
        "monthly_combined_forecast": [],
        "yearly_account_forecasts": [],
        "yearly_combined_forecast": [],
        "year_status": [],
        "account_records": {
            code: {
                "budget_code": code,
                "production_status": "ZERO_POLICY",
                "model": None,
                "trained_model": None,
                "algorithm": None,
            }
        },
        "conformal_prediction": {},
        "forecast_coverage": {"no_data_accounts": [], "zero_policy_accounts": [code]},
        "history_months": ["Jan-2023"],
        "models": {},
    }


class RetrainWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidates = self.root / "retrain_candidates"
        self.candidates.mkdir()
        self.production = self.root / "all_budget_code_models.pkl"
        self.production.write_bytes(b"previous-production-artifact")
        self.backend = use_memory_backend()
        set_runner(run_job_body)
        self.patches = [
            patch("app.services.retrain_jobs.candidate_directory", return_value=self.candidates),
            patch("app.services.retrain_jobs.production_artifact_path", return_value=self.production),
            patch("app.services.retrain_jobs.current_fingerprint", return_value=("fingerprint-1", 4)),
            patch("app.services.retrain_jobs.execute_training", side_effect=self._train),
            patch("app.services.retrain_jobs.publish_candidate", side_effect=self._publish),
        ]
        for item in self.patches:
            item.start()
        self.publish_calls = 0
        self.train_calls = 0

    def tearDown(self):
        set_runner(None)
        for item in reversed(self.patches):
            item.stop()
        reset_backend()
        self.temporary.cleanup()
        self.assertEqual(FORECAST_ARTIFACT_PATH.stat().st_size, PRODUCTION_STAT.st_size)
        self.assertEqual(FORECAST_ARTIFACT_PATH.stat().st_mtime_ns, PRODUCTION_STAT.st_mtime_ns)
        self.assertEqual(IS_ARTIFACT.stat().st_size, IS_STAT.st_size)
        self.assertEqual(IS_ARTIFACT.stat().st_mtime_ns, IS_STAT.st_mtime_ns)

    def _snapshot(self):
        return {
            "path": str(self.root / "snapshot.xlsx"),
            "fingerprint": "fingerprint-1",
            "revision": 4,
            "source": "database",
            "earliest_month": "2023-01",
            "latest_month": "2026-06",
            "budget_code_count": 2,
        }

    def _train(self, **kwargs):
        self.train_calls += 1
        self.assertFalse(kwargs["publish_production"])
        self.assertNotEqual(kwargs["output_file"], "all_budget_code_models.pkl")
        destination = Path(kwargs["output_dir"]) / kwargs["output_file"]
        destination.write_bytes(b"validated-candidate")
        kwargs["progress_callback"](
            {
                "stage": "final_training",
                "message": "[Final Training 1/2] 511101",
                "current": 1,
                "total": 2,
                "budget_code": "511101",
            }
        )
        return {"pickle_path": destination, "pickle_verified": True, "production_replaced": False}

    def _publish(self, candidate, production_path=None):
        self.publish_calls += 1
        destination = Path(production_path or self.production)
        self.assertEqual(destination.name, "all_budget_code_models.pkl")
        if getattr(self, "fail_save", False):
            raise OSError("disk full")
        destination.write_bytes(Path(candidate).read_bytes())
        return {"publication_status": "published", "cache_warning": getattr(self, "cache_warning", None), "sha256": "abc"}

    def test_training_does_not_publish_before_save_models(self):
        started = start_job(USER, snapshot=self._snapshot())
        view = status(USER)
        self.assertEqual(view["state"], "READY_TO_SAVE")
        self.assertEqual(view["logs"].count("[Final Training 1/2] 511101"), 1)
        self.assertEqual(view["progress_current"], 1)
        self.assertEqual(view["progress_total"], 2)
        self.assertEqual(view["progress_budget_code"], "511101")
        self.assertNotIn("percent", view)
        self.assertEqual(self.production.read_bytes(), b"previous-production-artifact")
        self.assertEqual(view["publication_status"], "not_published")
        self.assertTrue(view["locked"])
        self.assertTrue(view["can_manage"])

    def test_save_publishes_the_fixed_filename_and_repeat_save_is_idempotent(self):
        self.assertEqual(model_status()["status"], "unknown")
        self.assertIsNone(model_status()["published_at"])
        self.assertFalse(model_status()["retraining_required"])
        started = start_job(USER, snapshot=self._snapshot())
        saved = save_job(started["job_id"], USER)
        self.assertEqual(saved["publication_status"], "published")
        self.assertEqual(saved["message"], "Models saved successfully")
        self.assertEqual(self.production.read_bytes(), b"validated-candidate")
        self.assertEqual(self.publish_calls, 1)
        again = save_job(started["job_id"], USER)
        self.assertTrue(again["idempotent"])
        self.assertEqual(self.publish_calls, 1)
        self.assertEqual(self.production.name, "all_budget_code_models.pkl")
        published = model_status()
        self.assertEqual(published["status"], "current")
        self.assertTrue(published["published_at"])
        self.assertFalse(published["retraining_required"])
        with patch("app.services.retrain_jobs.current_fingerprint", return_value=("fingerprint-2", 5)):
            drifted = model_status()
        self.assertEqual(drifted["message"], "Data updated — retraining required")
        self.assertTrue(drifted["retraining_required"])
        self.assertEqual(drifted["published_at"], published["published_at"])

    def test_training_failure_preserves_production_and_releases_the_lock(self):
        def explode(**_kwargs):
            raise RuntimeError("candidate validation failed")

        with patch("app.services.retrain_jobs.execute_training", side_effect=explode):
            started = start_job(USER, snapshot=self._snapshot())
        view = status(USER)
        self.assertEqual(view["state"], "FAILED")
        self.assertIn("candidate validation failed", view["error_detail"])
        self.assertFalse(view["locked"])
        self.assertEqual(self.production.read_bytes(), b"previous-production-artifact")

    def test_save_failure_can_be_retried_or_cancelled(self):
        self.fail_save = True
        started = start_job(USER, snapshot=self._snapshot())
        with self.assertRaises(Exception) as caught:
            save_job(started["job_id"], USER)
        self.assertIn("previous production model is still active", str(caught.exception))
        failed = status(USER)
        self.assertEqual(failed["state"], "SAVE_FAILED")
        self.assertTrue(failed["locked"])
        self.assertEqual(self.production.read_bytes(), b"previous-production-artifact")
        self.assertTrue(any(self.candidates.iterdir()))
        self.fail_save = False
        save_job(started["job_id"], USER)
        self.assertEqual(self.production.read_bytes(), b"validated-candidate")
        sweep_persisted(utcnow() + timedelta(seconds=6))
        self.production.write_bytes(b"previous-production-artifact")
        self.fail_save = True
        started = start_job(USER, snapshot=self._snapshot())
        with self.assertRaises(Exception):
            save_job(started["job_id"], USER)
        cancelled = cancel_job(started["job_id"], USER)
        self.assertEqual(cancelled["state"], "CANCELLED")
        self.assertFalse(cancelled["locked"])
        self.assertEqual(self.production.read_bytes(), b"previous-production-artifact")
        self.assertEqual(list(self.candidates.glob("*.pkl")), [])

    def test_snapshot_mismatch_keeps_the_production_pickle(self):
        started = start_job(USER, snapshot=self._snapshot())
        with patch("app.services.retrain_jobs.current_fingerprint", return_value=("fingerprint-2", 5)):
            with self.assertRaises(SnapshotMismatch):
                save_job(started["job_id"], USER)
        view = status(USER)
        self.assertEqual(view["state"], "FAILED")
        self.assertIn("Retraining is required", view["error_detail"])
        self.assertEqual(self.production.read_bytes(), b"previous-production-artifact")
        self.assertEqual(self.publish_calls, 0)
        self.assertFalse(view["locked"])

    def test_duplicate_start_and_in_flight_forecast_are_rejected(self):
        started = start_job(USER, snapshot=self._snapshot())
        with self.assertRaises(RetrainAlreadyActive):
            start_job(USER, snapshot=self._snapshot())
        self.assertEqual(status(None)["job_id"], started["job_id"])

    def test_forecast_in_progress_rejects_retraining_without_interrupting_it(self):
        def acquire(state):
            return begin_operation(state, "forecast", utcnow())

        operation_id = mutate(acquire)
        with self.assertRaises(OperationInProgress) as caught:
            start_job(USER, snapshot=self._snapshot())
        self.assertIn("Wait for it to finish", str(caught.exception))
        self.assertIn("try retraining again", str(caught.exception).lower())

        def still_running(state):
            self.assertEqual(len(state["operations"]), 1)
            self.assertIsNone(state["coordination"]["retrain_job_id"])

        mutate(still_running)
        mutate(lambda state: end_operation(state, operation_id, utcnow()))
        started = start_job(USER, snapshot=self._snapshot())
        self.assertEqual(status(USER)["state"], "READY_TO_SAVE")

    def test_status_restores_ready_to_save_for_the_token_holder_only(self):
        started = start_job(USER, snapshot=self._snapshot())
        holder = status(USER)
        outsider = status(None)
        self.assertEqual(holder["state"], "READY_TO_SAVE")
        self.assertIn("Final Training", holder["logs"])
        self.assertTrue(holder["can_manage"])
        self.assertIn("Final Training", outsider["logs"])
        self.assertFalse(outsider["can_manage"])
        self.assertNotIn("candidate_path", outsider)
        self.assertNotIn("retrain_candidates", outsider["logs"])
        self.assertTrue(outsider["locked"])
        self.assertEqual(outsider["message"], "Model training in progress. Please wait.")
        with self.assertRaises(Exception):
            save_job(started["job_id"], OTHER)

    def test_success_countdown_unlocks_without_a_browser(self):
        started = start_job(USER, snapshot=self._snapshot())
        save_job(started["job_id"], USER)
        self.assertTrue(status(None)["locked"])
        later = utcnow() + timedelta(seconds=6)
        sweep_persisted(later)
        view = status(None)
        self.assertFalse(view["locked"])
        self.assertIsNone(view["job_id"])

    def test_quiet_training_stays_alive_and_a_dead_worker_is_interrupted(self):
        now = utcnow()

        def seed(state):
            state["jobs"]["quiet"] = {
                "id": "quiet",
                "state": "TRAINING",
                "manage_token_hash": "quiet",
                "logs": "old log",
                "heartbeat_at": now,
                "created_at": now - timedelta(hours=3),
                "updated_at": now - timedelta(hours=3),
                "worker_id": worker_id_for_process(),
                "candidate_validated": False,
                "candidate_path": None,
                "publication_status": "not_published",
            }
            state["coordination"]["retrain_job_id"] = "quiet"

        mutate(seed)
        sweep_persisted(now)
        self.assertEqual(self.backend.state["jobs"]["quiet"]["state"], "TRAINING")

        def kill(state):
            state["jobs"]["quiet"]["worker_id"] = f"{socket.gethostname()}:0"
            state["jobs"]["quiet"]["heartbeat_at"] = now

        mutate(kill)
        sweep_persisted(now)
        self.assertEqual(self.backend.state["jobs"]["quiet"]["state"], "INTERRUPTED")
        self.assertIsNone(self.backend.state["coordination"]["retrain_job_id"])
        self.assertEqual(self.production.read_bytes(), b"previous-production-artifact")

    def test_validated_candidate_can_be_recovered_and_abandoned_jobs_unlock(self):
        now = utcnow()
        candidate = self.candidates / "ready.pkl"
        candidate.write_bytes(b"validated-candidate")

        def seed(state):
            state["jobs"]["ready"] = {
                "id": "ready",
                "state": "VALIDATING",
                "manage_token_hash": "ready-hash",
                "heartbeat_at": now,
                "created_at": now,
                "worker_id": f"{socket.gethostname()}:0",
                "candidate_validated": True,
                "candidate_path": str(candidate),
                "publication_status": "not_published",
                "logs": "",
            }
            state["coordination"]["retrain_job_id"] = "ready"

        mutate(seed)
        sweep_persisted(now)
        self.assertEqual(self.backend.state["jobs"]["ready"]["state"], "READY_TO_SAVE")
        self.assertEqual(self.backend.state["coordination"]["retrain_job_id"], "ready")
        sweep_persisted(now + timedelta(hours=7))
        self.assertEqual(self.backend.state["jobs"]["ready"]["state"], "ABANDONED")
        self.assertFalse(candidate.exists())
        self.assertIsNone(self.backend.state["coordination"]["retrain_job_id"])
        self.assertEqual(self.production.read_bytes(), b"previous-production-artifact")

    def test_cache_warning_does_not_hide_a_completed_publication(self):
        self.cache_warning = "The production model was replaced, but the running forecast cache could not be refreshed: cache offline"
        started = start_job(USER, snapshot=self._snapshot())
        saved = save_job(started["job_id"], USER)
        self.assertEqual(saved["publication_status"], "published")
        self.assertIn("replaced", saved["cache_warning"])
        self.assertNotIn("still active", saved["cache_warning"])
        self.assertEqual(self.production.read_bytes(), b"validated-candidate")

    def test_retry_after_failure_starts_a_new_job(self):
        def explode(**_kwargs):
            raise RuntimeError("training failed")

        with patch("app.services.retrain_jobs.execute_training", side_effect=explode):
            started = start_job(USER, snapshot=self._snapshot())
        with patch("app.services.retrain_jobs.capture_snapshot", return_value=self._snapshot()):
            retried = retry_job(started["job_id"], USER)
        self.assertNotEqual(retried["job_id"], started["job_id"])
        self.assertEqual(status(USER)["state"], "READY_TO_SAVE")

    def test_name_is_not_authorization(self):
        started = start_job(USER, snapshot=self._snapshot())
        with self.assertRaises(Exception) as caught:
            save_job(started["job_id"], None)
        self.assertIn("not authorized", str(caught.exception))
        saved = save_job(started["job_id"], USER)
        self.assertEqual(saved["publication_status"], "published")


class RetrainRouteTests(unittest.TestCase):
    def setUp(self):
        self.backend = use_memory_backend()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        reset_backend()

    def test_locked_routes_return_423_and_health_stays_available(self):
        now = utcnow()

        def seed(state):
            state["jobs"]["held"] = {
                "id": "held",
                "state": "TRAINING",
                "manage_token_hash": "held",
                "heartbeat_at": now,
                "created_at": now,
                "worker_id": worker_id_for_process(),
                "candidate_validated": False,
                "publication_status": "not_published",
                "logs": "",
                "unlock_at": None,
            }
            state["coordination"]["retrain_job_id"] = "held"

        mutate(seed)
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        with patch("app.services.dataset_service.summary", return_value={"empty": True}):
            summary = self.client.get("/api/datasets/summary")
        self.assertEqual(summary.status_code, 200)
        forecast = self.client.post("/api/forecasting/generate", json={"forecast_months": 1})
        self.assertEqual(forecast.status_code, 423)
        self.assertEqual(forecast.json()["code"], "SYSTEM_LOCKED")
        self.assertEqual(forecast.json()["detail"], "Model training in progress. Please wait.")
        confirm = self.client.post("/api/datasets/confirm", json={"preview_id": "x"})
        self.assertIn(confirm.status_code, {401, 403, 423, 503})
        self.assertNotEqual(confirm.status_code, 200)
        stage = self.client.post("/api/datasets/previews/x/stage", json={})
        self.assertIn(stage.status_code, {401, 403, 423, 503})
        rollback = self.client.post("/api/datasets/revisions/x/rollback/confirm", json={})
        self.assertIn(rollback.status_code, {401, 403, 423, 503})
        status_response = self.client.get("/api/retraining/status")
        self.assertEqual(status_response.status_code, 200)
        self.assertTrue(status_response.json()["locked"])


class CliLockTests(unittest.TestCase):
    def setUp(self):
        use_memory_backend()

    def tearDown(self):
        reset_backend()

    def test_command_retraining_uses_the_shared_lock(self):
        order = []

        @contextmanager
        def scope():
            order.append("lock")
            yield "cli-job"
            order.append("unlock")

        def fake_training(**kwargs):
            order.append("train")
            path = Path(kwargs["output_dir"]) / kwargs["output_file"]
            path.write_bytes(b"published")
            return {"pickle_verified": True, "pickle_path": path}

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            snapshot = directory / "historical_actuals_master.xlsx"
            snapshot.write_bytes(b"snapshot")
            args = model_training.parse_args(["--retrain-from-master"])
            with (
                patch("app.services.retrain_jobs.cli_retrain_scope", scope),
                patch(
                    "app.services.dataset_master.resolve_training_snapshot",
                    return_value={
                        "source": "database",
                        "path": snapshot,
                        "budget_code_count": 1,
                        "earliest_month": "2023-01",
                        "latest_month": "2023-02",
                    },
                ),
                patch.object(model_training, "run_training", side_effect=fake_training),
            ):
                code = model_training.retrain_from_current_master(args, output_dir=directory)
        self.assertEqual(code, 0)
        self.assertEqual(order, ["lock", "train", "unlock"])

    def test_command_retraining_waits_when_a_forecast_is_running(self):
        mutate(lambda state: begin_operation(state, "forecast", utcnow()))

        def fail_if_called(**_kwargs):
            raise AssertionError("training should not start")

        with tempfile.TemporaryDirectory() as tmp:
            args = model_training.parse_args(["--retrain-from-master"])
            with patch.object(model_training, "run_training", side_effect=fail_if_called):
                code = model_training.retrain_from_current_master(args, output_dir=tmp)
        self.assertEqual(code, 1)
        mutate(lambda state: self.assertEqual(len(state["operations"]), 1))


class ArtifactReloadTests(unittest.TestCase):
    def setUp(self):
        reset_model_cache()

    def tearDown(self):
        reset_model_cache()
        self.assertEqual(FORECAST_ARTIFACT_PATH.stat().st_size, PRODUCTION_STAT.st_size)
        self.assertEqual(FORECAST_ARTIFACT_PATH.stat().st_mtime_ns, PRODUCTION_STAT.st_mtime_ns)

    def test_publication_reloads_the_new_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            production = directory / "all_budget_code_models.pkl"
            candidate = directory / "retrain_candidates" / "job.pkl"
            candidate.parent.mkdir()
            production.write_bytes(pickle.dumps(_bundle("Jun-2026")))
            candidate.write_bytes(pickle.dumps(_bundle("Jul-2026")))
            with patch.object(model_loader, "resolve_forecast_artifact_path", return_value=production.resolve()):
                load_production_model()
                previous = get_model_bundle()
                with patch("app.services.retrain_jobs.production_artifact_path", return_value=production.resolve()):
                    published = publish_candidate(candidate, production.resolve())
                updated = get_model_bundle()
            self.assertEqual(previous["historical_end"], "Jun-2026")
            self.assertEqual(updated["historical_end"], "Jul-2026")
            self.assertIsNone(published["cache_warning"])
            self.assertEqual(published["publication_status"], "published")

    def test_deferred_candidate_does_not_replace_production(self):
        bundle = _bundle("Jun-2026")
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            production = directory / "all_budget_code_models.pkl"
            production.write_bytes(b"previous-production-artifact")
            temporary = directory / "candidate.pkl.tmp"
            temporary.write_bytes(pickle.dumps(bundle))
            destination = directory / "retrain_candidates" / "job.pkl"
            destination.parent.mkdir()
            saved = model_training.commit_training_artifact(
                temporary,
                destination,
                bundle,
                publish_production=False,
                expected_account_count=1,
            )
            self.assertEqual(saved, destination.resolve())
            self.assertEqual(production.read_bytes(), b"previous-production-artifact")
            self.assertEqual(pickle.loads(destination.read_bytes())["historical_end"], "Jun-2026")


if __name__ == "__main__":
    unittest.main()
