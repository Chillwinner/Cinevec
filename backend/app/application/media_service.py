from __future__ import annotations

import mimetypes
import re
from typing import Any

from app.domain.errors import AppError, NotFoundError
from app.domain.ports import FileStore, MetadataRepository, SourceReader


def parse_byte_range(header: str | None, size: int) -> tuple[int, int, bool]:
    if size <= 0:
        return 0, -1, False
    if not header:
        return 0, size - 1, False
    match = re.match(r"bytes=(\d*)-(\d*)", header.strip())
    if not match:
        raise AppError("invalid_range", "无法解析 Range。", 416)
    start_s, end_s = match.group(1), match.group(2)
    if start_s == "" and end_s == "":
        raise AppError("invalid_range", "无法解析 Range。", 416)
    if start_s == "":
        length = int(end_s)
        start = max(size - length, 0)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    if start >= size or start < 0 or end < start:
        raise AppError("invalid_range", "Range 超出文件大小。", 416)
    return start, min(end, size - 1), True


class MediaService:
    """Authenticated original-file Range reads. Does not mint public bucket URLs."""

    def __init__(
        self,
        metadata: MetadataRepository,
        file_store: FileStore,
        source_reader: SourceReader | None = None,
    ) -> None:
        self._metadata = metadata
        self._store = file_store
        self._reader = source_reader

    def open(self, asset_id: str, range_header: str | None) -> dict[str, Any]:
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise NotFoundError("资源不存在")
        if getattr(asset, "excluded", False) or getattr(asset, "source_deleted", False):
            raise NotFoundError("原文件现在不在")
        content_type = mimetypes.guess_type(asset.object_key or "")[0] or "application/octet-stream"
        origin = getattr(asset, "origin", "external")
        if origin == "hosted":
            info = self._store.head(asset.object_key)
            size = int(info.get("size") or 0)
            content_type = info.get("content_type") or content_type
            start, end, partial = parse_byte_range(range_header, size)
            data = self._store.get_range(asset.object_key, start, end) if size else b""
            return {
                "data": data,
                "start": start,
                "end": end,
                "size": size,
                "partial": partial,
                "content_type": content_type,
            }
        source = self._metadata.get_source(asset.source_id)
        if source is None or self._reader is None:
            raise NotFoundError("原文件现在不在")
        access, secret = self._metadata.get_source_credentials(source.id)
        head = getattr(self._reader, "head_object", None)
        get_range = getattr(self._reader, "get_range", None)
        if not callable(head) or not callable(get_range):
            raise NotFoundError("原文件现在不在")
        try:
            info = head(source, access, secret, asset.object_key)
        except Exception as exc:
            raise NotFoundError("原文件现在不在") from exc
        size = int(info.get("size") or 0)
        content_type = info.get("content_type") or content_type
        start, end, partial = parse_byte_range(range_header, size)
        data = get_range(source, access, secret, asset.object_key, start, end) if size else b""
        return {
            "data": data,
            "start": start,
            "end": end,
            "size": size,
            "partial": partial,
            "content_type": content_type,
        }
