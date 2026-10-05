"""Load and atomically refresh the managed historical master workbook."""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.config import BACKEND_DIR, DATASET_STORAGE_DIR, HISTORICAL_MASTER_DATASET_PATH, MANAGED_MASTER_FILENAME
from app.services.dataset_parser import actuals_header_for_month, iso_month
from app.services.dataset_repository import list_historical_actuals, load_master_identity, master_version
from app.services.dataset_storage import ensure_storage_dir
from app.services.dataset_workflow import merge_budget_codes

ENGINE_DIR = BACKEND_DIR / "model_training_engine"


def fingerprint_master_state(state: dict) -> str:
    """Stable identity of the master revision used for a training snapshot."""
    codes = []
    for item in state.get("budget_codes") or []:
        codes.append(
            {
                "budget_code": str(item.get("budget_code")),
                "description": item.get("description") or "",
                "category": item.get("category") or "",
            }
        )
    records = []
    for item in state.get("records") or []:
        amount = item.get("amount")
        records.append(
            (
                str(item.get("budget_code")),
                str(item.get("month")),
                None if amount is None else float(amount),
            )
        )
    payload = {
        "version": int(state.get("version") or 0),
        "earliest_month": state.get("earliest_month"),
        "latest_month": state.get("latest_month"),
        "budget_codes": sorted(codes, key=lambda item: item["budget_code"]),
        "records": sorted(records),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_master_records() -> tuple[list[dict], int, str]:
    state = load_master_state()
    return state["records"], state["version"], state["source"]


def load_master_state() -> dict:
    """Current master, including budget codes that have no numeric actuals."""
    version = master_version()
    stored = list_historical_actuals()
    identity = load_master_identity()
    if stored or identity["budget_codes"]:
        earliest = identity["earliest_month"]
        latest = identity["latest_month"]
        numeric_months = sorted({str(item["month"]) for item in stored if item.get("month")})
        if not earliest and numeric_months:
            earliest = numeric_months[0]
        if not latest and numeric_months:
            latest = numeric_months[-1]
        return {
            "records": stored,
            "budget_codes": merge_budget_codes(identity["budget_codes"], [], stored),
            "earliest_month": earliest,
            "latest_month": latest,
            "version": version,
            "source": "database",
        }
    workbook = master_identity_from_workbook_path(authoritative_workbook_path())
    return {**workbook, "version": version, "source": "production_workbook"}


def master_identity_from_workbook_path(path: Path) -> dict:
    """Read every budget code and the workbook month range. Missing amounts stay missing."""
    _import_engine()
    from preprocessing import KIND_NUMBER, _collect_accounts, _load_original_excel, assign_categories, parse_amount_cell

    loaded = _load_original_excel(str(path))
    collected = _collect_accounts(loaded)
    assigned = assign_categories(loaded["df"], loaded["account_code_col"], loaded["account_name_col"])
    accounts = assigned[(~assigned["is_category_row"].fillna(False)) & assigned[loaded["account_code_col"]].notna()]
    metadata = collected["metadata"]
    codes = []
    for code in collected["raw_matrix"].index.tolist():
        meta = metadata.loc[str(code)] if str(code) in metadata.index else None
        description = "" if meta is None else str(meta.get("account_name") or "")
        category = "" if meta is None else str(meta.get("category") or "")
        if description.lower() == "nan":
            description = ""
        if category.lower() == "nan":
            category = ""
        codes.append({"budget_code": str(code), "description": description, "category": category})
    records = []
    code_column = loaded["account_code_col"]
    for _, row in accounts.iterrows():
        code = str(row.get("cleaned_account_code") or row.get(code_column) or "").strip()
        if not code or code.lower() == "nan":
            continue
        meta = next((item for item in codes if item["budget_code"] == code), None)
        for column, timestamp in zip(loaded["monthly_columns"], loaded["month_dates"]):
            parsed = parse_amount_cell(row.get(column))
            if parsed["kind"] != KIND_NUMBER:
                continue
            records.append(
                {
                    "budget_code": code,
                    "month": iso_month(timestamp),
                    "amount": float(parsed["value"]),
                    "description": "" if meta is None else meta["description"],
                    "category": "" if meta is None else meta["category"],
                    "source_file_id": None,
                }
            )
    months = [iso_month(timestamp) for timestamp in loaded["month_dates"]]
    if not codes or not months:
        raise MasterSnapshotError(f"The historical workbook has no budget codes: {path}")
    return {
        "records": records,
        "budget_codes": codes,
        "earliest_month": months[0],
        "latest_month": months[-1],
    }


def records_from_production_workbook() -> list[dict]:
    import sys

    from app.config import BACKEND_DIR

    engine = BACKEND_DIR / "model_training_engine"
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    from preprocessing import KIND_NUMBER, parse_amount_cell

    from app.routers.overview import _load_workbook

    workbook = _load_workbook()
    assigned = workbook["assigned"]
    code_column = workbook["account_code_col"]
    name_column = workbook["account_name_col"]
    records = []
    accounts = assigned.loc[~assigned["is_category_row"].fillna(False) & assigned["numeric_account_code"].notna()]
    for _, row in accounts.iterrows():
        code = str(row.get("cleaned_account_code") or "").strip()
        if not code or code.lower() == "nan":
            continue
        description = str(row.get(name_column) or "").strip()
        category = str(row.get("category") or "").strip()
        if category.lower() == "nan":
            category = ""
        for column, timestamp in zip(workbook["monthly_columns"], workbook["month_dates"]):
            parsed = parse_amount_cell(row.get(column))
            if parsed["kind"] != KIND_NUMBER:
                continue
            records.append(
                {
                    "budget_code": code,
                    "month": iso_month(timestamp),
                    "amount": float(parsed["value"]),
                    "description": description,
                    "category": category,
                    "source_file_id": None,
                }
            )
    if not records:
        raise ValueError("The production historical workbook did not contain usable monthly actuals.")
    return records


class MasterSnapshotError(RuntimeError):
    """The current historical master could not be prepared for training."""


def write_managed_master(records: list[dict]) -> Path:
    temp = prepare_managed_master(records)
    return publish_managed_master(temp)


def export_training_snapshot(
    records: list[dict],
    *,
    budget_codes: list[dict] | None = None,
    earliest_month: str | None = None,
    latest_month: str | None = None,
) -> Path:
    """Write the database master to the managed workbook and prove training can read it."""
    try:
        temp = prepare_managed_master(
            records,
            budget_codes=budget_codes,
            earliest_month=earliest_month,
            latest_month=latest_month,
        )
    except Exception as error:
        raise MasterSnapshotError(
            "Database historical master could not be exported. Retraining was not started."
        ) from error
    try:
        ensure_training_snapshot(temp)
        return publish_managed_master(temp)
    except Exception as error:
        temp.unlink(missing_ok=True)
        raise MasterSnapshotError(
            "Database historical master could not be exported. Retraining was not started."
        ) from error


def prepare_managed_master(
    records: list[dict],
    *,
    budget_codes: list[dict] | None = None,
    earliest_month: str | None = None,
    latest_month: str | None = None,
) -> Path:
    grouped: dict[str, dict] = {}
    months: set[str] = set()
    for item in budget_codes or []:
        code = str(item.get("budget_code") or "").strip()
        if not code:
            continue
        grouped.setdefault(
            code,
            {
                "description": item.get("description") or "",
                "category": item.get("category") or "",
                "amounts": {},
            },
        )
    if earliest_month:
        months.add(str(earliest_month))
    if latest_month:
        months.add(str(latest_month))
    for item in records:
        if item.get("amount") is None:
            continue
        code = str(item["budget_code"])
        bucket = grouped.setdefault(
            code,
            {
                "description": item.get("description") or "",
                "category": item.get("category") or "",
                "amounts": {},
            },
        )
        if item.get("description"):
            bucket["description"] = item["description"]
        if item.get("category"):
            bucket["category"] = item["category"]
        month = str(item["month"])
        months.add(month)
        bucket["amounts"][month] = float(item["amount"])
    if not grouped or not months:
        raise ValueError("The historical master has no numeric actuals to export.")
    ordered_months = _training_months(sorted(months))
    directory = ensure_storage_dir()
    temp = directory / f".tmp-master-{uuid.uuid4().hex}.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Historical Actuals"
    headers = ["ACT CODE", "ACT NAME", "Category", *[actuals_header_for_month(month) for month in ordered_months]]
    worksheet.append(headers)
    by_category: dict[str, list[str]] = {}
    for code in sorted(grouped):
        by_category.setdefault(str(grouped[code]["category"] or ""), []).append(code)
    category_headers = 0
    for category in sorted(by_category):
        if category:
            category_headers += 1
            worksheet.append([None, category, category, *([None] * len(ordered_months))])
        for code in by_category[category]:
            bucket = grouped[code]
            worksheet.append(
                [
                    code,
                    bucket["description"],
                    bucket["category"] or None,
                    *[bucket["amounts"].get(month) for month in ordered_months],
                ]
            )
    workbook.save(temp)
    workbook.close()
    _verify_workbook(temp, len(grouped), len(ordered_months), category_headers)
    return temp


def publish_managed_master(temp: Path) -> Path:
    directory = ensure_storage_dir()
    target = directory / MANAGED_MASTER_FILENAME
    backup = directory / f"{MANAGED_MASTER_FILENAME}.bak"
    if target.exists():
        backup.write_bytes(target.read_bytes())
    temp.replace(target)
    return target


def _verify_workbook(path: Path, code_count: int, month_count: int, category_header_count: int = 0) -> None:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = list(worksheet.iter_rows(values_only=True))
    finally:
        workbook.close()
    column_count = len(rows[0]) if rows else 0
    if len(rows) != code_count + category_header_count + 1 or column_count != month_count + 3:
        path.unlink(missing_ok=True)
        raise ValueError("The managed historical master workbook failed verification and was not replaced.")


def managed_master_path() -> Path:
    return Path(DATASET_STORAGE_DIR) / MANAGED_MASTER_FILENAME


def authoritative_workbook_path() -> Path:
    """Workbook used before historical actuals are stored in the database."""
    dataset_dir = ENGINE_DIR / "dataset"
    candidates = [
        Path(HISTORICAL_MASTER_DATASET_PATH),
        dataset_dir / "historical_actuals_master.xlsx",
        dataset_dir / "original_actual_data.xlsx",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise MasterSnapshotError("The authoritative historical workbook was not found.")


def resolve_training_snapshot() -> dict:
    """Return the current master snapshot. A failed database export does not use an older file."""
    state = load_master_state()
    if state["source"] == "database":
        path = export_training_snapshot(
            state["records"],
            budget_codes=state["budget_codes"],
            earliest_month=state["earliest_month"],
            latest_month=state["latest_month"],
        )
        summary = {
            "budget_code_count": len(state["budget_codes"]),
            "earliest_month": state["earliest_month"],
            "latest_month": state["latest_month"],
        }
        source = "database"
        version = state["version"]
    else:
        path = authoritative_workbook_path()
        summary = _summary_from_workbook(path)
        source = "production_workbook"
        version = state["version"]
    if not path.is_file() or path.stat().st_size <= 0:
        raise MasterSnapshotError(f"Historical master snapshot is missing: {path}")
    return {
        "source": source,
        "path": path,
        "master_version": version,
        **summary,
    }


def _summary_from_records(records: list[dict]) -> dict:
    codes = {str(item["budget_code"]) for item in records if item.get("amount") is not None and item.get("budget_code")}
    months = sorted({str(item["month"]) for item in records if item.get("amount") is not None and item.get("month")})
    if not codes or not months:
        raise MasterSnapshotError("The database historical master has no numeric actuals.")
    return {
        "budget_code_count": len(codes),
        "earliest_month": months[0],
        "latest_month": months[-1],
    }


def _summary_from_workbook(path: Path) -> dict:
    _import_engine()
    from preprocessing import _collect_accounts, _load_original_excel

    loaded = _load_original_excel(str(path))
    collected = _collect_accounts(loaded)
    months = list(loaded["month_dates"])
    if collected["account_count"] <= 0 or not months:
        raise MasterSnapshotError(f"The historical workbook has no trainable accounts: {path}")
    start = months[0]
    end = months[-1]
    return {
        "budget_code_count": int(collected["account_count"]),
        "earliest_month": f"{int(start.year):04d}-{int(start.month):02d}",
        "latest_month": f"{int(end.year):04d}-{int(end.month):02d}",
    }


def _training_months(observed_months: list[str]) -> list[str]:
    _import_engine()
    from preprocessing import HISTORY_START

    start_key = f"{int(HISTORY_START.year):04d}-{int(HISTORY_START.month):02d}"
    early = [month for month in observed_months if month < start_key]
    if early:
        raise ValueError(
            "The historical master contains months before the training calendar and was not exported."
        )
    latest = observed_months[-1]
    if latest < start_key:
        raise ValueError("The historical master ends before the training calendar and was not exported.")
    year, month = int(HISTORY_START.year), int(HISTORY_START.month)
    end_year, end_month = (int(part) for part in latest.split("-"))
    columns: list[str] = []
    while (year, month) <= (end_year, end_month):
        columns.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            month = 1
            year += 1
    return columns


def ensure_training_snapshot(path: Path) -> None:
    """Fail when the exported workbook is not readable by the training parser."""
    _import_engine()
    from preprocessing import _load_original_excel

    _load_original_excel(str(path))


def _import_engine() -> None:
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))
