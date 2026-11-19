from __future__ import annotations

import hashlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.domain.model_profile import CONTRACT_VERIFIED, PREFIX_BY_CLIENT, PREFIX_BY_SERVER
from app.infra.embeddinggemma_http import EmbeddingGemmaHttpSubtitleEncoder, EmbeddingGemmaHttpVisualEncoder
from server import NATIVE_DIM, create_app, resolve_dtype


class FakeGemmaBackend:
    def __init__(self, *, model_id: str = "google/embeddinggemma-2", ready: bool = True) -> None:
        self.model_id = model_id
        self.dimension = NATIVE_DIM
        self.ready = ready
        self.seen_texts: list[str] = []
        self.seen_image_sizes: list[tuple[int, int]] = []

    def status(self):
        return {
            "status": "ok" if self.ready else "loading",
            "model": self.model_id,
            "dimension": self.dimension,
            "device": "cpu",
            "dtype": "float32",
            "audio_disabled": True,
        }

    def _deterministic_vec(self, seed: str) -> list[float]:
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        vals = [((digest[i % len(digest)] + (i % 7)) / 255.0) + 0.01 for i in range(self.dimension)]
        return vals

    def encode_texts(self, texts: list[str]) -> list[list[float]]:
        self.seen_texts.extend(texts)
        return [self._deterministic_vec(t) for t in texts]

    def encode_image(self, image: Image.Image) -> list[float]:
        self.seen_image_sizes.append(image.size)
        return self._deterministic_vec(f"img:{image.size[0]}x{image.size[1]}")


def _png_bytes(width: int = 320, height: int = 180) -> bytes:
    img = Image.new("RGB", (width, height), (30, 120, 210))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_resolve_dtype_forbids_float16_and_uses_bfloat16_on_cuda():
    fake_torch = SimpleNamespace(
        bfloat16="torch.bfloat16",
        float32="torch.float32",
        cuda=SimpleNamespace(is_bf16_supported=lambda: True),
    )
    with pytest.raises(ValueError, match="float16"):
        resolve_dtype(fake_torch, "cuda", "float16")
    with pytest.raises(ValueError, match="float16"):
        resolve_dtype(fake_torch, "cpu", "fp16")

    assert resolve_dtype(fake_torch, "cuda", "auto") == ("torch.bfloat16", "bfloat16")
    assert resolve_dtype(fake_torch, "cpu", "auto") == ("torch.float32", "float32")


def test_health_and_embed_endpoints():
    backend = FakeGemmaBackend()
    client = TestClient(create_app(backend=backend))

    h = client.get("/health")
    assert h.status_code == 200
    assert h.json() == {
        "status": "ok",
        "model": "google/embeddinggemma-2",
        "dimension": 768,
        "device": "cpu",
        "dtype": "float32",
        "audio_disabled": True,
    }

    # Server prepends instruction once
    r1 = client.post("/api/v1/embed/text", json={"text": "sunset", "instruction": "task: search result | query: "})
    assert r1.status_code == 200
    p1 = r1.json()
    assert p1["dimension"] == 768
    assert len(p1["vector"]) == 768
    assert p1["timings"]["inference_ms"] >= 0
    assert backend.seen_texts[-1] == "task: search result | query: sunset"

    # Empty instruction or already-prefixed text must not double-prefix
    r2 = client.post("/api/v1/embed/text", json={"text": "task: search result | query: sunset", "instruction": "task: search result | query: "})
    assert r2.status_code == 200
    assert backend.seen_texts[-1] == "task: search result | query: sunset"

    r3 = client.post("/api/v1/embed/text", json={"text": "title: none | text: line 1", "instruction": ""})
    assert r3.status_code == 200
    assert backend.seen_texts[-1] == "title: none | text: line 1"

    # Batch texts endpoint
    rb = client.post(
        "/api/v1/embed/texts",
        json={"texts": ["one", "two"], "instruction": "title: none | text: "},
    )
    assert rb.status_code == 200
    pb = rb.json()
    assert pb["dimension"] == 768
    assert len(pb["vectors"]) == 2
    assert backend.seen_texts[-2:] == ["title: none | text: one", "title: none | text: two"]

    # Image endpoint receives full frame (no crop)
    img_bytes = _png_bytes(640, 360)
    ri = client.post("/api/v1/embed/image", files={"file": ("frame.png", img_bytes, "image/png")})
    assert ri.status_code == 200
    pi = ri.json()
    assert pi["dimension"] == 768
    assert len(pi["vector"]) == 768
    assert pi["received_size"] == {"width": 640, "height": 360}
    assert backend.seen_image_sizes[-1] == (640, 360)


def test_loading_state_returns_503():
    backend = FakeGemmaBackend(ready=False)
    client = TestClient(create_app(backend=backend))

    assert client.get("/health").status_code == 503
    assert client.post("/api/v1/embed/text", json={"text": "hello"}).status_code == 503


def test_contract_with_backend_embeddinggemma_http_adapter():
    backend = FakeGemmaBackend()
    client = TestClient(create_app(backend=backend))

    def test_transport(method: str, path: str, json_body, file_bytes, filename: str):
        if method == "GET":
            res = client.get(path)
        elif file_bytes is not None:
            res = client.post(path, files={"file": (filename, file_bytes, "image/png")})
        else:
            res = client.post(path, json=json_body)
        res.raise_for_status()
        return res.json()

    # Mode 1: task_prefix_applied_by="server" (default for CineVec Docker Compose)
    visual_srv = EmbeddingGemmaHttpVisualEncoder(
        "http://encoder:8001",
        transport=test_transport,
        contract_status=CONTRACT_VERIFIED,
        task_prefix_applied_by=PREFIX_BY_SERVER,
    )
    sub_srv = EmbeddingGemmaHttpSubtitleEncoder(visual_srv)

    assert visual_srv.available() is True
    q_vec = visual_srv.encode_text("redwood forest")
    s_vec = sub_srv.encode_text("Meet me at dawn.")
    img_vec = visual_srv.encode_image(_png_bytes(800, 450))
    assert len(q_vec) == 768 and len(s_vec) == 768 and len(img_vec) == 768
    assert backend.seen_texts[-2:] == [
        "task: search result | query: redwood forest",
        "title: none | text: Meet me at dawn.",
    ]
    assert backend.seen_image_sizes[-1] == (800, 450)
    meta = visual_srv.last_embed_meta()
    assert meta is not None
    assert meta["remote_split"] is True
    assert meta["received_width"] == 800
    assert meta["received_height"] == 450

    # Mode 2: task_prefix_applied_by="client" (no double prefixing on server)
    visual_cli = EmbeddingGemmaHttpVisualEncoder(
        "http://encoder:8001",
        transport=test_transport,
        contract_status=CONTRACT_VERIFIED,
        task_prefix_applied_by=PREFIX_BY_CLIENT,
    )
    visual_cli.encode_text("golden hour")
    assert backend.seen_texts[-1] == "task: search result | query: golden hour"
