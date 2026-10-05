"""Classify historical record inserts versus updates."""

from __future__ import annotations

from typing import Any


def flatten_parsed_rows(parsed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for row in parsed_rows:
        amounts = row.get("amounts") or {}
        for month, amount in amounts.items():
            if amount is None:
                continue
            records.append(
                {
                    "budget_code": str(row["budget_code"]),
                    "month": str(month),
                    "amount": float(amount),
                    "description": str(row.get("description") or ""),
                    "category": str(row.get("category") or ""),
                    "source_row": row.get("source_row"),
                }
            )
    return records


def _same_record(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    return (
        float(existing["amount"]) == float(incoming["amount"])
        and str(existing.get("description") or "") == str(incoming.get("description") or "")
        and str(existing.get("category") or "") == str(incoming.get("category") or "")
    )


def classify_historical_changes(
    incoming_rows: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    existing_map = {(str(item["budget_code"]), str(item["month"])): item for item in existing_rows}
    created: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for record in flatten_parsed_rows(incoming_rows):
        prior = existing_map.get((record["budget_code"], record["month"]))
        if prior is None:
            created.append(record)
            continue
        if _same_record(prior, record):
            unchanged.append({**record, "existing_amount": prior["amount"]})
        else:
            changed.append(
                {
                    **record,
                    "existing_amount": prior["amount"],
                    "existing_description": prior.get("description") or "",
                    "existing_category": prior.get("category") or "",
                }
            )
    return {
        "new": created,
        "changed": changed,
        "unchanged": unchanged,
        "new_count": len(created),
        "changed_count": len(changed),
        "unchanged_count": len(unchanged),
        "invalid_count": 0,
    }


def intended_edit_records(existing_rows: list[dict[str, Any]], edits: list[dict[str, Any]]) -> dict[str, Any]:
    existing_map = {(str(item["budget_code"]), str(item["month"])): item for item in existing_rows}
    created: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for edit in edits:
        key = (str(edit["budget_code"]), str(edit["month"]))
        prior = existing_map.get(key)
        expected = edit.get("expected_amount")
        incoming = {
            "budget_code": key[0],
            "month": key[1],
            "amount": float(edit["amount"]),
            "description": str(edit.get("description") or (prior or {}).get("description") or ""),
            "category": str(edit.get("category") or (prior or {}).get("category") or ""),
        }
        if prior is None:
            created.append(incoming)
            continue
        if expected is not None and float(prior["amount"]) != float(expected):
            conflicts.append(
                {
                    **incoming,
                    "existing_amount": prior["amount"],
                    "expected_amount": expected,
                    "message": (
                        f"{key[0]} {key[1]} was changed since this file was loaded. "
                        f"Current value is {prior['amount']}; refresh and confirm again."
                    ),
                }
            )
            continue
        if _same_record(prior, incoming):
            continue
        changed.append(
            {
                **incoming,
                "existing_amount": prior["amount"],
                "existing_description": prior.get("description") or "",
                "existing_category": prior.get("category") or "",
            }
        )
    return {"new": created, "changed": changed, "conflicts": conflicts}
