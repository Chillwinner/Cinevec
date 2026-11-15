from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.application.rebuild_service import RebuildService
from app.domain.errors import AppError, NotConfiguredError
from app.domain.media import SampleSettings
from app.domain.model_profile import (
    CONTRACT_PENDING,
    CONTRACT_VERIFIED,
    EG2_DOCUMENT_TEMPLATE,
    EG2_SEARCH_QUERY_TEMPLATE,
    FAMILY_EMBEDDINGGEMMA2,
    PREPROCESS_CLIP_OFFICIAL_SQUARE,
    PREPROCESS_FULL_FRAME,
    PREPROCESS_LEGACY_CENTER_CROP,
    PREFIX_BY_CLIENT,
    PREFIX_BY_SERVER,
    PREFIX_BY_UNVERIFIED,
    apply_text_task_prefix,
    format_embeddinggemma2_document,
    format_embeddinggemma2_query,
)
from app.domain.models import Asset, Job, Source
from app.domain.search import vector_visibility
from app.infra.clock import now
from app.infra.config import Settings
from app.infra.db import connect, init_db
from app.infra.embeddinggemma_http import EmbeddingGemmaHttpSubtitleEncoder, EmbeddingGemmaHttpVisualEncoder
from app.infra.milvus_rest import collection_name
from app.infra.model_space import space_from_settings
from app.infra.previews import render_legacy_center_crop, render_model_input, render_preview
from app.infra.sqlite_repos import SqliteMetadataRepository
from app.main import build_encoders


class RecordingTransport:
    def __init__(self, *, model="google/embeddinggemma-2", dimension=768, status="ok"):
        self.model = model
        self.dimension = dimension
        self.status = status
        self.calls = []
        self.texts = []
        self.images = []

    def __call__(self, method, path, json_body, file_bytes, filename):
        self.calls.append({"method": method, "path": path, "json_body": json_body, "has_file": file_bytes is not None, "filename": filename})
        if path == "/health" or path.endswith("/health"):
            return {"status": self.status, "model": self.model, "dimension": self.dimension}
        if file_bytes is not None:
            self.images.append(file_bytes)
            return {"dimension": self.dimension, "vector": [0.1] * int(self.dimension)}
        text = (json_body or {}).get("text") or ""
        self.texts.append(text)
        return {"dimension": self.dimension, "vector": [0.2] * int(self.dimension)}


def _raise_if_network(*args, **kwargs):
    raise AssertionError(f"unexpected real network request: {args} {kwargs}")


def test_pending_contract_does_not_touch_transport():
    transport = RecordingTransport()
    enc = EmbeddingGemmaHttpVisualEncoder(
        "http://127.0.0.1:8001",
        transport=transport,
        contract_status=CONTRACT_PENDING,
    )
    assert enc.available() is False
    status = enc.public_status()
    assert status["status"] == "pending_verification"
    assert status["inference_ready"] is False
    assert status["embed_dim"] == 768
    assert status["native_dim"] == 768
    assert status["save_is_not_health"] is True
    assert status["http_paths_unverified"] is True
    assert "尚未核实" in status["message"]
    with pytest.raises(NotConfiguredError) as exc:
        enc.encode_text("打篮球")
    assert exc.value.code == "blocked:encoder_contract_pending"
    with pytest.raises(NotConfiguredError):
        enc.encode_image(b"fake-image")
    assert transport.calls == []


def test_reject_512_health_and_512_vector_for_768_profile():
    health = RecordingTransport(model="ViT-B-16", dimension=512)
    enc = EmbeddingGemmaHttpVisualEncoder(
        "http://127.0.0.1:8001",
        expected_model="embeddinggemma-2",
        transport=health,
        contract_status=CONTRACT_VERIFIED,
    )
    assert enc.available() is False
    status = enc.public_status()
    assert status["inference_ready"] is False
    assert "512" in status["message"] and "768" in status["message"]
    assert "拒绝混用" in status["message"] or "不一致" in status["message"]

    vec_transport = RecordingTransport(dimension=512)
    ok_health = RecordingTransport(dimension=768)

    def mixed(method, path, json_body, file_bytes, filename):
        if path == "/health":
            return ok_health(method, path, json_body, file_bytes, filename)
        return vec_transport(method, path, json_body, file_bytes, filename)

    enc2 = EmbeddingGemmaHttpVisualEncoder(
        "http://127.0.0.1:8001",
        transport=mixed,
        contract_status=CONTRACT_VERIFIED,
    )
    assert enc2.available() is True
    with pytest.raises(Exception) as exc:
        enc2.encode_text("query")
    assert "512" in str(exc.value) and "768" in str(exc.value)


def test_task_prefix_duties_and_images_have_no_text_prefix():
    raw = "打羽毛球"
    pending = apply_text_task_prefix(raw, role="query", applied_by=PREFIX_BY_UNVERIFIED, family=FAMILY_EMBEDDINGGEMMA2)
    assert pending["text"] == raw
    assert pending["prefix_applied"] is False
    server = apply_text_task_prefix(raw, role="document", applied_by=PREFIX_BY_SERVER, family=FAMILY_EMBEDDINGGEMMA2)
    assert server["text"] == raw
    assert server["prefix_applied"] is False
    query = apply_text_task_prefix(raw, role="query", applied_by=PREFIX_BY_CLIENT, family=FAMILY_EMBEDDINGGEMMA2)
    assert query["text"] == format_embeddinggemma2_query(raw)
    assert query["text"] == "task: search result | query: 打羽毛球"
    assert query["template"] == EG2_SEARCH_QUERY_TEMPLATE
    doc = apply_text_task_prefix("字幕正文", role="document", applied_by=PREFIX_BY_CLIENT, family=FAMILY_EMBEDDINGGEMMA2)
    assert doc["text"] == format_embeddinggemma2_document("字幕正文")
    assert doc["text"] == "title: none | text: 字幕正文"
    assert doc["template"] == EG2_DOCUMENT_TEMPLATE

    transport = RecordingTransport(dimension=768)
    enc = EmbeddingGemmaHttpVisualEncoder(
        "http://127.0.0.1:8001",
        transport=transport,
        contract_status=CONTRACT_VERIFIED,
        task_prefix_applied_by=PREFIX_BY_CLIENT,
    )
    sub = EmbeddingGemmaHttpSubtitleEncoder(enc)
    enc.encode_text_for_task("打篮球", role="query")
    sub.encode_text("一段字幕")
    image = Image.new("RGB", (320, 200), (10, 20, 30))
    buf = BytesIO()
    image.save(buf, format="PNG")
    payload = buf.getvalue()
    enc.encode_image(payload)
    assert transport.texts[0] == "task: search result | query: 打篮球"
    assert transport.texts[1] == "title: none | text: 一段字幕"
    assert transport.images[0] == payload
    # 图片请求没有文本前缀字段
    image_calls = [c for c in transport.calls if c["has_file"]]
    assert image_calls and image_calls[0]["json_body"] is None
    meta = enc.last_embed_meta()
    assert meta["request_bytes"] == len(payload)
    assert meta["remote_split"] is False
    assert "http_seconds" in meta

    raw_transport = RecordingTransport(dimension=768)
    raw_enc = EmbeddingGemmaHttpVisualEncoder(
        "http://127.0.0.1:8001",
        transport=raw_transport,
        contract_status=CONTRACT_VERIFIED,
        task_prefix_applied_by=PREFIX_BY_UNVERIFIED,
    )
    raw_enc.encode_text_for_task("打篮球", role="query")
    assert raw_transport.texts == ["打篮球"]
    assert raw_enc.last_text_prefix()["prefix_applied"] is False

    server_transport = RecordingTransport(dimension=768)
    server_enc = EmbeddingGemmaHttpVisualEncoder(
        "http://127.0.0.1:8001",
        transport=server_transport,
        contract_status=CONTRACT_VERIFIED,
        task_prefix_applied_by=PREFIX_BY_SERVER,
    )
    server_sub = EmbeddingGemmaHttpSubtitleEncoder(server_enc)
    server_enc.encode_text_for_task("打篮球", role="query")
    server_sub.encode_text("一段字幕")
    # 服务端模式：text 保持原文，instruction 为官方前缀，避免双加
    assert server_transport.texts == ["打篮球", "一段字幕"]
    query_call = next(c for c in server_transport.calls if c["json_body"] and c["json_body"].get("text") == "打篮球")
    doc_call = next(c for c in server_transport.calls if c["json_body"] and c["json_body"].get("text") == "一段字幕")
    assert query_call["json_body"]["instruction"] == "task: search result | query: "
    assert doc_call["json_body"]["instruction"] == "title: none | text: "
    assert "task: search result | query: 打篮球" not in server_transport.texts

    client_bodies = [c["json_body"] for c in transport.calls if c["json_body"] and "text" in (c["json_body"] or {})]
    assert any(b.get("instruction") == "" and str(b.get("text","")).startswith("task: search result | query: ") for b in client_bodies)


def test_constructor_rejects_non_native_dim():
    with pytest.raises(ValueError):
        EmbeddingGemmaHttpVisualEncoder("http://127.0.0.1:8001", expected_dim=512, transport=RecordingTransport())


def test_encode_image_keeps_remote_timing_fields_when_present():
    class TimedTransport(RecordingTransport):
        def __call__(self, method, path, json_body, file_bytes, filename):
            payload = super().__call__(method, path, json_body, file_bytes, filename)
            if file_bytes is not None:
                payload = dict(payload)
                payload["timings"] = {"queue_ms": 50, "preprocess_ms": 400, "inference_ms": 12300}
            return payload

    enc = EmbeddingGemmaHttpVisualEncoder(
        "http://127.0.0.1:8001",
        transport=TimedTransport(),
        contract_status=CONTRACT_VERIFIED,
    )
    enc.encode_image(b"\x89PNG")
    meta = enc.last_embed_meta()
    assert meta["remote_split"] is True
    assert meta["queue_seconds"] == 0.05
    assert meta["preprocess_seconds"] == 0.4
    assert meta["inference_seconds"] == 12.3


def test_preprocess_strategies_keep_preview_independent_and_full_frame():
    settings = SampleSettings(preview_max_edge=200, preview_quality=85, model_input_size=224)
    source = Image.new("RGB", (1280, 720), (12, 80, 160))
    pix = source.load()
    for x in range(0, 1280, 5):
        pix[x, 10] = (255, x % 255, 40)
        pix[x, 700] = (10, 255, x % 255)
    preview = render_preview(source, settings)
    preview_img = Image.open(BytesIO(preview))
    full = render_model_input(source, settings, strategy=PREPROCESS_FULL_FRAME)
    full_img = Image.open(BytesIO(full))
    square = render_model_input(source, settings, strategy=PREPROCESS_CLIP_OFFICIAL_SQUARE)
    square_img = Image.open(BytesIO(square))
    crop = render_legacy_center_crop(source, 224)
    crop_img = Image.open(BytesIO(crop))
    default = render_model_input(source, settings)
    assert preview_img.format == "JPEG"
    assert max(preview_img.size) <= 200
    assert full_img.size == (1280, 720)
    assert full_img.format == "PNG"
    assert square_img.size == (224, 224)
    assert crop_img.size == (224, 224)
    assert default == square
    assert full != square
    assert square != crop
    assert preview != full
    # 从有损预览再生成应与原图完整画面不同
    via = render_model_input(preview_img, settings, strategy=PREPROCESS_FULL_FRAME)
    via_img = Image.open(BytesIO(via))
    assert via_img.size != (1280, 720)


def test_profile_isolation_and_rebuild_without_reregistration(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PICA_LOAD_DOTENV", "0")
    conn = connect(tmp_path / "pica.db")
    init_db(conn)
    meta = SqliteMetadataRepository(conn)
    source = meta.add_source(
        Source(
            id="src-1",
            name="s",
            endpoint="http://x",
            bucket="b",
            region="",
            role="input",
            is_platform_storage=False,
            access_key_configured=True,
            secret_configured=True,
            last_probe_status="ok",
            last_probe_message="ok",
            created_at=now(),
            updated_at=now(),
        ),
        "k",
        "s",
    )
    asset = meta.upsert_asset(
        Asset(
            id="asset-1",
            source_id=source.id,
            object_key="source/demo/user-people/pingpong.jpg",
            kind="image",
            vectorize=True,
            status="ready",
            created_at=source.created_at,
            updated_at=source.updated_at,
            lifecycle="active",
            index_state="ready",
            version=1,
            ready_version=1,
        )
    )
    clip_state = meta.upsert_asset_index_profile(
        {
            "asset_id": asset.id,
            "profile": "dev",
            "model_id": "chinese-clip-vit-b-16",
            "model_version": "int8_dynamic",
            "dim": 512,
            "preprocess_version": PREPROCESS_LEGACY_CENTER_CROP,
            "index_state": "ready",
            "indexed_content_version": 1,
            "collection": collection_name("dev", "chinese-clip-vit-b-16", "int8_dynamic", 512, "frame", PREPROCESS_LEGACY_CENTER_CROP),
        }
    )
    assert clip_state["index_state"] == "ready"
    # 资产级 ready 不能当作 Gemma 空间已索引
    assert vector_visibility(asset) == "searchable"
    assert (
        vector_visibility(asset, require_profile=True, profile_state=None)
        == "not_indexed"
    )
    gemma_state = meta.get_asset_index_profile(
        asset.id,
        profile="dev",
        model_id="embeddinggemma-2",
        model_version="unverified",
        dim=768,
        preprocess_version=PREPROCESS_FULL_FRAME,
    )
    assert gemma_state is None
    assert meta.list_assets()[0].id == asset.id
    assert meta.list_assets()[0].ready_version == 1

    clip_name = collection_name("dev", "chinese-clip-vit-b-16", "int8_dynamic", 512, "frame")
    gemma_name = collection_name("dev", "embeddinggemma-2", "unverified", 768, "frame", PREPROCESS_FULL_FRAME)
    assert clip_name != gemma_name
    assert "768" in gemma_name
    assert PREPROCESS_FULL_FRAME.replace("-", "_") in gemma_name or "full_frame" in gemma_name

    from app.domain.model_profile import EMBEDDINGGEMMA2_SPEC

    space = EMBEDDINGGEMMA2_SPEC.space(profile="dev")
    rebuild = RebuildService(meta)
    plan = rebuild.plan(space)
    assert plan["execute"] is False
    assert plan["writes_milvus"] is False
    assert plan["writes_s3"] is False
    assert plan["re_embeds"] is False
    assert plan["creates_remote_collection"] is False
    assert plan["keeps_old_collections"] is True
    assert plan["keeps_sqlite_business_records"] is True
    assert plan["rebuildable_count"] == 1
    assert plan["rebuildable"][0]["asset_id"] == asset.id
    assert plan["rebuildable"][0]["needs_reregistration"] is False
    assert "ready_version" in plan["rebuildable"][0]["reason"]

    with pytest.raises(AppError) as exc:
        rebuild.enqueue(space, execute=True)
    assert exc.value.code == "rebuild_execute_disabled"
    queued = rebuild.enqueue(space, execute=False)
    assert queued["queued_count"] == 1
    assert queued["queue_status"] == "planned"
    assert queued["queued"][0]["execute"] in {0, False}
    # 再次入队不重复注册资产，只更新 planned 队列
    queued2 = rebuild.enqueue(space, execute=False)
    assert queued2["queued_count"] == 1
    assert len(meta.list_rebuild_queue()) == 1
    assert len(meta.list_assets()) == 1
    again = meta.get_asset(asset.id)
    assert again is not None
    assert again.ready_version == 1
    assert again.object_key == "source/demo/user-people/pingpong.jpg"
    # 旧 profile 关联仍在
    assert meta.get_asset_index_profile(
        asset.id,
        profile="dev",
        model_id="chinese-clip-vit-b-16",
        model_version="int8_dynamic",
        dim=512,
        preprocess_version=PREPROCESS_LEGACY_CENTER_CROP,
    )["index_state"] == "ready"


def test_build_encoders_rejects_mixed_dimensions_without_network():
    clip = Settings(
        encoder_backend="remote_http",
        clip_http_base_url="http://127.0.0.1:8001",
        model_family="chinese_clip",
        model_architecture="ViT-B-16",
        model_dim=768,
        model_id="chinese-clip-vit-b-16",
        model_version="int8_dynamic",
    )
    enc, _ = build_encoders(clip)
    assert enc.available() is False
    assert "768" in enc.status_message()

    gemma_512 = Settings(
        encoder_backend="remote_http",
        clip_http_base_url="http://127.0.0.1:8001",
        model_family="embeddinggemma2",
        model_architecture="embeddinggemma-2",
        model_dim=512,
        model_id="embeddinggemma-2",
        model_version="unverified",
    )
    enc2, _ = build_encoders(gemma_512)
    assert enc2.available() is False
    assert "768" in enc2.status_message() and "512" in enc2.status_message()

    pending = Settings(
        encoder_backend="remote_http",
        clip_http_base_url="http://127.0.0.1:8001",
        model_family="embeddinggemma2",
        model_architecture="embeddinggemma-2",
        model_dim=768,
        model_id="embeddinggemma-2",
        model_version="unverified",
        encoder_contract_status=CONTRACT_PENDING,
        task_prefix_applied_by=PREFIX_BY_UNVERIFIED,
    )
    enc3, sub3 = build_encoders(pending)
    assert isinstance(enc3, EmbeddingGemmaHttpVisualEncoder)
    assert enc3.available() is False
    status = enc3.public_status()
    assert status["contract_status"] == CONTRACT_PENDING
    assert status["inference_ready"] is False
    space = space_from_settings(pending)
    assert space is not None
    assert space.dim == 768
    assert space.preprocess_version == PREPROCESS_FULL_FRAME
    assert space.family == FAMILY_EMBEDDINGGEMMA2


def test_settings_catalog_does_not_treat_save_as_health(tmp_path: Path):
    from app.application.settings_service import SettingsService
    from app.infra.file_store import UnconfiguredFileStore

    conn = connect(tmp_path / "settings.db")
    init_db(conn)
    meta = SqliteMetadataRepository(conn)
    settings = Settings(
        sqlite_path=tmp_path / "settings.db",
        encoder_backend="remote_http",
        clip_http_base_url="http://127.0.0.1:8001",
        model_family="embeddinggemma2",
        model_dim=768,
        model_id="embeddinggemma-2",
        worker_enabled=False,
    )
    view = SettingsService(meta, settings, UnconfiguredFileStore()).public_view()
    model = view["model"]
    assert model["save_is_not_health"] is True
    assert model["auto_activated"] is False
    ids = {item["id"] for item in model["catalog"]}
    assert "embeddinggemma2" in ids
    assert "chinese_clip_vit_b_16" in ids
    gemma = next(item for item in model["catalog"] if item["id"] == "embeddinggemma2")
    assert gemma["native_dim"] == 768
    assert gemma["contract_status"] == CONTRACT_PENDING
    assert gemma["verified"] is False
    assert gemma["active"] is True
    assert "密钥" not in str(view)
    assert "jwt" not in str(view).lower()


def test_rebuild_execute_writes_new_space_and_keeps_old(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PICA_LOAD_DOTENV", "0")
    from app.application.index_write_service import IndexWriteService
    from app.domain.model_profile import EMBEDDINGGEMMA2_SPEC
    from app.infra.clock import now
    from app.infra.file_store import LocalFileStore
    from app.infra.sqlite_repos import SqliteTaskRepository
    from tests.fakes import MemoryEncoder, MemoryVectorIndex

    conn = connect(tmp_path / "pica.db")
    init_db(conn)
    meta = SqliteMetadataRepository(conn)
    tasks = SqliteTaskRepository(conn)
    store = LocalFileStore(tmp_path / "store", label="pytest")
    source = meta.add_source(
        Source(
            id="src-1",
            name="s",
            endpoint="http://x",
            bucket="b",
            region="",
            role="input",
            is_platform_storage=False,
            access_key_configured=True,
            secret_configured=True,
            last_probe_status="ok",
            last_probe_message="ok",
            created_at=now(),
            updated_at=now(),
        ),
        "k",
        "s",
    )
    asset = meta.upsert_asset(
        Asset(
            id="asset-1",
            source_id=source.id,
            object_key="source/demo/ball.jpg",
            kind="image",
            vectorize=True,
            status="ready",
            created_at=now(),
            updated_at=now(),
            lifecycle="active",
            index_state="ready",
            version=1,
            ready_version=1,
        )
    )
    old_key = "model-inputs/asset-1/source.png"
    store.put_bytes(old_key, b"old-crop", "image/png")
    from types import SimpleNamespace
    meta.upsert_frame(
        asset.id,
        "job-old",
        SimpleNamespace(index=0, time_seconds=0.0, kept=True, drop_reason=None, preview_key="previews/asset-1/source.jpg", model_input_key=old_key, extra={"kind": "image"}),
    )
    old_index = MemoryVectorIndex(profile="dev", model_id="chinese-clip-vit-b-16", model_version="int8_dynamic", dim=512)
    old_enc = MemoryEncoder(dim=512)
    old_writer = IndexWriteService(
        meta, tasks, store, old_enc, old_index,
        profile="dev", model_id="chinese-clip-vit-b-16", model_version="int8_dynamic", dim=512,
        preprocess_version="",
        space=None,
    )
    # legacy write without space uses old vector id
    job_old = tasks.add_job(
        Job(
            id="job-old-write",
            job_type="index_asset",
            status="waiting",
            source_id=source.id,
            asset_id=asset.id,
            stage="embedding",
            step="old",
            progress=0,
            queue_position=None,
            error_code=None,
            error_message=None,
        )
    )
    assert old_writer.process_frames(job_old) == "completed"
    old_units = meta.list_vector_units(asset.id)
    assert len(old_units) == 1
    assert old_units[0]["collection"].endswith("_512_frame") or "512" in old_units[0]["collection"]

    space = EMBEDDINGGEMMA2_SPEC.space(profile="dev", model_version="float32")
    new_enc = MemoryEncoder(dim=768)
    new_index = MemoryVectorIndex(profile=space.profile, model_id=space.model_id, model_version=space.model_version, dim=space.dim)
    # MemoryVectorIndex.collection_name may not pass preprocess; writer still records collection string.
    new_writer = IndexWriteService(
        meta, tasks, store, new_enc, new_index,
        profile=space.profile, model_id=space.model_id, model_version=space.model_version, dim=space.dim,
        preprocess_version=space.preprocess_version,
        space=space,
    )

    class FakeReader:
        def download_readonly(self, source, access_key, secret_key, object_key, dest_path):
            dest_path = Path(dest_path)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(b"FULL-ORIGINAL-BYTES")
            return dest_path

    rebuild = RebuildService(
        meta,
        tasks=tasks,
        writer=new_writer,
        file_store=store,
        source_reader=FakeReader(),
        work_root=tmp_path / "work",
        current_space=space,
        encoder=new_enc,
        index=new_index,
    )
    queued = rebuild.enqueue(space, execute=True)
    assert queued["execute"] is True
    assert queued["job_count"] == 1
    job = tasks.get_job(queued["jobs"][0]["id"])
    assert job.status == "waiting"
    # prepare + write
    assert rebuild.process(job) == "completed"
    units = meta.list_vector_units(asset.id)
    assert len(units) == 2
    collections = {u["collection"] for u in units}
    assert any("512" in c for c in collections)
    assert any("768" in c for c in collections)
    gemma_state = meta.get_asset_index_profile(
        asset.id,
        profile=space.profile,
        model_id=space.model_id,
        model_version=space.model_version,
        dim=space.dim,
        preprocess_version=space.preprocess_version,
    )
    assert gemma_state is not None and gemma_state["index_state"] == "ready"
    # old crop file kept; new space encodes from local original bytes, not a second object-store copy
    assert store.get_bytes(old_key) == b"old-crop"
    frame = meta.get_frame(asset.id, 0)
    assert (frame["extra"] or {}).get("full_frame_source") == "original_object_bytes"
    assert frame["model_input_key"] == old_key
    again = meta.get_asset(asset.id)
    assert again.ready_version == 1
    assert again.object_key == "source/demo/ball.jpg"
