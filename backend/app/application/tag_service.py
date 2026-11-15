from __future__ import annotations

from typing import Any

from app.domain.errors import AppError, NotFoundError
from app.domain.ports import MetadataRepository

MAX_TAG_LEN = 64


def normalize_tag(value: str) -> str:
    tag = " ".join((value or "").strip().split())
    if not tag:
        raise AppError("invalid", "标签不能为空。")
    if len(tag) > MAX_TAG_LEN:
        raise AppError("invalid", f"标签最长 {MAX_TAG_LEN} 个字符。")
    return tag


class TagService:
    def __init__(self, metadata: MetadataRepository) -> None:
        self._metadata = metadata

    def list_for_asset(self, asset_id: str) -> list[str]:
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise NotFoundError("资源不存在")
        return self._metadata.list_tags(asset_id)

    def add(self, asset_id: str, tag: str) -> list[str]:
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise NotFoundError("资源不存在")
        self._metadata.add_tag(asset_id, normalize_tag(tag))
        return self._metadata.list_tags(asset_id)

    def add_many(self, asset_ids: list[str], tag: str) -> dict[str, list[str]]:
        value = normalize_tag(tag)
        out: dict[str, list[str]] = {}
        for asset_id in asset_ids:
            out[asset_id] = self.add(asset_id, value)
        return out

    def remove(self, asset_id: str, tag: str) -> list[str]:
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise NotFoundError("资源不存在")
        self._metadata.remove_tag(asset_id, normalize_tag(tag))
        return self._metadata.list_tags(asset_id)

    def tags_by_assets(self, asset_ids: list[str]) -> dict[str, list[str]]:
        getter = getattr(self._metadata, "list_tags_for_assets", None)
        if callable(getter):
            return getter(asset_ids)
        return {aid: self._metadata.list_tags(aid) for aid in asset_ids}
