from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from app.infra.config import Settings
from app.infra.file_store import LocalFileStore
from app.infra.object_keys import hosted_original_key, sanitize_relative_path
from app.main import create_app
from tests.conftest import FakeReader
from tests.fakes import MemoryEncoder, MemoryVectorIndex
from tests.media_fixtures import write_png_pattern


def _app(tmp_path: Path, store: LocalFileStore | None = None, encoder=None, index=None, reader=None):
    settings = Settings(sqlite_path=tmp_path / "db.sqlite", worker_enabled=False)
    return create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=reader or FakeReader("ok"),
        worker_enabled=False,
        file_store=store or LocalFileStore(tmp_path / "store", label="pytest"),
        work_root=tmp_path / "work",
        encoder=encoder,
        subtitle_encoder=encoder,
        index=index,
    )


def test_sanitize_rejects_traversal():
    try:
        sanitize_relative_path("../secret.jpg")
    except Exception as exc:
        assert "上级" in str(exc)
    else:
        raise AssertionError("traversal must be rejected")
    key = hosted_original_key("batch-1", "album/photo.jpg")
    assert key == "originals/batch-1/album/photo.jpg"


def test_tags_lexical_search_and_register_dashboard(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        source_id = client.post(
            "/api/sources",
            json={"name": "s", "endpoint": "http://x", "bucket": "b", "access_key": "k", "secret_key": "s"},
        ).json()["id"]
        imported = client.post(
            "/api/imports",
            json={
                "source_id": source_id,
                "items": [
                    {"object_key": "holiday/beach.jpg", "vectorize": False},
                    {"object_key": "notes.txt", "vectorize": False},
                ],
            },
        ).json()
        assert imported["jobs"] == []
        beach = next(a for a in imported["assets"] if a["object_key"].endswith("beach.jpg"))
        notes = next(a for a in imported["assets"] if a["object_key"].endswith("notes.txt"))
        tags = client.post(f"/api/assets/{beach['id']}/tags", json={"tag": "海边"}).json()["items"]
        assert tags == ["海边"]
        lexical = client.post("/api/search/lexical", json={"query": "海边"}).json()
        assert lexical["embedding_used"] is False
        assert lexical["items"][0]["id"] == beach["id"]
        assert "tag" in lexical["items"][0]["matched_on"]
        both = client.post("/api/search/lexical", json={"query": "beach"}).json()
        ids = {item["id"] for item in both["items"]}
        assert beach["id"] in ids
        assert notes["id"] not in ids
        dash = client.get("/api/jobs/dashboard").json()
        register = next(s for s in dash["stages"] if s["id"] == "register")
        encode = next(s for s in dash["stages"] if s["id"] == "encode")
        assert register["counts"]["completed"] == 2
        assert encode["counts"]["waiting"] == 0
        assert dash["resources"]["external_model"]["available"] is False
        assert dash["resources"]["external_model"]["status"] == "unavailable"
        queued = client.post(f"/api/assets/{notes['id']}/vectorize").json()
        assert queued["job"]["job_type"] == "index_asset"
        assert queued["job"]["status"] == "waiting"


def test_upload_preserves_relative_path_and_rejects_overwrite(tmp_path: Path):
    store = LocalFileStore(tmp_path / "store", label="pytest")
    with TestClient(_app(tmp_path, store)) as client:
        client.post(
            "/api/sources",
            json={
                "name": "platform",
                "endpoint": "http://x",
                "bucket": "pica-dev",
                "role": "storage",
                "is_platform_storage": True,
                "access_key": "k",
                "secret_key": "s",
            },
        )
        meta = json.dumps(
            [
                {"relative_path": "album/one.jpg", "vectorize": False},
                {"relative_path": "album/two.jpg", "vectorize": False},
            ]
        )
        response = client.post(
            "/api/uploads",
            data={"meta": meta},
            files=[
                ("files", ("one.jpg", BytesIO(b"one"), "image/jpeg")),
                ("files", ("two.jpg", BytesIO(b"two"), "image/jpeg")),
            ],
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["origin"] == "hosted"
        assert body["prefix"].startswith("originals/")
        keys = {item["object_key"] for item in body["stored"]}
        assert any(key.endswith("album/one.jpg") for key in keys)
        assert any(key.endswith("album/two.jpg") for key in keys)
        assert all(item["origin"] == "hosted" for item in body["assets"])
        bad = client.post(
            "/api/uploads",
            data={"meta": json.dumps([{"relative_path": "../x.jpg"}])},
            files=[("files", ("x.jpg", BytesIO(b"x"), "image/jpeg"))],
        )
        assert bad.status_code == 400

        again = client.post(
            "/api/uploads",
            data={"meta": json.dumps([{"relative_path": "album/one.jpg"}])},
            files=[("files", ("one.jpg", BytesIO(b"one"), "image/jpeg"))],
        )
        # 新批次目录，同名相对路径不会覆盖上一批次
        assert again.status_code == 201, again.text
        assert again.json()["prefix"] != body["prefix"]


def test_import_manual_subtitle_bind_does_not_autolink_by_name(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        source_id = client.post(
            "/api/sources",
            json={"name": "s", "endpoint": "http://x", "bucket": "b", "access_key": "k", "secret_key": "s"},
        ).json()["id"]
        imported = client.post(
            "/api/imports",
            json={
                "source_id": source_id,
                "items": [
                    {"object_key": "clip.mp4", "vectorize": False, "subtitle_object_key": "other.srt"},
                    {"object_key": "clip.srt", "vectorize": False},
                ],
            },
        )
        assert imported.status_code == 201, imported.text
        assocs = client.get("/api/subtitles/associations").json()["items"]
        assert len(assocs) == 1
        assert assocs[0]["subtitle_key"] == "other.srt"
        assert assocs[0]["video_key"] == "clip.mp4"


def test_status_resources_are_not_zero_filled_when_missing(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        data = client.get("/api/status").json()
        assert "resources" in data
        gpu = data["resources"]["gpu"]
        if not gpu.get("available"):
            assert gpu["status"] == "unavailable"
            assert gpu.get("utilization_percent") not in {0, 0.0}
        model = data["resources"]["external_model"]
        assert model["available"] is False
        assert model["status"] == "unavailable"
        if os.name == "nt":
            pica = data["resources"]["pica_process"]
            assert pica["available"] is True
            assert (pica.get("rss_bytes") or 0) > 0
            assert pica.get("cpu_percent") is not None
            host_cpu = data["resources"]["host"]["cpu"]
            assert host_cpu["available"] is True
            assert (host_cpu.get("logical_cpus") or 0) > 0
            assert host_cpu.get("cpu_percent") is not None
            host_mem = data["resources"]["host"]["memory"]
            assert host_mem["available"] is True
            assert (host_mem.get("total_bytes") or 0) > 0
            assert (host_mem.get("used_bytes") or 0) >= 0


def test_upload_limit_auto_cover_and_delete_by_asset(tmp_path: Path):
    encoder = MemoryEncoder(dim=8)
    index = MemoryVectorIndex(dim=8)
    img_path = tmp_path / "auto_cover.png"
    img_bytes = write_png_pattern(img_path, "checker")
    reader = FakeReader("ok", files={"auto_cover.png": img_path})
    with TestClient(_app(tmp_path, encoder=encoder, index=index, reader=reader)) as client:
        client.post(
            "/api/sources",
            json={
                "name": "platform",
                "endpoint": "http://x",
                "bucket": "b",
                "role": "storage",
                "is_platform_storage": True,
                "access_key": "k",
                "secret_key": "s",
            },
        )
        initial = client.get("/api/settings").json()
        assert initial["upload"]["max_upload_files"] == 20

        bad_limit = client.put("/api/settings", json={"upload": {"max_upload_files": 501}})
        assert bad_limit.status_code == 400

        updated_cfg = client.put("/api/settings", json={"upload": {"max_upload_files": 2}})
        assert updated_cfg.status_code == 200
        assert updated_cfg.json()["upload"]["max_upload_files"] == 2

        too_many = client.post(
            "/api/uploads",
            data={"meta": json.dumps([{"relative_path": f"{i}.png"} for i in range(3)])},
            files=[("files", (f"{i}.png", BytesIO(img_bytes), "image/png")) for i in range(3)],
        )
        assert too_many.status_code == 400
        assert too_many.json()["error"]["code"] == "upload_limit_exceeded"

        uploaded = client.post(
            "/api/uploads",
            data={"meta": json.dumps([{"relative_path": "auto_cover.png", "vectorize": True, "description": "棋盘测试图"}])},
            files=[("files", ("auto_cover.png", BytesIO(img_bytes), "image/png"))],
        )
        assert uploaded.status_code == 201, uploaded.text
        aid = uploaded.json()["assets"][0]["id"]

        jobs = client.app.state.container.jobs
        claimed = jobs.claim_next("test-worker")
        assert claimed is not None
        jobs.run_claimed(claimed, "test-worker")

        asset = client.get(f"/api/assets/{aid}").json()
        assert asset["cover_key"]
        assert asset["video_cover_locator"]
        cov = client.get(f"/api/assets/{aid}/cover")
        assert cov.status_code == 200
        assert len(cov.content) > 0

        assert client.get("/api/search/metadata?q=棋盘测试").json()["items"][0]["id"] == aid

        # Exclude & purge removes vectors by asset_id and excludes from lexical search
        client.post(f"/api/assets/{aid}/exclude")
        for _ in range(3):
            next_job = jobs.claim_next("test-worker")
            if next_job is None:
                break
            jobs.run_claimed(next_job, "test-worker")

        for col in index.collections.values():
            assert not any(row.get("asset_id") == aid for row in col["rows"].values())
        assert client.get("/api/search/metadata?q=棋盘测试").json()["items"] == []
