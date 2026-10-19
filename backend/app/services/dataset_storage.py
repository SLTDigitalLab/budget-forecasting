"""Durable dataset file storage with staging and recovery."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

from app.config import DATASET_STORAGE_DIR

SAFE_SUFFIXES = {".xlsx", ".xlsm", ".csv"}


class DatasetStorageError(Exception):
    pass


def ensure_storage_dir() -> Path:
    path = Path(DATASET_STORAGE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stored_name(file_id: str, original_filename: str) -> str:
    suffix = Path(original_filename or "").suffix.lower()
    if suffix not in SAFE_SUFFIXES:
        suffix = ".xlsx"
    ident = str(uuid.UUID(str(file_id)))
    return f"{ident}{suffix}"


def resolve_stored_path(stored_filename: str) -> Path:
    name = Path(stored_filename).name
    if not re.fullmatch(r"[0-9a-f-]{36}\.(xlsx|xlsm|csv)", name, flags=re.IGNORECASE):
        raise DatasetStorageError("Stored filename is not allowed.")
    return ensure_storage_dir() / name


def staged_path(stored_filename: str) -> Path:
    return ensure_storage_dir() / f".staging-{Path(stored_filename).name}"


def write_bytes_atomically(target: Path, data: bytes) -> None:
    ensure_storage_dir()
    temp = target.with_name(f".tmp-{uuid.uuid4().hex}-{target.name}")
    try:
        temp.write_bytes(data)
        os.replace(temp, target)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def replace_current_file(stored_filename: str, data: bytes) -> Path:
    final_path = resolve_stored_path(stored_filename)
    backup = final_path.with_name(f".prev-{final_path.name}")
    staged = staged_path(stored_filename)
    try:
        write_bytes_atomically(staged, data)
        if final_path.exists():
            os.replace(final_path, backup)
        os.replace(staged, final_path)
        if backup.exists():
            backup.unlink()
        return final_path
    except Exception as error:
        if backup.exists() and not final_path.exists():
            os.replace(backup, final_path)
        if staged.exists():
            try:
                staged.unlink()
            except OSError:
                pass
        raise DatasetStorageError("Unable to replace the stored dataset file.") from error


def restore_from_backup(stored_filename: str) -> None:
    final_path = resolve_stored_path(stored_filename)
    backup = final_path.with_name(f".prev-{final_path.name}")
    if backup.exists():
        os.replace(backup, final_path)


def cleanup_temp_files() -> None:
    root = ensure_storage_dir()
    for path in root.iterdir():
        name = path.name
        if name.startswith(".tmp-") or name.startswith(".staging-") or name.startswith(".prev-"):
            try:
                path.unlink()
            except OSError:
                continue


def read_stored_file(stored_filename: str) -> bytes:
    path = resolve_stored_path(stored_filename)
    if not path.exists():
        raise DatasetStorageError("The stored dataset file was not found.")
    return path.read_bytes()
