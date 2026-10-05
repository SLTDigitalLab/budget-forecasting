"""Schema initialization must not deadlock Settings reads.

Unit checks cover error classification and call placement. They do not prove
that PostgreSQL avoids a deadlock. The PostgreSQL tests below are skipped when
the database cannot be reached.
"""

from __future__ import annotations

import inspect
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import psycopg

from app.config import DATABASE_URL
from app.db import (
    SCHEMA_ADVISORY_LOCK_KEY,
    DatabaseConnectionError,
    DatabaseQueryError,
    ensure_forecast_tables,
    get_connection,
    reset_schema_ready,
    schema_is_ready,
)
from app.services.dataset_service import summary
from app.services.retrain_jobs import status
from app.services.retrain_state import PostgresBackend, reset_backend


def _configured_database_urls() -> list[str]:
    urls = [DATABASE_URL]
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    urls.append(value)
    expanded = []
    for url in urls:
        expanded.append(url)
        if "@db:" in url:
            expanded.append(url.replace("@db:", "@127.0.0.1:", 1))
    return expanded


def _reachable_database_url() -> str | None:
    candidates = _configured_database_urls()
    for url in candidates:
        try:
            with psycopg.connect(url, connect_timeout=3) as connection:
                connection.execute("SELECT 1")
            return url
        except Exception:
            continue
    return None


@contextmanager
def _postgres_url():
    url = _reachable_database_url()
    if url is None:
        yield None
        return
    with patch("app.db.DATABASE_URL", url):
        yield url


class SchemaFailureTests(unittest.TestCase):
    def tearDown(self):
        reset_schema_ready()

    def test_connection_failures_stay_distinct_from_query_failures(self):
        with patch("app.db.psycopg.connect", side_effect=psycopg.OperationalError("could not translate host name")):
            with self.assertRaises(DatabaseConnectionError) as caught:
                with get_connection():
                    pass
        self.assertIn("connect", str(caught.exception).lower())

        class Connected:
            def execute(self, *_args, **_kwargs):
                raise psycopg.OperationalError("deadlock detected")

            def commit(self):
                pass

            def rollback(self):
                self.rolled_back = True

            def close(self):
                self.closed = True

        connected = Connected()
        with patch("app.db.psycopg.connect", return_value=connected):
            with self.assertLogs("app.db", level="ERROR") as logs:
                with self.assertRaises(DatabaseQueryError) as query_error:
                    with get_connection() as connection:
                        connection.execute("ALTER TABLE retrain_jobs ADD COLUMN IF NOT EXISTS example TEXT")
        self.assertNotIsInstance(query_error.exception, DatabaseConnectionError)
        self.assertIn("deadlock detected", str(query_error.exception))
        self.assertNotIn("Unable to connect", str(query_error.exception))
        self.assertTrue(any("deadlock detected" in message for message in logs.output))
        self.assertTrue(getattr(connected, "rolled_back", False))

    def test_failed_initialization_is_not_marked_ready(self):
        reset_schema_ready()

        @contextmanager
        def broken_connection():
            class Connection:
                def execute(self, statement, *_args):
                    if "pg_advisory_xact_lock" in str(statement):
                        return None
                    raise psycopg.OperationalError("deadlock detected")

            yield Connection()

        with patch("app.db.get_connection", broken_connection):
            with self.assertRaises(psycopg.OperationalError):
                ensure_forecast_tables()
        self.assertFalse(schema_is_ready())

    def test_summary_and_status_do_not_initialize_the_schema(self):
        self.assertNotIn("ensure_forecast_tables", inspect.getsource(summary))
        self.assertNotIn("ensure_forecast_tables", inspect.getsource(status))
        self.assertNotIn("ensure_forecast_tables", inspect.getsource(PostgresBackend.mutate))
        self.assertIn("ensure_forecast_tables", inspect.getsource(
            __import__("app.services.retrain_jobs", fromlist=["cli_retrain_scope"]).cli_retrain_scope
        ))


class PostgresSchemaTests(unittest.TestCase):
    def setUp(self):
        self.url_context = _postgres_url()
        self.url = self.url_context.__enter__()
        if self.url is None:
            self.skipTest(
                "PostgreSQL is not reachable. This skip is not evidence that schema initialization avoids deadlocks."
            )
        reset_schema_ready()
        reset_backend()

    def tearDown(self):
        reset_schema_ready()
        reset_backend()
        if getattr(self, "url_context", None) is not None:
            self.url_context.__exit__(None, None, None)

    def test_initialization_waits_for_the_database_lock_before_schema_changes(self):
        holder = psycopg.connect(self.url, connect_timeout=3)
        holder.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_ADVISORY_LOCK_KEY,))
        outcome = []

        def initialize():
            try:
                ensure_forecast_tables()
                outcome.append("ready")
            except Exception as error:
                outcome.append(error)

        worker = threading.Thread(target=initialize)
        worker.start()
        worker.join(1.5)
        self.assertTrue(worker.is_alive(), "schema initialization ran without waiting for the advisory lock")
        holder.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_ADVISORY_LOCK_KEY,))
        holder.commit()
        holder.close()
        worker.join(30)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, ["ready"])
        self.assertTrue(schema_is_ready())

    def test_concurrent_initialization_completes(self):
        reset_schema_ready()
        errors = []

        def initialize():
            try:
                ensure_forecast_tables()
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=initialize) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(schema_is_ready())

    def test_simultaneous_summary_and_status_requests(self):
        ensure_forecast_tables()
        errors = []

        def run_summary():
            try:
                result = summary()
            except Exception as error:
                errors.append(error)
                return
            if not isinstance(result, dict) or "budget_code_count" not in result:
                errors.append(AssertionError(result))

        def run_status():
            try:
                with patch("app.services.retrain_state._save_state", lambda *_args, **_kwargs: None):
                    with patch("app.services.retrain_jobs._sweep_now", lambda *_args, **_kwargs: None):
                        result = status(None)
            except Exception as error:
                errors.append(error)
                return
            if not isinstance(result, dict) or "locked" not in result:
                errors.append(AssertionError(result))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run_summary), pool.submit(run_status)]
            for future in futures:
                future.result(timeout=30)
        self.assertEqual(errors, [])
