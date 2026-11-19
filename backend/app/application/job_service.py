from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

from app.application.pipeline_service import PipelineService
from app.domain.errors import AppError, NotFoundError
from app.domain.models import Job, JobStatus
from app.domain.ports import ImageTextEncoder, TaskRepository, VectorIndex
from app.infra.clock import now
from app.infra.encode_local import cleanup_encode_work, safe_rmtree
from app.infra.sqlite_repos import SqliteTaskRepository


class JobService:
    def __init__(
        self,
        tasks: TaskRepository,
        encoder: ImageTextEncoder,
        index: VectorIndex,
        pipeline: PipelineService | None = None,
        lease_seconds: int = 30,
        poll_seconds: float = 0.4,
        subtitle_pipeline=None,
        refresh_service=None,
        lifecycle_service=None,
        subtitle_encoder=None,
        index_writer=None,
        rebuild_service=None,
    ) -> None:
        self._tasks = tasks
        self._encoder = encoder
        self._index = index
        self._pipeline = pipeline
        self._lease = lease_seconds
        self._poll = poll_seconds
        self._subtitles = subtitle_pipeline
        self._refresh = refresh_service
        self._lifecycle = lifecycle_service
        self._subtitle_encoder = subtitle_encoder
        self._writer = index_writer
        self._rebuild = rebuild_service

    def list_jobs(self, status: JobStatus | None = None) -> list[Job]:
        return self._tasks.list_jobs(status)

    def get(self, job_id: str) -> Job:
        job = self._tasks.get_job(job_id)
        if job is None:
            raise NotFoundError("任务不存在")
        return job

    def pause(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status not in {"waiting", "running"}:
            raise AppError("invalid_state", "只有等待中或运行中的任务可以暂停")
        return self._tasks.update_job(
            job_id, status="paused", step="已暂停", lease_until=None
        )

    def resume(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status != "paused":
            raise AppError("invalid_state", "只有已暂停的任务可以恢复")
        return self._tasks.update_job(job_id, status="waiting", step="等待调度", error_code=None)

    def retry(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status not in {"failed", "blocked"}:
            raise AppError("invalid_state", "只有失败或卡住等待向量化的任务可以重试。")
        if job.error_code == "cancelled":
            raise AppError("invalid_state", "已取消的任务不能重试。")
        ck = job.checkpoint or {}
        units = ck.get("encode_units") or []
        if (
            ck.get("media_done")
            and units
            and all(u.get("local_path") and Path(str(u["local_path"])).is_file() for u in units)
        ):
            return self._tasks.update_job(
                job_id,
                status="waiting",
                stage="embed_pending",
                step="等待向量编码重试",
                error_code=None,
                error_message=None,
                lease_until=None,
            )
        # 保留 payload（object_key 等），从流水线起点重新调度，不新建资产、不重复导入。
        return self._tasks.update_job(
            job_id,
            status="waiting",
            stage="queued",
            step="等待调度",
            progress=0,
            error_code=None,
            error_message=None,
            lease_until=None,
            checkpoint={"resume_from": "dependency_check"},
        )

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status in {"completed"}:
            raise AppError("invalid_state", "已完成的任务不能取消")
        if job.status == "failed" and job.error_code == "cancelled":
            raise AppError("invalid_state", "已结束的任务不能取消")
        if job.status == "failed":
            raise AppError("invalid_state", "已结束的任务不能取消")
        updated = self._tasks.update_job(
            job_id,
            status="failed",
            error_code="cancelled",
            error_message="任务已被取消",
            step="已取消",
            lease_until=None,
        )
        cleanup_encode_work(job.checkpoint)
        return updated

    def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        if job.status in {"running", "waiting"}:
            raise AppError("invalid_state", "请先取消或暂停后再删除未结束任务")
        self._tasks.delete_job(job_id)

    def claim_next(self, worker_id: str, *, lane: str | None = None) -> Job | None:
        if self.query_priority_active():
            return None
        if lane is None:
            return self._tasks.claim_next(worker_id, lease_seconds=self._lease)
        try:
            return self._tasks.claim_next(worker_id, lease_seconds=self._lease, lane=lane)
        except TypeError:
            return self._tasks.claim_next(worker_id, lease_seconds=self._lease)

    def tick_schedule(self) -> None:
        if self._refresh is None:
            return
        self._refresh.tick_schedule()

    def run_claimed(self, job: Job, worker_id: str, *, lane: str | None = None) -> Job:
        current = self.get(job.id)
        checkpoint = dict(current.checkpoint or {})
        checkpoint["worker_id"] = worker_id
        if current.checkpoint != checkpoint and current.status != "paused":
            self._tasks.update_job(job.id, checkpoint=checkpoint)
        if job.job_type == "register_asset":
            return self.get(job.id)
        if job.job_type == "refresh_source":
            if self._refresh is None:
                raise AppError("refresh_unavailable", "刷新服务尚未装配")
            outcome = self._refresh.process(self.get(job.id))
            return self.get(job.id) if outcome in {"completed", "failed", "blocked", "paused"} else self.get(job.id)
        if job.job_type == "vector_purge":
            if self._lifecycle is None:
                raise AppError("lifecycle_unavailable", "生命周期服务尚未装配")
            self._lifecycle.process_purge(self.get(job.id))
            return self.get(job.id)
        if job.job_type == "reindex_profile":
            if self._rebuild is None:
                raise AppError("rebuild_unavailable", "重建服务尚未装配")
            outcome = self._rebuild.process(self.get(job.id))
            if outcome == "completed":
                return self._mark_completed(job.id)
            return self.get(job.id)
        if lane == "embed":
            return self._run_embed_lane(job.id)
        pipeline = self._subtitles if job.job_type == "index_subtitles" else self._pipeline
        if pipeline is None:
            raise AppError("index_pipeline_pending", "对应流水线尚未装配")
        while True:
            self._drain_purge_jobs(worker_id)
            current = self.get(job.id)
            if current.status == "paused":
                return current
            outcome = pipeline.process_unit(self.get(job.id))
            if outcome == "paused":
                return self.get(job.id)
            if outcome == "blocked":
                if lane == "preprocess":
                    latest = self.get(job.id)
                    if latest.status == "paused":
                        return latest
                    return self._tasks.update_job(
                        job.id,
                        status="waiting",
                        stage="embed_pending",
                        step="媒体预处理已完成，原片已清理，等待向量编码",
                        lease_until=None,
                        progress=1,
                    )
                written = self._try_embed(self.get(job.id))
                if written == "completed":
                    return self._mark_completed(job.id)
                if written == "failed":
                    return self.get(job.id)
                return self._mark_blocked(job.id, job_type=current.job_type)
            if outcome == "completed":
                return self._mark_completed(job.id)
            if outcome == "failed":
                return self.get(job.id)
            if self.query_priority_active():
                if self._wait_priority(job.id) == "paused":
                    return self.get(job.id)

    def _run_embed_lane(self, job_id: str) -> Job:
        current = self.get(job_id)
        if current.status == "paused":
            return current
        if self.query_priority_active():
            if self._wait_priority(job_id) == "paused":
                return self.get(job_id)
        current = self.get(job_id)
        if current.status == "paused":
            return current
        self._tasks.update_job(
            job_id,
            lease_until=now() + timedelta(seconds=self._lease),
            step="向量编码中",
        )
        written = self._try_embed(self.get(job_id))
        if written == "completed":
            return self._mark_completed(job_id)
        if written == "failed":
            return self.get(job_id)
        return self._mark_blocked(job_id, job_type=current.job_type)

    def _drain_purge_jobs(self, worker_id: str) -> None:
        if self._lifecycle is None:
            return
        try:
            waiting = self._tasks.list_jobs("waiting")
        except Exception:
            return
        for item in waiting:
            if item.job_type != "vector_purge":
                continue
            lease = now() + timedelta(seconds=self._lease)
            ck = dict(item.checkpoint or {})
            ck["worker_id"] = worker_id
            running = self._tasks.update_job(item.id, status="running", lease_until=lease, checkpoint=ck)
            try:
                self._lifecycle.process_purge(running)
            except Exception as exc:
                self.fail(item.id, getattr(exc, "code", "purge_failed"), getattr(exc, "message", str(exc)))


    def _try_embed(self, job: Job) -> str | None:
        if self._writer is None:
            return None
        try:
            if job.job_type == "index_asset":
                return self._writer.process_frames(job)
            if job.job_type == "index_subtitles":
                units = []
                assoc_id = (job.checkpoint or {}).get("association_id") or (job.payload or {}).get("association_id")
                metadata = getattr(self._subtitles, "_metadata", None)
                if assoc_id and metadata is not None:
                    groups = metadata.list_groups(assoc_id)
                    units = [
                        {
                            "unit_index": g.get("group_index"),
                            "start_seconds": g.get("start_seconds"),
                            "text": g.get("text"),
                        }
                        for g in groups
                    ]
                    if not units:
                        units = [
                            {
                                "unit_index": c.get("cue_index"),
                                "start_seconds": c.get("start_seconds"),
                                "text": c.get("text"),
                            }
                            for c in metadata.list_cues(assoc_id)
                        ]
                return self._writer.process_subtitles(job, units)
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            code = getattr(exc, "code", "embedding_failed")
            self.fail(job.id, code, message)
            return "failed"
        return None

    def _wait_priority(self, job_id: str) -> str:
        while self.query_priority_active():
            current = self.get(job_id)
            if current.status == "paused":
                return "paused"
            if current.status == "running":
                self._tasks.update_job(
                    job_id,
                    lease_until=now() + timedelta(seconds=self._lease),
                    step="查询优先，当前安全单元已结束，暂缓后续处理",
                )
            time.sleep(max(self._poll, 0.05))
        return "continue"

    def _mark_blocked(self, job_id: str, job_type: str | None = None) -> Job:
        current = self.get(job_id)
        if current.status == "paused":
            return current
        if (job_type or current.job_type) == "index_subtitles" and self._subtitles is not None:
            code, message = self._subtitles.blocked_message()
            step = "字幕解析已完成，等待字幕向量接口（尚未向量化）"
        elif self._encoder.available() and self._index.available():
            code = "embedding_pipeline_pending"
            message = "媒体阶段已完成，但向量化写入尚未实现，不会生成或写入假向量。整任务尚未向量化完成。"
            step = "媒体处理已完成，等待模型与向量库（尚未向量化）"
        else:
            code = "embedding_pending"
            message = (
                "媒体阶段已完成，整任务向量化尚未完成。"
                f"{self._encoder.status_message()} {self._index.status_message()}"
            )
            step = "媒体处理已完成，等待模型与向量库（尚未向量化）"
        return self._tasks.update_job(
            job_id,
            status="blocked",
            stage="embed_pending",
            step=step,
            error_code=code,
            error_message=message,
            lease_until=None,
            progress=1,
        )

    def _mark_completed(self, job_id: str) -> Job:
        return self._tasks.update_job(
            job_id,
            status="completed",
            stage="completed",
            step="整任务向量化已完成",
            error_code=None,
            error_message=None,
            lease_until=None,
            progress=1,
        )

    def fail(self, job_id: str, code: str, message: str) -> Job:
        current = self._tasks.get_job(job_id)
        if current and current.status == "paused":
            return current
        updated = self._tasks.update_job(
            job_id,
            status="failed",
            error_code=code,
            error_message=message,
            step="失败",
            lease_until=None,
        )
        if not (current and (current.checkpoint or {}).get("media_done")):
            cleanup_encode_work(current.checkpoint if current else None)
        return updated

    def cleanup_orphan_work(self, work_root: Path) -> int:
        if not work_root.is_dir():
            return 0
        keep_ids: set[str] = set()
        for job in self._tasks.list_jobs():
            ck = job.checkpoint or {}
            if job.status in {"waiting", "running", "paused", "blocked"}:
                keep_ids.add(job.id)
            elif job.status == "failed" and job.error_code != "cancelled" and ck.get("media_done"):
                keep_ids.add(job.id)
        removed = 0
        for child in work_root.iterdir():
            if not child.is_dir():
                continue
            if child.name == "_incoming" or child.name.startswith("."):
                continue
            if child.name not in keep_ids:
                safe_rmtree(child)
                removed += 1
        return removed

    def query_priority_active(self) -> bool:
        if not isinstance(self._tasks, SqliteTaskRepository):
            getter = getattr(self._tasks, "get_flag", None)
            if not getter:
                return False
            return getter("query_priority") == "1"
        return self._tasks.get_flag("query_priority") == "1"

    def set_query_priority(self, active: bool) -> None:
        setter = getattr(self._tasks, "set_flag", None)
        if not setter:
            return
        setter("query_priority", "1" if active else "0")
