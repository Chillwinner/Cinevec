from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Any

from app.application.search_aggregate import aggregate_hits, batch_limits, preview_locator
from app.domain.errors import AppError, NotConfiguredError, NotFoundError
from app.domain.ports import FileStore, ImageTextEncoder, MetadataRepository, SubtitleEncoder, VectorIndex
from app.domain.search import SCORE_MEANING, AssetResult, FrameHit, SearchSettings, vector_visibility
from app.infra.model_space import collection_for


class SearchService:
    def __init__(
        self,
        encoder: ImageTextEncoder,
        index: VectorIndex,
        jobs=None,
        metadata: MetadataRepository | None = None,
        file_store: FileStore | None = None,
        settings_getter=None,
        subtitle_encoder: SubtitleEncoder | None = None,
        current_space=None,
    ) -> None:
        self._encoder = encoder
        self._index = index
        self._jobs = jobs
        self._metadata = metadata
        self._store = file_store
        self._settings_getter = settings_getter
        self._subtitle_encoder = subtitle_encoder
        self._current_space = current_space


    def _profile_state(self, asset):
        space = self._current_space
        getter = getattr(self._metadata, "get_asset_index_profile", None) if self._metadata is not None else None
        if space is None or not callable(getter):
            return None
        return getter(
            asset.id,
            profile=space.profile,
            model_id=space.model_id,
            model_version=space.model_version,
            dim=int(space.dim),
            preprocess_version=space.preprocess_version,
        )

    def _visibility_kwargs(self, asset) -> dict:
        space = self._current_space
        if space is None:
            return {}
        return {"profile_state": self._profile_state(asset), "require_profile": True}

    def _indexed_content_version(self, asset) -> int:
        state = self._profile_state(asset) if self._current_space is not None else None
        if state is not None:
            return int(state.get("indexed_content_version") or 0)
        return int(getattr(asset, "ready_version", 0) or 0)

    def get_settings(self) -> SearchSettings:
        if callable(self._settings_getter):
            return self._settings_getter()
        return SearchSettings()

    def search(
        self,
        query_text: str | None,
        has_image: bool = False,
        image_bytes: bytes | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._jobs is not None:
            self._jobs.set_query_priority(True)
        try:
            text = (query_text or "").strip()
            if has_image and not image_bytes:
                raise AppError("invalid", "图片查询需要随请求上传查询图片，当前未附带图片，不会用文字冒充画面检索。")
            if not text and not image_bytes:
                raise AppError("invalid", "请输入文字，或提供图片。")
            reasons = []
            if image_bytes or text:
                if not self._encoder.available():
                    reasons.append(self._encoder.status_message())
            if not self._index.available():
                reasons.append(self._index.status_message())
            if reasons:
                raise NotConfiguredError(
                    "搜索暂不可用，不会返回示例或假结果。" + " ".join(reasons),
                    code="search_unconfigured",
                )
            if self._metadata is None:
                raise NotConfiguredError("元数据仓储尚未装配，拒绝检索。", code="search_unconfigured")

            settings = self._merge_settings(overrides)
            assets = {a.id: a for a in self._metadata.list_assets()}
            # 画面查询：仅 frame 路；文字：frame + subtitle，画面优先
            visual_hits: list[FrameHit] = []
            subtitle_hits: list[FrameHit] = []
            if image_bytes:
                qvec = self._encoder.encode_image(image_bytes)
                visual_hits = self._recall("frame", qvec, settings, assets)
            else:
                encode_task = getattr(self._encoder, "encode_text_for_task", None)
                qvec = encode_task(text, role="query") if callable(encode_task) else self._encoder.encode_text(text)
                sub_enc = self._subtitle_encoder
                if sub_enc is not None and sub_enc.available():
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        f_visual = pool.submit(self._recall, "frame", qvec, settings, assets)
                        svec = sub_enc.encode_text(text)
                        f_sub = pool.submit(self._recall, "subtitle", svec, settings, assets)
                        visual_hits = f_visual.result()
                        subtitle_hits = f_sub.result()
                else:
                    visual_hits = self._recall("frame", qvec, settings, assets)

            excerpts = self._excerpts(subtitle_hits)
            for asset in assets.values():
                frames = self._metadata.list_frames(asset.id) if hasattr(self._metadata, "list_frames") else []
                kept = [f for f in frames if f.get("kept") and f.get("preview_key")]
                if kept:
                    setattr(asset, "preview_frames", kept)
            results = aggregate_hits(visual_hits, subtitle_hits, assets, settings, excerpts)
            items = [self._with_provenance(item, assets[item.asset_id]) for item in results]
            return {
                "items": items,
                "metric": settings.metric,
                "score_meaning": SCORE_MEANING,
                "reranker": False,
                "settings": asdict(settings),
                "notes": [
                    "画面分取同视频最大帧分；异空间分数不会直接相加。",
                    "默认融合为画面优先；RRF 为未校准候选，需在设置中显式选择。85%/15% 加权未确认，未写死。",
                    "计数默认按资产（图片或视频）计入同一 TopN，此为待确认默认；接口同时支持 counting_mode=video。",
                    "相关阈值默认为关闭；TopN 是上限，不会为凑满而塞入未过阈值的结果，也不会未验证就用 0.8。",
                    "余弦相似度不是正确概率，不会显示成正确率百分比。",
                    "单视频最多 5 帧预览，不足不补。无 reranker。纯图片不因没有字幕扣分。",
                    "源文件可用状态来自最新业务记录，不是向量化时的旧快照。",
                ],
                "connections": {
                    "encoder": getattr(self._encoder, "public_status", lambda: {"status": "ok" if self._encoder.available() else "unconfigured", "message": self._encoder.status_message()})(),
                    "subtitle_encoder": (
                        getattr(self._subtitle_encoder, "public_status", lambda: {"status": "unconfigured"})()
                        if self._subtitle_encoder
                        else {"status": "unconfigured", "message": "未装配字幕编码器"}
                    ),
                    "milvus": getattr(self._index, "public_status", lambda: {"status": "ok" if self._index.available() else "unconfigured", "message": self._index.status_message()})(),
                },
            }
        finally:
            if self._jobs is not None:
                self._jobs.set_query_priority(False)

    def _with_provenance(self, item: AssetResult, asset) -> dict:
        data = asdict(item)
        lifecycle = getattr(asset, "lifecycle", "active")
        source_deleted = bool(getattr(asset, "source_deleted", False))
        available = (not source_deleted) and lifecycle not in {"missing", "source_invalid", "excluded"}
        data["source_available"] = available
        data["source_availability"] = {
            True: "available",
            False: "unavailable",
        }[available]
        data["origin"] = getattr(asset, "origin", "external")
        data["display_name"] = getattr(asset, "display_name", None) or asset.object_key
        data["description"] = getattr(asset, "description", "") or ""
        ext_ref = getattr(asset, "external_ref", None)
        data["external_ref"] = ext_ref
        source_url = None
        if isinstance(ext_ref, dict):
            source_url = ext_ref.get("source_url") or ext_ref.get("locator")
        data["source_url"] = source_url
        data["cover_key"] = getattr(asset, "cover_key", None)
        lister = getattr(self._metadata, "list_tags", None) if self._metadata is not None else None
        data["tags"] = list(lister(asset.id)) if callable(lister) else []
        if getattr(asset, "cover_key", None) or data.get("previews"):
            ver = str(getattr(asset, "cover_key", None) or "").rsplit("/", 1)[-1]
            data["video_cover_locator"] = f"/api/assets/{asset.id}/cover" + (f"?v={ver}" if ver else "")
        else:
            data["video_cover_locator"] = None
        profile = self._profile_state(asset) or {}
        space = self._current_space
        data["index_version"] = int(profile.get("indexed_content_version") or getattr(asset, "ready_version", 0) or 0)
        if space is not None:
            data["model_id"] = space.model_id
            data["model_version"] = space.model_version
            data["dim"] = int(space.dim)
            data["preprocess_version"] = space.preprocess_version
            data["profile"] = space.profile
            data["collection"] = collection_for(space, "frame")
        else:
            data["model_id"] = profile.get("model_id")
            data["model_version"] = profile.get("model_version")
            data["dim"] = profile.get("dim")
            data["preprocess_version"] = profile.get("preprocess_version")
            data["profile"] = profile.get("profile")
            data["collection"] = profile.get("collection")
        data["static_metadata_note"] = "入库静态元数据保留；源文件可用状态按当前业务记录计算。"
        return data

    def preview_bytes(self, asset_id: str, kind: str, unit_index: int) -> tuple[bytes, str]:
        if self._metadata is None or self._store is None:
            raise NotConfiguredError("预览接口尚未装配存储。", code="filestore_unconfigured")
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise NotFoundError("资源不存在")
        # 鉴权后续接管；此处只从平台存储读取已登记预览，不签发长期公开地址。
        if kind not in {"frames", "frame", "subtitles"}:
            raise AppError("invalid", "不支持的预览类型")
        frames = self._metadata.list_frames(asset_id)
        frame = next((f for f in frames if int(f.get("index") if f.get("index") is not None else f.get("frame_index") or -1) == int(unit_index)), None)
        if frame is None or not frame.get("preview_key"):
            raise NotFoundError("没有对应的画面预览")
        data = self._store.get_bytes(frame["preview_key"])
        return data, "image/jpeg"

    def cover_bytes(self, asset_id: str) -> tuple[bytes, str]:
        if self._metadata is None or self._store is None:
            raise NotConfiguredError("封面接口尚未装配存储。", code="filestore_unconfigured")
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise NotFoundError("资源不存在")
        cover_key = getattr(asset, "cover_key", None)
        if cover_key:
            try:
                return self._store.get_bytes(cover_key), "image/jpeg"
            except Exception:
                pass
        frames = self._metadata.list_frames(asset_id)
        kept = next((f for f in frames if f.get("kept") and f.get("preview_key")), None)
        if kept and kept.get("preview_key"):
            return self._store.get_bytes(kept["preview_key"]), "image/jpeg"
        raise NotFoundError("没有对应的视频/图片封面")

    def _merge_settings(self, overrides: dict[str, Any] | None) -> SearchSettings:
        base = asdict(self.get_settings())
        allowed = set(base)
        for key, value in (overrides or {}).items():
            if key in allowed and value is not None:
                base[key] = value
        settings = SearchSettings(**base)
        if int(settings.top_n) < 1:
            raise AppError("invalid", "TopN 至少为 1")
        if int(settings.max_previews_per_video) < 0 or int(settings.max_previews_per_video) > 5:
            raise AppError("invalid", "单视频预览帧数须在 0–5 之间，不足不补")
        if settings.counting_mode not in {"asset", "video"}:
            raise AppError("invalid", "计数模式只能是 asset（待确认默认）或 video")
        if settings.fusion not in {"visual_first", "rrf"}:
            raise AppError("invalid", "融合方式只能是 visual_first 或 rrf（候选）")
        if settings.subtitle_agg not in {"max", "sum"}:
            raise AppError("invalid", "字幕聚合只能是 max 或 sum（候选）")
        if int(settings.oversample_factor) < 1 or int(settings.max_scan) < int(settings.top_n):
            raise AppError("invalid", "过采样因子须为正，扫描上限不得小于 TopN")
        return settings

    def _recall(self, modality: str, vector: list[float], settings: SearchSettings, assets_by_id: dict) -> list[FrameHit]:
        hits: list[FrameHit] = []
        offset = 0
        passing: set[str] = set()
        scanned = 0
        target = settings.top_n * 2 if settings.counting_mode == "asset" else settings.top_n
        while scanned < settings.max_scan and len(passing) < target:
            take, _cap = batch_limits(settings, len(passing), target=target)
            limit = min(take, settings.max_scan - scanned)
            if limit <= 0:
                break
            raw = self._index.search(modality, vector, limit=limit, offset=offset)
            if not raw:
                break
            scanned += len(raw)
            offset += len(raw)
            for row in raw:
                asset = assets_by_id.get(row.get("asset_id"))
                if asset is None:
                    continue
                vis = vector_visibility(asset, **self._visibility_kwargs(asset))
                if vis in {"hidden", "not_indexed"}:
                    continue
                expected_version = self._indexed_content_version(asset)
                if int(row.get("version") or 0) != int(expected_version or 0):
                    continue
                hits.append(
                    FrameHit(
                        asset_id=asset.id,
                        version=int(row.get("version") or 0),
                        modality=modality,
                        unit_index=int(row.get("unit_index") or 0),
                        time_seconds=row.get("time_seconds"),
                        preview_key=row.get("preview_key") or None,
                        score=float(row.get("score") or 0),
                        vector_id=row.get("id"),
                    )
                )
                passing.add(asset.id)
            if len(raw) < limit:
                break
        return hits

    def _excerpts(self, hits: list[FrameHit]) -> dict[str, str]:
        out: dict[str, str] = {}
        if self._metadata is None or not hits:
            return out
        getter = getattr(self._metadata, "list_groups", None)
        list_assoc = getattr(self._metadata, "list_associations", None)
        if not callable(list_assoc):
            return out
        for hit in hits:
            assocs = list_assoc(hit.asset_id)
            for assoc in assocs:
                groups = getter(assoc["id"]) if callable(getter) else []
                for group in groups:
                    idx = int(group.get("group_index") or group.get("unit_index") or 0)
                    text = group.get("text") or ""
                    if idx == hit.unit_index and text:
                        out[hit.asset_id] = text
                        out[f"{hit.asset_id}:{idx}"] = text
        return out
