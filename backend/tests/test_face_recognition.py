from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.domain.face import FACE_DIM, DetectedFace, crop_face_jpeg
from app.domain.models import Asset, Source
from app.infra.config import Settings
from app.infra.file_store import LocalFileStore
from app.main import create_app
from tests.conftest import FakeReader
from tests.fakes import MemoryEncoder, MemoryVectorIndex


def _basis_vec(index: int, dim: int = FACE_DIM) -> list[float]:
    v = [0.0] * dim
    v[index] = 1.0
    return v


def _make_portrait_png(*, has_a: bool, has_b: bool) -> bytes:
    img = Image.new("RGB", (160, 100), (24, 24, 30))
    draw = ImageDraw.Draw(img)
    if has_a:
        # Warm crimson portrait on the left
        draw.ellipse((12, 14, 72, 84), fill=(220, 72, 64))
        draw.ellipse((28, 36, 36, 44), fill=(255, 255, 255))
        draw.ellipse((48, 36, 56, 44), fill=(255, 255, 255))
    if has_b:
        # Cool sapphire portrait on the right
        draw.ellipse((88, 14, 148, 84), fill=(64, 128, 232))
        draw.ellipse((104, 36, 112, 44), fill=(255, 255, 255))
        draw.ellipse((124, 36, 132, 44), fill=(255, 255, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _region_avg(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[float, float, float]:
    crop = img.crop(box)
    raw = crop.tobytes()
    n = max(1, len(raw) // 3)
    return (
        sum(raw[0::3]) / n,
        sum(raw[1::3]) / n,
        sum(raw[2::3]) / n,
    )


class FakeFaceRecognizer:
    """Deterministic 512-d face recognizer keyed on left/right portrait regions."""

    def __init__(self, enabled_getter=None) -> None:
        self._enabled_getter = enabled_getter

    def available(self) -> bool:
        if callable(self._enabled_getter):
            return bool(self._enabled_getter())
        return True

    def status_message(self) -> str:
        return "测试用 512 维人脸识别器"

    def public_status(self) -> dict:
        enabled = self.available()
        return {
            "status": "ok" if enabled else "disabled",
            "enabled": enabled,
            "available": enabled,
            "backend": "fake_face",
            "model": "buffalo_s_fake",
            "dim": FACE_DIM,
            "message": self.status_message(),
        }

    def detect_and_encode(self, image_bytes: bytes) -> list[DetectedFace]:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        if img.size != (160, 100):
            img = img.resize((160, 100))
        out: list[DetectedFace] = []
        r_a, _, b_a = _region_avg(img, (16, 18, 68, 80))
        if r_a - b_a > 60:
            bbox_a = [12.0, 14.0, 72.0, 84.0]
            out.append(
                DetectedFace(
                    bbox=bbox_a,
                    det_score=0.98,
                    embedding=_basis_vec(0),
                    crop_jpeg=crop_face_jpeg(image_bytes, bbox_a, size=128),
                )
            )
        r_b, _, b_b = _region_avg(img, (92, 18, 144, 80))
        if b_b - r_b > 60:
            bbox_b = [88.0, 14.0, 148.0, 84.0]
            out.append(
                DetectedFace(
                    bbox=bbox_b,
                    det_score=0.96,
                    embedding=_basis_vec(1),
                    crop_jpeg=crop_face_jpeg(image_bytes, bbox_b, size=128),
                )
            )
        return out


def _seed_asset_with_preview(
    app,
    store: LocalFileStore,
    *,
    asset_id: str,
    source_id: str,
    key: str,
    image_bytes: bytes,
    kind: str = "image",
    time_seconds: float = 0.0,
) -> Asset:
    now = datetime.now(timezone.utc)
    meta = app.state.container.faces._metadata
    asset = Asset(
        id=asset_id,
        source_id=source_id,
        object_key=key,
        kind=kind,  # type: ignore[arg-type]
        vectorize=True,
        status="indexed",
        lifecycle="active",
        index_state="ready",
        version=1,
        ready_version=1,
        origin="hosted",
        display_name=key.rsplit("/", 1)[-1],
        created_at=now,
        updated_at=now,
    )
    meta.upsert_asset(asset)
    prev_key = f"previews/{asset_id}/frame_0.png"
    store.put_bytes(prev_key, image_bytes, "image/png")
    meta.upsert_frame(
        asset_id,
        f"job-{asset_id}",
        SimpleNamespace(
            index=0,
            time_seconds=time_seconds,
            kept=True,
            drop_reason=None,
            preview_key=prev_key,
            model_input_key=prev_key,
            extra={},
        ),
    )
    return asset


def test_face_disabled_by_default_and_gated(tmp_path: Path):
    settings = Settings(sqlite_path=tmp_path / "db.sqlite", worker_enabled=False)
    assert settings.face_enabled is False

    app = create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=FakeReader("ok"),
        worker_enabled=False,
        file_store=LocalFileStore(tmp_path / "store", label="pytest"),
        work_root=tmp_path / "work",
        encoder=MemoryEncoder(),
        subtitle_encoder=MemoryEncoder(),
        index=MemoryVectorIndex(),
        face_index=MemoryVectorIndex(model_id="face_fake", dim=FACE_DIM),
    )
    with TestClient(app) as client:
        st = client.get("/api/status").json()
        assert st["face_recognition"]["enabled"] is False
        assert st["face_recognition"]["status"] == "disabled"

        portrait = _make_portrait_png(has_a=True, has_b=False)
        res = client.post("/api/search/face", files={"image": ("q.png", portrait, "image/png")})
        assert res.status_code in (400, 503)
        assert res.json()["error"]["code"] == "face_disabled"


def test_face_clustering_naming_tag_sync_multisearch_and_purge(tmp_path: Path):
    db_path = tmp_path / "face.sqlite"
    store = LocalFileStore(tmp_path / "store", label="pytest")
    flag = {"enabled": False}
    rec = FakeFaceRecognizer(enabled_getter=lambda: flag["enabled"])
    settings = Settings(sqlite_path=db_path, worker_enabled=False)

    app = create_app(
        settings,
        sqlite_path=db_path,
        source_reader=FakeReader("ok"),
        worker_enabled=False,
        file_store=store,
        work_root=tmp_path / "work",
        encoder=MemoryEncoder(),
        subtitle_encoder=MemoryEncoder(),
        index=MemoryVectorIndex(),
        face_recognizer=rec,
        face_index=MemoryVectorIndex(model_id="face_fake", dim=FACE_DIM),
    )

    img_a = _make_portrait_png(has_a=True, has_b=False)
    img_ab = _make_portrait_png(has_a=True, has_b=True)
    img_b = _make_portrait_png(has_a=False, has_b=True)

    with TestClient(app) as client:
        # Enable face recognition in runtime settings
        flag["enabled"] = True
        put_res = client.put(
            "/api/settings",
            json={"face": {"enabled": True, "cluster_threshold": 0.45, "sync_person_tags": True}},
        )
        assert put_res.status_code == 200
        assert put_res.json()["face"]["enabled"] is True

        # Seed source + 3 assets
        now = datetime.now(timezone.utc)
        src = Source(
            id="src-1",
            name="hosted",
            endpoint="local",
            bucket="pica-dev",
            region="us-east-1",
            role="both",
            is_platform_storage=True,
            access_key_configured=True,
            secret_configured=True,
            last_probe_status="ok",
            last_probe_message="ok",
            created_at=now,
            updated_at=now,
        )
        meta = app.state.container.faces._metadata
        meta.add_source(src, access_key="k", secret_key="s")
        _seed_asset_with_preview(app, store, asset_id="asset-a", source_id="src-1", key="alice.png", image_bytes=img_a)
        _seed_asset_with_preview(
            app,
            store,
            asset_id="asset-ab",
            source_id="src-1",
            key="team.mp4",
            image_bytes=img_ab,
            kind="video",
            time_seconds=12.5,
        )
        _seed_asset_with_preview(app, store, asset_id="asset-b", source_id="src-1", key="bob.png", image_bytes=img_b)

        # Backfill faces across all seeded assets -> 2 people, 4 faces
        bf = client.post("/api/faces/backfill", json={"force": True})
        assert bf.status_code == 200, bf.text
        assert bf.json()["processed_assets"] == 3
        assert bf.json()["total_faces"] == 4
        assert bf.json()["people_count"] == 2

        people = client.get("/api/people").json()["items"]
        assert len(people) == 2
        # Locate Person A (in asset-a and asset-ab) and Person B (in asset-ab and asset-b)
        asset_a_people = client.get("/api/assets/asset-a/faces").json()["people"]
        asset_b_people = client.get("/api/assets/asset-b/faces").json()["people"]
        assert len(asset_a_people) == 1
        assert len(asset_b_people) == 1
        person_a_id = asset_a_people[0]["id"]
        person_b_id = asset_b_people[0]["id"]
        assert person_a_id != person_b_id

        # Crop endpoint returns valid JPEG bytes
        crop_id = client.get("/api/assets/asset-a/faces").json()["faces"][0]["id"]
        crop_resp = client.get(f"/api/faces/{crop_id}/crop")
        assert crop_resp.status_code == 200
        assert crop_resp.headers["content-type"] == "image/jpeg"
        assert crop_resp.content[:2] == b"\xff\xd8"

        # Rename Person A -> "张三": auto-writes "张三" into asset_tags & searchable via lexical
        rename = client.patch(f"/api/people/{person_a_id}", json={"name": "张三"})
        assert rename.status_code == 200, rename.text
        assert rename.json()["display_name"] == "张三"

        def _tags_of(aid: str) -> list[str]:
            return meta.list_tags_for_assets([aid]).get(aid, [])

        assert "张三" in _tags_of("asset-a")
        assert "张三" in _tags_of("asset-ab")
        assert "张三" not in _tags_of("asset-b")

        lex = client.post("/api/search/lexical", json={"query": "张三"}).json()["items"]
        lex_ids = {item["id"] for item in lex}
        assert lex_ids == {"asset-a", "asset-ab"}

        # Rename "张三" -> "李四": removes old synced tag and adds new tag
        rename2 = client.patch(f"/api/people/{person_a_id}", json={"name": "李四"})
        assert rename2.status_code == 200
        assert "张三" not in _tags_of("asset-a")
        assert "李四" in _tags_of("asset-a")
        assert "李四" in _tags_of("asset-ab")

        # Multi-face search on /api/search/face (default face_index=0 -> A, face_index=1 -> B)
        q_default = client.post("/api/search/face", files={"image": ("group.png", img_ab, "image/png")})
        assert q_default.status_code == 200, q_default.text
        body0 = q_default.json()
        assert len(body0["detected_faces"]) == 2
        assert body0["detected_faces"][0]["crop_data_url"].startswith("data:image/jpeg;base64,")
        assert body0["selected_face_index"] == 0
        hit_ids0 = {h["asset_id"] for h in body0["items"]}
        assert hit_ids0 == {"asset-a", "asset-ab"}
        assert all(h["person_name"] == "李四" for h in body0["items"])

        q_second = client.post(
            "/api/search/face",
            files={"image": ("group.png", img_ab, "image/png")},
            data={"face_index": "1"},
        )
        assert q_second.status_code == 200
        body1 = q_second.json()
        assert body1["selected_face_index"] == 1
        assert {h["asset_id"] for h in body1["items"]} == {"asset-b", "asset-ab"}

        # Verify /api/v1/search/face & /api/v1/faces/{id}/crop with Bearer token
        tok = client.post("/api/settings/api-tokens", json={"name": "tg"}).json()["token"]
        v1_headers = {"Authorization": f"Bearer {tok}"}
        v1_res = client.post(
            "/api/v1/search/face",
            headers=v1_headers,
            files={"image": ("group.png", img_ab, "image/png")},
            data={"face_index": "0"},
        )
        assert v1_res.status_code == 200, v1_res.text
        v1_body = v1_res.json()
        assert v1_body["items"][0]["matched_face_crop_locator"].startswith("/api/v1/faces/")
        v1_crop = client.get(v1_body["items"][0]["matched_face_crop_locator"], headers=v1_headers)
        assert v1_crop.status_code == 200

        # Recluster preserves locked/named person and keeps unnamed group intact
        recluster = client.post("/api/faces/recluster", json={"threshold": 0.45})
        assert recluster.status_code == 200
        assert client.get(f"/api/people/{person_a_id}").json()["display_name"] == "李四"

        # Reassign face from B to a brand-new named person "王五", then merge back into 李四
        face_b_solo_id = client.get("/api/assets/asset-b/faces").json()["faces"][0]["id"]
        reassigned = client.post(
            f"/api/faces/{face_b_solo_id}/reassign",
            json={"new_person_name": "王五"},
        )
        assert reassigned.status_code == 200
        new_pid = reassigned.json()["person"]["id"]
        assert "王五" in _tags_of("asset-b")

        merged = client.post("/api/people/merge", json={"target_id": person_a_id, "source_ids": [new_pid]})
        assert merged.status_code == 200
        assert merged.json()["merged_faces"] == 1
        assert "王五" not in _tags_of("asset-b")
        assert "李四" in _tags_of("asset-b")

        # Excluding + purging asset-a removes its faces and updates person counts
        _, purge_job = app.state.container.lifecycle.exclude_asset("asset-a")
        app.state.container.lifecycle.process_purge(purge_job)
        assert meta.list_faces_by_asset("asset-a") == []
        updated_a = client.get(f"/api/people/{person_a_id}").json()
        # Now 李四 remains on asset-ab and asset-b -> 2 assets
        assert updated_a["asset_count"] == 2


def test_pure_onnx_recognizer_helpers_and_exif_crop(tmp_path: Path):
    from app.infra.face_insightface import InsightFaceLocalRecognizer, align_face_112

    rec = InsightFaceLocalRecognizer(model_name="buffalo_sc", cache_dir=tmp_path)
    # In this venv onnxruntime and numpy are installed without needing insightface/cv2
    assert rec._deps_installed() is True
    assert rec.available() is True

    # Identity 5-pt alignment preserves 112x112 size and portrait center
    canvas = Image.new("RGB", (200, 200), (20, 30, 40))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((45, 45, 115, 115), fill=(220, 120, 80))
    kps_shifted = [
        (38.2946 + 20.0, 51.6963 + 20.0),
        (73.5318 + 20.0, 51.5014 + 20.0),
        (56.0252 + 20.0, 71.7366 + 20.0),
        (41.5493 + 20.0, 92.3655 + 20.0),
        (70.7299 + 20.0, 92.2041 + 20.0),
    ]
    aligned = align_face_112(canvas, kps_shifted)
    assert aligned.size == (112, 112)
    center_px = aligned.getpixel((56, 70))
    assert center_px[0] > 180 and center_px[2] < 120

    # EXIF orientation tag (6 = 90 CW raw -> transposed portrait) is honored by crop_face_jpeg
    raw = Image.new("RGB", (200, 100), (10, 10, 10))
    d2 = ImageDraw.Draw(raw)
    # Drawn at (0..90, 0..90) in raw; after EXIF 6 (rotate 270 CW = right side in transposed 100x200)
    d2.rectangle((0, 0, 90, 90), fill=(240, 40, 40))
    exif = raw.getexif()
    exif[274] = 6
    buf = BytesIO()
    raw.save(buf, format="JPEG", exif=exif.tobytes())
    cropped_bytes = crop_face_jpeg(buf.getvalue(), [0.5, 0.0, 1.0, 0.5], size=64, margin=0.0)
    cropped_img = Image.open(BytesIO(cropped_bytes)).convert("RGB")
    px = cropped_img.getpixel((32, 32))
    assert px[0] > 180 and px[1] < 90


def test_flip_tta_averages_mirrored_embeddings(tmp_path: Path):
    import numpy as np
    from app.domain.face import l2_normalize
    from app.infra.face_insightface import InsightFaceLocalRecognizer

    class _FakeInput:
        name = "input.1"

    class _FakeRecSession:
        def __init__(self) -> None:
            self.blobs: list[np.ndarray] = []

        def get_inputs(self):
            return [_FakeInput()]

        def run(self, _out, feed: dict):
            blob = feed["input.1"]
            self.blobs.append(blob.copy())
            # Distinguish left-heavy vs right-heavy R channel so flip changes the output vector
            left_mean = float(blob[0, 0, :, :56].mean())
            right_mean = float(blob[0, 0, :, 56:].mean())
            vec = np.zeros((1, FACE_DIM), dtype=np.float32)
            vec[0, 0] = max(0.0, left_mean + 2.0)
            vec[0, 1] = max(0.0, right_mean + 2.0)
            return [vec]

    chip = Image.new("RGB", (112, 112), (0, 0, 0))
    ImageDraw.Draw(chip).rectangle((0, 0, 40, 112), fill=(255, 0, 0))

    sess_no_tta = _FakeRecSession()
    rec_no_tta = InsightFaceLocalRecognizer(model_name="buffalo_sc", cache_dir=tmp_path, flip_tta=False)
    v_single = rec_no_tta._encode_arcface(sess_no_tta, chip)
    assert len(sess_no_tta.blobs) == 1
    assert v_single[0] > v_single[1]

    sess_tta = _FakeRecSession()
    rec_tta = InsightFaceLocalRecognizer(model_name="buffalo_sc", cache_dir=tmp_path, flip_tta=True)
    v_tta = rec_tta._encode_arcface(sess_tta, chip)
    assert len(sess_tta.blobs) == 2
    # Second pass must be horizontally mirrored
    np.testing.assert_allclose(sess_tta.blobs[1][0], sess_tta.blobs[0][0, :, :, ::-1], atol=1e-5)
    # Asymmetric left bar + flipped right bar average cleanly to symmetric unit vector [1/sqrt(2), 1/sqrt(2), ...]
    expected = l2_normalize([1.0, 1.0] + [0.0] * (FACE_DIM - 2))
    assert abs(v_tta[0] - expected[0]) < 1e-3
    assert abs(v_tta[1] - expected[1]) < 1e-3


def test_static_image_face_index_prefers_original_object_bytes(tmp_path: Path):
    db_path = tmp_path / "hires.sqlite"
    store = LocalFileStore(tmp_path / "store", label="pytest")
    recorded_bytes: list[bytes] = []

    class _RecordingRecognizer(FakeFaceRecognizer):
        def detect_and_encode(self, image_bytes: bytes) -> list[DetectedFace]:
            recorded_bytes.append(image_bytes)
            return super().detect_and_encode(image_bytes)

    settings = Settings(sqlite_path=db_path, worker_enabled=False, face_enabled=True)
    app = create_app(
        settings,
        sqlite_path=db_path,
        source_reader=FakeReader("ok"),
        worker_enabled=False,
        file_store=store,
        work_root=tmp_path / "work",
        encoder=MemoryEncoder(),
        subtitle_encoder=MemoryEncoder(),
        index=MemoryVectorIndex(),
        face_recognizer=_RecordingRecognizer(lambda: True),
        face_index=MemoryVectorIndex(model_id="face_fake", dim=FACE_DIM),
    )

    hires_png = _make_portrait_png(has_a=True, has_b=False)
    lowres_preview_png = _make_portrait_png(has_a=False, has_b=True)
    assert hires_png != lowres_preview_png

    with TestClient(app):
        container = app.state.container
        container.app_settings.update_face({"enabled": True})
        meta = container.faces._metadata
        now = datetime.now(timezone.utc)
        meta.add_source(
            Source(
                id="src-1",
                name="hosted",
                endpoint="local",
                bucket="b",
                region="us-east-1",
                role="both",
                is_platform_storage=True,
                access_key_configured=True,
                secret_configured=True,
                last_probe_status="ok",
                last_probe_message="ok",
                created_at=now,
                updated_at=now,
            ),
            access_key="k",
            secret_key="s",
        )
        asset = Asset(
            id="img-hires",
            source_id="src-1",
            object_key="originals/img-hires/full.png",
            kind="image",
            vectorize=True,
            status="indexed",
            lifecycle="active",
            index_state="ready",
            version=1,
            ready_version=1,
            origin="hosted",
            display_name="full.png",
            created_at=now,
            updated_at=now,
        )
        meta.upsert_asset(asset)
        store.put_bytes(asset.object_key, hires_png, "image/png")
        store.put_bytes("previews/img-hires/frame_0.png", lowres_preview_png, "image/png")
        meta.upsert_frame(
            "img-hires",
            "job-1",
            SimpleNamespace(
                index=0,
                time_seconds=0.0,
                kept=True,
                drop_reason=None,
                preview_key="previews/img-hires/frame_0.png",
                model_input_key="previews/img-hires/frame_0.png",
                extra={},
            ),
        )

        container.faces.index_asset_faces("img-hires")
        assert recorded_bytes[-1] == hires_png

        # When full original object is unavailable, gracefully fall back to preview_key
        store.delete_objects([asset.object_key])
        container.faces.index_asset_faces("img-hires")
        assert recorded_bytes[-1] == lowres_preview_png
