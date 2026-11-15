from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ListCompleteResult:
    entries: list
    complete: bool
    pages: int = 0
    truncated: bool = False
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class VectorLocator:
    """A remote vector entity is identified by collection + actual entity id, not the local association PK."""
    collection: str
    remote_id: str
    modality: str = ""
    asset_id: str = ""

    def key(self) -> str:
        return f"{self.collection}::{self.remote_id}"


def as_vector_locators(refs) -> list[VectorLocator]:
    """Require collection + remote id. Bare id strings are rejected so a shared short id cannot cross collections."""
    out: list[VectorLocator] = []
    for item in refs or []:
        if isinstance(item, VectorLocator):
            loc = item
        elif isinstance(item, dict):
            loc = VectorLocator(
                collection=str(item.get("collection") or ""),
                remote_id=str(item.get("remote_id") or item.get("id") or ""),
                modality=str(item.get("modality") or ""),
                asset_id=str(item.get("asset_id") or ""),
            )
        else:
            raise ValueError("向量删除必须同时提供 collection 与远端实体 id，避免短 id 跨集合误删。")
        if not (loc.collection or "").strip() or not (loc.remote_id or "").strip():
            raise ValueError("向量删除缺少 collection 或远端实体 id，拒绝猜测目标集合。")
        out.append(
            VectorLocator(
                collection=loc.collection.strip(),
                remote_id=loc.remote_id.strip(),
                modality=(loc.modality or "").strip(),
                asset_id=(loc.asset_id or "").strip(),
            )
        )
    return out


def serialize_vector_refs(refs) -> list[str]:
    out: list[str] = []
    for item in refs or []:
        if isinstance(item, VectorLocator):
            out.append(item.key())
        elif isinstance(item, dict) and (item.get("collection") and (item.get("remote_id") or item.get("id"))):
            out.append(f"{item.get('collection')}::{item.get('remote_id') or item.get('id')}")
        else:
            out.append(str(item))
    return out


@dataclass
class VectorDeleteResult:
    attempted: bool
    succeeded: bool
    pending: bool
    message: str
    refs: list[str] = field(default_factory=list)


@dataclass
class RefreshSettings:
    enabled: bool = False
    interval_minutes: int = 60
    last_enqueued_at: str | None = None
    last_completed_at: str | None = None
    last_result: str | None = None
    next_due_at: str | None = None


@dataclass
class LifecycleSettings:
    # 排除后：先删向量，确认成功再清预览、抽帧和平台托管原片。网盘原文件仍不删。
    preview_cleanup_on_exclude: bool = True


@dataclass
class VectorizePolicy:
    source_id: str
    prefix: str
    vectorize: bool
    updated_at: datetime | None = None


@dataclass
class Exclusion:
    source_id: str
    object_key: str
    reason: str
    created_at: datetime | None = None
    restored_at: datetime | None = None
    active: bool = True


@dataclass
class ScanRecord:
    id: str
    source_id: str
    prefix: str
    job_id: str | None
    status: str
    complete: bool
    pages: int
    listed_count: int
    error_code: str | None
    error_message: str | None
    started_at: str | None
    finished_at: str | None
    extra: dict[str, Any] = field(default_factory=dict)
