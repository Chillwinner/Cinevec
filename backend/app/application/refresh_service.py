from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from app.domain.errors import AppError, NotConfiguredError, NotFoundError, DependencyError
from app.domain.lifecycle import RefreshSettings
from app.domain.models import Asset, Job, ObjectEntry
from app.domain.ports import MetadataRepository, SourceReader, TaskRepository
from app.application.import_service import infer_kind
from app.infra.clock import iso, now


class RefreshService:
    def __init__(
        self,
        metadata: MetadataRepository,
        tasks: TaskRepository,
        reader: SourceReader,
        settings_getter,
        settings_setter=None,
    ) -> None:
        self._metadata = metadata
        self._tasks = tasks
        self._reader = reader
        self._settings_getter = settings_getter
        self._settings_setter = settings_setter

    def request(self, source_id: str, prefix: str = "", reason: str = "manual") -> Job:
        source = self._metadata.get_source(source_id)
        if source is None or source.deleted:
            raise NotFoundError("存储源不存在或已停用")
        finder = getattr(self._tasks, "find_active_jobs", None)
        if callable(finder):
            active = [
                job
                for job in finder("refresh_source", source_id=source_id)
                if (job.payload or {}).get("prefix", "") == (prefix or "")
            ]
            if active:
                return active[0]
        return self._tasks.add_job(
            Job(
                id=str(uuid.uuid4()),
                job_type="refresh_source",
                status="waiting",
                source_id=source_id,
                asset_id=None,
                stage="queued",
                step="等待刷新扫描",
                progress=0,
                queue_position=None,
                error_code=None,
                error_message=None,
                checkpoint={"prefix": prefix or "", "reason": reason},
                payload={"prefix": prefix or "", "reason": reason},
            )
        )

    def tick_schedule(self) -> list[Job]:
        settings = self._settings_getter()
        if not settings.enabled:
            return []
        due = True
        if settings.next_due_at:
            try:
                from datetime import datetime

                due = datetime.fromisoformat(settings.next_due_at) <= now()
            except ValueError:
                due = True
        if not due:
            return []
        jobs = []
        for source in self._metadata.list_sources():
            jobs.append(self.request(source.id, prefix="", reason="schedule"))
        self._stamp_enqueued(settings)
        return jobs

    def _stamp_enqueued(self, settings: RefreshSettings) -> None:
        current = now()
        settings.last_enqueued_at = iso(current)
        settings.next_due_at = iso(current + timedelta(minutes=max(int(settings.interval_minutes), 1)))
        if self._settings_setter:
            self._settings_setter(settings)

    def mark_result(self, message: str, ok: bool) -> None:
        settings = self._settings_getter()
        settings.last_completed_at = iso()
        settings.last_result = ("成功：" if ok else "失败：") + message
        if settings.enabled:
            settings.next_due_at = iso(now() + timedelta(minutes=max(int(settings.interval_minutes), 1)))
        if self._settings_setter:
            self._settings_setter(settings)

    def process(self, job: Job) -> str:
        source_id = job.source_id
        if not source_id:
            raise AppError("invalid_state", "刷新任务缺少存储源")
        source = self._metadata.get_source(source_id)
        if source is None or source.deleted:
            raise AppError("source_invalid", "存储源不存在或已停用")
        prefix = (job.payload or {}).get("prefix") or (job.checkpoint or {}).get("prefix") or ""
        scan_id = str(uuid.uuid4())
        started = iso()
        self._metadata.insert_scan(
            {
                "id": scan_id,
                "source_id": source_id,
                "prefix": prefix,
                "job_id": job.id,
                "status": "running",
                "complete": False,
                "pages": 0,
                "listed_count": 0,
                "error_code": None,
                "error_message": None,
                "started_at": started,
                "finished_at": None,
                "extra": {},
            }
        )
        self._tasks.update_job(
            job.id,
            stage="scan",
            step="正在完整列举来源（未完成前不会判定缺失）",
            progress=0.2,
            checkpoint={**(job.checkpoint or {}), "scan_id": scan_id},
        )
        access, secret = self._metadata.get_source_credentials(source_id)
        lister = getattr(self._reader, "list_complete", None)
        if not callable(lister):
            result = type("R", (), {"complete": False, "entries": [], "pages": 0, "error_code": "list_unsupported", "error_message": "当前来源适配器不支持完整列举，未判定缺失。"})()
        else:
            try:
                result = lister(source, access, secret, prefix)
            except NotConfiguredError as exc:
                result = type("R", (), {"complete": False, "entries": [], "pages": 0, "error_code": exc.code, "error_message": exc.message})()
            except DependencyError as exc:
                result = type("R", (), {"complete": False, "entries": [], "pages": 0, "error_code": exc.code, "error_message": exc.message})()
            except Exception as exc:
                result = type("R", (), {"complete": False, "entries": [], "pages": 0, "error_code": "list_failed", "error_message": f"列举失败：{exc}"})()

        listed = [e for e in (result.entries or []) if not getattr(e, "is_dir", False)]
        self._metadata.update_scan(
            scan_id,
            status="completed" if result.complete else "failed",
            complete=bool(result.complete),
            pages=int(getattr(result, "pages", 0) or 0),
            listed_count=len(listed),
            error_code=getattr(result, "error_code", None),
            error_message=getattr(result, "error_message", None),
            finished_at=iso(),
        )
        scan = self._metadata.get_scan(scan_id)
        if not scan["complete"] or scan["status"] != "completed":
            message = scan.get("error_message") or "完整扫描未成功，不会把未列出的对象当作已删除。"
            self._tasks.update_job(
                job.id,
                status="failed",
                stage="scan_incomplete",
                step="扫描未完成",
                error_code=scan.get("error_code") or "scan_incomplete",
                error_message=message,
                progress=1,
                lease_until=None,
                checkpoint={**(job.checkpoint or {}), "scan_id": scan_id, "complete": False},
            )
            self.mark_result(message, ok=False)
            return "failed"

        summary = self._apply_complete_scan(source_id, prefix, scan_id, listed)
        self._metadata.update_scan(scan_id, extra=summary)
        message = (
            f"扫描完成（scan_id 已校验）。新增 {summary['added']}，内容变化 {summary['updated']}，"
            f"确认缺失 {summary['missing']}，排除跳过 {summary['skipped_excluded']}。"
        )
        self._tasks.update_job(
            job.id,
            status="completed",
            stage="refresh_done",
            step=message,
            error_code=None,
            error_message=None,
            progress=1,
            lease_until=None,
            checkpoint={**(job.checkpoint or {}), "scan_id": scan_id, "complete": True, "summary": summary},
        )
        self.mark_result(message, ok=True)
        return "completed"

    def _apply_complete_scan(self, source_id: str, prefix: str, scan_id: str, listed: list[ObjectEntry]) -> dict[str, int]:
        # 再次校验 completion
        scan = self._metadata.get_scan(scan_id)
        if not scan["complete"] or scan["status"] != "completed":
            raise AppError("scan_incomplete", "扫描未通过完成校验，拒绝写入缺失状态")
        found = {item.key: item for item in listed}
        added = updated = missing = skipped = 0
        existing = [
            a
            for a in self._metadata.list_assets(source_id)
            if (not prefix or a.object_key.startswith(prefix))
        ]
        existing_map = {a.object_key: a for a in existing}
        for key, entry in found.items():
            if self._metadata.is_excluded(source_id, key):
                skipped += 1
                continue
            asset = existing_map.get(key)
            if asset is None:
                policy = self._metadata.resolve_policy(source_id, key)
                vectorize = bool(policy) if policy is not None else False
                asset = self._metadata.upsert_asset(
                    Asset(
                        id=str(uuid.uuid4()),
                        source_id=source_id,
                        object_key=key,
                        kind=infer_kind(key),  # type: ignore[arg-type]
                        vectorize=vectorize,
                        status="registered",
                        created_at=now(),
                        updated_at=now(),
                        lifecycle="active",
                        size=entry.size,
                        etag=entry.etag,
                        last_modified=entry.last_modified,
                    )
                )
                added += 1
                if vectorize and asset.kind in {"image", "video"}:
                    self._enqueue_index(asset)
                continue
            if asset.excluded:
                skipped += 1
                continue
            changed = _changed(asset, entry)
            fields = {
                "size": entry.size,
                "etag": entry.etag,
                "last_modified": entry.last_modified,
                "source_deleted": False,
            }
            if asset.lifecycle == "missing":
                fields["lifecycle"] = "pending_update" if changed or asset.ready_version else "active"
            if changed:
                fields["version"] = asset.version + 1
                fields["lifecycle"] = "pending_update"
                fields["index_state"] = "inactive"
                fields["status"] = "pending_update"
                self._metadata.update_asset(asset.id, **fields)
                updated += 1
                if asset.vectorize and asset.kind in {"image", "video"}:
                    refreshed = self._metadata.get_asset(asset.id)
                    if refreshed:
                        self._enqueue_index(refreshed)
            else:
                if asset.lifecycle == "missing":
                    fields["lifecycle"] = "active"
                self._metadata.update_asset(asset.id, **fields)
        for asset in existing:
            if asset.object_key in found:
                continue
            if asset.excluded:
                continue
            if asset.lifecycle != "missing":
                self._metadata.update_asset(asset.id, lifecycle="missing", status="missing")  # 保留 ready_version/index_state：旧有效向量仍可查并标失效
                missing += 1
        return {"added": added, "updated": updated, "missing": missing, "skipped_excluded": skipped, "listed": len(found)}

    def _enqueue_index(self, asset: Asset) -> None:
        finder = getattr(self._tasks, "find_active_jobs", None)
        if callable(finder) and finder("index_asset", asset_id=asset.id):
            return
        self._tasks.add_job(
            Job(
                id=str(uuid.uuid4()),
                job_type="index_asset",
                status="waiting",
                source_id=asset.source_id,
                asset_id=asset.id,
                stage="queued",
                step="内容变更后等待重新处理（旧索引保持不可用直到新版本就绪）",
                progress=0,
                queue_position=None,
                error_code=None,
                error_message=None,
                checkpoint={"resume_from": "download", "version": asset.version},
                payload={"object_key": asset.object_key, "kind": asset.kind, "version": asset.version},
            )
        )


def _changed(asset: Asset, entry: ObjectEntry) -> bool:
    if entry.etag and asset.etag and entry.etag != asset.etag:
        return True
    if entry.size is not None and asset.size is not None and int(entry.size) != int(asset.size):
        return True
    if entry.last_modified and asset.last_modified and entry.last_modified != asset.last_modified:
        return True
    # 仅当两侧都有可比较指纹时才判断变化；都缺失则不视为修改
    return False
