from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.domain.errors import AppError
from app.domain.media import SamplePoint, SampleSettings
from app.domain.model_profile import PREPROCESS_FULL_FRAME, ModelSpace
from app.domain.models import Job
from app.infra.clock import now
from app.infra.encode_local import remember_encode_unit
from app.infra.model_space import collection_for
from app.infra.previews import render_model_input


class RebuildService:
    """Plan and optionally enqueue isolated re-embeds. Never deletes old collections or business rows."""

    def __init__(
        self,
        metadata,
        tasks=None,
        writer=None,
        file_store=None,
        source_reader=None,
        sampler=None,
        work_root: Path | None = None,
        current_space: ModelSpace | None = None,
        encoder=None,
        index=None,
    ) -> None:
        self._metadata = metadata
        self._tasks = tasks
        self._writer = writer
        self._store = file_store
        self._reader = source_reader
        self._sampler = sampler
        self._work_root = Path(work_root) if work_root is not None else None
        self._current_space = current_space
        self._encoder = encoder
        self._index = index

    def plan(self, space: ModelSpace) -> dict[str, Any]:
        assets = list(self._metadata.list_assets())
        rebuildable = []
        already = []
        skipped = []
        for asset in assets:
            state = None
            getter = getattr(self._metadata, "get_asset_index_profile", None)
            if callable(getter):
                state = getter(
                    asset.id,
                    profile=space.profile,
                    model_id=space.model_id,
                    model_version=space.model_version,
                    dim=int(space.dim),
                    preprocess_version=space.preprocess_version,
                )
            row = {
                "asset_id": asset.id,
                "source_id": asset.source_id,
                "object_key": asset.object_key,
                "kind": asset.kind,
                "excluded": bool(getattr(asset, "excluded", False)),
                "content_version": int(getattr(asset, "version", 1) or 1),
                "asset_ready_version": int(getattr(asset, "ready_version", 0) or 0),
                "asset_index_state": getattr(asset, "index_state", "none"),
                "target_profile_state": state,
                "needs_reregistration": False,
            }
            if getattr(asset, "excluded", False) or getattr(asset, "kind", "") not in {"image", "video", "subtitle"}:
                row["needs_rebuild"] = False
                row["reason"] = "excluded_or_unsupported_kind"
                skipped.append(row)
                continue
            ready_here = (
                bool(state)
                and state.get("index_state") == "ready"
                and int(state.get("indexed_content_version") or 0) == int(asset.version)
            )
            if ready_here:
                row["needs_rebuild"] = False
                row["reason"] = "already_ready_for_target_space"
                already.append(row)
                continue
            row["needs_rebuild"] = True
            if int(getattr(asset, "ready_version", 0) or 0) > 0:
                row["reason"] = "asset_ready_version_is_content_only_not_this_model_space"
            else:
                row["reason"] = "registered_not_indexed_for_target_space"
            rebuildable.append(row)
        return {
            "target_space": asdict(space),
            "frame_collection": collection_for(space, "frame"),
            "subtitle_collection": collection_for(space, "subtitle"),
            "execute": False,
            "re_embeds": False,
            "writes_milvus": False,
            "writes_s3": False,
            "creates_remote_collection": False,
            "keeps_old_collections": True,
            "keeps_sqlite_business_records": True,
            "asset_count": len(assets),
            "rebuildable_count": len(rebuildable),
            "model_mismatch_count": len(
                [
                    row
                    for row in rebuildable
                    if row.get("reason") == "asset_ready_version_is_content_only_not_this_model_space"
                ]
            ),
            "already_ready_count": len(already),
            "skipped_count": len(skipped),
            "rebuildable": rebuildable,
            "already_ready_for_target": already,
            "skipped": skipped,
            "note": "资产级 ready_version 只表示内容版本，不能当作新模型空间已索引。默认只生成计划与 planned 队列。execute=true 时进入正常 waiting 任务并重嵌到独立 collection，不会删除旧 collection 或业务记录，也不会假标 ready。",
        }

    def _can_execute(self, space: ModelSpace) -> None:
        if self._tasks is None or self._writer is None or self._store is None or self._reader is None:
            raise AppError(
                "rebuild_execute_disabled",
                "重建执行器尚未装配完整（任务队列、索引写入、存储、来源读取），只能生成计划。",
            )
        if self._current_space is None:
            raise AppError("rebuild_execute_disabled", "当前没有已激活的模型空间，拒绝执行重嵌入。")
        if (
            self._current_space.profile != space.profile
            or self._current_space.model_id != space.model_id
            or self._current_space.model_version != space.model_version
            or int(self._current_space.dim) != int(space.dim)
            or self._current_space.preprocess_version != space.preprocess_version
        ):
            raise AppError(
                "rebuild_space_mismatch",
                "重建目标空间与当前激活配置不一致，拒绝执行，以免写入错误 collection。",
            )
        encoder = self._encoder
        index = self._index
        if encoder is not None and not encoder.available():
            raise AppError("rebuild_execute_disabled", f"编码器当前不可用，拒绝执行重嵌入。{encoder.status_message()}")
        if index is not None and not index.available():
            raise AppError("rebuild_execute_disabled", f"向量库当前不可用，拒绝执行重嵌入。{index.status_message()}")

    def enqueue(self, space: ModelSpace, *, execute: bool = False) -> dict[str, Any]:
        plan = self.plan(space)
        enqueue = getattr(self._metadata, "upsert_rebuild_item", None)
        if not callable(enqueue):
            raise AppError("rebuild_queue_unavailable", "重建队列仓储尚未装配。")
        ledger = []
        for row in plan["rebuildable"]:
            ledger.append(
                enqueue(
                    {
                        "asset_id": row["asset_id"],
                        "profile": space.profile,
                        "model_id": space.model_id,
                        "model_version": space.model_version,
                        "dim": int(space.dim),
                        "preprocess_version": space.preprocess_version,
                        "status": "waiting" if execute else "planned",
                        "execute": False,
                        "payload": {
                            "reason": row.get("reason"),
                            "content_version": row.get("content_version"),
                            "execute": bool(execute),
                        },
                    }
                )
            )
        plan["queued"] = ledger
        plan["queued_count"] = len(ledger)
        plan["queue_status"] = "waiting" if execute else "planned"
        if not execute:
            plan["jobs"] = []
            plan["job_count"] = 0
            return plan
        self._can_execute(space)
        jobs = []
        for row in plan["rebuildable"]:
            job = self._tasks.add_job(
                Job(
                    id=str(uuid.uuid4()),
                    job_type="reindex_profile",
                    status="waiting",
                    source_id=row.get("source_id"),
                    asset_id=row["asset_id"],
                    stage="queued",
                    step="等待按新模型空间重嵌",
                    progress=0,
                    queue_position=None,
                    error_code=None,
                    error_message=None,
                    checkpoint={},
                    payload={
                        "object_key": row.get("object_key"),
                        "kind": row.get("kind"),
                        "execute": True,
                        "space": asdict(space),
                    },
                    created_at=now(),
                    updated_at=now(),
                )
            )
            jobs.append({"id": job.id, "asset_id": job.asset_id, "status": job.status, "job_type": job.job_type})
        plan["execute"] = True
        plan["re_embeds"] = True
        plan["writes_milvus"] = True
        plan["writes_s3"] = True
        plan["creates_remote_collection"] = True
        plan["jobs"] = jobs
        plan["job_count"] = len(jobs)
        plan["note"] = (
            "已将重建任务写入正常 waiting 队列。将为当前空间生成完整画面模型输入并写入独立 collection，"
            "不会删除旧 collection、旧模型输入或 SQLite 业务记录，也不会把旧 ready 假标为新空间已索引。"
        )
        return plan

    def process(self, job: Job) -> str:
        space = self._current_space
        if space is None:
            raise AppError("rebuild_execute_disabled", "当前没有已激活的模型空间。")
        payload_space = (job.payload or {}).get("space") or {}
        if payload_space:
            if (
                payload_space.get("profile") != space.profile
                or payload_space.get("model_id") != space.model_id
                or payload_space.get("model_version") != space.model_version
                or int(payload_space.get("dim") or 0) != int(space.dim)
                or payload_space.get("preprocess_version") != space.preprocess_version
            ):
                raise AppError("rebuild_space_mismatch", "任务空间与当前激活配置不一致，已拒绝写入。")
        asset_id = job.asset_id
        if not asset_id:
            raise AppError("invalid", "重建任务缺少资源 ID")
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise AppError("not_found", "资源不存在")
        self._persist(job.id, stage="prepare", step="正在准备完整画面模型输入", progress=0.1)
        if asset.kind == "subtitle":
            outcome = self._reindex_subtitles(job, asset)
        else:
            self._prepare_model_inputs(job, asset, space)
            self._persist(job.id, stage="embed", step="正在写入新模型空间向量", progress=0.6)
            if self._writer is None:
                raise AppError("rebuild_execute_disabled", "索引写入器尚未装配")
            if self._tasks is not None:
                job = self._tasks.get_job(job.id) or job
            outcome = self._writer.process_frames(job)
        if outcome == "completed":
            # 字幕关联如存在则随后写入，但不把没有关联的情况当成失败。
            if asset.kind != "subtitle":
                self._reindex_associated_subtitles(job, asset)
            self._persist(job.id, stage="completed", step="新模型空间重嵌完成", progress=1.0, status="completed")
            return "completed"
        if outcome == "blocked":
            self._persist(job.id, stage="blocked", step="重嵌未完成，已阻断，不会假标 ready", progress=0.7, status="blocked")
            return "blocked"
        return outcome

    def _prepare_model_inputs(self, job: Job, asset, space: ModelSpace) -> None:
        frames = [f for f in self._metadata.list_frames(asset.id) if f.get("kept")]
        if not frames:
            raise AppError("invalid", "该资源没有可重嵌的已保留采样帧，拒绝空写 ready。")
        local = self._download_source(job, asset)
        if asset.kind == "image":
            self._store_full_image(job, asset, frames[0], local, space)
            return
        if asset.kind == "video":
            if self._sampler is None:
                raise AppError("rebuild_execute_disabled", "视频采样器尚未装配，无法按原时间点抽取完整画面。")
            encode_dir = self._work_root / job.id / "encode"
            encode_dir.mkdir(parents=True, exist_ok=True)
            ck = dict(job.checkpoint or {})
            for frame in frames:
                png = self._sampler.extract_one(
                    local,
                    SamplePoint(index=int(frame["index"]), time_seconds=float(frame.get("time_seconds") or 0)),
                )
                from PIL import Image
                from io import BytesIO

                image = Image.open(BytesIO(png))
                image.load()
                index = int(frame["index"])
                dest = encode_dir / f"unit_{index:06d}.png"
                if space.preprocess_version == PREPROCESS_FULL_FRAME:
                    dest.write_bytes(png)
                else:
                    dest.write_bytes(render_model_input(image, SampleSettings(), strategy=space.preprocess_version))
                remember_encode_unit(ck, index, dest)
                self._metadata.merge_frame_extra(
                    asset.id,
                    index,
                    {"full_frame_source": "sampled_frame", "encode_local": True},
                )
            ck["work_path"] = str(local)
            self._persist(job.id, checkpoint=ck)
            return
        raise AppError("invalid", f"不支持的资源类型：{asset.kind}")

    def _store_full_image(self, job: Job, asset, frame: dict[str, Any], local: Path, space: ModelSpace) -> None:
        ck = dict(job.checkpoint or {})
        path = Path(local)
        extra = {"full_frame_source": "original_object_bytes", "encode_local": True}
        if space.preprocess_version != PREPROCESS_FULL_FRAME:
            from PIL import Image

            image = Image.open(path).convert("RGB")
            dest = path.parent / "encode"
            dest.mkdir(parents=True, exist_ok=True)
            out = dest / "unit_000000.png"
            out.write_bytes(render_model_input(image, SampleSettings(), strategy=space.preprocess_version))
            path = out
            extra["full_frame_source"] = "local_preprocessed"
        remember_encode_unit(ck, int(frame["index"]), path)
        ck["work_path"] = str(local)
        self._persist(job.id, checkpoint=ck)
        self._metadata.merge_frame_extra(asset.id, int(frame["index"]), extra)

    def _download_source(self, job: Job, asset) -> Path:
        if self._work_root is None or self._reader is None:
            raise AppError("rebuild_execute_disabled", "工作目录或来源读取器尚未装配。")
        dest = self._work_root / job.id / f"original{Path(asset.object_key).suffix or '.bin'}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        source = self._metadata.get_source(asset.source_id)
        if source is None:
            raise AppError("not_found", "存储源不存在")
        access, secret = self._metadata.get_source_credentials(asset.source_id)
        return self._reader.download_readonly(source, access, secret, asset.object_key, dest)

    def _reindex_associated_subtitles(self, job: Job, asset) -> None:
        lister = getattr(self._metadata, "list_associations", None)
        if not callable(lister):
            return
        try:
            assocs = lister(video_asset_id=asset.id)
        except Exception:
            return
        if not assocs:
            return
        # 有关联时才尝试；当前库若无关联则跳过。
        if self._writer is None:
            return
        for assoc in assocs:
            units = []
            assoc_id = assoc.get("id") or assoc.get("association_id")
            if assoc_id and hasattr(self._metadata, "list_groups"):
                groups = self._metadata.list_groups(assoc_id)
                units = [
                    {"unit_index": g.get("group_index"), "start_seconds": g.get("start_seconds"), "text": g.get("text")}
                    for g in groups
                ]
            if units:
                self._writer.process_subtitles(job, units)

    def _reindex_subtitles(self, job: Job, asset) -> str:
        return "blocked"

    def _persist(self, job_id: str, **fields) -> None:
        if self._tasks is None:
            return
        current = self._tasks.get_job(job_id)
        if current is None or current.status == "paused":
            return
        allowed = {k: v for k, v in fields.items() if k in {"stage", "step", "progress", "checkpoint", "status"}}
        if allowed:
            self._tasks.update_job(job_id, **allowed)
