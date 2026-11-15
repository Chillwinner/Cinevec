from __future__ import annotations

from typing import Any


def plan_remote_id_backfill(
    local_units: list[dict[str, Any]],
    remote_entities: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Map existing SQLite associations to real remote entity ids.

    Match only on collection + asset_id + version + unit_index from provided records.
    Do not invent ids: skip incomplete, missing, or ambiguous matches. Do not
    overwrite a different already-stored remote_vector_id.
    """
    grouped: dict[tuple, list[str]] = {}
    for entity in remote_entities or []:
        collection = str(entity.get("collection") or "").strip()
        asset_id = str(entity.get("asset_id") or "").strip()
        remote_id = entity.get("id") if entity.get("id") not in (None, "") else entity.get("remote_id")
        if not collection or not asset_id or remote_id in (None, "") or entity.get("version") is None or entity.get("unit_index") is None:
            continue
        key = (collection, asset_id, int(entity["version"]), int(entity["unit_index"]))
        grouped.setdefault(key, []).append(str(remote_id))

    updates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for unit in local_units or []:
        collection = str(unit.get("collection") or "").strip()
        asset_id = str(unit.get("asset_id") or "").strip()
        vector_id = unit.get("vector_id")
        if not collection or not asset_id or not vector_id or unit.get("version") is None or unit.get("unit_index") is None:
            unmatched.append({"vector_id": vector_id, "reason": "incomplete_local"})
            continue
        key = (collection, asset_id, int(unit["version"]), int(unit["unit_index"]))
        ids = list(dict.fromkeys(grouped.get(key) or []))
        if len(ids) != 1:
            unmatched.append({"vector_id": vector_id, "reason": "missing" if not ids else "ambiguous"})
            continue
        remote = ids[0]
        current = str(unit.get("remote_vector_id") or "")
        if current == remote:
            unchanged.append({"vector_id": vector_id, "remote_vector_id": remote})
            continue
        if current and current != remote:
            conflicts.append({"vector_id": vector_id, "existing": current, "observed": remote})
            continue
        updates.append({"vector_id": vector_id, "remote_vector_id": remote})
    return {
        "updates": updates,
        "conflicts": conflicts,
        "unmatched": unmatched,
        "unchanged": unchanged,
    }
