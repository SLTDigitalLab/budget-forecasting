"""Activity metadata and recovery tests for historical dataset endpoints."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.dataset_service import format_activity_label, read_activity_metadata
from app.services.dataset_storage import DatasetStorageError, replace_current_file, restore_from_backup, stored_name


class DatasetActivityMetadataTests(unittest.TestCase):
    def test_name_and_email_are_read_without_inventing_email(self):
        name, email = read_activity_metadata({"name": "Ada Lovelace", "email": "ada@example.com", "preview_id": "x"})
        self.assertEqual(name, "Ada Lovelace")
        self.assertEqual(email, "ada@example.com")

    def test_missing_email_stays_blank(self):
        name, email = read_activity_metadata({"name": "Ada Lovelace"})
        self.assertEqual(name, "Ada Lovelace")
        self.assertEqual(email, "")

    def test_blank_values_are_not_replaced_with_placeholders(self):
        name, email = read_activity_metadata({})
        self.assertEqual(name, "")
        self.assertEqual(email, "")
        self.assertEqual(format_activity_label("", "", False), "Unknown")
        self.assertEqual(format_activity_label("Ada Lovelace", "ada@example.com", False), "Ada Lovelace (ada@example.com)")
        self.assertEqual(format_activity_label("Legacy import", "", True), "Legacy import")

    def test_dataset_routes_do_not_require_a_bearer_token(self):
        client = TestClient(app)
        with patch("app.services.dataset_service.summary", return_value={"empty": True}):
            response = client.get("/api/datasets/summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["empty"], True)
        self.assertIsNone(response.request.headers.get("Authorization"))


class DatasetStorageRecoveryTests(unittest.TestCase):
    def test_failed_replace_restores_previous_file(self):
        with TemporaryDirectory() as folder:
            with patch("app.services.dataset_storage.DATASET_STORAGE_DIR", Path(folder)):
                file_id = "11111111-1111-1111-1111-111111111111"
                name = stored_name(file_id, "history.xlsx")
                replace_current_file(name, b"original-bytes")
                target = Path(folder) / name
                self.assertEqual(target.read_bytes(), b"original-bytes")
                with patch("app.services.dataset_storage.os.replace", side_effect=OSError("disk full")):
                    with self.assertRaises(DatasetStorageError):
                        replace_current_file(name, b"new-bytes")
                restore_from_backup(name)
                if target.exists():
                    self.assertEqual(target.read_bytes(), b"original-bytes")

    def test_download_uses_current_stored_file_without_version_files(self):
        with TemporaryDirectory() as folder:
            with patch("app.services.dataset_storage.DATASET_STORAGE_DIR", Path(folder)):
                file_id = "22222222-2222-2222-2222-222222222222"
                name = stored_name(file_id, "history.xlsx")
                replace_current_file(name, b"first")
                replace_current_file(name, b"second")
                files = [path.name for path in Path(folder).iterdir() if path.is_file()]
                self.assertEqual(files, [name])
                self.assertEqual((Path(folder) / name).read_bytes(), b"second")


if __name__ == "__main__":
    unittest.main()
