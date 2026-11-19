from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import UploadFile
from fastapi.responses import Response

from app.domain.errors import AppError, NotFoundError
from app.domain.models import Asset
from app.infra.object_keys import sanitize_relative_path
from app.presentation.deps import Container


def parse_bool_param(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.lower() in {"1", "true", "yes", "on"}


def cover_locator_for(asset: Asset, prefix: str = "/api") -> str | None:
    cover_key = getattr(asset, "cover_key", None)
    if cover_key:
        ver = str(cover_key).rsplit("/", 1)[-1]
        return f"{prefix}/assets/{asset.id}/cover?v={ver}"
    return None


async def ingest_multipart_upload(
    container: Container,
    *,
    files: list[UploadFile],
    meta: str,
    relative_paths: list[str] | None,
    batch_id: str | None = None,
    cover: UploadFile | None = None,
    plugin: bool = False,
) -> dict[str, Any]:
    uploads = container.uploads
    if uploads is None:
        raise AppError("invalid", "上传服务尚未装配。")
    try:
        meta_items = json.loads(meta or "[]")
    except json.JSONDecodeError as exc:
        raise AppError("invalid", "上传元数据不是合法 JSON。") from exc
    if not isinstance(meta_items, list):
        raise AppError("invalid", "上传元数据必须是数组。")
    entries: list[dict[str, Any]] = []
    for index, upload in enumerate(files):
        rel = None
        if relative_paths and index < len(relative_paths):
            rel = relative_paths[index]
        elif index < len(meta_items) and isinstance(meta_items[index], dict):
            rel = meta_items[index].get("relative_path") or meta_items[index].get("filename")
        resolved_rel = rel or upload.filename or f"file-{index}"
        entries.append(
            {
                "filename": upload.filename or f"file-{index}",
                "relative_path": resolved_rel,
                "stream": upload.file,
                "content_type": upload.content_type or "application/octet-stream",
            }
        )
    cover_blobs: dict[str, bytes] = {}
    if cover is not None and entries:
        cov_bytes = await cover.read()
        if cov_bytes:
            if len(entries) == 1:
                rel = sanitize_relative_path(entries[0]["relative_path"], fallback_name=entries[0]["filename"])
                cover_blobs[rel] = cov_bytes
            else:
                cov_name = sanitize_relative_path(cover.filename or "cover.jpg", fallback_name="cover.jpg")
                cover_blobs[cov_name] = cov_bytes
    return uploads.ingest(entries, items=meta_items, batch_id=batch_id, plugin=plugin, cover_blobs=cover_blobs or None)


def apply_asset_patch(container: Container, asset_id: str, payload: dict[str, Any]) -> tuple[Asset, list[str]]:
    metadata = container.imports._metadata
    asset = metadata.get_asset(asset_id)
    if asset is None:
        raise NotFoundError("资源不存在")
    updates: dict[str, Any] = {}
    if "description" in payload and payload["description"] is not None:
        updates["description"] = str(payload["description"]).strip()
    if "display_name" in payload and payload["display_name"] is not None:
        cleaned = str(payload["display_name"]).strip()
        if cleaned:
            updates["display_name"] = cleaned
    if "source_url" in payload and payload["source_url"] is not None:
        url = str(payload["source_url"]).strip()
        existing_ref = dict(getattr(asset, "external_ref", None) or {})
        if url:
            existing_ref.setdefault("ref_id", str(uuid.uuid4()))
            existing_ref["locator"] = url
            existing_ref["source_url"] = url
            updates["external_ref"] = existing_ref
        else:
            existing_ref.pop("source_url", None)
            if existing_ref.get("locator"):
                existing_ref.pop("locator", None)
            updates["external_ref"] = existing_ref or None
    if "lifecycle" in payload and payload["lifecycle"] is not None:
        val = str(payload["lifecycle"]).strip()
        if val not in {"active", "missing", "source_invalid"}:
            raise AppError("invalid", "lifecycle 仅支持 active、missing 或 source_invalid。")
        updates["lifecycle"] = val
        updates["source_deleted"] = val == "source_invalid"
    updated = metadata.update_asset(asset_id, **updates) if updates else asset
    tags_svc = container.tags
    if "tags" in payload and payload["tags"] is not None:
        if tags_svc is None:
            raise AppError("invalid", "标签服务尚未装配。")
        wanted = [str(t).strip() for t in (payload["tags"] or []) if str(t).strip()]
        existing_tags = set(tags_svc.list_for_asset(asset_id))
        wanted_set = set(wanted)
        for t in existing_tags - wanted_set:
            tags_svc.remove(asset_id, t)
        for t in wanted:
            if t not in existing_tags:
                tags_svc.add(asset_id, t)
    tags = tags_svc.list_for_asset(asset_id) if tags_svc else []
    return updated, tags


def media_range_response(container: Container, asset_id: str, range_header: str | None) -> Response:
    media = container.media
    if media is None:
        raise AppError("invalid", "原件读取尚未装配。")
    payload = media.open(asset_id, range_header)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": payload["content_type"],
        "Content-Length": str(len(payload["data"])),
        "Cache-Control": "private, max-age=60",
    }
    status = 200
    if payload["partial"]:
        status = 206
        headers["Content-Range"] = f"bytes {payload['start']}-{payload['end']}/{payload['size']}"
    return Response(
        content=payload["data"],
        status_code=status,
        media_type=payload["content_type"],
        headers=headers,
    )


def cover_image_response(container: Container, asset_id: str) -> Response:
    data, content_type = container.search.cover_bytes(asset_id)
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=60"},
    )


def preview_image_response(container: Container, asset_id: str, kind: str, unit_index: int) -> Response:
    data, content_type = container.search.preview_bytes(asset_id, kind, unit_index)
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=60"},
    )
