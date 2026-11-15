from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.infra.config import Settings
from app.infra.db import connect
from app.main import create_app
from tests.conftest import FakeReader


def test_health(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_does_not_claim_external_deps(client: TestClient):
    data = client.get("/api/status").json()
    assert data["sqlite"]["status"] == "ok"
    assert data["milvus"]["status"] == "unconfigured"
    assert data["embedding_model"]["status"] == "unconfigured"
    assert data["auth"]["status"] == "disabled"
    assert data["auth"]["secret_configured"] is False
    assert data["sample_interval_seconds"] == 5.0


def test_source_crud_and_probe_unconfigured(client: TestClient):
    created = client.post(
        "/api/sources",
        json={"name": "家庭 MinIO", "endpoint": "", "bucket": "", "role": "input"},
    )
    assert created.status_code == 201
    source = created.json()
    assert source["name"] == "家庭 MinIO"
    assert source["secret_configured"] is False
    assert "secret_key" not in source
    source_id = source["id"]

    listed = client.get("/api/sources").json()["items"]
    assert len(listed) == 1

    probed = client.post(f"/api/sources/{source_id}/probe").json()
    assert probed["last_probe_status"] == "failed"
    assert "未配置" in probed["last_probe_message"]

    browse = client.get(f"/api/sources/{source_id}/browse")
    assert browse.status_code == 503
    assert browse.json()["error"]["code"] == "not_configured"

    client.patch(f"/api/sources/{source_id}", json={"name": "主存储"})
    assert client.get(f"/api/sources/{source_id}").json()["name"] == "主存储"

    assert client.delete(f"/api/sources/{source_id}").json()["ok"] is True
    assert client.get("/api/sources").json()["items"] == []


def test_import_creates_assets_and_jobs(tmp_path: Path):
    settings = Settings(sqlite_path=tmp_path / "db.sqlite", worker_enabled=False)
    app = create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=FakeReader("ok"),
        worker_enabled=False,
    )
    with TestClient(app) as client:
        source_id = client.post(
            "/api/sources",
            json={
                "name": "ok-source",
                "endpoint": "http://127.0.0.1:9000",
                "bucket": "media",
                "access_key": "k",
                "secret_key": "s",
            },
        ).json()["id"]
        browsed = client.get(f"/api/sources/{source_id}/browse").json()["items"]
        assert any(item["key"].endswith("a.jpg") for item in browsed)

        imported = client.post(
            "/api/imports",
            json={
                "source_id": source_id,
                "items": [
                    {"object_key": "a.jpg", "vectorize": True},
                    {"object_key": "notes.txt", "vectorize": False},
                ],
            },
        )
        assert imported.status_code == 201
        body = imported.json()
        assert len(body["assets"]) == 2
        assert len(body["jobs"]) == 1
        assert body["jobs"][0]["status"] == "waiting"
        listed_jobs = client.get("/api/jobs").json()["items"]
        register_jobs = [j for j in listed_jobs if j["job_type"] == "register_asset"]
        assert len(register_jobs) == 2
        assert all(j["status"] == "completed" for j in register_jobs)
        assert client.get("/api/assets").json()["items"][0]["object_key"] in {"a.jpg", "notes.txt"}


def test_job_crud_persistence_and_worker(tmp_path: Path):
    from app.infra.file_store import LocalFileStore
    from tests.media_fixtures import ffmpeg_or_skip, generate_testsrc_video

    ffmpeg_or_skip()
    video = tmp_path / "clip.mp4"
    generate_testsrc_video(video, duration=1.2, fps=5)
    db = tmp_path / "jobs.sqlite"
    store = LocalFileStore(tmp_path / "store", label="pytest-temp")
    reader = FakeReader("ok", files={"clip.mp4": video})
    settings = Settings(sqlite_path=db, worker_enabled=True, worker_poll_seconds=0.05)
    app = create_app(
        settings,
        sqlite_path=db,
        source_reader=reader,
        worker_enabled=True,
        file_store=store,
        work_root=tmp_path / "work",
    )
    with TestClient(app) as client:
        source_id = client.post(
            "/api/sources",
            json={"name": "s", "endpoint": "http://x", "bucket": "b", "access_key": "k", "secret_key": "s"},
        ).json()["id"]
        job_id = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "clip.mp4", "vectorize": True}]},
        ).json()["jobs"][0]["id"]

        paused = client.post(f"/api/jobs/{job_id}/pause")
        # 可能已被 worker 取走，暂停失败也可接受
        if paused.status_code == 200:
            assert paused.json()["status"] == "paused"
            resumed = client.post(f"/api/jobs/{job_id}/resume")
            assert resumed.status_code == 200
            assert resumed.json()["status"] == "waiting"

        deadline = time.time() + 12
        status = None
        while time.time() < deadline:
            status = client.get(f"/api/jobs/{job_id}").json()
            if status["status"] in {"blocked", "failed"}:
                break
            time.sleep(0.05)
        assert status is not None
        assert status["status"] == "blocked"
        assert status["error_code"] == "embedding_pending"
        assert "媒体阶段已完成" in (status["error_message"] or "")
        assert "假向量" in (status["error_message"] or "") or "尚未" in (status["error_message"] or "")
        assert status["checkpoint"].get("media_done") is True

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
        assert saved["error_code"] == "embedding_pending"
        assert saved["checkpoint"].get("media_done") is True


def test_search_does_not_return_fake_hits(client: TestClient):
    response = client.post("/api/search", json={"query": "海边"})
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "search_unconfigured"
    assert "假" in body["error"]["message"] or "不可用" in body["error"]["message"]
    assert "items" not in body


def test_query_priority_flag(client: TestClient):
    response = client.post("/api/jobs/query-priority", json={"active": True})
    assert response.status_code == 200
    assert response.json()["query_priority"] is True


def test_settings_persist_topn_sample_refresh(client: TestClient):
    payload = {
        "sample": {"mode": "uniform_total", "uniform_total": 12, "interval_seconds": 5},
        "refresh": {"enabled": False, "interval_minutes": 90},
        "search": {"top_n": 15, "counting_mode": "asset", "fusion": "visual_first", "max_previews_per_video": 5, "subtitle_agg": "max", "oversample_factor": 4, "max_scan": 400, "metric": "cosine", "rrf_k": 60, "score_threshold": None},
    }
    saved = client.put("/api/settings", json=payload)
    assert saved.status_code == 200
    body = saved.json()
    assert body["sample"]["mode"] == "uniform_total"
    assert body["sample"]["uniform_total"] == 12
    assert body["refresh"]["enabled"] is False
    assert body["refresh"]["interval_minutes"] == 90
    assert body["search"]["top_n"] == 15
    again = client.get("/api/settings").json()
    assert again["sample"]["mode"] == "uniform_total"
    assert again["sample"]["uniform_total"] == 12
    assert again["refresh"]["interval_minutes"] == 90
    assert again["search"]["top_n"] == 15

def test_source_secrets_are_stored_but_never_echoed(tmp_path: Path):
    """S3 keys are persisted, but API / health / status / settings never re-echo them."""
    settings = Settings(sqlite_path=tmp_path / "db.sqlite", worker_enabled=False)
    app = create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=FakeReader("ok"),
        worker_enabled=False,
    )
    access = "AKIA_TEST_ACCESS_UNIQUE"
    secret = "wJalrXUtnFEMI/K7MDENG/TESTSECRET"
    with TestClient(app) as client:
        created = client.post(
            "/api/sources",
            json={
                "name": "minio",
                "endpoint": "http://127.0.0.1:9000",
                "bucket": "media",
                "region": "us-east-1",
                "access_key": access,
                "secret_key": secret,
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["access_key_configured"] is True
        assert body["secret_configured"] is True
        assert "access_key" not in body
        assert "secret_key" not in body
        assert access not in created.text
        assert secret not in created.text

        listed = client.get("/api/sources")
        got = client.get(f"/api/sources/{body['id']}")
        probed = client.post(f"/api/sources/{body['id']}/probe")
        patched = client.patch(f"/api/sources/{body['id']}", json={"name": "minio-2"})
        health = client.get("/api/health")
        status = client.get("/api/status")
        cfg = client.get("/api/settings")
        for resp in (listed, got, probed, patched, health, status, cfg):
            assert resp.status_code < 500
            assert access not in resp.text
            assert secret not in resp.text
            if resp.headers.get("content-type", "").startswith("application/json"):
                payload = resp.json()
                if isinstance(payload, dict) and "secret_configured" in payload:
                    assert "secret_key" not in payload
                    assert "access_key" not in payload
        # stored for later use (not a success-of-connection signal by itself)
        assert got.json()["secret_configured"] is True
        assert probed.json().get("last_probe_status") in {"ok", "failed"}
