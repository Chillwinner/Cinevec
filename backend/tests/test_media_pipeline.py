from __future__ import annotations

import time
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.domain.errors import NotConfiguredError
from app.domain.media import SampleSettings, VideoInfo
from app.infra.cleaner import DefaultFrameCleaner
from app.infra.config import Settings
from app.infra.file_store import LocalFileStore, UnconfiguredFileStore
from app.infra.ffmpeg_tools import probe_video
from app.infra.previews import render_model_input, render_preview
from app.infra.redact import redact
from app.infra.sampler import build_sample_plan
from app.main import create_app
from tests.conftest import FakeReader
from tests.media_fixtures import (
    ffmpeg_or_skip,
    generate_color_video,
    generate_static_pattern_video,
    generate_testsrc_video,
    write_png_pattern,
)


def test_sample_plans_three_modes():
    info = VideoInfo(path="x.mp4", duration_seconds=16.0, fps=25, width=64, height=36, approx_frames=400)
    interval = build_sample_plan(info, SampleSettings(mode="interval_seconds", interval_seconds=5))
    assert [round(p.time_seconds, 2) for p in interval] == [0.0, 5.0, 10.0, 15.0]
    uniform = build_sample_plan(info, SampleSettings(mode="uniform_total", uniform_total=4))
    assert len(uniform) == 4
    assert uniform[0].time_seconds == 0.0
    assert uniform[-1].time_seconds < 16.0
    per_min = build_sample_plan(info, SampleSettings(mode="frames_per_minute", frames_per_minute=12))
    # 12 帧/分钟 = 每 5 秒一帧，16 秒应得到 0/5/10/15
    assert [round(p.time_seconds, 2) for p in per_min] == [0.0, 5.0, 10.0, 15.0]
    short = build_sample_plan(
        VideoInfo(path="s", duration_seconds=3.0, fps=25, width=8, height=8, approx_frames=75),
        SampleSettings(mode="interval_seconds", interval_seconds=5),
    )
    assert [p.time_seconds for p in short] == [0.0]


def test_cleaner_filters_and_consecutive_similar():
    cleaner = DefaultFrameCleaner()
    settings = SampleSettings()
    black = write_png_pattern(None, "black")
    white = write_png_pattern(None, "white")
    gray = write_png_pattern(None, "gray")
    a = write_png_pattern(None, "checker")
    b = write_png_pattern(None, "checker2")
    assert cleaner.inspect(black, settings, None)["drop_reason"] == "all_black"
    assert cleaner.inspect(white, settings, None)["drop_reason"] == "all_white"
    assert cleaner.inspect(gray, settings, None)["drop_reason"] == "severe_blur"
    first = cleaner.inspect(a, settings, None)
    assert first["kept"] is True
    dup = cleaner.inspect(a, settings, first["signature"])
    assert dup["drop_reason"] == "consecutive_similar"
    nxt = cleaner.inspect(b, settings, first["signature"])
    assert nxt["kept"] is True
    # 静态图不走视频清洗时，本测试只覆盖清洗器本身
    bad = cleaner.inspect(b"not-an-image", settings, None)
    assert bad["drop_reason"] == "decode_failed"


def test_preview_and_model_input_are_independent():
    settings = SampleSettings(preview_max_edge=720, preview_quality=85, model_input_size=224)
    source = Image.new("RGB", (1280, 720), (12, 80, 160))
    preview = render_preview(source, settings)
    model = render_model_input(source, settings)
    preview_img = Image.open(BytesIO(preview))
    model_img = Image.open(BytesIO(model))
    assert preview_img.format == "JPEG"
    assert max(preview_img.size) <= 720
    assert model_img.size == (224, 224)
    assert model_img.format == "PNG"
    # 模型输入不是从有损预览再生成：预览是 JPEG，模型是 224 PNG，且来源尺寸不同
    assert preview != model
    # 再从预览生成一份模型输入，应与直接从原图生成的不同（有损）
    from_preview = render_model_input(preview_img, settings)
    # 不强制像素必不同（纯色图可能相同），但编码格式/路径已分离；用非纯色再断言
    busy = Image.new("RGB", (900, 500), (0, 0, 0))
    pix = busy.load()
    for x in range(0, 900, 3):
        pix[x, 10] = (255, x % 255, 40)
    p2 = Image.open(BytesIO(render_preview(busy, settings)))
    direct = render_model_input(busy, settings)
    via = render_model_input(p2, settings)
    assert direct != via


def test_unconfigured_filestore_does_not_pretend_s3(tmp_path: Path):
    store = UnconfiguredFileStore()
    assert store.available() is False
    assert "S3" in store.status_message() or "未接通" in store.status_message()
    try:
        store.put_bytes("previews/x.jpg", b"abc", "image/jpeg")
        raise AssertionError("should not write")
    except NotConfiguredError as exc:
        assert exc.code == "filestore_unconfigured"
        assert "冒充" in exc.message or "未接通" in exc.message
    local = LocalFileStore(tmp_path / "store", label="pytest")
    assert "不是已连接的 S3" in local.status_message()
    key = local.put_bytes("a/b.jpg", b"hello", "image/jpeg")
    assert key == "a/b.jpg"
    local.put_bytes("a/b.jpg", b"hello2", "image/jpeg")
    assert local.put_count == 1
    assert local.skipped_existing == 1
    assert (tmp_path / "store" / "a" / "b.jpg").read_bytes() == b"hello"


def test_redact_does_not_leak_secrets():
    raw = "login password=super-secret-value and aws_secret_access_key=abcd"
    hidden = redact(raw)
    assert "super-secret-value" not in hidden
    assert "abcd" not in hidden
    assert "<redacted>" in hidden


def _source_payload():
    return {
        "name": "test-source",
        "endpoint": "http://127.0.0.1:9000",
        "bucket": "media",
        "access_key": "test-access",
        "secret_key": "test-secret",
    }


def _make_app(tmp_path: Path, files: dict[str, Path], worker=False, poll=0.05):
    store = LocalFileStore(tmp_path / "previews", label="pytest-temp")
    settings = Settings(
        sqlite_path=tmp_path / "db.sqlite",
        worker_enabled=worker,
        worker_poll_seconds=poll,
        lease_seconds=8,
        auth_enabled=False,
    )
    reader = FakeReader("ok", files=files)
    app = create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=reader,
        worker_enabled=worker,
        file_store=store,
        work_root=tmp_path / "work",
    )
    return app, store, reader


def _wait_status(client: TestClient, job_id: str, wanted: set[str], timeout=12.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get(f"/api/jobs/{job_id}").json()
        if last.get("status") in wanted:
            return last
        time.sleep(0.05)
    raise AssertionError(f"timeout waiting {wanted}, last={last}")


def test_pipeline_video_media_done_is_blocked_not_completed(tmp_path: Path):
    ffmpeg_or_skip()
    video = tmp_path / "clips" / "demo.mp4"
    generate_testsrc_video(video, duration=2.2, fps=5)
    # 默认 5 秒间隔对 2.2 秒视频只采 1 帧，加快测试
    app, store, reader = _make_app(tmp_path, {"demo.mp4": video}, worker=True)
    with TestClient(app) as client:
        source_id = client.post("/api/sources", json=_source_payload()).json()["id"]
        body = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "demo.mp4", "vectorize": True}]},
        ).json()
        job_id = body["jobs"][0]["id"]
        asset_id = body["assets"][0]["id"]
        job = _wait_status(client, job_id, {"blocked", "failed"})
        assert job["status"] == "blocked"
        assert job["stage"] == "embed_pending"
        assert job["error_code"] == "embedding_pending"
        assert "媒体阶段已完成" in job["error_message"]
        assert "尚未" in job["error_message"] or "未" in job["error_message"]
        assert job["checkpoint"].get("media_done") is True
        frames = client.get(f"/api/assets/{asset_id}/frames").json()["items"]
        assert len(frames) >= 1
        kept = [f for f in frames if f["kept"]]
        assert kept
        assert kept[0]["preview_key"]
        assert not kept[0]["model_input_key"]
        assert (job["checkpoint"].get("encode_units") or [])
        assert "signature" not in (kept[0].get("extra") or {})
        assert store.put_count >= 1  # preview only; model input stays local
        asset = client.get("/api/assets").json()["items"][0]
        assert asset["id"] == asset_id
        assert asset["index_state"] == "media_ready"
        assert asset["ready_version"] == 0
        assert asset["lifecycle"] != "pending_update"
        assert reader.download_count == 1
        # 来源密钥不得出现在任务或状态响应里
        dumped = str(job) + str(client.get("/api/status").json()) + str(client.get("/api/sources").json())
        assert "test-secret" not in dumped
        assert "test-access" not in dumped


def test_black_frames_dropped_and_idempotent_restart(tmp_path: Path):
    ffmpeg_or_skip()
    video = tmp_path / "clips" / "black.mp4"
    generate_color_video(video, color="black", duration=1.2, fps=5)
    db = tmp_path / "idemp.sqlite"
    store = LocalFileStore(tmp_path / "previews", label="pytest-temp")
    settings = Settings(sqlite_path=db, worker_enabled=True, worker_poll_seconds=0.05, lease_seconds=8)
    reader = FakeReader("ok", files={"black.mp4": video})
    app = create_app(settings, sqlite_path=db, source_reader=reader, worker_enabled=True, file_store=store, work_root=tmp_path / "work")
    with TestClient(app) as client:
        # 用较短间隔采多点
        client.put("/api/settings", json={"mode": "interval_seconds", "interval_seconds": 0.4})
        source_id = client.post("/api/sources", json=_source_payload()).json()["id"]
        job_id = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "black.mp4", "vectorize": True}]},
        ).json()["jobs"][0]["id"]
        asset_id = client.get("/api/assets").json()["items"][0]["id"]
        job = _wait_status(client, job_id, {"blocked", "failed"})
        assert job["status"] == "blocked"
        frames = client.get(f"/api/assets/{asset_id}/frames").json()["items"]
        assert frames
        assert all(not f["kept"] and f["drop_reason"] == "all_black" for f in frames)
        assert store.put_count == 0
        puts_before = store.put_count
        skipped_before = store.skipped_existing
        downloads = reader.download_count

    # 重启同一数据库，不应再写入或当作已完成向量化
    app2 = create_app(
        Settings(sqlite_path=db, worker_enabled=False),
        sqlite_path=db,
        source_reader=reader,
        worker_enabled=False,
        file_store=store,
        work_root=tmp_path / "work2",
    )
    with TestClient(app2) as client:
        saved = client.get(f"/api/jobs/{job_id}").json()
        assert saved["status"] == "blocked"
        assert saved["checkpoint"].get("media_done") is True
        assert store.put_count == puts_before
        assert store.skipped_existing == skipped_before
        assert reader.download_count == downloads


def test_consecutive_similar_dedup_on_static_video(tmp_path: Path):
    ffmpeg_or_skip()
    video = tmp_path / "clips" / "static.mp4"
    generate_static_pattern_video(video, "checker", duration=1.6, fps=5)
    app, store, _reader = _make_app(tmp_path, {"static.mp4": video}, worker=True)
    with TestClient(app) as client:
        client.put("/api/settings", json={"interval_seconds": 0.4})
        source_id = client.post("/api/sources", json=_source_payload()).json()["id"]
        imported = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "static.mp4", "vectorize": True}]},
        ).json()
        job = _wait_status(client, imported["jobs"][0]["id"], {"blocked", "failed"})
        assert job["status"] == "blocked"
        frames = client.get(f"/api/assets/{imported['assets'][0]['id']}/frames").json()["items"]
        kept = [f for f in frames if f["kept"]]
        dropped = [f for f in frames if not f["kept"]]
        assert len(kept) == 1
        assert dropped
        assert all(f["drop_reason"] == "consecutive_similar" for f in dropped)


def test_image_skips_video_cleaner(tmp_path: Path):
    image = tmp_path / "files" / "dark.jpg"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (320, 200), (0, 0, 0)).save(image, format="JPEG")
    app, store, _reader = _make_app(tmp_path, {"dark.jpg": image}, worker=True)
    with TestClient(app) as client:
        source_id = client.post("/api/sources", json=_source_payload()).json()["id"]
        imported = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "dark.jpg", "vectorize": True}]},
        ).json()
        job = _wait_status(client, imported["jobs"][0]["id"], {"blocked", "failed"})
        assert job["status"] == "blocked"
        frames = client.get(f"/api/assets/{imported['assets'][0]['id']}/frames").json()["items"]
        assert len(frames) == 1
        assert frames[0]["kept"] is True
        assert store.put_count == 1


def test_pause_resume_from_checkpoint(tmp_path: Path):
    ffmpeg_or_skip()
    video = tmp_path / "clips" / "long.mp4"
    generate_testsrc_video(video, duration=3.0, fps=5)
    app, store, _reader = _make_app(tmp_path, {"long.mp4": video}, worker=False)
    with TestClient(app) as client:
        client.put("/api/settings", json={"interval_seconds": 0.5})
        source_id = client.post("/api/sources", json=_source_payload()).json()["id"]
        job_id = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "long.mp4", "vectorize": True}]},
        ).json()["jobs"][0]["id"]
        jobs = client.app.state.container.jobs
        claimed = jobs.claim_next("t1")
        assert claimed is not None
        # 跑若干单元后暂停
        for _ in range(3):
            jobs._pipeline.process_unit(jobs.get(job_id))
        paused = jobs.pause(job_id)
        assert paused.status == "paused"
        ck = paused.checkpoint
        assert ck.get("downloaded") or ck.get("probed") or ck.get("planned") or ck.get("next_index") is not None
        puts = store.put_count
        jobs.resume(job_id)
        claimed2 = jobs.claim_next("t1")
        assert claimed2 is not None
        result = jobs.run_claimed(claimed2, "t1")
        assert result.status == "blocked"
        assert result.checkpoint.get("media_done") is True
        # 已成功步骤不会把预览再写一遍
        frames = client.get(f"/api/assets/{client.get('/api/assets').json()['items'][0]['id']}/frames").json()["items"]
        kept = [f for f in frames if f["kept"]]
        # 每个保留帧只写预览；模型输入留在本地工作副本
        assert store.put_count == len(kept)
        assert store.put_count >= puts


def test_search_query_priority_try_finally(tmp_path: Path):
    app, _store, _reader = _make_app(tmp_path, {}, worker=False)
    with TestClient(app) as client:
        jobs = client.app.state.container.jobs
        assert jobs.query_priority_active() is False
        response = client.post("/api/search", json={"query": "海边"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "search_unconfigured"
        assert "items" not in response.json()
        assert jobs.query_priority_active() is False
        # 503 之后 worker 不应被永久卡住
        source_id = client.post("/api/sources", json=_source_payload()).json()["id"]
        # 没有对应文件的任务应失败而不是一直等待优先标志
        job_id = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "missing.mp4", "vectorize": True}]},
        ).json()["jobs"][0]["id"]
        claimed = jobs.claim_next("t")
        assert claimed is not None
        try:
            jobs.run_claimed(claimed, "t")
        except Exception:
            jobs.fail(job_id, "expected", "missing file")
        # 优先标志仍为关闭，可继续领取（本任务可能已失败）
        assert jobs.query_priority_active() is False


def test_query_priority_yields_between_units(tmp_path: Path):
    ffmpeg_or_skip()
    video = tmp_path / "clips" / "prio.mp4"
    generate_testsrc_video(video, duration=2.5, fps=5)
    app, _store, _reader = _make_app(tmp_path, {"prio.mp4": video}, worker=False)
    with TestClient(app) as client:
        client.put("/api/settings", json={"interval_seconds": 0.5})
        source_id = client.post("/api/sources", json=_source_payload()).json()["id"]
        job_id = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "prio.mp4", "vectorize": True}]},
        ).json()["jobs"][0]["id"]
        jobs = client.app.state.container.jobs
        claimed = jobs.claim_next("t")
        jobs._pipeline.process_unit(jobs.get(job_id))  # download
        jobs.set_query_priority(True)
        assert jobs.claim_next("other") is None
        jobs.set_query_priority(False)
        result = jobs.run_claimed(jobs.get(job_id), "t")
        assert result.status == "blocked"


def test_production_default_has_no_synthetic_media_and_unconfigured_store(client: TestClient):
    status = client.get("/api/status").json()
    assert status["file_store"]["status"] == "unconfigured"
    assert "冒充" in status["file_store"]["message"] or "未接通" in status["file_store"]["message"]
    assert status["milvus"]["status"] == "unconfigured"
    assert status["embedding_model"]["status"] == "unconfigured"
    assert "假向量" in status["embedding_model"]["message"] or "未下载" in status["embedding_model"]["message"]
    assert client.get("/api/assets").json()["items"] == []
    assert client.get("/api/jobs").json()["items"] == []
    settings = client.get("/api/settings").json()
    assert settings["sample"]["interval_seconds"] == 5.0
    assert settings["sample"]["mode"] == "interval_seconds"
    assert "暂定" in settings["provisional_note"]


def test_settings_roundtrip(client: TestClient):
    updated = client.put(
        "/api/settings",
        json={"mode": "frames_per_minute", "frames_per_minute": 8, "uniform_total": 10},
    ).json()
    assert updated["sample"]["mode"] == "frames_per_minute"
    assert updated["sample"]["frames_per_minute"] == 8
    again = client.get("/api/settings").json()
    assert again["sample"]["mode"] == "frames_per_minute"


def test_probe_real_ffmpeg_on_tiny_video(tmp_path: Path):
    ffmpeg_or_skip()
    video = tmp_path / "tiny.mp4"
    generate_color_video(video, "red", duration=1.0, fps=5)
    info = probe_video(video)
    assert info.duration_seconds >= 0.8
    assert info.width == 80
    assert info.height == 48
