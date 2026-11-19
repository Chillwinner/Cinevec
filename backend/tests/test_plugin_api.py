from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from app.infra.config import Settings
from app.infra.file_store import LocalFileStore
from app.main import create_app
from tests.conftest import FakeReader
from tests.fakes import MemoryEncoder, MemoryVectorIndex
from tests.media_fixtures import write_png_pattern


def _app(tmp_path: Path, encoder=None, index=None):
    settings = Settings(sqlite_path=tmp_path / "db.sqlite", worker_enabled=False)
    return create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=FakeReader("ok"),
        worker_enabled=False,
        file_store=LocalFileStore(tmp_path / "store", label="pytest"),
        work_root=tmp_path / "work",
        encoder=encoder,
        subtitle_encoder=encoder,
        index=index,
    )


def _platform(client: TestClient) -> None:
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


def _token(client: TestClient) -> str:
    created = client.post("/api/settings/api-tokens", json={"name": "test-plugin"})
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    assert token.startswith("pica_")
    listed = client.get("/api/settings/api-tokens").json()["items"]
    assert listed[0]["name"] == "test-plugin"
    assert "token" not in listed[0]
    return token


def test_v1_requires_bearer(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/api/v1/health").status_code == 401
        token = _token(client)
        ok = client.get("/api/v1/health", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200
        assert ok.json()["api"] == "v1"
        bad = client.get("/api/v1/health", headers={"Authorization": "Bearer pica_nope"})
        assert bad.status_code == 401


def test_plugin_import_copy_and_progress(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        _platform(client)
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        meta = json.dumps([{"relative_path": "album/one.png", "vectorize": True, "copy": True}])
        response = client.post(
            "/api/v1/imports",
            headers=headers,
            data={"meta": meta},
            files=[("files", ("one.png", BytesIO(b"png-bytes"), "image/png"))],
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["batch_id"]
        assert body["jobs"][0]["phase"] == "queued"
        assert body["jobs"][0]["searchable"] is False
        assert "checkpoint" not in body["jobs"][0]
        assert body["assets"][0]["copy"] is True
        assert "object_key" not in body["assets"][0]
        job_id = body["jobs"][0]["id"]
        polled = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
        assert polled.status_code == 200
        assert polled.json()["id"] == job_id
        batch = client.get(f"/api/v1/batches/{body['batch_id']}", headers=headers)
        assert batch.status_code == 200
        assert batch.json()["counts"]["queued"] == 1
        internal = client.get(f"/api/jobs/{job_id}").json()
        assert internal["payload"]["batch_id"] == body["batch_id"]


def test_plugin_rejects_subtitle_and_copy_false_needs_ref(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        _platform(client)
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        refused = client.post(
            "/api/v1/imports",
            headers=headers,
            data={"meta": json.dumps([{"relative_path": "a.srt"}])},
            files=[("files", ("a.srt", BytesIO(b"1"), "text/plain"))],
        )
        assert refused.status_code == 400
        assert refused.json()["error"]["code"] == "unsupported_kind"
        missing = client.post(
            "/api/v1/imports",
            headers=headers,
            data={"meta": json.dumps([{"relative_path": "a.jpg", "copy": False}])},
            files=[("files", ("a.jpg", BytesIO(b"jpg"), "image/jpeg"))],
        )
        assert missing.status_code == 400


def test_plugin_copy_false_skips_originals(tmp_path: Path):
    work = tmp_path / "work"
    with TestClient(_app(tmp_path)) as client:
        _platform(client)
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        meta = json.dumps(
            [
                {
                    "relative_path": "shot.jpg",
                    "copy": False,
                    "vectorize": True,
                    "ref": {"ref_id": "ext-1", "locator": "plugin://page/1", "params": {"n": 1}},
                }
            ]
        )
        response = client.post(
            "/api/v1/imports",
            headers=headers,
            data={"meta": meta},
            files=[("files", ("shot.jpg", BytesIO(b"jpeg-bytes"), "image/jpeg"))],
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["assets"][0]["copy"] is False
        assert body["assets"][0]["origin"] == "plugin"
        assert body["assets"][0]["ref"]["ref_id"] == "ext-1"
        job_id = body["jobs"][0]["id"]
        internal = client.get(f"/api/jobs/{job_id}").json()
        work_path = Path(internal["checkpoint"]["work_path"])
        assert work_path.is_file()
        assert work_path.read_bytes() == b"jpeg-bytes"
        assert str(work) in str(work_path)
        store = tmp_path / "store"
        originals = list(store.rglob("originals/**/*")) if store.exists() else []
        assert not any(path.is_file() and "originals" in str(path) for path in originals)
        asset = client.get(f"/api/v1/assets/{body['assets'][0]['id']}", headers=headers).json()
        assert asset["searchable"] is False
        assert asset["ref"]["locator"] == "plugin://page/1"


def test_revoke_token(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        token = _token(client)
        item = client.get("/api/settings/api-tokens").json()["items"][0]
        revoked = client.delete(f"/api/settings/api-tokens/{item['id']}")
        assert revoked.status_code == 200
        denied = client.get("/api/v1/health", headers={"Authorization": f"Bearer {token}"})
        assert denied.status_code == 401


def test_v1_store_original_false_search_three_routes_patch_and_cover(tmp_path: Path):
    encoder = MemoryEncoder(dim=8)
    index = MemoryVectorIndex(dim=8)
    with TestClient(_app(tmp_path, encoder=encoder, index=index)) as client:
        _platform(client)
        token = _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        img_bytes = write_png_pattern(None, "checker")
        cov_bytes = write_png_pattern(None, "checker2")

        missing_url = client.post(
            "/api/v1/imports",
            headers=headers,
            data={"meta": json.dumps([{"relative_path": "a.png", "store_original": False}])},
            files=[("files", ("a.png", BytesIO(img_bytes), "image/png"))],
        )
        assert missing_url.status_code == 400

        meta = json.dumps(
            [
                {
                    "relative_path": "sunset/boat.png",
                    "vectorize": True,
                    "store_original": False,
                    "source_url": "https://example.com/v/42",
                    "description": "海边落日帆船",
                }
            ]
        )
        imported = client.post(
            "/api/v1/imports",
            headers=headers,
            data={"meta": meta},
            files=[
                ("files", ("boat.png", BytesIO(img_bytes), "image/png")),
                ("cover", ("cover.png", BytesIO(cov_bytes), "image/png")),
            ],
        )
        assert imported.status_code == 201, imported.text
        body = imported.json()
        aid = body["assets"][0]["id"]
        assert body["assets"][0]["store_original"] is False
        assert body["assets"][0]["copy"] is False
        assert body["assets"][0]["source_url"] == "https://example.com/v/42"
        assert body["assets"][0]["description"] == "海边落日帆船"

        cov_get = client.get(f"/api/v1/assets/{aid}/cover", headers=headers)
        assert cov_get.status_code == 200
        assert cov_get.content.startswith(b"\xff\xd8")
        cov_query = client.get(f"/api/v1/assets/{aid}/cover?access_token={token}")
        assert cov_query.status_code == 200
        assert cov_query.content == cov_get.content

        job_id = body["jobs"][0]["id"]
        work_path = Path(client.get(f"/api/jobs/{job_id}").json()["checkpoint"]["work_path"])
        assert work_path.is_file()

        jobs = client.app.state.container.jobs
        claimed = jobs.claim_next("test-worker")
        assert claimed is not None
        jobs.run_claimed(claimed, "test-worker")

        assert not work_path.exists()
        store = tmp_path / "store"
        originals = [p for p in store.rglob("*") if p.is_file() and "originals" in p.parts]
        assert originals == []

        asset = client.get(f"/api/v1/assets/{aid}", headers=headers).json()
        assert asset["searchable"] is True

        txt = client.post("/api/v1/search/text", headers=headers, json={"query": "海边", "top_n": 5})
        assert txt.status_code == 200, txt.text
        hit = next(h for h in txt.json()["items"] if h["asset_id"] == aid)
        assert hit["description"] == "海边落日帆船"
        assert hit["source_url"] == "https://example.com/v/42"
        assert hit["video_cover_locator"]

        img = client.post(
            "/api/v1/search/image",
            headers=headers,
            files={"image": ("q.png", BytesIO(img_bytes), "image/png")},
        )
        assert img.status_code == 200, img.text
        assert any(h["asset_id"] == aid for h in img.json()["items"])

        md_get = client.get("/api/v1/search/metadata?q=落日帆船", headers=headers)
        assert md_get.status_code == 200
        assert md_get.json()["items"][0]["id"] == aid
        assert "description" in md_get.json()["items"][0]["matched_on"]

        patched = client.patch(
            f"/api/v1/assets/{aid}",
            headers=headers,
            json={"description": "新描述测试", "source_url": "https://example.com/v/99", "tags": ["夕阳", "海景"]},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["description"] == "新描述测试"
        assert patched.json()["source_url"] == "https://example.com/v/99"
        assert set(patched.json()["tags"]) == {"夕阳", "海景"}

        md_post = client.post("/api/v1/search/metadata", headers=headers, json={"query": "夕阳"})
        assert md_post.status_code == 200
        assert md_post.json()["items"][0]["id"] == aid

        new_cov = write_png_pattern(None, "black")
        rep = client.post(
            f"/api/v1/assets/{aid}/cover",
            headers=headers,
            files={"cover": ("new.png", BytesIO(new_cov), "image/png")},
        )
        assert rep.status_code == 200
        rep_bytes = client.get(f"/api/v1/assets/{aid}/cover", headers=headers).content
        assert rep_bytes.startswith(b"\xff\xd8")
        assert rep_bytes != cov_get.content


def test_v1_enhancements_tags_display_name_ref_id_lifecycle_and_auth_locators(tmp_path: Path):
    encoder = MemoryEncoder(dim=8)
    index = MemoryVectorIndex(dim=8)
    with TestClient(_app(tmp_path, encoder=encoder, index=index)) as client:
        _platform(client)
        # Enable web auth so /api/previews and /api/assets require JWT, proving /api/v1 locators work with API token
        enable = client.post(
            "/api/auth/enable",
            json={
                "username": "admin",
                "password": "password12345",
                "password_confirm": "password12345",
                "signing_key_mode": "custom",
                "custom_signing_key": "test-signing-key-aaaa-bbbb-cccc-dddd",
                "confirm_customization": True,
            },
        )
        assert enable.status_code == 200, enable.text
        jwt = enable.json()["token"]
        jwt_headers = {"Authorization": f"Bearer {jwt}"}

        created_tok = client.post("/api/settings/api-tokens", headers=jwt_headers, json={"name": "tg-plugin"})
        assert created_tok.status_code == 201, created_tok.text
        token = created_tok.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        img_bytes = write_png_pattern(None, "checker")
        meta = json.dumps(
            [
                {
                    "relative_path": "clip.png",
                    "display_name": "Custom Clip Name",
                    "vectorize": True,
                    "store_original": False,
                    "source_url": "https://t.me/testchan/101",
                    "tags": ["running", "outdoor"],
                    "ref": {"ref_id": "tg_-1001_101", "locator": "https://t.me/testchan/101"},
                }
            ]
        )
        imp = client.post(
            "/api/v1/imports",
            headers=headers,
            data={"meta": meta},
            files=[("files", ("clip.png", BytesIO(img_bytes), "image/png"))],
        )
        assert imp.status_code == 201, imp.text
        asset_out = imp.json()["assets"][0]
        aid = asset_out["id"]
        assert asset_out["display_name"] == "Custom Clip Name"
        assert set(asset_out["tags"]) == {"running", "outdoor"}
        assert asset_out["lifecycle"] == "active"

        jobs = client.app.state.container.jobs
        claimed = jobs.claim_next("test-worker")
        assert claimed is not None
        jobs.run_claimed(claimed, "test-worker")

        # Exact ref_id lookup
        found = client.get("/api/v1/assets?ref_id=tg_-1001_101", headers=headers)
        assert found.status_code == 200
        assert found.json()["total"] == 1
        assert found.json()["items"][0]["id"] == aid

        # Locators in text, image, and metadata search must use /api/v1/ and succeed with API token
        txt = client.post("/api/v1/search/text", headers=headers, json={"query": "outdoor", "top_n": 5})
        assert txt.status_code == 200
        txt_hit = next(h for h in txt.json()["items"] if h["asset_id"] == aid)
        assert txt_hit["video_cover_locator"].startswith(f"/api/v1/assets/{aid}/cover")
        assert txt_hit["previews"][0]["locator"].startswith(f"/api/v1/previews/{aid}/")
        assert client.get(f"/api/previews/{aid}/frames/0", headers=headers).status_code == 401
        assert client.get(txt_hit["previews"][0]["locator"], headers=headers).status_code == 200
        assert client.get(txt_hit["video_cover_locator"], headers=headers).status_code == 200

        img = client.post(
            "/api/v1/search/image",
            headers=headers,
            files={"image": ("q.png", BytesIO(img_bytes), "image/png")},
        )
        assert img.status_code == 200
        img_hit = next(h for h in img.json()["items"] if h["asset_id"] == aid)
        assert img_hit["previews"][0]["locator"].startswith(f"/api/v1/previews/{aid}/")

        md = client.get("/api/v1/search/metadata?q=Custom Clip Name", headers=headers)
        assert md.status_code == 200
        md_item = md.json()["items"][0]
        assert md_item["cover_locator"].startswith(f"/api/v1/assets/{aid}/cover")
        assert md_item["video_cover_locator"].startswith(f"/api/v1/assets/{aid}/cover")

        # Lifecycle updates via PATCH /api/v1/assets/{id}
        bad_lc = client.patch(f"/api/v1/assets/{aid}", headers=headers, json={"lifecycle": "bogus"})
        assert bad_lc.status_code == 400

        invalidated = client.patch(f"/api/v1/assets/{aid}", headers=headers, json={"lifecycle": "source_invalid"})
        assert invalidated.status_code == 200
        assert invalidated.json()["lifecycle"] == "source_invalid"

        restored = client.patch(f"/api/v1/assets/{aid}", headers=headers, json={"lifecycle": "active"})
        assert restored.status_code == 200
        assert restored.json()["lifecycle"] == "active"
