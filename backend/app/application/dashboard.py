from __future__ import annotations

from typing import Any

from app.domain.models import Job
from app.domain.ports import TaskRepository
from app.infra.resources import snapshot as resource_snapshot

STAGE_DEFS = [
    {"id": "scan", "label": "扫描", "job_types": ("refresh_source",)},
    {"id": "register", "label": "登记", "job_types": ("register_asset",)},
    {"id": "preprocess", "label": "预处理", "job_types": ("index_asset", "index_subtitles")},
    {"id": "encode", "label": "向量编码", "job_types": ("index_asset", "index_subtitles", "reindex_profile")},
    {"id": "index_write", "label": "写入索引", "job_types": ("index_asset", "index_subtitles", "reindex_profile")},
    {"id": "cleanup", "label": "清理", "job_types": ("vector_purge",)},
]

PREPROCESS_STAGES = {
    "queued",
    "download",
    "probe",
    "plan",
    "sample",
    "clean",
    "preview",
    "persist",
    "media_done",
    "parse",
    "merge",
}
ENCODE_STAGES = {"embedding", "encoding", "embed", "embed_pending"}
WRITE_STAGES = {"writing", "associating", "associated", "activated"}
COUNT_STATUSES = ("waiting", "running", "paused", "blocked", "completed", "failed")


def _embed_substage(job: Job) -> str:
    ck = job.checkpoint or {}
    embed = ck.get("embed") if isinstance(ck.get("embed"), dict) else ck
    return str(embed.get("stage") or job.stage or "")


def dashboard_stage(job: Job) -> str | None:
    jtype = job.job_type
    if jtype == "refresh_source":
        return "scan"
    if jtype == "register_asset":
        return "register"
    if jtype == "vector_purge":
        return "cleanup"
    if jtype in {"index_asset", "index_subtitles", "reindex_profile"}:
        sub = _embed_substage(job)
        stage = job.stage or ""
        if sub in WRITE_STAGES or stage in WRITE_STAGES:
            return "index_write"
        if sub in ENCODE_STAGES or stage in ENCODE_STAGES:
            return "encode"
        if stage in PREPROCESS_STAGES or sub in PREPROCESS_STAGES:
            return "preprocess"
        if job.status == "blocked":
            return "encode"
        if job.status == "completed":
            return "index_write"
        return "preprocess"
    return None


def elapsed_seconds(job: Job) -> float | None:
    if not job.created_at or not job.updated_at:
        return None
    return max((job.updated_at - job.created_at).total_seconds(), 0.0)


class DashboardService:
    def __init__(self, tasks: TaskRepository, encoder=None) -> None:
        self._tasks = tasks
        self._encoder = encoder

    def snapshot(self, *, stage: str | None = None) -> dict[str, Any]:
        jobs = self._tasks.list_jobs()
        grouped: dict[str, list[Job]] = {item["id"]: [] for item in STAGE_DEFS}
        for job in jobs:
            sid = dashboard_stage(job)
            if sid in grouped:
                grouped[sid].append(job)
        stages = []
        for spec in STAGE_DEFS:
            rows = grouped[spec["id"]]
            counts = {status: 0 for status in COUNT_STATUSES}
            for job in rows:
                if job.status in counts:
                    counts[job.status] += 1
            ordered = sorted(
                rows,
                key=lambda job: (
                    job.created_at.isoformat() if job.created_at else "",
                    job.queue_position or 0,
                ),
            )
            stages.append(
                {
                    "id": spec["id"],
                    "label": spec["label"],
                    "counts": counts,
                    "total": len(rows),
                    "jobs": [self._job_card(job) for job in ordered],
                }
            )
        selected = None
        if stage:
            if stage not in grouped:
                selected = {"id": stage, "jobs": [], "note": "未知阶段"}
            else:
                selected = {
                    "id": stage,
                    "label": next(s["label"] for s in STAGE_DEFS if s["id"] == stage),
                    "jobs": [self._job_card(j) for j in grouped[stage]],
                }
        return {
            "stages": stages,
            "selected": selected,
            "resources": resource_snapshot(encoder=self._encoder),
            "notes": [
                "进度来自 SQLite 持久任务，关闭网页不会丢失。",
                "仅登记任务在登记完成后即为 completed，不会显示为等待向量化。",
                "查询优先时，后台任务在当前安全单元结束后暂停。",
            ],
        }

    def _job_card(self, job: Job) -> dict[str, Any]:
        payload = job.payload or {}
        ck = job.checkpoint or {}
        return {
            "id": job.id,
            "job_type": job.job_type,
            "status": job.status,
            "stage": job.stage,
            "step": job.step,
            "progress": job.progress,
            "queue_position": job.queue_position,
            "asset_id": job.asset_id,
            "source_id": job.source_id,
            "object_key": payload.get("object_key") or ck.get("object_key"),
            "error_code": job.error_code,
            "error_message": job.error_message,
            "elapsed_seconds": elapsed_seconds(job),
            "timings": ck.get("timings") or {},
            "checkpoint_stage": (ck.get("embed") or {}).get("stage") if isinstance(ck.get("embed"), dict) else ck.get("stage"),
            "can_cancel": job.status in {"waiting", "running", "paused", "blocked"},
            "can_retry": (job.status == "failed" and job.error_code != "cancelled") or job.status == "blocked",
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
