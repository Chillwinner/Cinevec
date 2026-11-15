from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.models import ObjectEntry
from app.infra.config import Settings
from app.infra.file_store import LocalFileStore
from app.infra.stubs import UnconfiguredVectorIndex
from app.main import create_app
from tests.conftest import FakeReader


def _make(tmp_path: Path, reader: FakeReader, worker=True):
    settings = Settings(sqlite_path=tmp_path / "db.sqlite", worker_enabled=worker, worker_poll_seconds=0.05)
    store = LocalFileStore(tmp_path / "store", label="pytest-temp")
    return create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=reader,
        worker_enabled=worker,
        file_store=store,
        work_root=tmp_path / "work",
    )


def _source(client):
    return client.post(
        "/api/sources",
        json={"name": "s", "endpoint": "http://x", "bucket": "b", "access_key": "k", "secret_key": "s"},
    ).json()["id"]


def _wait(client, job_id, wanted, timeout=10):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get(f"/api/jobs/{job_id}").json()
        if last.get("status") in wanted:
            return last
        time.sleep(0.05)
    raise AssertionError(f"timeout {wanted} last={last}")


def test_incomplete_or_auth_failure_does_not_mark_missing(tmp_path: Path):
    listing = [
        ObjectEntry(key="keep.mp4", is_dir=False, size=10, etag="a", last_modified="2026-01-01T00:00:00"),
    ]
    reader = FakeReader("ok", listing=listing, complete=True)
    app = _make(tmp_path, reader, worker=False)
    with TestClient(app) as client:
        source_id = _source(client)
        client.post("/api/imports", json={"source_id": source_id, "items": [{"object_key": "keep.mp4", "vectorize": False}, {"object_key": "gone.mp4", "vectorize": False}]})
        # 鉴权失败
        reader.mode = "authfail"
        job = client.post("/api/refresh", json={"source_id": source_id}).json()["job"]
        # 手动跑
        jobs = client.app.state.container.jobs
        claimed = jobs.claim_next("t")
        assert claimed is not None
        try:
            jobs.run_claimed(claimed, "t")
        except Exception as exc:
            jobs.fail(claimed.id, getattr(exc, "code", "err"), str(exc))
        failed = client.get(f"/api/jobs/{job['id']}").json()
        assert failed["status"] == "failed"
        assert failed["error_code"] in {"dependency_error", "scan_incomplete", "list_incomplete"}
        assert "删除" in failed["error_message"] or "缺失" in failed["error_message"]
        assets = {a["object_key"]: a for a in client.get("/api/assets").json()["items"]}
        assert assets["gone.mp4"]["lifecycle"] != "missing"

        # 不完整扫描同样不得判缺失
        reader.mode = "ok"
        reader.complete = False
        job2 = client.post("/api/refresh", json={"source_id": source_id}).json()["job"]
        claimed = jobs.claim_next("t")
        jobs.run_claimed(claimed, "t")
        again = client.get(f"/api/jobs/{job2['id']}").json()
        assert again["status"] == "failed"
        assert again["error_code"] == "list_incomplete"
        assets = {a["object_key"]: a for a in client.get("/api/assets").json()["items"]}
        assert assets["gone.mp4"]["lifecycle"] != "missing"
        scan = client.get(f"/api/scans/{again['checkpoint']['scan_id']}").json()
        assert scan["complete"] is False


def test_complete_scan_marks_missing_and_follows_policy(tmp_path: Path):
    reader = FakeReader(
        "ok",
        listing=[
            ObjectEntry(key="photos/a.jpg", is_dir=False, size=3, etag="1"),
            ObjectEntry(key="photos/new.jpg", is_dir=False, size=4, etag="2"),
        ],
    )
    app = _make(tmp_path, reader, worker=False)
    with TestClient(app) as client:
        source_id = _source(client)
        client.post(
            "/api/imports",
            json={
                "source_id": source_id,
                "items": [
                    {"object_key": "photos/a.jpg", "vectorize": False},
                    {"object_key": "old.mp4", "vectorize": False},
                ],
            },
        )
        client.post("/api/policies", json={"source_id": source_id, "prefix": "photos/", "vectorize": True})
        job = client.post("/api/refresh", json={"source_id": source_id}).json()["job"]
        jobs = client.app.state.container.jobs
        jobs.run_claimed(jobs.claim_next("t"), "t")
        done = client.get(f"/api/jobs/{job['id']}").json()
        assert done["status"] == "completed"
        assert done["checkpoint"]["complete"] is True
        scan = client.get(f"/api/scans/{done['checkpoint']['scan_id']}").json()
        assert scan["complete"] is True
        assets = {a["object_key"]: a for a in client.get("/api/assets").json()["items"]}
        assert assets["old.mp4"]["lifecycle"] == "missing"
        assert assets["photos/new.jpg"]["vectorize"] is True
        # 新图按策略入队
        index_jobs = [j for j in client.get("/api/jobs").json()["items"] if j["job_type"] == "index_asset" and j["payload"].get("object_key") == "photos/new.jpg"]
        assert index_jobs


def test_file_change_inactivates_old_index_until_new_ready(tmp_path: Path):
    reader = FakeReader(
        "ok",
        listing=[ObjectEntry(key="clip.mp4", is_dir=False, size=10, etag="old")],
    )
    app = _make(tmp_path, reader, worker=False)
    with TestClient(app) as client:
        source_id = _source(client)
        imported = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "clip.mp4", "vectorize": True}]},
        ).json()
        asset_id = imported["assets"][0]["id"]
        # 先取消导入时入队的索引任务，避免 claim 到下载失败的旧任务
        for job in imported["jobs"]:
            client.post(f"/api/jobs/{job['id']}/cancel")
        client.app.state.container.imports._metadata.update_asset(
            asset_id, ready_version=1, version=1, index_state="ready", lifecycle="active", etag="old", size=10, vectorize=True
        )
        reader.listing = [ObjectEntry(key="clip.mp4", is_dir=False, size=20, etag="new")]
        jobs = client.app.state.container.jobs
        refresh = client.post("/api/refresh", json={"source_id": source_id}).json()["job"]
        claimed = jobs.claim_next("t")
        assert claimed is not None and claimed.id == refresh["id"]
        jobs.run_claimed(claimed, "t")
        assert client.get(f"/api/jobs/{refresh['id']}").json()["status"] == "completed"
        asset = client.get("/api/assets").json()["items"][0]
        assert asset["id"] == asset_id
        assert asset["version"] == 2
        assert asset["ready_version"] == 1
        assert asset["lifecycle"] == "pending_update"
        assert asset["index_state"] == "inactive"
        assert asset["lifecycle_label"] == "待更新"


def test_source_delete_keeps_records_and_catalog_marks_invalid(tmp_path: Path):
    app = _make(tmp_path, FakeReader("ok", listing=[]), worker=False)
    with TestClient(app) as client:
        source_id = _source(client)
        client.post("/api/imports", json={"source_id": source_id, "items": [{"object_key": "keep.mp4", "vectorize": False}]})
        deleted = client.delete(f"/api/sources/{source_id}").json()
        assert deleted["ok"] is True
        assert deleted["assets_retained"] == 1
        assert client.get("/api/sources").json()["items"] == []
        assets = client.get("/api/assets").json()["items"]
        assert len(assets) == 1
        assert assets[0]["lifecycle"] == "source_invalid"
        assert assets[0]["source_deleted"] is True
        catalog = client.post("/api/catalog", json={"query": "keep"}).json()
        assert catalog["kind"] == "registry"
        assert "向量检索" in catalog["note"]
        assert catalog["items"][0]["lifecycle_label"] == "来源已失效"


def test_exclude_tombstone_restore_and_honest_vector_purge(tmp_path: Path):
    app = _make(tmp_path, FakeReader("ok", listing=[ObjectEntry(key="clip.mp4", is_dir=False, size=1, etag="x")]), worker=False)
    with TestClient(app) as client:
        source_id = _source(client)
        asset_id = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "clip.mp4", "vectorize": False}]},
        ).json()["assets"][0]["id"]
        excluded = client.post(f"/api/assets/{asset_id}/exclude").json()
        assert excluded["fully_deleted"] is False
        assert excluded["asset"]["lifecycle"] == "excluded"
        assert excluded["asset"]["fully_deleted"] is False
        job_id = excluded["job"]["id"]
        jobs = client.app.state.container.jobs
        claimed = jobs.claim_next("t")
        if claimed:
            jobs.run_claimed(claimed, "t")
        job = client.get(f"/api/jobs/{job_id}").json()
        # 从未写入向量：跳过 Milvus，只清理平台对象（此条没有预览/托管原片）
        assert job["status"] == "completed"
        assert job["checkpoint"].get("fully_deleted") is True
        asset = client.get("/api/assets").json()["items"][0]
        assert asset["lifecycle"] == "excluded"
        assert asset["vector_purge_status"] == "done"
        assert asset["artifact_purge_status"] == "done"
        assert asset["fully_deleted"] is True
        assert asset["s3_original_untouched"] is True
        result = UnconfiguredVectorIndex().delete_vectors(["asset:x"])
        assert result.succeeded is False
        assert result.pending is True

        # 墓碑阻止再导入
        denied = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "clip.mp4", "vectorize": False}]},
        )
        assert denied.status_code == 400
        assert denied.json()["error"]["code"] == "excluded"

        # 刷新也不会把排除项当新资源
        client.post("/api/refresh", json={"source_id": source_id})
        jobs.run_claimed(jobs.claim_next("t"), "t")
        assets = client.get("/api/assets").json()["items"]
        assert len(assets) == 1
        assert assets[0]["lifecycle"] == "excluded"

        restored = client.post(f"/api/assets/{asset_id}/restore").json()["asset"]
        assert restored["excluded"] is False
        assert restored["lifecycle"] == "active"
        # 恢复后可以再登记
        again = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "clip.mp4", "vectorize": False}]},
        )
        assert again.status_code == 201


def test_refresh_dedup_and_schedule_default_off(tmp_path: Path):
    reader = FakeReader("ok", listing=[])
    app = _make(tmp_path, reader, worker=False)
    with TestClient(app) as client:
        settings = client.get("/api/settings").json()
        assert settings["refresh"]["enabled"] is False
        assert "尚未拍板" in settings["undecided_note"] or "未定" in settings["undecided_note"]
        source_id = _source(client)
        a = client.post("/api/refresh", json={"source_id": source_id}).json()["job"]
        b = client.post("/api/refresh", json={"source_id": source_id}).json()["job"]
        assert a["id"] == b["id"]
        # 开启调度后，到期应入队；同一来源仍去重
        client.put("/api/settings", json={"refresh": {"enabled": True, "interval_minutes": 1, "next_due_at": "2000-01-01T00:00:00+00:00"}})
        created = client.app.state.container.refresh.tick_schedule()
        assert created
        assert created[0].id == a["id"]


def test_preview_cleanup_default_on(tmp_path: Path):
    app = _make(tmp_path, FakeReader("ok"), worker=False)
    with TestClient(app) as client:
        settings = client.get("/api/settings").json()
        assert settings["lifecycle"]["preview_cleanup_on_exclude"] is True
        ok = client.put("/api/settings", json={"lifecycle": {"preview_cleanup_on_exclude": False}})
        assert ok.status_code == 200
        assert ok.json()["lifecycle"]["preview_cleanup_on_exclude"] is False


def test_exclude_deletes_vectors_then_platform_objects(tmp_path: Path):
    from tests.fakes import MemoryVectorIndex
    from app.domain.media import PreparedFrame
    from app.infra.milvus_rest import collection_name

    store = LocalFileStore(tmp_path / "store", label="pytest-temp")
    settings = Settings(sqlite_path=tmp_path / "db.sqlite", worker_enabled=False, worker_poll_seconds=0.05)
    index = MemoryVectorIndex(profile="dev", model_id="embeddinggemma-2", model_version="float32", dim=768)
    col = collection_name("dev", "embeddinggemma-2", "float32", 768, "frame", "full_frame_passthrough_v1")
    index.seed_collection(col, 768, {"vid-1": {"id": "vid-1", "vector": [0.0] * 768}})
    app = create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=FakeReader("ok", listing=[ObjectEntry(key="clip.mp4", is_dir=False, size=1, etag="x")]),
        worker_enabled=False,
        file_store=store,
        work_root=tmp_path / "work",
        index=index,
    )
    with TestClient(app) as client:
        source_id = client.post(
            "/api/sources",
            json={"name": "s", "endpoint": "http://x", "bucket": "b", "access_key": "k", "secret_key": "s", "is_platform_storage": True},
        ).json()["id"]
        imported = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "clip.mp4", "vectorize": False, "origin": "hosted"}]},
        ).json()
        asset_id = imported["assets"][0]["id"]
        store.put_bytes("clip.mp4", b"video-bytes", "video/mp4")
        store.put_bytes("previews/clip.jpg", b"jpeg", "image/jpeg")
        meta = client.app.state.container.imports._metadata
        meta.upsert_frame(
            asset_id,
            "job-x",
            PreparedFrame(index=0, time_seconds=0, kept=True, drop_reason=None, preview_key="previews/clip.jpg"),
        )
        meta.upsert_vector_unit(
            {
                "vector_id": "local-1",
                "asset_id": asset_id,
                "version": 1,
                "modality": "frame",
                "unit_index": 0,
                "collection": col,
                "remote_vector_id": "vid-1",
                "preview_key": "previews/clip.jpg",
            }
        )
        excluded = client.post(f"/api/assets/{asset_id}/exclude").json()
        assert excluded["fully_deleted"] is False
        jobs = client.app.state.container.jobs
        claimed = jobs.claim_next("t")
        assert claimed is not None
        jobs.run_claimed(claimed, "t")
        job = client.get(f"/api/jobs/{claimed.id}").json()
        assert job["status"] == "completed"
        assert job["checkpoint"].get("fully_deleted") is True
        assert store.exists("clip.mp4") is False
        assert store.exists("previews/clip.jpg") is False
        assert "vid-1" not in index.collections[col]["rows"]
        asset = client.get("/api/assets").json()["items"][0]
        assert asset["fully_deleted"] is True
        assert asset["s3_original_untouched"] is False
        assert asset["can_restore"] is False
        assert meta.list_frames(asset_id) == []
        denied = client.post(f"/api/assets/{asset_id}/restore")
        assert denied.status_code == 400


def test_default_app_has_no_seed(client: TestClient):
    assert client.get("/api/assets").json()["items"] == []
    assert client.get("/api/subtitles/associations").json()["items"] == []
    assert client.get("/api/exclusions").json()["items"] == []
    assert client.get("/api/jobs").json()["items"] == []
