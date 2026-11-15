from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.domain.errors import DependencyError, NotConfiguredError
from app.infra.clip_http import (
    IMAGE_PATH,
    TEXT_PATH,
    TEXTS_PATH,
    ClipHttpSubtitleEncoder,
    ClipHttpVisualEncoder,
)
from app.infra.config import Settings
from app.infra.milvus_rest import MilvusRestIndex
from app.main import build_encoders
from tests.test_milvus_rest import FakeTransport


def _unit(values: list[float]) -> list[float]:
    n = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / n for v in values]


class FakeClipTransport:
    def __init__(self, model="ViT-B-16", precision="int8_dynamic", dimension=512):
        self.model = model
        self.precision = precision
        self.dimension = dimension
        self.calls = []
        self.text_map = {"你好": _unit([0.2] * 256 + [0.4] * 256)}
        self.image_vector = _unit([0.1] * 128 + [0.3] * 384)
        self.fail_health = False

    def __call__(self, method, path, json_body, file_bytes, filename):
        self.calls.append((method, path, json_body is not None, file_bytes is not None))
        if path == "/health":
            if self.fail_health:
                raise DependencyError("远程编码器不可达")
            return {
                "status": "ok",
                "model": self.model,
                "precision": self.precision,
                "dimension": self.dimension,
                "milvus": "connected",
            }
        if path == TEXT_PATH:
            text = (json_body or {}).get("text") or ""
            vec = self.text_map.get(text) or _unit([0.5] * self.dimension)
            return {"dimension": self.dimension, "vector": vec}
        if path == TEXTS_PATH:
            texts = (json_body or {}).get("texts") or []
            vectors = [self.text_map.get(t) or _unit([0.5] * self.dimension) for t in texts]
            return {"count": len(vectors), "dimension": self.dimension, "vectors": vectors}
        if path == IMAGE_PATH:
            assert file_bytes, "image path requires file bytes"
            return {"dimension": self.dimension, "vector": self.image_vector}
        raise AssertionError(f"unexpected path {path}")


def test_health_and_embed_contract_shape_and_norm():
    transport = FakeClipTransport()
    enc = ClipHttpVisualEncoder(
        "http://127.0.0.1:8001",
        expected_dim=512,
        expected_model="ViT-B-16",
        expected_precision="int8_dynamic",
        transport=transport,
    )
    sub = ClipHttpSubtitleEncoder(enc)
    assert enc.available() is True
    status = enc.public_status()
    assert status["inference_ready"] is True
    assert status["backend"] == "remote_http"
    assert status["embed_dim"] == 512
    assert status["quality_verified"] is False
    assert sub.public_status()["quality_unverified"] is True
    assert "待验证" in sub.status_message()

    vec = enc.encode_text("你好")
    assert len(vec) == 512
    assert abs(math.sqrt(sum(v * v for v in vec)) - 1.0) < 1e-6
    img = enc.encode_image(b"\x89PNG")
    assert len(img) == 512
    batch = enc.encode_texts(["你好", "hello"])
    assert len(batch) == 2 and len(batch[0]) == 512
    assert sub.encode_text("你好") == vec
    paths = [c[1] for c in transport.calls]
    assert "/health" in paths and TEXT_PATH in paths and IMAGE_PATH in paths and TEXTS_PATH in paths


def test_signature_mismatch_is_blocked():
    transport = FakeClipTransport(model="ViT-L-14", dimension=768)
    enc = ClipHttpVisualEncoder(
        "http://127.0.0.1:8001",
        expected_dim=512,
        expected_model="ViT-B-16",
        expected_precision="int8_dynamic",
        transport=transport,
    )
    assert enc.available() is False
    assert "不一致" in enc.status_message()
    with pytest.raises(NotConfiguredError):
        enc.encode_text("你好")


def test_zero_or_wrong_dim_vector_is_blocked():
    transport = FakeClipTransport()

    def bad(method, path, json_body, file_bytes, filename):
        if path == "/health":
            return {"status": "ok", "model": "ViT-B-16", "precision": "int8_dynamic", "dimension": 512}
        return {"dimension": 512, "vector": [0.0] * 512}

    enc = ClipHttpVisualEncoder("http://x", expected_dim=512, transport=bad)
    with pytest.raises(DependencyError, match="零向量"):
        enc.encode_text("x")

    def wrong_dim(method, path, json_body, file_bytes, filename):
        if path == "/health":
            return {"status": "ok", "model": "ViT-B-16", "precision": "int8_dynamic", "dimension": 512}
        return {"dimension": 512, "vector": [0.1, 0.2]}

    enc2 = ClipHttpVisualEncoder("http://x", expected_dim=512, transport=wrong_dim)
    with pytest.raises(DependencyError, match="维度"):
        enc2.encode_text("x")


def test_build_encoders_refuses_to_mix_http_and_onnx(tmp_path: Path):
    settings = Settings(
        sqlite_path=tmp_path / "db.sqlite",
        worker_enabled=False,
        encoder_backend="remote_http",
        clip_http_base_url="http://127.0.0.1:8001",
        image_onnx_path=str(tmp_path / "toy.img.fp32.onnx"),
        text_onnx_path=str(tmp_path / "toy.txt.fp32.onnx"),
        tokenizer_vocab_path=str(tmp_path / "vocab.txt"),
        model_dim=512,
        model_architecture="ViT-B-16",
        model_version="int8_dynamic",
    )
    visual, sub = build_encoders(settings)
    assert visual.available() is False
    assert "拒绝混合" in visual.status_message()
    with pytest.raises(NotConfiguredError, match="拒绝混合"):
        visual.encode_text("你好")


def test_build_encoders_selects_remote_http(tmp_path: Path):
    settings = Settings(
        sqlite_path=tmp_path / "db.sqlite",
        worker_enabled=False,
        encoder_backend="remote_http",
        clip_http_base_url="http://127.0.0.1:8001",
        model_dim=512,
        model_architecture="ViT-B-16",
        model_version="int8_dynamic",
    )
    visual, sub = build_encoders(settings)
    assert visual.__class__.__name__ == "ClipHttpVisualEncoder"
    assert sub.__class__.__name__ == "ClipHttpSubtitleEncoder"
    assert visual.expected_dim == 512
    assert visual.client.base_url == "http://127.0.0.1:8001"


def test_milvus_describe_accepts_list_style_params():
    transport = FakeTransport()
    name = "pica_dev_clip_int8_dynamic_512_frame"
    transport.collections[name] = {
        "fields": [
            {"fieldName": "id", "dataType": "VarChar"},
            {
                "fieldName": "vector",
                "dataType": "FloatVector",
                "elementTypeParams": [{"key": "dim", "value": "512"}],
            },
        ],
        "indexes": [{"fieldName": "vector", "indexName": "vector", "metricType": "COSINE"}],
        "rows": [],
    }
    idx = MilvusRestIndex(
        "http://127.0.0.1:19530",
        dim=512,
        profile="dev",
        model_id="clip",
        model_version="int8_dynamic",
        transport=transport,
    )
    assert idx.ensure_collection("frame") == name
    # must not attempt create/drop of the existing isolated collection
    assert not any("create" in path or "drop" in path for path, _, _ in transport.calls)
