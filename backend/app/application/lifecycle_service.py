from __future__ import annotations

import uuid
from typing import Any

from app.domain.errors import AppError, NotFoundError
from app.domain.lifecycle import VectorDeleteResult, VectorLocator
from app.domain.models import Job
from app.domain.ports import MetadataRepository, TaskRepository, VectorIndex


def lifecycle_flags(asset) -> dict[str, Any]:
    origin = getattr(asset, "origin", "external") or "external"
    vector = getattr(asset, "vector_purge_status", "not_required") or "not_required"
    artifact = getattr(asset, "artifact_purge_status", "not_required") or "not_required"
    excluded = bool(getattr(asset, "excluded", False))
    hosted_original_gone = origin == "hosted" and artifact == "done"
    fully = excluded and vector in {"done", "not_required"} and artifact == "done"
    return {
        "excluded": excluded,
        "lifecycle": getattr(asset, "lifecycle", "active"),
        "vector_purge_status": vector,
        "vector_purge_message": getattr(asset, "vector_purge_message", None),
        "artifact_purge_status": artifact,
        "artifact_purge_message": getattr(asset, "artifact_purge_message", None),
        "fully_deleted": fully,
        "s3_original_untouched": origin != "hosted" or artifact != "done",
        "preview_retained": artifact != "done",
        "ui_state": "已排除" if excluded else getattr(asset, "lifecycle", "active"),
        "can_restore": excluded and not hosted_original_gone,
        "origin": origin,
    }


class LifecycleService:
    def __init__(
        self,
        metadata: MetadataRepository,
        tasks: TaskRepository,
        index: VectorIndex,
        settings_getter=None,
        file_store=None,
        face_service=None,
    ) -> None:
        self._metadata = metadata
        self._tasks = tasks
        self._index = index
        self._settings_getter = settings_getter
        self._store = file_store
        self._face_service = face_service

    def exclude_asset(self, asset_id: str, reason: str = "用户手动排除") -> tuple[Any, Job]:
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise NotFoundError("资源不存在")
        self._metadata.add_exclusion(asset.source_id, asset.object_key, reason)
        self._metadata.update_asset(
            asset_id,
            excluded=True,
            lifecycle="excluded",
            vector_purge_status="pending",
            vector_purge_message="元数据已排除。向量删除尚未确认成功。",
            artifact_purge_status="pending",
            artifact_purge_message="须先确认向量已删除，再清理预览、抽帧和平台托管原片。",
        )
        job = self._enqueue_purge(asset, reason)
        return self._metadata.get_asset(asset_id), job

    def restore_asset(self, asset_id: str, enqueue_index: bool = True) -> Any:
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise NotFoundError("资源不存在")
        flags = lifecycle_flags(asset)
        if not flags["can_restore"]:
            raise AppError(
                "gone",
                "平台托管的原片已经删掉，无法从资料库恢复。请重新上传。",
            )
        self._cancel_purge_jobs(asset.id)
        self._metadata.restore_exclusion(asset.source_id, asset.object_key)
        lifecycle = "source_invalid" if asset.source_deleted else "active"
        self._metadata.update_asset(
            asset_id,
            excluded=False,
            lifecycle=lifecycle,
            vector_purge_status="not_required",
            vector_purge_message=None,
            artifact_purge_status="not_required",
            artifact_purge_message=None,
        )
        asset = self._metadata.get_asset(asset_id)
        if enqueue_index and asset and asset.vectorize and not asset.source_deleted:
            self._enqueue_index(asset)
        return asset

    def exclude_status(self, asset) -> dict[str, Any]:
        return lifecycle_flags(asset)

    def process_purge(self, job: Job) -> str:
        asset_id = job.asset_id
        asset = self._metadata.get_asset(asset_id or "")
        if asset is None:
            raise NotFoundError("资源不存在")
        if not asset.excluded:
            self._tasks.update_job(
                job.id,
                status="completed",
                stage="purge_skipped",
                step="资源已恢复，跳过清理。",
                error_code=None,
                error_message=None,
                progress=1,
                lease_until=None,
                checkpoint={**(job.checkpoint or {}), "skipped": True, "fully_deleted": False},
            )
            return "completed"
        ck = dict(job.checkpoint or {})
        if not ck.get("vector_delete_succeeded"):
            if self._face_service is not None:
                try:
                    self._face_service.purge_asset_faces(asset.id)
                except Exception:
                    pass
            result = self._purge_vectors(asset)
            if not result.succeeded:
                status = "blocked" if result.pending else "failed"
                self._metadata.update_asset(
                    asset.id,
                    vector_purge_status="pending" if result.pending else "failed",
                    vector_purge_message=result.message,
                )
                self._tasks.update_job(
                    job.id,
                    status=status,
                    stage="purge_pending",
                    step="元数据已排除，向量删除未成功，尚未清理预览和原片。",
                    error_code="vector_delete_pending" if result.pending else "vector_delete_failed",
                    error_message=result.message,
                    progress=1,
                    lease_until=None,
                    checkpoint={
                        **ck,
                        "vector_delete_succeeded": False,
                        "fully_deleted": False,
                        "pending": result.pending,
                    },
                )
                return status
            ck["vector_delete_succeeded"] = True
            ck["vector_delete_message"] = result.message
            self._metadata.update_asset(
                asset.id,
                vector_purge_status="done",
                vector_purge_message=result.message,
            )

        cleanup = True
        if self._settings_getter is not None:
            try:
                cleanup = bool(self._settings_getter().preview_cleanup_on_exclude)
            except Exception:
                cleanup = True
        if not cleanup:
            self._metadata.update_asset(
                asset.id,
                artifact_purge_status="not_required",
                artifact_purge_message="当前设置为排除后保留预览和原片。",
            )
            self._finish_job(
                job,
                ck,
                fully=False,
                step="向量已删除。按设置保留预览和原片。",
            )
            return "completed"

        artifact = self._purge_artifacts(asset)
        if not artifact["ok"]:
            self._metadata.update_asset(
                asset.id,
                vector_purge_status="done",
                artifact_purge_status="pending" if artifact["pending"] else "failed",
                artifact_purge_message=artifact["message"],
            )
            self._tasks.update_job(
                job.id,
                status="blocked" if artifact["pending"] else "failed",
                stage="artifact_pending",
                step="向量已删除，预览或平台原片尚未清理完。",
                error_code="artifact_delete_pending" if artifact["pending"] else "artifact_delete_failed",
                error_message=artifact["message"],
                progress=1,
                lease_until=None,
                checkpoint={
                    **ck,
                    "vector_delete_succeeded": True,
                    "artifact_delete_succeeded": False,
                    "fully_deleted": False,
                    "deleted_keys": artifact.get("deleted") or [],
                },
            )
            return "blocked" if artifact["pending"] else "failed"

        clearer = getattr(self._metadata, "delete_frames", None)
        if callable(clearer):
            clearer(asset.id)
        units = getattr(self._metadata, "delete_vector_units", None)
        if callable(units):
            units(asset.id)
        origin = getattr(asset, "origin", "external") or "external"
        original_note = (
            "平台托管原片已删。"
            if origin == "hosted"
            else "网盘/来源上的原文件未改动。"
        )
        self._metadata.update_asset(
            asset.id,
            vector_purge_status="done",
            artifact_purge_status="done",
            artifact_purge_message=f"{artifact['message']} {original_note}".strip(),
            index_state="none",
        )
        self._finish_job(
            job,
            {
                **ck,
                "vector_delete_succeeded": True,
                "artifact_delete_succeeded": True,
                "fully_deleted": True,
                "deleted_keys": artifact.get("deleted") or [],
                "s3_original_untouched": origin != "hosted",
            },
            fully=True,
            step=f"向量已删除。预览和抽帧已清理。{original_note}",
        )
        return "completed"

    def _purge_vectors(self, asset) -> VectorDeleteResult:
        lister = getattr(self._metadata, "list_vector_units", None)
        units = list(lister(asset.id)) if callable(lister) else []
        locators = []
        incomplete = 0
        for unit in units:
            collection = (unit.get("collection") or "").strip()
            remote = (unit.get("remote_vector_id") or "").strip()
            if collection and remote:
                locators.append(
                    VectorLocator(
                        collection=collection,
                        remote_id=remote,
                        modality=unit.get("modality") or "",
                        asset_id=asset.id,
                    )
                )
            else:
                incomplete += 1
        if units and not locators:
            return VectorDeleteResult(
                attempted=False,
                succeeded=False,
                pending=True,
                message="存在向量关联但缺少 collection 或远端实体 id，未执行删除，不会假装成功。",
                refs=[],
            )
        if incomplete and locators:
            return VectorDeleteResult(
                attempted=False,
                succeeded=False,
                pending=True,
                message="部分向量关联缺少 collection 或远端实体 id，拒绝部分删除。",
                refs=[loc.key() for loc in locators],
            )
        if not locators:
            return VectorDeleteResult(True, True, False, "没有需要删除的向量引用。", [])
        return self._index.delete_vectors(locators)

    def _artifact_keys(self, asset) -> list[str]:
        keys: list[str] = []
        seen: set[str] = set()

        def add(value: Any) -> None:
            key = str(value or "").strip()
            if not key or key in seen:
                return
            seen.add(key)
            keys.append(key)

        frames = getattr(self._metadata, "list_frames", None)
        if callable(frames):
            for frame in frames(asset.id) or []:
                add(frame.get("preview_key"))
                add(frame.get("model_input_key"))
                extra = frame.get("extra") or {}
                mapped = extra.get("model_inputs_by_preprocess") or {}
                if isinstance(mapped, dict):
                    for item in mapped.values():
                        add(item)
        units = getattr(self._metadata, "list_vector_units", None)
        if callable(units):
            for unit in units(asset.id) or []:
                add(unit.get("preview_key"))
        if (getattr(asset, "origin", "external") or "external") == "hosted":
            add(asset.object_key)
        add(getattr(asset, "cover_key", None))
        return keys

    def _purge_artifacts(self, asset) -> dict[str, Any]:
        keys = self._artifact_keys(asset)
        if not keys:
            return {"ok": True, "pending": False, "message": "没有需要清理的预览或平台原片。", "deleted": []}
        if self._store is None:
            return {
                "ok": False,
                "pending": True,
                "message": "对象存储未装配，未删除预览或平台原片，不会假装成功。",
                "deleted": [],
            }
        batch_deleter = getattr(self._store, "delete_objects", None)
        deleter = getattr(self._store, "delete_object", None)
        if not callable(batch_deleter) and not callable(deleter):
            return {
                "ok": False,
                "pending": True,
                "message": "当前存储适配器不能删除对象，未清理预览或平台原片。",
                "deleted": [],
            }
        deleted: list[str] = []
        try:
            if callable(batch_deleter):
                deleted = list(batch_deleter(keys))
            else:
                for key in keys:
                    deleter(key)
                    deleted.append(key)
        except Exception as exc:
            return {
                "ok": False,
                "pending": True,
                "message": f"对象删除未成功：{getattr(exc, 'message', exc)}。已删 {len(deleted)} 个键，将保留记录以便重试。",
                "deleted": deleted,
            }
        return {
            "ok": True,
            "pending": False,
            "message": f"已删除 {len(deleted)} 个平台对象（预览/抽帧/托管原片）。",
            "deleted": deleted,
        }

    def _finish_job(self, job: Job, checkpoint: dict[str, Any], *, fully: bool, step: str) -> None:
        self._tasks.update_job(
            job.id,
            status="completed",
            stage="purge_done",
            step=step,
            error_code=None,
            error_message=None,
            progress=1,
            lease_until=None,
            checkpoint={**checkpoint, "fully_deleted": fully},
        )

    def _enqueue_purge(self, asset, reason: str) -> Job:
        finder = getattr(self._tasks, "find_active_jobs", None)
        if callable(finder):
            active = finder("vector_purge", asset_id=asset.id)
            if active:
                return active[0]
        return self._tasks.add_job(
            Job(
                id=str(uuid.uuid4()),
                job_type="vector_purge",
                status="waiting",
                source_id=asset.source_id,
                asset_id=asset.id,
                stage="queued",
                step="等待向量清理（元数据已排除）",
                progress=0,
                queue_position=None,
                error_code=None,
                error_message=None,
                checkpoint={"reason": reason, "fully_deleted": False},
                payload={"object_key": asset.object_key, "kind": asset.kind},
            )
        )

    def _cancel_purge_jobs(self, asset_id: str) -> None:
        jobs = []
        lister = getattr(self._tasks, "list_jobs", None)
        if callable(lister):
            jobs = lister()
        else:
            finder = getattr(self._tasks, "find_active_jobs", None)
            if callable(finder):
                jobs = finder("vector_purge", asset_id=asset_id)
        for job in jobs:
            if getattr(job, "job_type", "") != "vector_purge":
                continue
            if getattr(job, "asset_id", None) != asset_id:
                continue
            if job.status not in {"waiting", "running", "paused", "blocked"}:
                continue
            self._tasks.update_job(
                job.id,
                status="failed",
                error_code="cancelled",
                error_message="已恢复，取消清理。",
                step="已取消",
                lease_until=None,
            )

    def _enqueue_index(self, asset) -> Job | None:
        finder = getattr(self._tasks, "find_active_jobs", None)
        job_type = "index_subtitles" if asset.kind == "subtitle" else "index_asset"
        if callable(finder):
            active = finder(job_type, asset_id=asset.id)
            if active:
                return active[0]
        if asset.kind == "subtitle":
            return None
        return self._tasks.add_job(
            Job(
                id=str(uuid.uuid4()),
                job_type="index_asset",
                status="waiting",
                source_id=asset.source_id,
                asset_id=asset.id,
                stage="queued",
                step="恢复后等待重新索引",
                progress=0,
                queue_position=None,
                error_code=None,
                error_message=None,
                checkpoint={"resume_from": "download"},
                payload={"object_key": asset.object_key, "kind": asset.kind},
            )
        )

    def catalog(self, query: str, source_id: str | None = None) -> list[dict[str, Any]]:
        if not (query or "").strip():
            raise AppError("invalid", "请输入要在登记目录中查找的关键字。这不是向量检索。")
        assets = self._metadata.search_catalog(query.strip(), source_id)
        return [self.asset_view(a) for a in assets]

    def asset_view(self, asset) -> dict[str, Any]:
        label = {
            "active": "有效",
            "pending_update": "待更新",
            "stale": "过期",
            "missing": "来源中未再发现",
            "source_invalid": "来源已失效",
            "excluded": "已排除",
        }.get(asset.lifecycle, asset.lifecycle)
        if asset.version > asset.ready_version and asset.lifecycle in {"pending_update", "stale", "active"}:
            if asset.lifecycle == "active":
                label = "待更新"
        flags = lifecycle_flags(asset)
        return {
            "id": asset.id,
            "source_id": asset.source_id,
            "object_key": asset.object_key,
            "kind": asset.kind,
            "vectorize": asset.vectorize,
            "status": asset.status,
            "lifecycle": asset.lifecycle,
            "lifecycle_label": label,
            "index_state": asset.index_state,
            "version": asset.version,
            "ready_version": asset.ready_version,
            "excluded": asset.excluded,
            "source_deleted": asset.source_deleted,
            "vector_purge_status": flags["vector_purge_status"],
            "vector_purge_message": flags["vector_purge_message"],
            "artifact_purge_status": flags["artifact_purge_status"],
            "artifact_purge_message": flags["artifact_purge_message"],
            "fully_deleted": flags["fully_deleted"],
            "s3_original_untouched": flags["s3_original_untouched"],
            "can_restore": flags["can_restore"],
            "size": asset.size,
            "etag": asset.etag,
            "last_modified": asset.last_modified,
            "origin": getattr(asset, "origin", "external"),
            "store_original": getattr(asset, "origin", "external") == "hosted",
            "display_name": getattr(asset, "display_name", None) or asset.object_key,
            "description": getattr(asset, "description", "") or "",
            "cover_key": getattr(asset, "cover_key", None),
            "external_ref": getattr(asset, "external_ref", None),
            "source_url": (
                (getattr(asset, "external_ref", None) or {}).get("source_url")
                or (getattr(asset, "external_ref", None) or {}).get("locator")
                if isinstance(getattr(asset, "external_ref", None), dict)
                else None
            ),
            "created_at": asset.created_at.isoformat() if asset.created_at else None,
            "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
        }
