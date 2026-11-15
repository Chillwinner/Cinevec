from __future__ import annotations

from collections import defaultdict

from app.domain.search import (
    SCORE_MEANING,
    AssetResult,
    FrameHit,
    SearchSettings,
    vector_visibility,
)

LIFECYCLE_LABELS = {
    "active": "有效",
    "pending_update": "待更新",
    "stale": "过期",
    "missing": "来源中未再发现（仍可查，已标失效）",
    "source_invalid": "来源已失效",
    "excluded": "已排除",
}


def preview_locator(asset_id: str, unit_index: int, modality: str = "frame") -> str:
    """后端授权预览路径，不暴露对象存储桶的长期公开地址。"""
    kind = "frames" if modality == "frame" else "subtitles"
    return f"/api/previews/{asset_id}/{kind}/{unit_index}"


def counts_toward_topn(kind: str, settings: SearchSettings) -> bool:
    if settings.counting_mode == "video":
        return kind == "video"
    return kind in {"video", "image"}


def batch_limits(settings: SearchSettings, already: int, *, target: int | None = None) -> tuple[int, int]:
    """返回 (本次取回上限, 扫描上限)。过采样避免单视频占满候选，但不超过 max_scan。"""
    remaining = max((target if target is not None else settings.top_n) - already, 1)
    raw = remaining * max(settings.max_previews_per_video, 1) * max(settings.oversample_factor, 1)
    take = min(max(raw, settings.top_n), max(settings.max_scan, settings.top_n))
    return take, settings.max_scan


def _passes_threshold(score: float, settings: SearchSettings) -> bool:
    if settings.score_threshold is None:
        return True
    return score >= settings.score_threshold


def aggregate_hits(
    visual_hits: list[FrameHit],
    subtitle_hits: list[FrameHit],
    assets_by_id: dict,
    settings: SearchSettings,
    excerpts: dict[str, str] | None = None,
) -> list[AssetResult]:
    """按资产聚合两路召回。异空间分数不直接相加；默认画面优先，RRF 为未校准候选。"""
    excerpts = excerpts or {}
    visual_by: dict[str, list[FrameHit]] = defaultdict(list)
    sub_by: dict[str, list[FrameHit]] = defaultdict(list)
    for hit in visual_hits:
        if _passes_threshold(hit.score, settings):
            visual_by[hit.asset_id].append(hit)
    for hit in subtitle_hits:
        if _passes_threshold(hit.score, settings):
            sub_by[hit.asset_id].append(hit)

    visual_score = {aid: max(h.score for h in hits) for aid, hits in visual_by.items()}
    if settings.subtitle_agg == "sum":
        sub_score = {aid: sum(h.score for h in hits) for aid, hits in sub_by.items()}
    else:
        sub_score = {aid: max(h.score for h in hits) for aid, hits in sub_by.items()}

    # ranks for optional RRF (candidate, not default score add)
    def _ranks(score_map: dict[str, float]) -> dict[str, int]:
        ordered = sorted(score_map, key=lambda k: score_map[k], reverse=True)
        return {aid: i + 1 for i, aid in enumerate(ordered)}

    v_rank, s_rank = _ranks(visual_score), _ranks(sub_score)
    ids = set(visual_by) | set(sub_by)
    scored: list[tuple[float, str, str]] = []  # score, id, source for sort
    for aid in ids:
        asset = assets_by_id.get(aid)
        if asset is None:
            continue
        vis = vector_visibility(asset)
        if vis in {"hidden", "not_indexed"}:
            continue
        has_v, has_s = aid in visual_score, aid in sub_score
        if settings.fusion == "rrf":
            score = 0.0
            if has_v:
                score += 1.0 / (settings.rrf_k + v_rank[aid])
            if has_s:
                score += 1.0 / (settings.rrf_k + s_rank[aid])
            source = "mixed" if has_v and has_s else ("visual" if has_v else "subtitle")
        else:
            # visual_first: 画面分决定排序；仅字幕命中也可进入，使用字幕分，不与画面分相加。
            if has_v:
                score = visual_score[aid]
                source = "mixed" if has_s else "visual"
            elif has_s:
                score = sub_score[aid]
                source = "subtitle"
            else:
                continue
        scored.append((score, aid, source))

    # 画面优先：先按是否有画面命中，再按分数
    if settings.fusion == "visual_first":
        scored.sort(key=lambda row: (0 if row[2] in {"visual", "mixed"} else 1, -row[0]))
    else:
        scored.sort(key=lambda row: -row[0])

    images: list[AssetResult] = []
    videos: list[AssetResult] = []
    for score, aid, source in scored:
        asset = assets_by_id[aid]
        kind = getattr(asset, "kind", "other")
        if settings.counting_mode == "video" and kind == "image":
            # video 模式下图片不占用「20 个视频」名额，但接口仍可单独带回？本实现按设置不计 TopN。
            # 为避免悄悄丢图，仍附在结果里由调用方决定；默认 asset 模式会计入。
            pass
        if not counts_toward_topn(kind, settings):
            continue
        bucket = images if kind == "image" else videos
        if len(bucket) >= settings.top_n:
            continue
        vis = vector_visibility(asset)
        frames = sorted(visual_by.get(aid, []), key=lambda h: h.score, reverse=True)
        previews = []
        for hit in frames[: settings.max_previews_per_video]:
            previews.append(
                {
                    "frame_index": hit.unit_index,
                    "time_seconds": hit.time_seconds,
                    "preview_key": hit.preview_key,
                    "locator": preview_locator(aid, hit.unit_index, "frame"),
                    "score": hit.score,
                }
            )
        excerpt = excerpts.get(aid)
        if source == "subtitle" and not previews:
            near = _nearest_preview(asset, sub_by.get(aid, []), assets_by_id)
            if near:
                previews.append(near)
        if source == "subtitle" and not excerpt:
            best = max(sub_by[aid], key=lambda h: h.score)
            excerpt = excerpts.get(f"{aid}:{best.unit_index}") or excerpt
        label = LIFECYCLE_LABELS.get(getattr(asset, "lifecycle", ""), getattr(asset, "lifecycle", ""))
        if vis == "searchable_invalid" and getattr(asset, "lifecycle", "") == "missing":
            label = "来源中未再发现（仍可查，已标失效）"
        sub_hits = []
        for hit in sorted(sub_by.get(aid, []), key=lambda h: h.score, reverse=True)[:3]:
            sub_hits.append(
                {
                    "unit_index": hit.unit_index,
                    "time_seconds": hit.time_seconds,
                    "score": hit.score,
                    "excerpt": excerpts.get(f"{aid}:{hit.unit_index}") or excerpts.get(aid),
                }
            )
        hit_method = {
            "visual": "visual_only",
            "subtitle": "subtitle_only",
            "mixed": "visual_and_subtitle",
        }.get(source, source)
        bucket.append(
            AssetResult(
                asset_id=aid,
                kind=kind,
                object_key=getattr(asset, "object_key", ""),
                lifecycle=getattr(asset, "lifecycle", "active"),
                lifecycle_label=label,
                visibility=vis,
                score=score,
                metric=settings.metric,
                score_meaning=SCORE_MEANING,
                hit_source=source,
                hit_method=hit_method,
                previews=previews,
                subtitle_excerpt=excerpt,
                subtitle_hits=sub_hits,
                time_seconds=(previews[0]["time_seconds"] if previews else None),
            )
        )
        if len(images) >= settings.top_n and len(videos) >= settings.top_n:
            break
    return images + videos


def _nearest_preview(asset, sub_hits: list[FrameHit], assets_by_id: dict) -> dict | None:
    """仅字幕命中时，选择时间最近的已登记画面预览，不凑数。"""
    frames = []
    getter = getattr(assets_by_id.get(asset.id) if not isinstance(asset, dict) else None, "id", None)
    # frames come from a side channel if attached
    extra = getattr(asset, "preview_frames", None) or []
    if not extra or not sub_hits:
        return None
    t = sub_hits[0].time_seconds or 0.0
    best = min(extra, key=lambda f: abs((f.get("time_seconds") or 0) - t))
    return {
        "frame_index": best.get("index") or best.get("frame_index"),
        "time_seconds": best.get("time_seconds"),
        "preview_key": best.get("preview_key"),
        "locator": preview_locator(asset.id, int(best.get("index") or best.get("frame_index") or 0), "frame"),
        "score": None,
        "near_subtitle": True,
    }
