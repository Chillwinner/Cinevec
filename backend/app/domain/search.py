from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CountingMode = Literal["asset", "video"]
FusionMode = Literal["visual_first", "rrf"]
SubtitleAgg = Literal["max", "sum"]
Visibility = Literal["hidden", "searchable", "searchable_invalid", "not_indexed"]

SCORE_MEANING = "cosine 相似度（同空间，越大越近），不是正确概率，不会映射到 0–100%。"


@dataclass
class SearchSettings:
    """检索参数。已确认：默认 Top 20、单视频最多 5 帧预览且不足不补、无 reranker、文字查询画面优先。其余为候选。"""

    top_n: int = 20
    max_previews_per_video: int = 5
    # 待确认默认：图片与视频统一按资产计数进入同一 TopN，不另开两套排行。
    # 同时保留 counting_mode=video，以支持此前已讨论的「Top 20 个视频」。
    counting_mode: CountingMode = "asset"
    fusion: FusionMode = "visual_first"
    subtitle_agg: SubtitleAgg = "max"
    rrf_k: int = 60
    score_threshold: float | None = None
    oversample_factor: int = 4
    max_scan: int = 400
    metric: str = "cosine"


def vector_visibility(
    asset,
    *,
    has_ready_vectors: bool | None = None,
    profile_state: dict | None = None,
    require_profile: bool = False,
) -> Visibility:
    """内容更新：旧向量隐藏直至新版本向量写完并激活。确认缺失/来源配置停用：旧有效向量仍可查，并标失效。排除：不参与。

    传入 require_profile / profile_state 时，以模型空间关联为准：资产级 ready_version
    只是内容版本，不能当作新 profile 已索引。
    """
    if getattr(asset, "excluded", False):
        return "hidden"
    ready = int(getattr(asset, "ready_version", 0) or 0)
    version = int(getattr(asset, "version", 1) or 1)
    state = getattr(asset, "index_state", "none") or "none"
    lifecycle = getattr(asset, "lifecycle", "active") or "active"
    if require_profile or profile_state is not None:
        indexed_ver = int((profile_state or {}).get("indexed_content_version") or 0)
        profile_ready = bool(profile_state) and (profile_state or {}).get("index_state") == "ready" and indexed_ver > 0
        indexed = bool(has_ready_vectors) if has_ready_vectors is not None else profile_ready
        if lifecycle in {"missing", "source_invalid"}:
            return "searchable_invalid" if indexed and indexed_ver > 0 else "not_indexed"
        if indexed and indexed_ver == version and (profile_state or {}).get("index_state") == "ready":
            return "searchable"
        if indexed and indexed_ver != version:
            return "hidden"
        return "not_indexed"
    indexed = bool(has_ready_vectors) if has_ready_vectors is not None else (state == "ready" and ready > 0)

    # 文件内容已更新、新版本尚未激活：旧向量不参与查询。
    pending_new = lifecycle == "pending_update" or state == "inactive" or version > ready
    if pending_new and lifecycle not in {"missing", "source_invalid"}:
        return "hidden" if indexed or ready > 0 or state == "inactive" else "not_indexed"

    if lifecycle in {"missing", "source_invalid"}:
        return "searchable_invalid" if indexed else "not_indexed"

    if indexed and state == "ready":
        return "searchable"
    return "not_indexed"


@dataclass
class FrameHit:
    asset_id: str
    version: int
    modality: str
    unit_index: int
    time_seconds: float | None
    preview_key: str | None
    score: float
    vector_id: str | None = None


@dataclass
class AssetResult:
    asset_id: str
    kind: str
    object_key: str
    lifecycle: str
    lifecycle_label: str
    visibility: str
    score: float
    metric: str
    score_meaning: str
    hit_source: str
    previews: list[dict] = field(default_factory=list)
    subtitle_excerpt: str | None = None
    time_seconds: float | None = None
    hit_method: str | None = None
    source_available: bool | None = None
    source_availability: str | None = None
    index_version: int | None = None
    model_id: str | None = None
    model_version: str | None = None
    dim: int | None = None
    preprocess_version: str | None = None
    collection: str | None = None
    profile: str | None = None
    subtitle_hits: list[dict] = field(default_factory=list)
