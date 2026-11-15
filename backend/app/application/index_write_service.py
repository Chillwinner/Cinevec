from __future__ import annotations

from typing import Any

from app.domain.errors import AppError, NotConfiguredError
from app.domain.lifecycle import VectorLocator
from app.domain.models import Job
from app.domain.ports import FileStore, ImageTextEncoder, MetadataRepository, SubtitleEncoder, TaskRepository, VectorIndex
from pathlib import Path

from app.infra.encode_local import cleanup_encode_work, local_path_for
from app.infra.milvus_rest import collection_name
from app.infra.pipeline_timing import end as timing_end
from app.infra.pipeline_timing import image_bytes_info
from app.infra.pipeline_timing import note as timing_note
from app.infra.pipeline_timing import start as timing_start


def vector_ref(
    asset_id: str,
    version: int,
    modality: str,
    unit_index: int,
    space=None,
    *,
    profile: str = "",
    model_id: str = "",
    model_version: str = "",
    dim=None,
    preprocess_version: str = "",
) -> str:
    """Vector association id is unique per model space, not only per asset/version/unit."""
    base = f"{asset_id}:{version}:{modality}:{unit_index}"
    if space is not None:
        profile = getattr(space, "profile", "") or profile
        model_id = getattr(space, "model_id", "") or model_id
        model_version = getattr(space, "model_version", "") or model_version
        dim = getattr(space, "dim", None) if getattr(space, "dim", None) is not None else dim
        preprocess_version = getattr(space, "preprocess_version", "") or preprocess_version
    suffix = ":".join(
        [
            (profile or ""),
            (model_id or ""),
            (model_version or ""),
            str(dim or ""),
            (preprocess_version or ""),
        ]
    )
    if not suffix.replace(":", ""):
        return base
    ident = f"{base}:{suffix}"
    return ident[:128]


def entity_id_for_unit(asset_id: str, version: int, modality: str, unit_index: int) -> str:
    """Actual remote entity id. Keep the historical short form so existing entities can be reused."""
    return f"{asset_id}:{version}:{modality}:{unit_index}"[:128]


class IndexWriteService:
    """跨存储幂等写入：先向量库 upsert，再 sqlite 关联，全部关联后才激活 ready_version。"""

    def __init__(
        self,
        metadata: MetadataRepository,
        tasks: TaskRepository,
        file_store: FileStore,
        encoder: ImageTextEncoder,
        index: VectorIndex,
        subtitle_encoder: SubtitleEncoder | None = None,
        profile: str = "default",
        model_id: str = "unset",
        model_version: str = "unset",
        dim: int | None = None,
        preprocess_version: str = "",
        space=None,
    ) -> None:
        self._metadata = metadata
        self._tasks = tasks
        self._store = file_store
        self._encoder = encoder
        self._index = index
        self._sub_encoder = subtitle_encoder
        self._profile = profile
        self._model_id = model_id
        self._model_version = model_version
        self._dim = dim
        self._preprocess_version = preprocess_version or ""
        self._space = space

    def process_frames(self, job: Job) -> str:
        asset_id = job.asset_id
        if not asset_id:
            raise AppError("invalid", "索引任务缺少资源 ID")
        if not self._encoder.available() or not self._index.available():
            return "blocked"
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise AppError("not_found", "资源不存在")
        frames = [f for f in self._metadata.list_frames(asset_id) if f.get("kept")]
        checkpoint = job.checkpoint or {}
        records = []
        for frame in frames:
            idx = int(frame.get("index") if frame.get("index") is not None else frame.get("frame_index") or 0)
            extra = frame.get("extra") or {}
            key = self._model_input_key(frame)
            local = local_path_for(checkpoint, idx)
            from_original = extra.get("full_frame_source") == "original_object_bytes"
            if not key and not local and not from_original:
                continue
            records.append(
                {
                    "unit_index": idx,
                    "time_seconds": frame.get("time_seconds"),
                    "preview_key": frame.get("preview_key"),
                    "source_key": key,
                    "local_path": local,
                    "from_original": from_original,
                    "kind": "image",
                }
            )
        return self._write_units(job, asset, "frame", records, self._encoder, encode_image=True)

    def process_subtitles(self, job: Job, units: list[dict[str, Any]]) -> str:
        encoder = self._sub_encoder
        if encoder is None or not encoder.available() or not self._index.available():
            return "blocked"
        asset_id = job.payload.get("video_asset_id") or job.asset_id
        if not asset_id:
            raise AppError("invalid", "字幕索引进程缺少视频资源")
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise AppError("not_found", "资源不存在")
        records = []
        for unit in units:
            records.append(
                {
                    "unit_index": int(unit.get("unit_index") or unit.get("group_index") or unit.get("cue_index") or 0),
                    "time_seconds": unit.get("start_seconds") or unit.get("time_seconds"),
                    "preview_key": unit.get("preview_key"),
                    "text": unit.get("text") or "",
                    "kind": "text",
                }
            )
        return self._write_units(job, asset, "subtitle", records, encoder, encode_image=False)

    def _write_units(self, job: Job, asset, modality: str, units: list[dict[str, Any]], encoder, encode_image: bool) -> str:
        checkpoint = dict(job.checkpoint or {})
        embed = dict(checkpoint.get("embed") or {})
        done = set(embed.get("associated_ids") or [])
        written = set(embed.get("written_ids") or [])
        version = int(asset.version)
        collection = ""
        if self._dim:
            collection = collection_name(
                self._profile, self._model_id, self._model_version, int(self._dim), modality,
                preprocess_version=self._preprocess_version,
            )
        else:
            name_fn = getattr(self._index, "name_for", None)
            if callable(name_fn):
                collection = str(name_fn(modality) or "")
        total = len(units)
        find_existing = getattr(self._metadata, "find_vector_unit_in_space", None)
        for unit in units:
            unit_index = int(unit["unit_index"])
            vid = vector_ref(
                asset.id,
                version,
                modality,
                unit_index,
                space=self._space,
                profile=self._profile,
                model_id=self._model_id,
                model_version=self._model_version,
                dim=self._dim,
                preprocess_version=self._preprocess_version,
            )
            existing = None
            if callable(find_existing):
                existing = find_existing(
                    asset.id,
                    version,
                    modality,
                    unit_index,
                    profile=self._profile,
                    model_id=self._model_id,
                    model_version=self._model_version,
                    dim=self._dim,
                    preprocess_version=self._preprocess_version,
                )
            if existing and existing.get("vector_id"):
                vid = existing["vector_id"]
            remote_id = (existing or {}).get("remote_vector_id") or entity_id_for_unit(
                asset.id, version, modality, unit_index
            )
            if vid in done:
                continue
            self._checkpoint(job.id, checkpoint, embed, stage="encoding", current=vid)
            timing_start(checkpoint, "encode")
            if encode_image:
                timing_start(checkpoint, "encode_fetch")
                data, source = self._load_encode_bytes(asset, unit)
                timing_end(checkpoint, "encode_fetch")
                timing_note(checkpoint, "encode_fetch", **image_bytes_info(data), source=source)
                timing_start(checkpoint, "encode_http")
                vector = encoder.encode_image(data)
                timing_end(checkpoint, "encode_http")
                last = getattr(encoder, "last_embed_meta", None)
                meta = last() if callable(last) else None
                if meta:
                    timing_note(checkpoint, "encode_remote", **meta)
            else:
                encode_task = getattr(encoder, "encode_text_for_task", None)
                if callable(encode_task):
                    vector = encode_task(unit.get("text") or "", role="document")
                else:
                    vector = encoder.encode_text(unit.get("text") or "")
            timing_end(checkpoint, "encode")
            record = {
                "id": remote_id,
                "vector": vector,
                "asset_id": asset.id,
                "version": version,
                "unit_index": unit_index,
                "time_seconds": unit.get("time_seconds") or 0,
                "preview_key": unit.get("preview_key") or "",
            }
            self._checkpoint(job.id, checkpoint, embed, stage="writing", current=vid, remote_id=remote_id)
            timing_start(checkpoint, "index_write")
            self._index.upsert_records(modality, [record])
            timing_end(checkpoint, "index_write")
            written.add(remote_id)
            embed["written_ids"] = sorted(written)
            self._checkpoint(job.id, checkpoint, embed, stage="associating", current=vid, remote_id=remote_id)
            timing_start(checkpoint, "associate")
            self._metadata.upsert_vector_unit(
                {
                    "vector_id": vid,
                    "asset_id": asset.id,
                    "version": version,
                    "modality": modality,
                    "unit_index": unit_index,
                    "time_seconds": unit.get("time_seconds"),
                    "preview_key": unit.get("preview_key"),
                    "collection": collection,
                    "status": "associated",
                    "profile": self._profile,
                    "model_id": self._model_id,
                    "model_version": self._model_version,
                    "dim": int(self._dim) if self._dim else None,
                    "preprocess_version": self._preprocess_version or "",
                    "remote_vector_id": remote_id,
                }
            )
            timing_end(checkpoint, "associate")
            done.add(vid)
            embed["associated_ids"] = sorted(done)
            embed["written_ids"] = sorted(written)
            self._checkpoint(job.id, checkpoint, embed, stage="associated", current=vid, associated=len(done), total=total)

        if total == 0 or len(done) < total:
            if total == 0:
                return "blocked"
            return "blocked"
        timing_start(checkpoint, "activate")
        self._activate(asset.id, version)
        timing_end(checkpoint, "activate")
        embed["stage"] = "activated"
        self._checkpoint(job.id, checkpoint, embed, stage="activated", associated=len(done), total=total)
        cleanup_encode_work(checkpoint)
        return "completed"

    def _load_encode_bytes(self, asset, unit: dict[str, Any]) -> tuple[bytes, str]:
        local = unit.get("local_path")
        if local:
            path = Path(local)
            if path.is_file():
                return path.read_bytes(), "local"
        key = unit.get("source_key")
        if key:
            return self._store.get_bytes(key), "store"
        if unit.get("from_original") and asset.object_key:
            return self._store.get_bytes(asset.object_key), "original_object"
        raise AppError("encode_input_missing", "没有可编码的本地副本或对象，拒绝空写向量。")

    def _model_input_key(self, frame: dict[str, Any]) -> str | None:
        extra = frame.get("extra") or {}
        by_prep = extra.get("model_inputs_by_preprocess") or {}
        version = (self._preprocess_version or "").strip()
        if version:
            key = by_prep.get(version)
            if key:
                return key
            # 只有从未按空间标注过的帧，才用当前这版预处理刚生成的 model_input_key。
            # 已经挂着别的 preprocess 版本时不得乱回退。
            if not by_prep:
                return frame.get("model_input_key")
            return None
        return frame.get("model_input_key")

    def _activate(self, asset_id: str, version: int) -> None:
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            return
        fields = {"ready_version": version, "index_state": "ready"}
        if asset.excluded:
            fields["lifecycle"] = "excluded"
        elif getattr(asset, "lifecycle", "active") in {"missing", "source_invalid"}:
            pass
        else:
            fields["lifecycle"] = "active"
        self._metadata.update_asset(asset_id, **fields)
        upsert = getattr(self._metadata, "upsert_asset_index_profile", None)
        if callable(upsert) and self._dim:
            collection = collection_name(
                self._profile, self._model_id, self._model_version, int(self._dim), "frame",
                preprocess_version=self._preprocess_version,
            )
            upsert(
                {
                    "asset_id": asset_id,
                    "profile": self._profile,
                    "model_id": self._model_id,
                    "model_version": self._model_version,
                    "dim": int(self._dim),
                    "preprocess_version": self._preprocess_version or "unspecified",
                    "index_state": "ready",
                    "indexed_content_version": version,
                    "collection": collection,
                }
            )

    def _checkpoint(self, job_id: str, checkpoint: dict, embed: dict, **extra) -> None:
        payload = dict(embed)
        payload.update(extra)
        current = self._tasks.get_job(job_id)
        if current is None or current.status == "paused":
            return
        merged = dict(current.checkpoint or {})
        merged.update(checkpoint)
        merged["embed"] = payload
        if checkpoint.get("timings"):
            merged["timings"] = checkpoint["timings"]
        checkpoint.clear()
        checkpoint.update(merged)
        self._tasks.update_job(job_id, checkpoint=merged, step=f"向量写入 {payload.get('stage', '')}", stage="embedding")

    def cleanup_old_versions(self, asset_id: str, keep_version: int) -> dict[str, Any]:
        """删除当前模型空间中非 keep_version 的向量引用。不跨越其它 profile/collection。失败不假装成功。"""
        list_units = getattr(self._metadata, "list_vector_units")
        try:
            units = list_units(
                asset_id,
                profile=self._profile,
                model_id=self._model_id,
                model_version=self._model_version,
                dim=self._dim,
                preprocess_version=self._preprocess_version or "",
            )
        except TypeError:
            units = [u for u in list_units(asset_id) if (u.get("profile") or "") == self._profile and (u.get("model_id") or "") == self._model_id]
        stale = []
        locators = []
        for unit in units:
            if int(unit["version"]) == int(keep_version):
                continue
            stale.append(unit)
            collection = (unit.get("collection") or "").strip()
            remote = (unit.get("remote_vector_id") or "").strip()
            if not collection or not remote:
                return {
                    "attempted": False,
                    "succeeded": False,
                    "pending": True,
                    "message": "当前空间存在缺少 collection 或远端实体 id 的旧版本关联，拒绝猜测删除目标。",
                    "refs": [],
                }
            locators.append(VectorLocator(collection=collection, remote_id=remote, modality=unit.get("modality") or ""))
        if not locators:
            return {"attempted": False, "succeeded": True, "refs": []}
        result = self._index.delete_vectors(locators)
        if result.succeeded:
            self._metadata.delete_vector_units(
                asset_id,
                profile=self._profile,
                model_id=self._model_id,
                model_version=self._model_version,
                dim=self._dim,
                preprocess_version=self._preprocess_version or "",
                vector_ids=[u["vector_id"] for u in stale],
            )
        return {
            "attempted": result.attempted,
            "succeeded": result.succeeded,
            "pending": result.pending,
            "message": result.message,
            "refs": result.refs,
        }
