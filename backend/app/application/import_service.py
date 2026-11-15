from __future__ import annotations

import uuid

from app.domain.errors import AppError, NotFoundError
from app.domain.models import Asset, Job
from app.domain.ports import MetadataRepository, TaskRepository
from app.infra.clock import now
from app.infra.object_keys import display_name_from_key


def infer_kind(key: str) -> str:
    lower = key.lower()
    if lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff")):
        return "image"
    if lower.endswith((".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v")):
        return "video"
    if lower.endswith((".srt", ".vtt", ".ass")):
        return "subtitle"
    return "other"


class ImportService:
    def __init__(self, metadata: MetadataRepository, tasks: TaskRepository, subtitles=None) -> None:
        self._metadata = metadata
        self._tasks = tasks
        self._subtitles = subtitles

    def import_items(
        self, source_id: str, items: list[dict]
    ) -> tuple[list[Asset], list[Job]]:
        source = self._metadata.get_source(source_id)
        if source is None:
            raise NotFoundError("存储源不存在")
        if not items:
            raise AppError("invalid", "请至少选择一个文件")
        assets: list[Asset] = []
        jobs: list[Job] = []
        pending_assoc: list[tuple[str, str]] = []
        for item in items:
            key = (item.get("object_key") or item.get("key") or "").strip()
            if not key or key.endswith("/"):
                raise AppError("invalid", "只能登记文件，不能登记目录本身")
            if hasattr(self._metadata, "is_excluded") and self._metadata.is_excluded(source_id, key):
                raise AppError("excluded", f"「{key}」已被排除（墓碑仍在），不会自动重新导入。请先主动恢复。")
            vectorize = bool(item.get("vectorize"))
            origin = (item.get("origin") or "external").strip() or "external"
            if origin not in {"external", "hosted", "plugin"}:
                raise AppError("invalid", "来源类型只能是 external（外部引用）、hosted（上传托管）或 plugin。")
            display = (item.get("display_name") or display_name_from_key(key)).strip() or None
            kind_hint = (item.get("kind") or "").strip()
            kind = kind_hint if kind_hint in {"image", "video", "subtitle", "other"} else infer_kind(key)
            batch_id = (item.get("batch_id") or "").strip() or None
            external_ref = item.get("external_ref") if isinstance(item.get("external_ref"), dict) else None
            description = str(item.get("description") or "").strip()
            cover_key = str(item.get("cover_key") or "").strip() or None
            asset = self._metadata.upsert_asset(
                Asset(
                    id=str(uuid.uuid4()),
                    source_id=source_id,
                    object_key=key,
                    kind=kind,  # type: ignore[arg-type]
                    vectorize=vectorize,
                    status="registered",
                    created_at=now(),
                    updated_at=now(),
                    origin=origin,
                    display_name=display,
                    external_ref=external_ref,
                    description=description,
                    cover_key=cover_key,
                )
            )
            assets.append(asset)
            self._register_job(asset, vectorize=vectorize, batch_id=batch_id)
            subtitle_key = (item.get("subtitle_object_key") or "").strip()
            if subtitle_key:
                if asset.kind != "video":
                    raise AppError("invalid", "只有视频可以手动关联字幕，不会按同名自动绑定。")
                pending_assoc.append((asset.id, subtitle_key))
            if vectorize and asset.kind == "subtitle":
                continue
            if vectorize:
                jobs.append(self._index_job(asset, batch_id=batch_id))
        for video_id, subtitle_key in pending_assoc:
            sub = self._ensure_subtitle_asset(source_id, subtitle_key)
            if self._subtitles is None:
                raise AppError("invalid", "字幕服务尚未装配，无法记录手动关联。")
            _assoc, sub_job = self._subtitles.associate(video_id, sub.id, enqueue=True)
            if sub_job:
                jobs.append(sub_job)
        return assets, jobs

    def enqueue_index(self, asset_id: str) -> Job:
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise NotFoundError("资源不存在")
        if asset.kind == "subtitle":
            raise AppError("invalid", "字幕需要先手动关联到视频后再解析，不会按文件名自动绑定。")
        finder = getattr(self._tasks, "find_active_jobs", None)
        if callable(finder):
            active = finder("index_asset", asset_id=asset.id)
            if active:
                return active[0]
        self._metadata.update_asset(asset.id, vectorize=True)
        asset = self._metadata.get_asset(asset_id)
        assert asset is not None
        return self._index_job(asset)

    def _ensure_subtitle_asset(self, source_id: str, object_key: str) -> Asset:
        existing = self._metadata.get_asset_by_key(source_id, object_key)
        if existing:
            return existing
        if infer_kind(object_key) != "subtitle":
            raise AppError("invalid", "关联对象必须是字幕文件。不会按同名自动猜测。")
        return self._metadata.upsert_asset(
            Asset(
                id=str(uuid.uuid4()),
                source_id=source_id,
                object_key=object_key,
                kind="subtitle",
                vectorize=False,
                status="registered",
                created_at=now(),
                updated_at=now(),
            )
        )

    def _register_job(self, asset: Asset, *, vectorize: bool, batch_id: str | None = None) -> Job:
        payload = {
            "object_key": asset.object_key,
            "kind": asset.kind,
            "vectorize": vectorize,
            "origin": asset.origin,
        }
        if batch_id:
            payload["batch_id"] = batch_id
        return self._tasks.add_job(
            Job(
                id=str(uuid.uuid4()),
                job_type="register_asset",
                status="completed",
                source_id=asset.source_id,
                asset_id=asset.id,
                stage="register",
                step="仅登记已完成" if not vectorize else "登记已完成，向量化另有任务",
                progress=1,
                queue_position=None,
                error_code=None,
                error_message=None,
                checkpoint={"resume_from": "done"},
                payload=payload,
            )
        )

    def _index_job(self, asset: Asset, batch_id: str | None = None) -> Job:
        payload = {"object_key": asset.object_key, "kind": asset.kind, "origin": asset.origin}
        if batch_id:
            payload["batch_id"] = batch_id
        return self._tasks.add_job(
            Job(
                id=str(uuid.uuid4()),
                job_type="index_asset",
                status="waiting",
                source_id=asset.source_id,
                asset_id=asset.id,
                stage="queued",
                step="等待调度",
                progress=0,
                queue_position=None,
                error_code=None,
                error_message=None,
                checkpoint={"resume_from": "dependency_check"},
                payload=payload,
            )
        )

    def list_assets(self, source_id: str | None = None, **kwargs):
        return self._metadata.list_assets(source_id, **kwargs)

    def count_assets(self, source_id: str | None = None, **kwargs) -> int:
        counter = getattr(self._metadata, "count_assets", None)
        if callable(counter):
            return int(counter(source_id, **kwargs))
        return len(self.list_assets(source_id, **kwargs))

    def list_object_keys(self, source_id: str | None = None) -> list[str]:
        getter = getattr(self._metadata, "list_object_keys", None)
        if callable(getter):
            return getter(source_id)
        return [asset.object_key for asset in self.list_assets(source_id)]

    def list_frames(self, asset_id: str):
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise NotFoundError("资源不存在")
        return self._metadata.list_frames(asset_id)
