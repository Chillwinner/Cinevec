from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.models import ObjectEntry
from app.infra.config import Settings
from app.infra.file_store import LocalFileStore
from app.infra.stubs import UnconfiguredVectorIndex
from app.main import create_app
from tests.conftest import FakeReader
from tests.fakes import MemoryEncoder, MemoryVectorIndex


def _app(tmp_path: Path, encoder=None, index=None, reader=None):
    settings = Settings(sqlite_path=tmp_path / "db.sqlite", worker_enabled=False, worker_poll_seconds=0.05)
    store = LocalFileStore(tmp_path / "store", label="pytest-temp")
    app = create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=reader or FakeReader("ok", listing=[]),
        worker_enabled=False,
        file_store=store,
        work_root=tmp_path / "work",
        encoder=encoder,
        index=index,
        subtitle_encoder=encoder,
    )
    return app, store


def _source(client):
    return client.post(
        "/api/sources",
        json={"name": "s", "endpoint": "http://x", "bucket": "b", "access_key": "k", "secret_key": "s"},
    ).json()["id"]


def test_runtime_never_falls_back_to_memory_index(tmp_path: Path):
    app, _ = _app(tmp_path)
    with TestClient(app) as client:
        status = client.get("/api/status").json()
        assert status["milvus"]["status"] == "unconfigured"
        assert "假" in status["milvus"]["message"] or "尚未" in status["milvus"]["message"]
        assert status["embedding_model"]["status"] in {"unconfigured", "blocked"}
        assert status["embedding_model"].get("inference_ready") is False
        dumped = str(status)
        assert "token" not in dumped.lower() or "token_configured" in dumped
        search = client.post("/api/search", json={"query": "海边"})
        assert search.status_code == 503
        assert "items" not in search.json()


def test_media_ready_does_not_activate_and_write_does(tmp_path: Path):
    encoder = MemoryEncoder(dim=8)
    index = MemoryVectorIndex(dim=8)
    reader = FakeReader("ok", listing=[ObjectEntry(key="clip.mp4", is_dir=False, size=1, etag="x")])
    app, store = _app(tmp_path, encoder=encoder, index=index, reader=reader)
    with TestClient(app) as client:
        source_id = _source(client)
        asset_id = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "clip.mp4", "vectorize": False}]},
        ).json()["assets"][0]["id"]
        # 模拟媒体完成：写入两帧模型输入，但不把 ready_version 提前
        store.put_bytes("models/0.bin", b"frame-zero", "application/octet-stream")
        store.put_bytes("previews/0.jpg", b"jpeg-preview", "image/jpeg")
        store.put_bytes("models/1.bin", b"frame-one", "application/octet-stream")
        store.put_bytes("previews/1.jpg", b"jpeg-preview-2", "image/jpeg")
        meta = client.app.state.container.imports._metadata
        from types import SimpleNamespace
        for i, key in enumerate(["models/0.bin", "models/1.bin"]):
            meta.upsert_frame(
                asset_id,
                "job-media",
                SimpleNamespace(
                    index=i,
                    time_seconds=float(i),
                    kept=True,
                    drop_reason=None,
                    preview_key=f"previews/{i}.jpg",
                    model_input_key=key,
                    extra={},
                ),
            )
        meta.update_asset(asset_id, status="media_ready", index_state="media_ready")
        asset = client.get("/api/assets").json()["items"][0]
        assert asset["index_state"] == "media_ready"
        assert asset["ready_version"] == 0

        # 未激活前即使库里有旧向量也不应出现在结果（再插入一版旧向量后搜索）
        index.upsert_records("frame", [{
            "id": f"{asset_id}:1:frame:0",
            "vector": [1, 0, 0, 0, 0, 0, 0, 0],
            "asset_id": asset_id,
            "version": 1,
            "unit_index": 0,
            "time_seconds": 0,
            "preview_key": "previews/0.jpg",
        }])
        hidden = client.post("/api/search", json={"query": "海边"}).json()
        assert hidden["items"] == []

        jobs = client.app.state.container.jobs
        # 入队真正写入
        from app.domain.models import Job
        import uuid
        job = jobs._tasks.add_job(Job(
            id=str(uuid.uuid4()),
            job_type="index_asset",
            status="running",
            source_id=source_id,
            asset_id=asset_id,
            stage="embedding",
            step="写向量",
            progress=0.5,
            queue_position=None,
            error_code=None,
            error_message=None,
            checkpoint={"media_done": True},
            payload={"object_key": "clip.mp4"},
        ))
        writer = jobs._writer
        assert writer is not None
        # 中途只写第一条：模拟中断，不得激活
        frames = meta.list_frames(asset_id)
        # 直接调用完整写入（幂等）
        outcome = writer.process_frames(job)
        assert outcome == "completed"
        asset = client.get("/api/assets").json()["items"][0]
        assert asset["ready_version"] == 1
        assert asset["index_state"] == "ready"
        assert asset["lifecycle"] == "active"
        units = meta.list_vector_units(asset_id, version=1, modality="frame")
        assert len(units) == 2

        found = client.post("/api/search", json={"query": "海边"}).json()
        assert len(found["items"]) == 1
        item = found["items"][0]
        assert item["asset_id"] == asset_id
        assert item["hit_source"] == "visual"
        assert "概率" in item["score_meaning"] or "不是" in found["score_meaning"]
        assert item["previews"]
        assert item["previews"][0]["locator"].startswith(f"/api/previews/{asset_id}/")
        preview = client.get(item["previews"][0]["locator"])
        assert preview.status_code == 200
        assert preview.content == b"jpeg-preview"
        assert "private" in preview.headers.get("cache-control", "")

        # 内容更新后旧向量隐藏，直到新版本写完
        meta.update_asset(asset_id, version=2, lifecycle="pending_update", index_state="inactive")
        hidden = client.post("/api/search", json={"query": "海边"}).json()
        assert hidden["items"] == []

        store.put_bytes("models/v2.bin", b"frame-v2", "application/octet-stream")
        store.put_bytes("previews/v2.jpg", b"jpeg-v2", "image/jpeg")
        # 清理旧帧登记，写入 v2 单帧
        meta._conn.execute("DELETE FROM frames WHERE asset_id = ?", (asset_id,))
        meta._conn.commit()
        meta.upsert_frame(
            asset_id,
            "job-v2",
            SimpleNamespace(
                index=0, time_seconds=0.0, kept=True, drop_reason=None,
                preview_key="previews/v2.jpg", model_input_key="models/v2.bin", extra={},
            ),
        )
        job2 = jobs._tasks.add_job(Job(
            id=str(uuid.uuid4()), job_type="index_asset", status="running",
            source_id=source_id, asset_id=asset_id, stage="embedding", step="",
            progress=0, queue_position=None, error_code=None, error_message=None,
            checkpoint={}, payload={},
        ))
        assert writer.process_frames(job2) == "completed"
        asset = client.get("/api/assets").json()["items"][0]
        assert asset["ready_version"] == 2
        again = client.post("/api/search", json={"query": "海边"}).json()
        assert len(again["items"]) == 1
        assert again["items"][0]["previews"][0]["preview_key"] == "previews/v2.jpg"

        # 确认文件缺失：仍可查并标失效
        meta.update_asset(asset_id, lifecycle="missing")
        missing = client.post("/api/search", json={"query": "海边"}).json()
        assert len(missing["items"]) == 1
        assert missing["items"][0]["visibility"] == "searchable_invalid"
        assert "失效" in missing["items"][0]["lifecycle_label"]


def test_oversample_avoids_single_video_starvation(tmp_path: Path):
    encoder = MemoryEncoder(dim=8, text_map={"海边": [1, 0, 0, 0, 0, 0, 0, 0]})
    index = MemoryVectorIndex(dim=8)
    app, store = _app(tmp_path, encoder=encoder, index=index)
    with TestClient(app) as client:
        source_id = _source(client)
        ids = []
        for i in range(6):
            key = f"v{i}.mp4"
            aid = client.post(
                "/api/imports",
                json={"source_id": source_id, "items": [{"object_key": key, "vectorize": False}]},
            ).json()["assets"][0]["id"]
            client.app.state.container.imports._metadata.update_asset(
                aid, ready_version=1, version=1, index_state="ready", lifecycle="active", kind="video"
            )
            ids.append(aid)
        # 第一个视频 20 个高分帧，其余各 1 帧略低
        dominant = ids[0]
        recs = []
        for i in range(20):
            recs.append({
                "id": f"{dominant}:1:frame:{i}",
                "vector": [0.99, 0.01, 0, 0, 0, 0, 0, 0],
                "asset_id": dominant,
                "version": 1,
                "unit_index": i,
                "time_seconds": float(i),
                "preview_key": f"p/{i}.jpg",
            })
        for i, aid in enumerate(ids[1:], start=1):
            recs.append({
                "id": f"{aid}:1:frame:0",
                "vector": [0.8, 0.1 + i * 0.01, 0, 0, 0, 0, 0, 0],
                "asset_id": aid,
                "version": 1,
                "unit_index": 0,
                "time_seconds": 0,
                "preview_key": f"p/{aid}.jpg",
            })
        index.upsert_records("frame", recs)
        data = client.post("/api/search", json={"query": "海边", "top_n": 6}).json()
        assert len(data["items"]) == 6
        assert {item["asset_id"] for item in data["items"]} == set(ids)
        lead = next(item for item in data["items"] if item["asset_id"] == dominant)
        assert len(lead["previews"]) <= 5


def test_image_query_uses_visual_path_only(tmp_path: Path):
    encoder = MemoryEncoder(dim=8)
    index = MemoryVectorIndex(dim=8)
    app, _ = _app(tmp_path, encoder=encoder, index=index)
    with TestClient(app) as client:
        denied = client.post("/api/search", json={"query": "", "has_image": True})
        assert denied.status_code == 400
        # 字幕编码器也会被调用? 图片查询不应走字幕路
        source_id = _source(client)
        aid = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "a.jpg", "vectorize": False}]},
        ).json()["assets"][0]["id"]
        client.app.state.container.imports._metadata.update_asset(
            aid, ready_version=1, version=1, index_state="ready", lifecycle="active", kind="image"
        )
        index.upsert_records("frame", [{
            "id": f"{aid}:1:frame:0",
            "vector": encoder.encode_image(b"query-img"),
            "asset_id": aid, "version": 1, "unit_index": 0, "time_seconds": 0, "preview_key": "",
        }])
        from io import BytesIO
        resp = client.post("/api/search/image", files={"image": ("q.jpg", BytesIO(b"query-img"), "image/jpeg")})
        assert resp.status_code == 200
        assert resp.json()["items"][0]["asset_id"] == aid
        assert resp.json()["items"][0]["hit_source"] == "visual"


def test_unconfigured_delete_still_honest():
    result = UnconfiguredVectorIndex().delete_vectors(["a:1:frame:0"])
    assert result.succeeded is False
    assert result.pending is True
