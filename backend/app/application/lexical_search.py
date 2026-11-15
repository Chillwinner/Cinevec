from __future__ import annotations

from typing import Any

from app.application.lifecycle_service import LifecycleService
from app.domain.errors import AppError
from app.domain.ports import MetadataRepository
from app.infra.object_keys import display_name_from_key

LEXICAL_NOTES = [
    "这是文件名、人工标签与描述信息的包含匹配，不调用 embedding，不与语义搜索融合。",
    "同一资产同时命中文件名、标签和描述时合并，并标明匹配依据。",
    "当前不做错别字容错或语义理解；排序为：多字段命中优先，其次文件名命中，然后按对象键。",
    "标签仅文件级，不支持时间段标签。",
]


class LexicalSearchService:
    def __init__(self, metadata: MetadataRepository, lifecycle: LifecycleService) -> None:
        self._metadata = metadata
        self._lifecycle = lifecycle

    def search(self, query: str, source_id: str | None = None) -> dict[str, Any]:
        text = (query or "").strip()
        if not text:
            raise AppError("invalid", "请输入文件名或标签关键字。这不是向量检索。")
        rows = self._metadata.search_lexical(text, source_id)
        getter = getattr(self._metadata, "preview_covers", None)
        covers = getter([row["asset"].id for row in rows]) if callable(getter) else {}
        items = []
        for row in rows:
            asset = row["asset"]
            view = self._lifecycle.asset_view(asset)
            view["tags"] = list(row.get("tags") or [])
            view["matched_on"] = list(row.get("matched_on") or [])
            view["filename"] = display_name_from_key(getattr(asset, "display_name", None) or asset.object_key)
            view["origin"] = getattr(asset, "origin", "external")
            cover_key = getattr(asset, "cover_key", None)
            if cover_key or asset.id in covers:
                ver = str(cover_key or "").rsplit("/", 1)[-1]
                loc = f"/api/assets/{asset.id}/cover" + (f"?v={ver}" if ver else "")
                view["cover_locator"] = loc
                view["video_cover_locator"] = loc
                view["cover_status"] = "ready"
            items.append(view)
        return {
            "kind": "lexical",
            "query": text,
            "embedding_used": False,
            "notes": LEXICAL_NOTES,
            "items": items,
        }
