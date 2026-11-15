from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from app.domain.errors import AppError, NotFoundError
from app.domain.models import Job
from app.domain.ports import MetadataRepository, SourceReader, SubtitleEncoder, TaskRepository
from app.domain.subtitle import MergeSettings
from app.infra.clock import now
from app.infra.subtitle_merge import merge_adjacent
from app.infra.subtitle_parser import parse_subtitle_file


class SubtitleService:
    def __init__(
        self,
        metadata: MetadataRepository,
        tasks: TaskRepository,
        reader: SourceReader,
        encoder: SubtitleEncoder,
        settings_getter,
        work_root: Path,
    ) -> None:
        self._metadata = metadata
        self._tasks = tasks
        self._reader = reader
        self._encoder = encoder
        self._settings_getter = settings_getter
        self._work_root = work_root

    def list_associations(self, video_asset_id: str | None = None) -> list[dict[str, Any]]:
        items = self._metadata.list_associations(video_asset_id)
        out = []
        for item in items:
            video = self._metadata.get_asset(item["video_asset_id"])
            subtitle = self._metadata.get_asset(item["subtitle_asset_id"])
            out.append(
                {
                    **item,
                    "video_key": video.object_key if video else None,
                    "subtitle_key": subtitle.object_key if subtitle else None,
                    "cue_count": len(self._metadata.list_cues(item["id"])),
                    "group_count": len(self._metadata.list_groups(item["id"])),
                }
            )
        return out

    def get_detail(self, assoc_id: str) -> dict[str, Any]:
        assoc = self._metadata.get_association(assoc_id)
        video = self._metadata.get_asset(assoc["video_asset_id"])
        subtitle = self._metadata.get_asset(assoc["subtitle_asset_id"])
        return {
            **assoc,
            "video_key": video.object_key if video else None,
            "subtitle_key": subtitle.object_key if subtitle else None,
            "cues": self._metadata.list_cues(assoc_id),
            "groups": self._metadata.list_groups(assoc_id),
            "merge_note": "相邻合并规则为暂定实现值，产品尚未拍板。各条字幕与合并段起止均已保留。",
        }

    def associate(self, video_asset_id: str, subtitle_asset_id: str, enqueue: bool = True) -> tuple[dict[str, Any], Job | None]:
        video = self._metadata.get_asset(video_asset_id)
        subtitle = self._metadata.get_asset(subtitle_asset_id)
        if video is None or subtitle is None:
            raise NotFoundError("视频或字幕资源不存在")
        if video.kind != "video":
            raise AppError("invalid", "只能把字幕关联到视频，不会按文件名自动绑定")
        if subtitle.kind != "subtitle":
            raise AppError("invalid", "关联对象必须是已登记的字幕文件")
        if video.source_deleted or subtitle.source_deleted:
            raise AppError("invalid_state", "来源已失效的资源不能新建关联")
        existing = self._metadata.get_association_pair(video_asset_id, subtitle_asset_id)
        if existing:
            job = None
            if enqueue:
                job = self._enqueue(existing["id"], subtitle, video)
            return existing, job
        assoc = self._metadata.add_association(str(uuid.uuid4()), video_asset_id, subtitle_asset_id)
        job = self._enqueue(assoc["id"], subtitle, video) if enqueue else None
        return assoc, job

    def delete_association(self, assoc_id: str) -> None:
        self._metadata.delete_association(assoc_id)

    def _enqueue(self, assoc_id: str, subtitle, video) -> Job:
        active = []
        finder = getattr(self._tasks, "find_active_jobs", None)
        if callable(finder):
            active = finder("index_subtitles", asset_id=subtitle.id)
        if active:
            return active[0]
        return self._tasks.add_job(
            Job(
                id=str(uuid.uuid4()),
                job_type="index_subtitles",
                status="waiting",
                source_id=subtitle.source_id,
                asset_id=subtitle.id,
                stage="queued",
                step="等待解析关联字幕",
                progress=0,
                queue_position=None,
                error_code=None,
                error_message=None,
                checkpoint={"association_id": assoc_id, "video_asset_id": video.id},
                payload={
                    "object_key": subtitle.object_key,
                    "kind": "subtitle",
                    "association_id": assoc_id,
                    "video_asset_id": video.id,
                    "video_key": video.object_key,
                },
            )
        )

    def process_unit(self, job: Job) -> str:
        current = self._tasks.get_job(job.id)
        if current is None:
            raise NotFoundError("任务不存在")
        if current.status == "paused":
            return "paused"
        ck = dict(current.checkpoint or {})
        assoc_id = ck.get("association_id") or (current.payload or {}).get("association_id")
        if not assoc_id:
            raise AppError("invalid_state", "字幕任务缺少手动关联编号，不会按文件名猜测绑定")
        self._metadata.get_association(assoc_id)
        subtitle = self._metadata.get_asset(current.asset_id or "")
        if subtitle is None:
            raise NotFoundError("字幕资源不存在")
        if ck.get("media_done"):
            return "blocked"
        if not ck.get("downloaded"):
            return self._download(current, subtitle, ck)
        if not ck.get("parsed"):
            return self._parse(current, subtitle, ck)
        if not ck.get("persisted"):
            return self._persist_cues(current, ck)
        ck["media_done"] = True
        if hasattr(self._metadata, "update_asset"):
            self._metadata.update_asset(subtitle.id, status="subtitle_ready", index_state="parsed")  # 解析完成≠索引就绪，不提升 ready_version
        self._cleanup(ck)
        saved = self._persist_job(
            current.id,
            stage="media_done",
            step="字幕已解析并保存各条与合并段，尚未向量化",
            progress=1,
            checkpoint=ck,
        )
        return "paused" if saved == "paused" else "blocked"

    def _download(self, job: Job, subtitle, ck: dict[str, Any]) -> str:
        source = self._metadata.get_source(subtitle.source_id)
        if source is None or source.deleted:
            raise AppError("source_invalid", "字幕来源已失效或不可用，无法只读下载")
        access, secret = self._metadata.get_source_credentials(subtitle.source_id)
        dest = self._work_root / job.id / Path(subtitle.object_key).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        path = self._reader.download_readonly(source, access, secret, subtitle.object_key, dest)
        try:
            Path(path).chmod(0o444)
        except OSError:
            pass
        ck["work_path"] = str(path)
        ck["downloaded"] = True
        saved = self._persist_job(job.id, stage="download", step="已只读下载字幕（不修改来源对象）", progress=0.2, checkpoint=ck)
        return "paused" if saved == "paused" else "continue"

    def _parse(self, job: Job, subtitle, ck: dict[str, Any]) -> str:
        path = Path(ck["work_path"])
        parsed = parse_subtitle_file(path)
        ck["format"] = parsed.format
        ck["cue_count"] = len(parsed.cues)
        ck["parsed"] = True
        # 解析结果暂存到侧车 json，避免把全部字幕正文写入任务 checkpoint / 列表接口
        sidecar = Path(ck["work_path"]).with_suffix(".parsed.json")
        import json
        sidecar.write_text(
            json.dumps(
                {
                    "format": parsed.format,
                    "cues": [
                        {
                            "index": c.index,
                            "start_seconds": c.start_seconds,
                            "end_seconds": c.end_seconds,
                            "text": c.text,
                        }
                        for c in parsed.cues
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ck["parsed_path"] = str(sidecar)
        saved = self._persist_job(
            job.id,
            stage="parse",
            step=f"已解析 {parsed.format.upper()}，共 {len(parsed.cues)} 条（内容按纯文本保存）",
            progress=0.55,
            checkpoint=ck,
        )
        return "paused" if saved == "paused" else "continue"

    def _persist_cues(self, job: Job, ck: dict[str, Any]) -> str:
        import json

        from app.domain.subtitle import SubtitleCue

        data = json.loads(Path(ck["parsed_path"]).read_text(encoding="utf-8"))
        cues = [SubtitleCue(**item) for item in data["cues"]]
        settings = self._settings_getter() or MergeSettings()
        groups = merge_adjacent(cues, settings)
        assoc_id = ck["association_id"]
        self._metadata.replace_cues_and_groups(assoc_id, cues, groups)
        ck["group_count"] = len(groups)
        ck["persisted"] = True
        saved = self._persist_job(
            job.id,
            stage="merge",
            step=f"已保存 {len(cues)} 条字幕与 {len(groups)} 个暂定合并段",
            progress=0.85,
            checkpoint=ck,
        )
        return "paused" if saved == "paused" else "continue"

    def _cleanup(self, ck: dict[str, Any]) -> None:
        work = ck.get("work_path")
        if work:
            shutil.rmtree(Path(work).parent, ignore_errors=True)
        ck.pop("work_path", None)
        ck.pop("parsed_path", None)

    def _persist_job(self, job_id: str, **fields: Any):
        current = self._tasks.get_job(job_id)
        if current and current.status == "paused":
            allowed = {k: fields[k] for k in ("checkpoint", "stage", "step", "progress") if k in fields}
            if allowed:
                self._tasks.update_job(job_id, **allowed)
            return "paused"
        return self._tasks.update_job(job_id, **fields)

    def blocked_message(self) -> tuple[str, str]:
        if self._encoder.available():
            return (
                "embedding_pipeline_pending",
                "字幕解析已完成，但向量化写入尚未实现，不会生成假向量。",
            )
        return (
            "subtitle_embedding_pending",
            "字幕解析已完成，整任务向量化尚未完成。" + self._encoder.status_message(),
        )
