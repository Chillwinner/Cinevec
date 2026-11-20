from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from app.domain.errors import AppError
from app.domain.face import FaceSettings
from app.domain.lifecycle import LifecycleSettings, RefreshSettings
from app.domain.media import SampleSettings
from app.domain.search import SearchSettings, SCORE_MEANING
from app.domain.ports import FileStore, MetadataRepository
from app.domain.subtitle import MergeSettings
from app.domain.model_profile import catalog_public_view, normalize_family
from app.infra.config import Settings
from app.infra.ffmpeg_tools import ffmpeg_status
from app.infra.model_space import collection_for, space_from_settings

SAMPLE_KEY = "sample"
MERGE_KEY = "subtitle_merge"
REFRESH_KEY = "refresh"
LIFECYCLE_KEY = "lifecycle"
SEARCH_KEY = "search"
UPLOAD_KEY = "upload"
FACE_KEY = "face"


class SettingsService:
    def __init__(
        self,
        metadata: MetadataRepository,
        settings: Settings,
        file_store: FileStore,
    ) -> None:
        self._metadata = metadata
        self._settings = settings
        self._file_store = file_store

    def _load(self, key: str) -> dict[str, Any]:
        getter = getattr(self._metadata, "get_setting", None)
        if callable(getter):
            return getter(key) or {}
        return {}

    def _save(self, key: str, value: dict[str, Any]) -> None:
        setter = getattr(self._metadata, "set_setting", None)
        if callable(setter):
            setter(key, value)

    def get_sample(self) -> SampleSettings:
        raw = self._load(SAMPLE_KEY)
        base = SampleSettings()
        base.interval_seconds = float(self._settings.sample_interval_seconds or 5.0)
        allowed = {f.name for f in fields(SampleSettings)}
        updates = {k: raw[k] for k in raw if k in allowed}
        if "interval_seconds" not in updates:
            updates["interval_seconds"] = base.interval_seconds
        return SampleSettings(**{**asdict(base), **updates})

    def update_sample(self, payload: dict[str, Any]) -> SampleSettings:
        current = asdict(self.get_sample())
        allowed = {f.name for f in fields(SampleSettings)}
        for key, value in payload.items():
            if key not in allowed:
                raise AppError("invalid", f"不支持的采样设置项：{key}")
            current[key] = value
        sample = SampleSettings(**current)
        if sample.mode not in {"interval_seconds", "uniform_total", "frames_per_minute"}:
            raise AppError("invalid", "采样模式只能是按间隔、按总帧数或每分钟帧数")
        if sample.interval_seconds <= 0:
            raise AppError("invalid", "采样间隔必须大于 0 秒")
        if int(sample.uniform_total) < 1:
            raise AppError("invalid", "均匀总帧数至少为 1")
        if sample.frames_per_minute <= 0:
            raise AppError("invalid", "每分钟帧数必须大于 0")
        if int(sample.model_input_size) < 1 or int(sample.preview_max_edge) < 1:
            raise AppError("invalid", "预览边长与模型输入尺寸必须为正整数")
        self._save(SAMPLE_KEY, asdict(sample))
        return sample

    def get_merge(self) -> MergeSettings:
        raw = self._load(MERGE_KEY)
        allowed = {f.name for f in fields(MergeSettings)}
        base = asdict(MergeSettings())
        base.update({k: raw[k] for k in raw if k in allowed})
        return MergeSettings(**base)

    def update_merge(self, payload: dict[str, Any]) -> MergeSettings:
        current = asdict(self.get_merge())
        allowed = {f.name for f in fields(MergeSettings)}
        for key, value in payload.items():
            if key not in allowed:
                raise AppError("invalid", f"不支持的字幕合并设置项：{key}")
            current[key] = value
        settings = MergeSettings(**current)
        if settings.max_gap_seconds < 0 or settings.max_group_duration_seconds <= 0 or settings.max_group_chars < 1:
            raise AppError("invalid", "合并阈值必须为非负，且时长与字数上限为正")
        self._save(MERGE_KEY, asdict(settings))
        return settings

    def get_refresh(self) -> RefreshSettings:
        raw = self._load(REFRESH_KEY)
        allowed = {f.name for f in fields(RefreshSettings)}
        base = asdict(RefreshSettings())
        base.update({k: raw[k] for k in raw if k in allowed})
        return RefreshSettings(**base)

    def update_refresh(self, payload: dict[str, Any]) -> RefreshSettings:
        current = asdict(self.get_refresh())
        allowed = {f.name for f in fields(RefreshSettings)}
        for key, value in payload.items():
            if key not in allowed:
                raise AppError("invalid", f"不支持的刷新设置项：{key}")
            current[key] = value
        settings = RefreshSettings(**current)
        settings.enabled = bool(settings.enabled)
        if int(settings.interval_minutes) < 1:
            raise AppError("invalid", "刷新间隔至少为 1 分钟")
        self._save(REFRESH_KEY, asdict(settings))
        return settings

    def save_refresh(self, settings: RefreshSettings) -> None:
        self._save(REFRESH_KEY, asdict(settings))

    def get_lifecycle(self) -> LifecycleSettings:
        raw = self._load(LIFECYCLE_KEY)
        allowed = {f.name for f in fields(LifecycleSettings)}
        base = asdict(LifecycleSettings())
        base.update({k: raw[k] for k in raw if k in allowed})
        return LifecycleSettings(**base)

    def update_lifecycle(self, payload: dict[str, Any]) -> LifecycleSettings:
        current = asdict(self.get_lifecycle())
        allowed = {f.name for f in fields(LifecycleSettings)}
        for key, value in payload.items():
            if key not in allowed:
                raise AppError("invalid", f"不支持的生命周期设置项：{key}")
            current[key] = value
        settings = LifecycleSettings(**current)
        settings.preview_cleanup_on_exclude = bool(settings.preview_cleanup_on_exclude)
        self._save(LIFECYCLE_KEY, asdict(settings))
        return settings

    def get_search(self) -> SearchSettings:
        raw = self._load(SEARCH_KEY)
        allowed = {f.name for f in fields(SearchSettings)}
        base = asdict(SearchSettings())
        base.update({k: raw[k] for k in raw if k in allowed})
        return SearchSettings(**base)

    def update_search(self, payload: dict[str, Any]) -> SearchSettings:
        current = asdict(self.get_search())
        allowed = {f.name for f in fields(SearchSettings)}
        for key, value in payload.items():
            if key not in allowed:
                raise AppError("invalid", f"不支持的检索设置项：{key}")
            current[key] = value
        settings = SearchSettings(**current)
        if int(settings.top_n) < 1:
            raise AppError("invalid", "TopN 至少为 1")
        if not (0 <= int(settings.max_previews_per_video) <= 5):
            raise AppError("invalid", "单视频预览帧数须在 0–5 之间")
        if settings.counting_mode not in {"asset", "video"}:
            raise AppError("invalid", "计数模式只能是 asset（待确认默认）或 video")
        if settings.fusion not in {"visual_first", "rrf"}:
            raise AppError("invalid", "融合方式只能是 visual_first 或 rrf（候选）")
        if settings.subtitle_agg not in {"max", "sum"}:
            raise AppError("invalid", "字幕聚合只能是 max 或 sum（候选）")
        if int(settings.oversample_factor) < 1 or int(settings.max_scan) < int(settings.top_n):
            raise AppError("invalid", "过采样因子须为正，扫描上限不得小于 TopN")
        self._save(SEARCH_KEY, asdict(settings))
        return settings

    def get_upload(self) -> dict[str, Any]:
        raw = self._load(UPLOAD_KEY)
        default_limit = int(getattr(self._settings, "max_upload_files", 20) or 20)
        value = raw.get("max_upload_files", default_limit)
        try:
            limit = int(value)
        except (TypeError, ValueError):
            limit = default_limit
        return {"max_upload_files": max(1, limit)}

    def update_upload(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_upload()
        for key, value in payload.items():
            if key != "max_upload_files":
                raise AppError("invalid", f"不支持的传输设置项：{key}")
            try:
                num = int(value)
            except (TypeError, ValueError) as exc:
                raise AppError("invalid", "单次最大传输文件数必须是整数") from exc
            if num < 1 or num > 500:
                raise AppError("invalid", "单次最大传输文件数须在 1–500 之间")
            current["max_upload_files"] = num
        self._save(UPLOAD_KEY, current)
        return current

    def get_face(self) -> FaceSettings:
        raw = self._load(FACE_KEY)
        base = FaceSettings(
            enabled=bool(getattr(self._settings, "face_enabled", False)),
            cluster_threshold=float(getattr(self._settings, "face_cluster_threshold", 0.45) or 0.45),
            det_threshold=float(getattr(self._settings, "face_det_threshold", 0.55) or 0.55),
            min_face_px=int(getattr(self._settings, "face_min_size_px", 24) or 24),
            sync_person_tags=True,
        )
        allowed = {f.name for f in fields(FaceSettings)}
        updates = {k: raw[k] for k in raw if k in allowed}
        return FaceSettings(**{**asdict(base), **updates})

    def update_face(self, payload: dict[str, Any]) -> FaceSettings:
        current = asdict(self.get_face())
        allowed = {f.name for f in fields(FaceSettings)}
        for key, value in payload.items():
            if key not in allowed:
                raise AppError("invalid", f"不支持的人脸设置项：{key}")
            current[key] = value
        settings = FaceSettings(**current)
        settings.enabled = bool(settings.enabled)
        settings.sync_person_tags = bool(settings.sync_person_tags)
        if not (0.15 <= float(settings.cluster_threshold) <= 0.95):
            raise AppError("invalid", "人脸聚类阈值须在 0.15–0.95 之间")
        if not (0.1 <= float(settings.det_threshold) <= 0.99):
            raise AppError("invalid", "人脸检测置信度须在 0.10–0.99 之间")
        if int(settings.min_face_px) < 8:
            raise AppError("invalid", "最小人脸像素须 >= 8")
        self._save(FACE_KEY, asdict(settings))
        return settings

    def update_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        known = {"sample", "subtitle_merge", "refresh", "lifecycle", "search", "upload", "max_upload_files", "face"}
        sample_fields = {f.name for f in fields(SampleSettings)}
        # 兼容第一、二阶段只传扁平采样字段
        flat = {k: v for k, v in payload.items() if k in sample_fields}
        if flat:
            self.update_sample(flat)
        if "sample" in payload and isinstance(payload["sample"], dict):
            self.update_sample(payload["sample"])
        if "subtitle_merge" in payload and isinstance(payload["subtitle_merge"], dict):
            self.update_merge(payload["subtitle_merge"])
        if "refresh" in payload and isinstance(payload["refresh"], dict):
            self.update_refresh(payload["refresh"])
        if "lifecycle" in payload and isinstance(payload["lifecycle"], dict):
            self.update_lifecycle(payload["lifecycle"])
        if "search" in payload and isinstance(payload["search"], dict):
            self.update_search(payload["search"])
        if "upload" in payload and isinstance(payload["upload"], dict):
            self.update_upload(payload["upload"])
        if "face" in payload and isinstance(payload["face"], dict):
            self.update_face(payload["face"])
        if "max_upload_files" in payload:
            self.update_upload({"max_upload_files": payload["max_upload_files"]})
        unknown = [k for k in payload if k not in known and k not in sample_fields]
        if unknown:
            raise AppError("invalid", f"不支持的设置项：{', '.join(unknown)}")
        return self.public_view()

    def public_view(self) -> dict[str, Any]:
        from dataclasses import asdict as _asdict

        sample = _asdict(self.get_sample())
        merge = _asdict(self.get_merge())
        refresh = _asdict(self.get_refresh())
        life = _asdict(self.get_lifecycle())
        search = _asdict(self.get_search())
        upload = self.get_upload()
        face = _asdict(self.get_face())
        ff = ffmpeg_status(self._settings)
        store = self._file_store
        return {
            "sample": sample,
            "subtitle_merge": merge,
            "refresh": refresh,
            "lifecycle": life,
            "search": search,
            "upload": upload,
            "face": face,
            "search_notes": {
                "score_meaning": SCORE_MEANING,
                "counting_mode": "默认 asset（图片或视频统一按资产计入同一 TopN）为待确认默认；接口保留 video 以支持此前讨论的 Top 20 个视频，未硬改该约定。",
                "fusion": "默认 visual_first（画面优先）。rrf 为未校准候选，不会把异空间 raw score 直接相加。",
                "subtitle_agg": "字幕分聚合 max/sum 均为候选。",
                "threshold": "相关阈值可选，默认关闭。未验证前不会写成 0.8。TopN 是上限，不会为凑满而塞入未过阈值的结果。",
                "fusion_weights": "画面/字幕加权公式与 85%/15% 尚未确认，未写死。默认 visual_first（画面优先，异空间原始分不相加）。",
                "reranker": False,
                "max_previews_per_video": 5,
            },
            "provisional_note": "黑白/模糊过滤、连续相似去重、预览尺寸以及相邻字幕合并规则均为暂定实现值，产品尚未拍板。",
            "undecided_note": "定时刷新默认关闭；刷新频率为未定候选默认。排除后会先删向量再清理平台预览/抽帧/托管原片，网盘原文件仍不删。图视频混合 TopN 计数默认按资产，仍为待确认。",
            "ffmpeg": ff,
            "file_store": {
                "status": "ok" if store.available() else "unconfigured",
                "message": store.status_message(),
                "kind": type(store).__name__,
            },
            "model": self._model_view(),
        }

    def _model_view(self) -> dict[str, Any]:
        settings = self._settings
        space = space_from_settings(settings)
        family = normalize_family(settings.model_family)
        contract = (settings.encoder_contract_status or "").strip() or None
        prefix = (settings.task_prefix_applied_by or "").strip() or None
        collections = None
        if space is not None:
            collections = {
                "frame": collection_for(space, "frame"),
                "subtitle": collection_for(space, "subtitle"),
            }
        return {
            "catalog": catalog_public_view(
                active_family=family or (space.family if space else ""),
                active_space=space,
                contract_override=contract,
                prefix_override=prefix,
            ),
            "active_family": family or (space.family if space else ""),
            "active_space": {
                "profile": space.profile,
                "family": space.family,
                "model_id": space.model_id,
                "model_version": space.model_version,
                "dim": space.dim,
                "native_dim": space.native_dim,
                "preprocess_version": space.preprocess_version,
                "metric": space.metric,
            } if space else None,
            "collections": collections,
            "encoder_backend": settings.encoder_backend or "",
            "http_base_url_configured": bool((settings.clip_http_base_url or "").strip()),
            "http_base_url": (settings.clip_http_base_url or "").strip(),
            "contract_status": contract,
            "task_prefix_applied_by": prefix,
            "save_is_not_health": True,
            "auto_activated": False,
            "note": "模型为可选配置。保存设置不等于健康检查通过，也不会自动激活新模型或启动新索引。EmbeddingGemma 2 原生 768，图片完整上传，文字前缀由服务端添加。",
        }

