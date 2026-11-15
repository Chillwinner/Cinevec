from __future__ import annotations

from typing import Any

from app.application.dashboard import dashboard_stage
from app.domain.models import Asset, Job, asset_source_url
from app.domain.search import vector_visibility


def ref_out(asset: Asset) -> dict[str, Any] | None:
    raw = getattr(asset, "external_ref", None)
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    if raw.get("ref_id"):
        out["ref_id"] = raw["ref_id"]
    if raw.get("locator"):
        out["locator"] = raw["locator"]
    if isinstance(raw.get("params"), dict) and raw["params"]:
        out["params"] = raw["params"]
    return out or None


def asset_is_searchable(asset: Asset | None) -> bool:
    if asset is None:
        return False
    return vector_visibility(asset) == "searchable"


def plugin_phase(job: Job | None, asset: Asset | None) -> str:
    if asset_is_searchable(asset):
        return "searchable"
    if job is None:
        if asset is not None and not asset.vectorize:
            return "accepted"
        return "queued"
    if job.status == "failed":
        return "failed"
    if job.status == "blocked":
        return "blocked"
    if job.job_type == "register_asset" and not (asset and asset.vectorize):
        return "accepted"
    stage = dashboard_stage(job) if job.job_type in {"index_asset", "index_subtitles", "reindex_profile"} else None
    if job.stage == "queued" or (job.status == "waiting" and (job.stage or "") in {"queued", ""}):
        return "queued"
    if stage == "encode":
        return "encode"
    if stage == "index_write":
        return "index_write"
    if stage == "preprocess":
        return "preprocess"
    if job.status == "completed":
        return "index_write"
    return "queued"


def plugin_asset_out(asset: Asset, tags: list[str] | None = None) -> dict[str, Any]:
    origin = asset.origin or "external"
    cover_key = getattr(asset, "cover_key", None)
    return {
        "id": asset.id,
        "kind": asset.kind,
        "origin": origin,
        "copy": origin == "hosted",
        "store_original": origin == "hosted",
        "display_name": asset.display_name or asset.object_key.rsplit("/", 1)[-1],
        "description": getattr(asset, "description", "") or "",
        "source_url": asset_source_url(asset),
        "cover_key": cover_key,
        "cover_url": f"/api/v1/assets/{asset.id}/cover",
        "excluded": bool(getattr(asset, "excluded", False)),
        "vectorize": asset.vectorize,
        "index_state": asset.index_state,
        "searchable": asset_is_searchable(asset),
        "ref": ref_out(asset),
        "tags": list(tags or []),
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


def plugin_job_out(job: Job, asset: Asset | None) -> dict[str, Any]:
    searchable = asset_is_searchable(asset)
    phase = plugin_phase(job, asset)
    payload = job.payload or {}
    error = None
    if job.error_code or job.error_message:
        error = {"code": job.error_code, "message": job.error_message}
    return {
        "id": job.id,
        "job_type": job.job_type,
        "asset_id": job.asset_id,
        "batch_id": payload.get("batch_id"),
        "phase": phase,
        "searchable": searchable,
        "progress": job.progress,
        "status": job.status,
        "step": job.step,
        "queue_position": job.queue_position,
        "error": error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def batch_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {
        "accepted": 0,
        "queued": 0,
        "preprocess": 0,
        "encode": 0,
        "index_write": 0,
        "searchable": 0,
        "blocked": 0,
        "failed": 0,
    }
    for item in items:
        phase = item.get("phase") or "queued"
        counts[phase] = counts.get(phase, 0) + 1
    counts["searchable_flag"] = sum(1 for item in items if item.get("searchable"))
    return counts
