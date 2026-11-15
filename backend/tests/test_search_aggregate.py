from __future__ import annotations

from types import SimpleNamespace

from app.application.search_aggregate import aggregate_hits, batch_limits
from app.domain.search import FrameHit, SearchSettings, vector_visibility


def _asset(aid, kind="video", **kw):
    defaults = dict(
        id=aid, kind=kind, object_key=f"{aid}.mp4", lifecycle="active",
        excluded=False, index_state="ready", version=1, ready_version=1,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _hit(aid, score, idx=0, t=0.0, modality="frame", version=1):
    return FrameHit(aid, version, modality, idx, t, f"p/{aid}/{idx}.jpg", score, f"{aid}:{idx}")


def test_visibility_media_ready_is_not_indexed():
    asset = _asset("a", index_state="media_ready", ready_version=0, version=1, lifecycle="active")
    assert vector_visibility(asset) == "not_indexed"


def test_visibility_pending_update_hides_old_vectors():
    asset = _asset("a", lifecycle="pending_update", index_state="inactive", version=2, ready_version=1)
    assert vector_visibility(asset) == "hidden"


def test_visibility_missing_keeps_searchable_invalid():
    asset = _asset("a", lifecycle="missing", index_state="ready", version=1, ready_version=1)
    assert vector_visibility(asset) == "searchable_invalid"


def test_visibility_source_invalid_keeps_searchable_invalid():
    asset = _asset("a", lifecycle="source_invalid", index_state="ready", version=1, ready_version=1)
    assert vector_visibility(asset) == "searchable_invalid"


def test_visibility_excluded_hidden():
    asset = _asset("a", excluded=True, lifecycle="excluded")
    assert vector_visibility(asset) == "hidden"


def test_visual_max_and_does_not_add_subtitle_raw_scores():
    settings = SearchSettings(top_n=10, fusion="visual_first")
    assets = {"v1": _asset("v1")}
    visual = [_hit("v1", 0.4, 0), _hit("v1", 0.9, 1), _hit("v1", 0.2, 2)]
    subs = [_hit("v1", 0.8, 0, modality="subtitle")]
    results = aggregate_hits(visual, subs, assets, settings)
    assert len(results) == 1
    assert results[0].score == 0.9
    assert results[0].hit_source == "mixed"
    assert results[0].score != 0.9 + 0.8


def test_subtitle_only_enters_topn_after_visual():
    settings = SearchSettings(top_n=5, fusion="visual_first")
    assets = {"vis": _asset("vis"), "sub": _asset("sub")}
    visual = [_hit("vis", 0.3, 0)]
    subs = [_hit("sub", 0.99, 0, modality="subtitle")]
    results = aggregate_hits(visual, subs, assets, settings)
    assert [r.asset_id for r in results] == ["vis", "sub"]
    assert results[1].hit_source == "subtitle"
    assert results[1].score == 0.99


def test_pending_update_excluded_from_results_but_missing_included():
    settings = SearchSettings(top_n=10)
    assets = {
        "old": _asset("old", lifecycle="pending_update", index_state="inactive", version=2, ready_version=1),
        "gone": _asset("gone", lifecycle="missing", index_state="ready", version=1, ready_version=1),
        "ok": _asset("ok"),
    }
    visual = [_hit("old", 0.99), _hit("gone", 0.5), _hit("ok", 0.4)]
    results = aggregate_hits(visual, [], assets, settings)
    ids = [r.asset_id for r in results]
    assert "old" not in ids
    assert ids == ["gone", "ok"]
    assert results[0].visibility == "searchable_invalid"


def test_max_five_previews_no_padding():
    settings = SearchSettings(top_n=5, max_previews_per_video=5)
    assets = {"v": _asset("v")}
    visual = [_hit("v", 1 - i * 0.01, i) for i in range(8)]
    results = aggregate_hits(visual, [], assets, settings)
    assert len(results[0].previews) == 5
    # 不会凑到更多
    visual2 = [_hit("v", 0.9, 0), _hit("v", 0.8, 1)]
    results2 = aggregate_hits(visual2, [], assets, settings)
    assert len(results2[0].previews) == 2


def test_threshold_filters_low_scores():
    settings = SearchSettings(top_n=10, score_threshold=0.5)
    assets = {"a": _asset("a"), "b": _asset("b")}
    visual = [_hit("a", 0.8), _hit("b", 0.2)]
    results = aggregate_hits(visual, [], assets, settings)
    assert [r.asset_id for r in results] == ["a"]


def test_counting_mode_video_skips_images_asset_includes_both():
    assets = {"v": _asset("v", kind="video"), "i": _asset("i", kind="image", object_key="i.jpg")}
    visual = [_hit("v", 0.6), _hit("i", 0.9)]
    asset_mode = aggregate_hits(visual, [], assets, SearchSettings(top_n=20, counting_mode="asset"))
    video_mode = aggregate_hits(visual, [], assets, SearchSettings(top_n=20, counting_mode="video"))
    assert {r.asset_id for r in asset_mode} == {"v", "i"}
    assert [r.asset_id for r in video_mode] == ["v"]


def test_asset_mode_splits_image_and_video_topn():
    assets = {f"v{i}": _asset(f"v{i}", kind="video") for i in range(5)}
    assets.update({f"i{i}": _asset(f"i{i}", kind="image", object_key=f"i{i}.jpg") for i in range(5)})
    visual = [_hit(f"v{i}", 0.9 - i * 0.01) for i in range(5)] + [_hit(f"i{i}", 0.8 - i * 0.01) for i in range(5)]
    results = aggregate_hits(visual, [], assets, SearchSettings(top_n=2, counting_mode="asset"))
    kinds = [r.kind for r in results]
    assert kinds.count("video") == 2
    assert kinds.count("image") == 2


def test_topn_limit_and_batch_limits_oversample():
    settings = SearchSettings(top_n=20, max_previews_per_video=5, oversample_factor=4, max_scan=400)
    take, cap = batch_limits(settings, 0)
    assert take == 20 * 5 * 4
    assert cap == 400
    assets = {f"v{i}": _asset(f"v{i}") for i in range(30)}
    visual = [_hit(f"v{i}", 1 - i * 0.01) for i in range(30)]
    results = aggregate_hits(visual, [], assets, settings)
    assert len(results) == 20


def test_rrf_is_optional_and_not_raw_sum():
    settings = SearchSettings(top_n=5, fusion="rrf", rrf_k=60)
    assets = {"a": _asset("a"), "b": _asset("b")}
    visual = [_hit("a", 0.9), _hit("b", 0.8)]
    subs = [_hit("b", 0.99, modality="subtitle")]
    results = aggregate_hits(visual, subs, assets, settings)
    # b 两路召回 RRF 应高于仅画面的 a，且分数不是 raw 相加
    assert results[0].asset_id == "b"
    assert results[0].score != 0.8 + 0.99
    assert results[0].score < 1
