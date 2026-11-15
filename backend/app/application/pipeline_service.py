from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from app.domain.errors import AppError, NotConfiguredError, NotFoundError
from app.domain.media import PreparedFrame, SamplePoint, SampleSettings, VideoInfo
from app.domain.models import Job
from app.domain.ports import FileStore, FrameCleaner, FrameSampler, MetadataRepository, SourceReader, TaskRepository
from app.domain.model_profile import PREPROCESS_FULL_FRAME
from app.infra.encode_local import remember_encode_unit, safe_rmtree, safe_unlink
from app.infra.pipeline_timing import end as timing_end
from app.infra.pipeline_timing import image_bytes_info
from app.infra.pipeline_timing import note as timing_note
from app.infra.pipeline_timing import start as timing_start
from app.infra.previews import render_model_input, render_preview


def _suffix_for(key: str, kind: str) -> str:
    name = key.rsplit("/", 1)[-1]
    if "." in name:
        return "." + name.rsplit(".", 1)[-1].lower()
    return ".jpg" if kind == "image" else ".mp4"


class PipelineService:
    def __init__(
        self,
        metadata: MetadataRepository,
        tasks: TaskRepository,
        reader: SourceReader,
        file_store: FileStore,
        sampler: FrameSampler,
        cleaner: FrameCleaner,
        settings_getter,
        work_root: Path,
        preprocess_getter=None,
    ) -> None:
        self._metadata = metadata
        self._tasks = tasks
        self._reader = reader
        self._store = file_store
        self._sampler = sampler
        self._cleaner = cleaner
        self._settings_getter = settings_getter
        self._work_root = work_root
        self._preprocess_getter = preprocess_getter

    def _model_strategy(self) -> str | None:
        if callable(self._preprocess_getter):
            return self._preprocess_getter()
        return None

    def process_unit(self, job: Job) -> str:
        current = self._tasks.get_job(job.id)
        if current is None:
            raise NotFoundError("任务不存在")
        if current.status == "paused":
            return "paused"
        if current.status in {"blocked", "completed", "failed"}:
            return current.status
        asset_id = current.asset_id
        if not asset_id:
            raise AppError("invalid_state", "任务缺少资源编号")
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise NotFoundError("资源不存在")
        if asset.kind in {"subtitle", "other"}:
            raise AppError(
                "media_unsupported",
                f"当前媒体流水线不处理「{asset.kind}」类型，字幕索引尚未实现。",
            )
        ck = dict(current.checkpoint or {})
        if ck.get("media_done"):
            return "blocked"
        if asset.kind == "image":
            return self._process_image(current, asset, ck)
        return self._process_video(current, asset, ck)

    def _sample_settings(self) -> SampleSettings:
        return self._settings_getter()

    def _persist(self, job_id: str, **fields: Any) -> Job | str:
        current = self._tasks.get_job(job_id)
        if current is None:
            raise NotFoundError("任务不存在")
        if current.status == "paused":
            allowed = {k: fields[k] for k in ("checkpoint", "stage", "step", "progress") if k in fields}
            if allowed:
                self._tasks.update_job(job_id, **allowed)
            return "paused"
        return self._tasks.update_job(job_id, **fields)

    def _process_image(self, job: Job, asset, ck: dict[str, Any]) -> str:
        path = self._ensure_local(job, asset, ck)
        if path == "paused" or path == "continue":
            return path
        path = Path(ck.get("work_path") or path)
        from PIL import Image

        timing_start(ck, "decode")
        try:
            image = Image.open(path)
            image.load()
            image = image.convert("RGB")
        except Exception as exc:
            raise AppError("decode_failed", f"图片无法解码：{exc}") from exc
        finally:
            timing_end(ck, "decode")
        if not self._store.available():
            raise NotConfiguredError(self._store.status_message(), code="filestore_unconfigured")
        settings = self._sample_settings()
        preview_key = f"previews/{asset.id}/source.jpg"
        existing = self._metadata.get_frame(asset.id, 0) if hasattr(self._metadata, "get_frame") else None
        encode_path = self._stage_image_encode(job, ck, path, image, settings)
        remember_encode_unit(ck, 0, encode_path)
        timing_note(ck, "model_input_file", **image_bytes_info(Path(encode_path).read_bytes()), source_width=image.width, source_height=image.height, strategy=self._model_strategy(), stored="local")
        if not existing:
            timing_start(ck, "preview")
            self._store.put_bytes(preview_key, render_preview(image, settings), "image/jpeg")
            timing_end(ck, "preview")
            extra = self._frame_extra(image, model_key=None, kind="image")
            extra["full_frame_source"] = "original_object_bytes" if self._is_passthrough() else "local_preprocessed"
            extra["encode_local"] = True
            timing_start(ck, "frame_meta")
            self._metadata.upsert_frame(
                asset.id,
                job.id,
                PreparedFrame(
                    index=0,
                    time_seconds=0.0,
                    kept=True,
                    drop_reason=None,
                    preview_key=preview_key,
                    model_input_key=None,
                    extra=extra,
                ),
            )
            timing_end(ck, "frame_meta")
        ck["media_done"] = True
        ck["auto_cover_key"] = preview_key
        ck["kept_count"] = 1
        ck["dropped_count"] = 0
        ck["kind"] = "image"
        marked = self._finish_media(job.id, asset.id, ck, "图片预处理完成（未经过视频滤帧）")
        return "paused" if marked == "paused" else "blocked"

    def _process_video(self, job: Job, asset, ck: dict[str, Any]) -> str:
        ck.setdefault("kind", "video")
        path = self._ensure_local(job, asset, ck)
        if path == "paused" or path == "continue":
            return path
        work = Path(ck["work_path"])
        if not ck.get("probed"):
            info = self._sampler.probe(work)
            ck["video_info"] = {
                "duration_seconds": info.duration_seconds,
                "fps": info.fps,
                "width": info.width,
                "height": info.height,
                "approx_frames": info.approx_frames,
            }
            ck["probed"] = True
            saved = self._persist(
                job.id,
                stage="probe",
                step=f"已探测时长 {info.duration_seconds:.2f}s，约 {info.approx_frames} 帧",
                progress=0.12,
                checkpoint=ck,
            )
            return "paused" if saved == "paused" else "continue"
        if not ck.get("planned"):
            info_data = ck["video_info"]
            info = VideoInfo(path=str(work), **info_data)
            plan = self._sampler.plan(info, self._sample_settings())
            ck["plan"] = [{"index": p.index, "time_seconds": p.time_seconds} for p in plan]
            ck["next_index"] = 0
            ck["kept_count"] = 0
            ck["dropped_count"] = 0
            ck["planned"] = True
            saved = self._persist(
                job.id,
                stage="plan",
                step=f"采样计划共 {len(plan)} 个时间点",
                progress=0.18,
                checkpoint=ck,
            )
            return "paused" if saved == "paused" else "continue"
        plan = ck.get("plan") or []
        idx = int(ck.get("next_index") or 0)
        if idx >= len(plan):
            marked = self._finish_media(job.id, asset.id, ck, "全部采样点已处理")
            return "paused" if marked == "paused" else "blocked"
        point = SamplePoint(index=int(plan[idx]["index"]), time_seconds=float(plan[idx]["time_seconds"]))
        existing = self._metadata.get_frame(asset.id, point.index) if hasattr(self._metadata, "get_frame") else None
        if existing:
            if existing.get("kept"):
                ck["kept_count"] = int(ck.get("kept_count") or 0)
                ck["last_kept_index"] = point.index
            else:
                ck["dropped_count"] = int(ck.get("dropped_count") or 0)
            ck["next_index"] = idx + 1
            saved = self._persist(
                job.id,
                stage="sample",
                step=f"跳过已处理采样点 {idx + 1}/{len(plan)}（幂等）",
                progress=_progress(idx + 1, len(plan)),
                checkpoint=ck,
            )
            return "paused" if saved == "paused" else "continue"
        saved = self._persist(
            job.id,
            stage="sample",
            step=f"抽取第 {idx + 1}/{len(plan)} 个采样点（t={point.time_seconds:.2f}s）",
            progress=_progress(idx, len(plan)),
            checkpoint=ck,
        )
        if saved == "paused":
            return "paused"
        try:
            png = self._sampler.extract_one(work, point)
        except AppError as exc:
            frame = PreparedFrame(
                index=point.index,
                time_seconds=point.time_seconds,
                kept=False,
                drop_reason="decode_failed",
                extra={"detail": exc.message},
            )
            self._metadata.upsert_frame(asset.id, job.id, frame)
            ck["dropped_count"] = int(ck.get("dropped_count") or 0) + 1
            ck["next_index"] = idx + 1
            saved = self._persist(job.id, stage="clean", step=f"采样点解码失败，已记录并跳过", checkpoint=ck, progress=_progress(idx + 1, len(plan)))
            return "paused" if saved == "paused" else "continue"
        last_sig = self._last_signature(asset.id, ck)
        inspected = self._cleaner.inspect(png, self._sample_settings(), last_sig)
        if not inspected.get("kept"):
            frame = PreparedFrame(
                index=point.index,
                time_seconds=point.time_seconds,
                kept=False,
                drop_reason=inspected.get("drop_reason"),
                extra={"detail": inspected.get("detail") or ""},
            )
            self._metadata.upsert_frame(asset.id, job.id, frame)
            ck["dropped_count"] = int(ck.get("dropped_count") or 0) + 1
            ck["next_index"] = idx + 1
            saved = self._persist(
                job.id,
                stage="clean",
                step=f"已过滤：{inspected.get('drop_reason')}（{inspected.get('detail') or ''}）",
                progress=_progress(idx + 1, len(plan)),
                checkpoint=ck,
            )
            return "paused" if saved == "paused" else "continue"
        if not self._store.available():
            raise NotConfiguredError(self._store.status_message(), code="filestore_unconfigured")
        image = inspected["image"]
        settings = self._sample_settings()
        preview_key = f"previews/{asset.id}/frame_{point.index:06d}.jpg"
        self._store.put_bytes(preview_key, render_preview(image, settings), "image/jpeg")
        if not ck.get("auto_cover_key"):
            ck["auto_cover_key"] = preview_key
        encode_path = self._stage_video_encode(job, ck, point.index, png, image, settings)
        extra = self._frame_extra(image, model_key=None, extra={"signature": inspected.get("signature") or []})
        extra["full_frame_source"] = "sampled_frame"
        extra["encode_local"] = True
        self._metadata.upsert_frame(
            asset.id,
            job.id,
            PreparedFrame(
                index=point.index,
                time_seconds=point.time_seconds,
                kept=True,
                drop_reason=None,
                preview_key=preview_key,
                model_input_key=None,
                extra=extra,
            ),
        )
        remember_encode_unit(ck, point.index, encode_path)
        ck["kept_count"] = int(ck.get("kept_count") or 0) + 1
        ck["last_kept_index"] = point.index
        ck["next_index"] = idx + 1
        saved = self._persist(
            job.id,
            stage="preview",
            step=f"已生成预览与模型输入（第 {idx + 1}/{len(plan)} 点，二者独立，不从预览再编码）",
            progress=_progress(idx + 1, len(plan)),
            checkpoint=ck,
        )
        return "paused" if saved == "paused" else "continue"

    def _ensure_local(self, job: Job, asset, ck: dict[str, Any]):
        existing = ck.get("work_path")
        if existing and Path(existing).is_file():
            ck["downloaded"] = True
            return Path(existing)
        if ck.get("downloaded") and not (existing and Path(existing).is_file()):
            ck["downloaded"] = False
        saved = self._persist(job.id, stage="download", step="只读下载原始对象（不会修改或删除来源）", progress=0.05, checkpoint=ck)
        if saved == "paused":
            return "paused"
        source = self._metadata.get_source(asset.source_id)
        if source is None:
            raise NotFoundError("存储源不存在")
        access, secret = self._metadata.get_source_credentials(asset.source_id)
        dest = self._work_root / job.id / f"original{_suffix_for(asset.object_key, asset.kind)}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        timing_start(ck, "download")
        path = self._reader.download_readonly(source, access, secret, asset.object_key, dest)
        timing_end(ck, "download")
        if dest.exists():
            try:
                dest.chmod(0o444)
            except OSError:
                pass
        ck["work_path"] = str(path)
        ck["downloaded"] = True
        saved = self._persist(job.id, stage="download", step="原始对象已落到只读工作副本", progress=0.08, checkpoint=ck)
        return "paused" if saved == "paused" else "continue"

    def _is_passthrough(self) -> bool:
        return (self._model_strategy() or "").strip() == PREPROCESS_FULL_FRAME

    def _encode_dir(self, job: Job, ck: dict[str, Any]) -> Path:
        work = ck.get("work_path")
        if work:
            return Path(work).parent / "encode"
        return self._work_root / job.id / "encode"

    def _stage_image_encode(self, job: Job, ck: dict[str, Any], original: Path, image, settings) -> Path:
        if self._is_passthrough():
            return original
        timing_start(ck, "model_input_render")
        blob = render_model_input(image, settings, strategy=self._model_strategy())
        timing_end(ck, "model_input_render")
        dest = self._encode_dir(job, ck)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "unit_000000.png"
        path.write_bytes(blob)
        return path

    def _stage_video_encode(self, job: Job, ck: dict[str, Any], index: int, extracted_png: bytes, image, settings) -> Path:
        dest = self._encode_dir(job, ck)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"unit_{int(index):06d}.png"
        if self._is_passthrough():
            path.write_bytes(extracted_png)
            return path
        timing_start(ck, "model_input_render")
        blob = render_model_input(image, settings, strategy=self._model_strategy())
        timing_end(ck, "model_input_render")
        path.write_bytes(blob)
        return path

    def _frame_extra(self, image, model_key: str | None = None, *, kind: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(extra or {})
        if kind:
            payload["kind"] = kind
            payload["width"] = image.width
            payload["height"] = image.height
        strategy = (self._model_strategy() or "").strip()
        if strategy and model_key:
            mapped = dict(payload.get("model_inputs_by_preprocess") or {})
            mapped[strategy] = model_key
            payload["model_inputs_by_preprocess"] = mapped
        return payload

    def _last_signature(self, asset_id: str, ck: dict[str, Any]) -> list[float] | None:
        last = ck.get("last_kept_index")
        if last is None or not hasattr(self._metadata, "get_frame"):
            return None
        row = self._metadata.get_frame(asset_id, int(last))
        if not row:
            return None
        sig = (row.get("extra") or {}).get("signature")
        if isinstance(sig, list) and sig:
            return [float(x) for x in sig]
        return None

    def _finish_media(self, job_id: str, asset_id: str, ck: dict[str, Any], note: str) -> Job | str:
        ck["media_done"] = True
        if hasattr(self._metadata, "update_asset"):
            asset = self._metadata.get_asset(asset_id)
            # 仅媒体/预览完成不得当作索引就绪，不得提前提升 ready_version。
            fields: dict[str, Any] = {"status": "media_ready", "index_state": "media_ready"}
            if asset and not getattr(asset, "cover_key", None) and ck.get("auto_cover_key"):
                fields["cover_key"] = ck["auto_cover_key"]
            if asset and asset.excluded:
                fields["lifecycle"] = "excluded"
            elif asset and asset.lifecycle == "pending_update":
                fields["lifecycle"] = "pending_update"
            elif asset and asset.ready_version > 0 and asset.version > asset.ready_version:
                fields["lifecycle"] = "pending_update"
            self._metadata.update_asset(asset_id, **fields)
        elif hasattr(self._metadata, "update_asset_status"):
            self._metadata.update_asset_status(asset_id, "media_ready")
        work = ck.get("work_path")
        if work:
            if not ck.get("keep_work"):
                safe_rmtree(Path(work).parent)
                ck.pop("work_path", None)
            else:
                encode_paths = {str(u.get("local_path") or "") for u in (ck.get("encode_units") or [])}
                if str(work) not in encode_paths:
                    # 预处理结束后立即删掉重型原片（视频/原图），仅保留轻量的待编码帧切片，避免占用磁盘。
                    safe_unlink(Path(work))
                    ck.pop("work_path", None)
        kept = int(ck.get("kept_count") or 0)
        dropped = int(ck.get("dropped_count") or 0)
        return self._persist(
            job_id,
            stage="media_done",
            step=f"{note}。保留 {kept} 帧，过滤 {dropped} 帧。尚未向量化。",
            progress=1.0,
            checkpoint=ck,
        )


def _progress(done: int, total: int) -> float:
    if total <= 0:
        return 0.2
    return min(0.95, 0.2 + 0.75 * (done / total))
