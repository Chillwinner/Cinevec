from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Iterable

from app.application.import_service import ImportService, infer_kind
from app.domain.errors import AppError, NotConfiguredError
from app.domain.ports import FileStore, MetadataRepository
from app.infra.object_keys import display_name_from_key, hosted_original_key, plugin_object_key, sanitize_relative_path, suffix_for

CHUNK = 1024 * 1024


def _truthy(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def normalize_external_ref(raw: Any) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, dict):
        raise AppError("invalid", "ref 必须是对象，含 ref_id、locator、params。")
    ref_id = str(raw.get("ref_id") or "").strip()
    locator = str(raw.get("locator") or "").strip()
    params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    out: dict[str, Any] = {}
    if ref_id:
        out["ref_id"] = ref_id
    if locator:
        out["locator"] = locator
    if params:
        out["params"] = params
    return out or None


class UploadService:
    """Save uploaded originals into the configured platform bucket, then reuse import/register."""

    def __init__(
        self,
        metadata: MetadataRepository,
        file_store: FileStore,
        imports: ImportService,
        work_root: Path | None = None,
        max_files_getter=None,
    ) -> None:
        self._metadata = metadata
        self._store = file_store
        self._imports = imports
        self._work_root = Path(work_root) if work_root else None
        self._max_files_getter = max_files_getter

    def set_asset_cover(self, asset_id: str, cover_bytes: bytes) -> Any:
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise AppError("not_found", "资源不存在", 404)
        if not cover_bytes:
            raise AppError("invalid", "封面图片不能为空。")
        if not self._store.available():
            raise NotConfiguredError(self._store.status_message(), code="filestore_unconfigured")
        from io import BytesIO
        from PIL import Image
        from app.domain.media import SampleSettings
        from app.infra.previews import render_preview

        try:
            image = Image.open(BytesIO(cover_bytes))
            image.load()
            image = image.convert("RGB")
        except Exception as exc:
            raise AppError("decode_failed", f"封面图片无法解码：{exc}") from exc
        jpeg = render_preview(image, SampleSettings())
        old_key = getattr(asset, "cover_key", None)
        key = f"covers/{asset.id}/cover_{uuid.uuid4().hex[:8]}.jpg"
        self._store.put_bytes(key, jpeg, "image/jpeg")
        updated = self._metadata.update_asset(asset.id, cover_key=key)
        if old_key and old_key != key and str(old_key).startswith("covers/"):
            try:
                self._store.delete_object(old_key)
            except Exception:
                pass
        return updated

    def ingest(
        self,
        files: Iterable[dict[str, Any]],
        *,
        items: list[dict] | None = None,
        batch_id: str | None = None,
        plugin: bool = False,
        cover_blobs: dict[str, bytes] | None = None,
    ) -> dict[str, Any]:
        platform = self._metadata.get_platform_storage()
        if platform is None:
            raise NotConfiguredError("尚未配置平台存储，无法保存上传原文件。不会新建桶。", code="filestore_unconfigured")
        if not self._store.available():
            raise NotConfiguredError(self._store.status_message(), code="filestore_unconfigured")
        put_stream = getattr(self._store, "put_stream", None)
        if not callable(put_stream):
            raise NotConfiguredError("当前存储适配器不支持流式写入。", code="filestore_unconfigured")

        file_list = list(files)
        if not file_list:
            raise AppError("invalid", "请至少上传一个文件。")
        meta_items = list(items or [])
        meta_by_rel: dict[str, dict] = {}
        cover_rels: set[str] = set()
        for item in meta_items:
            if not isinstance(item, dict):
                continue
            rel = sanitize_relative_path(item.get("relative_path") or item.get("filename") or "")
            meta_by_rel[rel] = item
            if _truthy(item.get("is_cover"), False):
                cover_rels.add(rel)
            cov = str(item.get("cover_relative_path") or "").strip()
            if cov:
                cover_rels.add(sanitize_relative_path(cov))

        primary_files: list[dict[str, Any]] = []
        cover_bytes_by_rel: dict[str, bytes] = dict(cover_blobs or {})
        for entry in file_list:
            filename = entry.get("filename") or ""
            relative = sanitize_relative_path(entry.get("relative_path") or filename, fallback_name=filename)
            if relative in cover_rels or _truthy(entry.get("is_cover"), False):
                stream = entry.get("stream")
                if stream is not None:
                    cover_bytes_by_rel[relative] = stream.read()
                continue
            primary_files.append(entry)

        if not primary_files:
            raise AppError("invalid", "请至少上传一个图片或视频文件。")

        max_files = int(self._max_files_getter()) if callable(self._max_files_getter) else 20
        if len(primary_files) > max_files:
            raise AppError(
                "upload_limit_exceeded",
                f"单次最多允许传输 {max_files} 个文件（当前 {len(primary_files)} 个）。请在设置中调整上限或分批上传。",
            )

        requested_batch = (batch_id or "").strip()
        if not requested_batch and meta_items:
            first = meta_items[0] if isinstance(meta_items[0], dict) else {}
            requested_batch = str(first.get("batch_id") or "").strip()
        if requested_batch:
            batch_id = sanitize_relative_path(requested_batch)
        else:
            batch_id = str(uuid.uuid4())

        if plugin:
            self._reject_unsupported(primary_files, meta_by_rel)

        import_items: list[dict] = []
        stored: list[dict[str, Any]] = []
        rel_to_key: dict[str, str] = {}
        local_spools: list[dict[str, Any]] = []
        pending_covers: list[tuple[str, bytes]] = []
        for entry in primary_files:
            filename = entry.get("filename") or ""
            relative = sanitize_relative_path(entry.get("relative_path") or filename, fallback_name=filename)
            item_meta = meta_by_rel.get(relative) or {}
            copy_default = _truthy(item_meta.get("copy"), True)
            copy = _truthy(item_meta.get("store_original"), copy_default) if "store_original" in item_meta else copy_default
            vectorize = _truthy(item_meta.get("vectorize"), True if plugin else False)
            source_url = str(item_meta.get("source_url") or item_meta.get("url") or "").strip()
            description = str(item_meta.get("description") or "").strip()
            ref = normalize_external_ref(item_meta.get("ref") or item_meta.get("external_ref"))
            if source_url:
                if ref is None:
                    ref = {
                        "ref_id": str(item_meta.get("ref_id") or uuid.uuid4()),
                        "locator": source_url,
                        "source_url": source_url,
                    }
                else:
                    ref.setdefault("ref_id", str(item_meta.get("ref_id") or uuid.uuid4()))
                    ref.setdefault("locator", source_url)
                    ref["source_url"] = source_url
            if not copy:
                if not ref or not ref.get("ref_id") or not ref.get("locator"):
                    raise AppError("invalid", "不保存源文件到 S3 时必须提供外部网址 source_url（或 ref.ref_id 与 ref.locator）。")
            kind = infer_kind(relative or filename)
            stream: BinaryIO = entry["stream"]
            content_type = entry.get("content_type") or "application/octet-stream"
            if copy:
                key = hosted_original_key(batch_id, relative, fallback_name=filename)
                origin = "hosted"
                put_stream(key, stream, content_type, overwrite=False)
            else:
                if self._work_root is None:
                    raise AppError("invalid", "工作目录未装配，无法在不保存原件时导入。")
                key = plugin_object_key(str(ref["ref_id"]))
                origin = "plugin"
                spool = self._work_root / "_incoming" / batch_id / relative.replace("/", "_")
                spool.parent.mkdir(parents=True, exist_ok=True)
                self._copy_stream(stream, spool)
                local_spools.append({"relative": relative, "path": spool, "kind": kind, "filename": filename, "object_key": key})
            rel_to_key[relative] = key
            cov_rel = str(item_meta.get("cover_relative_path") or "").strip()
            if cov_rel:
                cov_norm = sanitize_relative_path(cov_rel)
                cov_blob = cover_bytes_by_rel.get(cov_norm) or cover_bytes_by_rel.get(relative)
                if not cov_blob:
                    raise AppError("invalid", f"找不到指定的封面图片文件：{cov_rel}")
                pending_covers.append((key, cov_blob))
            elif relative in cover_bytes_by_rel:
                pending_covers.append((key, cover_bytes_by_rel[relative]))

            row: dict[str, Any] = {
                "object_key": key,
                "vectorize": vectorize,
                "origin": origin,
                "display_name": display_name_from_key(relative),
                "kind": kind,
                "batch_id": batch_id,
                "subtitle_relative_path": item_meta.get("subtitle_relative_path"),
                "description": description,
            }
            if ref:
                row["external_ref"] = ref
            import_items.append(row)
            stored.append(
                {
                    "object_key": None if origin == "plugin" else key,
                    "relative_path": relative,
                    "display_name": display_name_from_key(relative),
                    "copy": copy,
                    "store_original": copy,
                    "origin": origin,
                }
            )

        for item in import_items:
            sub_rel = item.pop("subtitle_relative_path", None)
            if sub_rel:
                sub_key = rel_to_key.get(sanitize_relative_path(sub_rel))
                if not sub_key:
                    raise AppError("invalid", f"找不到手动指定的字幕文件：{sub_rel}。不会按同名自动绑定。")
                item["subtitle_object_key"] = sub_key

        assets, jobs = self._imports.import_items(platform.id, import_items)
        self._attach_local_work(assets, jobs, local_spools)
        if pending_covers:
            by_key = {a.object_key: a for a in assets}
            updated_map: dict[str, Any] = {}
            for obj_key, cov_blob in pending_covers:
                target = by_key.get(obj_key)
                if target is not None:
                    updated_map[target.id] = self.set_asset_cover(target.id, cov_blob)
            if updated_map:
                assets = [updated_map.get(a.id, a) for a in assets]
        return {
            "batch_id": batch_id,
            "prefix": f"originals/{batch_id}/" if any(item.get("origin") == "hosted" for item in import_items) else None,
            "origin": "plugin" if plugin else "hosted",
            "stored": stored,
            "assets": assets,
            "jobs": jobs,
            "message": (
                "插件导入已登记。"
                if plugin
                else "上传已完成登记；勾选保存的原件已写入主存储 originals/ 目录，未保存原件将在预处理完成后清理。"
            ),
        }

    def _reject_unsupported(self, file_list: list[dict[str, Any]], meta_by_rel: dict[str, dict]) -> None:
        bad: list[str] = []
        for entry in file_list:
            filename = entry.get("filename") or ""
            relative = sanitize_relative_path(entry.get("relative_path") or filename, fallback_name=filename)
            kind = infer_kind(relative or filename)
            if kind not in {"image", "video"}:
                bad.append(relative or filename)
        if bad:
            raise AppError(
                "unsupported_kind",
                "本接口只接受图片和视频，已拒绝：" + "、".join(bad),
            )

    def _copy_stream(self, stream: BinaryIO, dest: Path) -> None:
        with dest.open("wb") as handle:
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)

    def _attach_local_work(self, assets, jobs, local_spools: list[dict[str, Any]]) -> None:
        if not local_spools:
            return
        if self._work_root is None:
            raise AppError("invalid", "工作目录未装配。")
        by_key = {asset.object_key: asset for asset in assets}
        by_name = {asset.display_name: asset for asset in assets}
        index_jobs = [job for job in jobs if job.job_type == "index_asset"]
        job_by_asset = {job.asset_id: job for job in index_jobs}
        for spool in local_spools:
            name = display_name_from_key(spool["relative"])
            asset = by_key.get(spool.get("object_key") or "") or by_name.get(name)
            if asset is None:
                asset = next((item for item in assets if item.origin == "plugin"), None)
            if asset is None:
                raise AppError("invalid", "不复制导入未能对上资产。")
            job = job_by_asset.get(asset.id)
            dest_dir = self._work_root / (job.id if job else asset.id)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"original{suffix_for(spool['filename'] or spool['relative'], spool['kind'])}"
            shutil.move(str(spool["path"]), dest)
            try:
                spool["path"].parent.rmdir()
            except OSError:
                pass
            try:
                dest.chmod(0o444)
            except OSError:
                pass
            if job is None:
                continue
            checkpoint = dict(job.checkpoint or {})
            checkpoint["work_path"] = str(dest)
            checkpoint["downloaded"] = True
            checkpoint["keep_work"] = True
            checkpoint["resume_from"] = "dependency_check"
            self._imports._tasks.update_job(job.id, checkpoint=checkpoint)
            job.checkpoint = checkpoint
