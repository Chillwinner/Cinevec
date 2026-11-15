from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

SourceRole = Literal["input", "storage", "both"]
JobStatus = Literal["waiting", "running", "paused", "blocked", "completed", "failed"]
AssetKind = Literal["image", "video", "subtitle", "other"]


@dataclass
class Source:
    id: str
    name: str
    endpoint: str
    bucket: str
    region: str
    role: SourceRole
    is_platform_storage: bool
    access_key_configured: bool
    secret_configured: bool
    last_probe_status: str
    last_probe_message: str
    created_at: datetime
    updated_at: datetime
    deleted: bool = False


@dataclass
class Asset:
    id: str
    source_id: str
    object_key: str
    kind: AssetKind
    vectorize: bool
    status: str
    created_at: datetime
    updated_at: datetime
    lifecycle: str = "active"
    index_state: str = "none"
    version: int = 1
    ready_version: int = 0
    size: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    excluded: bool = False
    source_deleted: bool = False
    vector_purge_status: str = "not_required"
    vector_purge_message: str | None = None
    artifact_purge_status: str = "not_required"
    artifact_purge_message: str | None = None
    origin: str = "external"
    display_name: str | None = None
    external_ref: dict[str, Any] | None = None
    description: str = ""
    cover_key: str | None = None


def asset_source_url(asset: Any) -> str | None:
    ref = getattr(asset, "external_ref", None)
    if not isinstance(ref, dict):
        return None
    url = str(ref.get("source_url") or ref.get("locator") or "").strip()
    return url or None


@dataclass
class Job:
    id: str
    job_type: str
    status: JobStatus
    source_id: str | None
    asset_id: str | None
    stage: str
    step: str
    progress: float
    queue_position: int | None
    error_code: str | None
    error_message: str | None
    checkpoint: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    lease_until: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class ObjectEntry:
    key: str
    is_dir: bool
    size: int | None = None
    last_modified: str | None = None
    etag: str | None = None
