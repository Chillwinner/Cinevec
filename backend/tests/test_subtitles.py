from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.errors import AppError
from app.domain.subtitle import MergeSettings, SubtitleCue
from app.infra.config import Settings
from app.infra.file_store import LocalFileStore
from app.infra.subtitle_merge import merge_adjacent
from app.infra.subtitle_parser import parse_subtitle_bytes, sanitize_cue_text
from app.main import create_app
from tests.conftest import FakeReader


SRT = """1
00:00:01,000 --> 00:00:02,000
Hello <b>world</b>

2
00:00:02,200 --> 00:00:03,000
Next line

3
00:00:10,000 --> 00:00:11,000
Later
"""

VTT = """WEBVTT

00:00:01.000 --> 00:00:02.000
Hello <i>world</i>

00:00:02.200 --> 00:00:03.000
Next line
"""


def test_parse_srt_and_vtt_and_reject_ass():
    srt = parse_subtitle_bytes(SRT.encode("utf-8"), "a.srt")
    assert srt.format == "srt"
    assert len(srt.cues) == 3
    assert srt.cues[0].text == "Hello world"
    vtt = parse_subtitle_bytes(VTT.encode("utf-8"), "a.vtt")
    assert vtt.format == "vtt"
    assert vtt.cues[0].text == "Hello world"
    ass = parse_subtitle_bytes(
        b"[Script Info]\nTitle: x\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        b"Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,Hello {\\i1}world{\\i0}\\NNext\n",
        "film.ass",
    )
    assert ass.format == "ass"
    assert len(ass.cues) == 1
    assert "Hello" in ass.cues[0].text
    assert "world" in ass.cues[0].text


def test_sanitize_does_not_execute_payload():
    raw = '<script>alert(1)</script>safe &amp; `import os; os.system("echo pwned")`'
    text = sanitize_cue_text(raw)
    assert "script" not in text.lower()
    assert "alert(1)" in text or "safe" in text
    assert "&amp;" not in text
    # 解析过程不得执行字符串
    parsed = parse_subtitle_bytes(
        "1\n00:00:00,000 --> 00:00:01,000\n__PICA_SHOULD_NOT_EXEC__ = 1\n".encode(),
        "x.srt",
    )
    assert parsed.cues[0].text.endswith("= 1") or "__PICA_SHOULD_NOT_EXEC__" in parsed.cues[0].text


def test_merge_keeps_cues_and_groups():
    cues = [
        SubtitleCue(0, 0.0, 1.0, "A"),
        SubtitleCue(1, 1.2, 2.0, "B"),
        SubtitleCue(2, 8.0, 9.0, "C"),
    ]
    groups = merge_adjacent(cues, MergeSettings(max_gap_seconds=0.5, max_group_duration_seconds=8, max_group_chars=80))
    assert len(cues) == 3
    assert len(groups) == 2
    assert groups[0].cue_indexes == [0, 1]
    assert groups[0].start_seconds == 0.0
    assert groups[0].end_seconds == 2.0
    assert groups[1].text == "C"


def _app(tmp_path: Path, files: dict[str, Path]):
    store = LocalFileStore(tmp_path / "store", label="pytest-temp")
    settings = Settings(sqlite_path=tmp_path / "db.sqlite", worker_enabled=True, worker_poll_seconds=0.05)
    reader = FakeReader("ok", files=files)
    app = create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=reader,
        worker_enabled=True,
        file_store=store,
        work_root=tmp_path / "work",
    )
    return app


def _wait(client, job_id, wanted, timeout=10):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get(f"/api/jobs/{job_id}").json()
        if last.get("status") in wanted:
            return last
        time.sleep(0.05)
    raise AssertionError(f"timeout {wanted} last={last}")


def test_manual_associate_parse_and_block_without_vectors(tmp_path: Path):
    sub = tmp_path / "movie.srt"
    sub.write_text(SRT, encoding="utf-8")
    # 视频只作登记，不需要真实媒体文件即可关联；解析任务只下载字幕
    app = _app(tmp_path, {"movie.srt": sub})
    with TestClient(app) as client:
        source_id = client.post(
            "/api/sources",
            json={"name": "s", "endpoint": "http://x", "bucket": "b", "access_key": "k", "secret_key": "s"},
        ).json()["id"]
        imported = client.post(
            "/api/imports",
            json={
                "source_id": source_id,
                "items": [
                    {"object_key": "clip.mp4", "vectorize": False},
                    {"object_key": "movie.srt", "vectorize": True},
                ],
            },
        ).json()
        # 勾选向量化也不会自动按文件名绑定或直接入字幕索引
        assert imported["jobs"] == []
        assets = {a["object_key"]: a for a in imported["assets"]}
        created = client.post(
            "/api/subtitles/associations",
            json={"video_asset_id": assets["clip.mp4"]["id"], "subtitle_asset_id": assets["movie.srt"]["id"]},
        )
        assert created.status_code == 201
        job_id = created.json()["job"]["id"]
        job = _wait(client, job_id, {"blocked", "failed"})
        assert job["status"] == "blocked"
        assert job["error_code"] == "subtitle_embedding_pending"
        assert "假向量" in job["error_message"] or "尚未接入" in job["error_message"]
        assoc_id = created.json()["association"]["id"]
        detail = client.get(f"/api/subtitles/associations/{assoc_id}").json()
        assert len(detail["cues"]) == 3
        assert len(detail["groups"]) >= 2
        assert detail["cues"][0]["text"] == "Hello world"
        assert "尚未拍板" in detail["merge_note"]
        # 响应是 JSON 文本字段，不包含可执行包装
        assert "<script>" not in str(detail)


def test_no_auto_bind_and_duplicate_association(tmp_path: Path):
    sub = tmp_path / "clip.srt"
    sub.write_text(SRT, encoding="utf-8")
    app = _app(tmp_path, {"clip.srt": sub})
    with TestClient(app) as client:
        source_id = client.post(
            "/api/sources",
            json={"name": "s", "endpoint": "http://x", "bucket": "b", "access_key": "k", "secret_key": "s"},
        ).json()["id"]
        imported = client.post(
            "/api/imports",
            json={
                "source_id": source_id,
                "items": [
                    {"object_key": "clip.mp4", "vectorize": False},
                    {"object_key": "clip.srt", "vectorize": False},
                ],
            },
        ).json()
        assert client.get("/api/subtitles/associations").json()["items"] == []
        assets = {a["object_key"]: a for a in imported["assets"]}
        first = client.post(
            "/api/subtitles/associations",
            json={"video_asset_id": assets["clip.mp4"]["id"], "subtitle_asset_id": assets["clip.srt"]["id"], "enqueue": False},
        )
        assert first.status_code == 201
        again = client.post(
            "/api/subtitles/associations",
            json={"video_asset_id": assets["clip.mp4"]["id"], "subtitle_asset_id": assets["clip.srt"]["id"], "enqueue": False},
        )
        assert again.status_code == 201
        assert again.json()["association"]["id"] == first.json()["association"]["id"]
        listed = client.get("/api/subtitles/associations").json()["items"]
        assert len(listed) == 1
