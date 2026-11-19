"""Standalone EmbeddingGemma 2 multimodal HTTP encoder service for CineVec.

Implements the HTTP contract expected by ``backend/app/infra/embeddinggemma_http.py``:
- ``GET  /health``
- ``POST /api/v1/embed/text``
- ``POST /api/v1/embed/texts``
- ``POST /api/v1/embed/image``

Keep ``torch`` and ``sentence_transformers`` lazily imported so the FastAPI app
and contract tests can run without heavy ML wheels installed locally.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Any, Protocol

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

logger = logging.getLogger("cinevec.encoder")

NATIVE_DIM = 768
DEFAULT_MODEL_ID = "google/embeddinggemma-2"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def resolve_device(torch_mod: Any, requested: str = "auto") -> str:
    pref = (requested or "auto").strip().lower()
    if pref and pref != "auto":
        return pref
    if torch_mod.cuda.is_available():
        return "cuda"
    mps_backend = getattr(getattr(torch_mod, "backends", None), "mps", None)
    if mps_backend is not None and mps_backend.is_available():
        return "mps"
    return "cpu"


def resolve_dtype(torch_mod: Any, device: str, requested: str = "auto") -> tuple[Any, str]:
    """Resolve safe precision for EmbeddingGemma 2.

    EmbeddingGemma 2 activations overflow in standard float16; forbid fp16.
    """
    pref = (requested or "auto").strip().lower()
    if pref in {"float16", "fp16", "half"}:
        raise ValueError(
            "EmbeddingGemma 2 does not support float16 (activations overflow). Use 'bfloat16', 'float32', or 'auto'."
        )
    if pref == "bfloat16" or pref == "bf16":
        return torch_mod.bfloat16, "bfloat16"
    if pref == "float32" or pref == "fp32":
        return torch_mod.float32, "float32"
    if pref != "auto":
        raise ValueError(f"Unsupported EMBED_DTYPE={requested!r}. Expected auto, bfloat16, or float32.")

    if device.startswith("cuda"):
        is_bf16_ok = getattr(torch_mod.cuda, "is_bf16_supported", None)
        if callable(is_bf16_ok) and is_bf16_ok():
            return torch_mod.bfloat16, "bfloat16"
    return torch_mod.float32, "float32"


def apply_instruction(text: str, instruction: str | None) -> str:
    """Prepend task instruction exactly once when provided."""
    if instruction and not text.startswith(instruction):
        return f"{instruction}{text}"
    return text


def _to_vector_list(raw_vec: Any) -> list[float]:
    if hasattr(raw_vec, "tolist"):
        raw_vec = raw_vec.tolist()
    return [float(x) for x in raw_vec]


class EncoderBackend(Protocol):
    model_id: str
    dimension: int

    def status(self) -> dict[str, Any]: ...

    def encode_texts(self, texts: list[str]) -> list[list[float]]: ...

    def encode_image(self, image: Image.Image) -> list[float]: ...


class SentenceTransformerBackend:
    """Lazy-loading SentenceTransformer runtime for google/embeddinggemma-2."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        device_pref: str = "auto",
        dtype_pref: str = "auto",
        disable_audio: bool = True,
        hf_token: str | None = None,
    ) -> None:
        self.model_id = (model_id or DEFAULT_MODEL_ID).strip() or DEFAULT_MODEL_ID
        self.device_pref = (device_pref or "auto").strip() or "auto"
        self.dtype_pref = (dtype_pref or "auto").strip() or "auto"
        self.disable_audio = disable_audio
        self.hf_token = (hf_token or "").strip() or None
        self.dimension = NATIVE_DIM

        self._device: str = self.device_pref
        self._dtype_name: str = self.dtype_pref
        self._model: Any = None
        self._state: str = "loading"
        self._error: str | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "SentenceTransformerBackend":
        return cls(
            model_id=os.getenv("EMBED_MODEL_ID", DEFAULT_MODEL_ID),
            device_pref=os.getenv("EMBED_DEVICE", "auto"),
            dtype_pref=os.getenv("EMBED_DTYPE", "auto"),
            disable_audio=_env_bool("EMBED_DISABLE_AUDIO", True),
            hf_token=os.getenv("HF_TOKEN") or None,
        )

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            self._state = "loading"
            self._error = None
            try:
                import torch
                from sentence_transformers import SentenceTransformer

                device = resolve_device(torch, self.device_pref)
                torch_dtype, dtype_name = resolve_dtype(torch, device, self.dtype_pref)
                self._device = device
                self._dtype_name = dtype_name

                kwargs: dict[str, Any] = {
                    "device": device,
                    "model_kwargs": {"torch_dtype": torch_dtype},
                }
                if self.disable_audio:
                    # Omit the 300M audio tower when CineVec only encodes text + images/video frames.
                    kwargs["config_kwargs"] = {"audio_config": None}
                if self.hf_token:
                    kwargs["token"] = self.hf_token

                logger.info(
                    "Loading model=%s device=%s dtype=%s disable_audio=%s",
                    self.model_id,
                    device,
                    dtype_name,
                    self.disable_audio,
                )
                model = SentenceTransformer(self.model_id, **kwargs)
                dim_getter = getattr(model, "get_sentence_embedding_dimension", None)
                detected_dim = dim_getter() if callable(dim_getter) else None
                if detected_dim:
                    self.dimension = int(detected_dim)
                self._model = model
                self._state = "ok"
                logger.info("Model loaded cleanly: model=%s dim=%s device=%s", self.model_id, self.dimension, device)
            except Exception as exc:  # noqa: BLE001
                self._model = None
                self._state = "error"
                self._error = str(exc)
                logger.exception("Failed to load model %s: %s", self.model_id, exc)

    def status(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self._state,
            "model": self.model_id,
            "dimension": self.dimension,
            "device": self._device,
            "dtype": self._dtype_name,
            "audio_disabled": self.disable_audio,
        }
        if self._error:
            payload["detail"] = self._error
        return payload

    def encode_texts(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            raise RuntimeError(self._error or "Model is still loading.")
        raw = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [_to_vector_list(row) for row in raw]

    def encode_image(self, image: Image.Image) -> list[float]:
        if self._model is None:
            raise RuntimeError(self._error or "Model is still loading.")
        raw = self._model.encode(
            image,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return _to_vector_list(raw)


class TextEmbedRequest(BaseModel):
    text: str
    instruction: str | None = None


class TextsEmbedRequest(BaseModel):
    texts: list[str]
    instruction: str | None = None


def create_app(backend: EncoderBackend | None = None) -> FastAPI:
    runtime_backend = backend or SentenceTransformerBackend.from_env()
    owns_lifecycle = backend is None
    inference_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if owns_lifecycle and hasattr(runtime_backend, "load"):
            # Load in a background thread so /health stays reachable (returning 503 loading) during first boot.
            loader_thread = threading.Thread(
                target=runtime_backend.load,  # type: ignore[attr-defined]
                name="encoder-loader",
                daemon=True,
            )
            loader_thread.start()
        yield

    app = FastAPI(
        title="CineVec EmbeddingGemma 2 Encoder",
        version="1.0.0",
        lifespan=lifespan,
    )

    def _require_ready() -> dict[str, Any]:
        st = runtime_backend.status()
        if st.get("status") != "ok":
            raise HTTPException(status_code=503, detail=st.get("detail") or f"Encoder status is {st.get('status')}")
        return st

    def _run_serialized(fn):
        t_wait0 = time.perf_counter()
        with inference_lock:
            t_inf0 = time.perf_counter()
            result = fn()
            t_inf1 = time.perf_counter()
        queue_ms = round((t_inf0 - t_wait0) * 1000.0, 2)
        inference_ms = round((t_inf1 - t_inf0) * 1000.0, 2)
        return result, queue_ms, inference_ms

    @app.get("/health")
    def health():
        st = runtime_backend.status()
        code = 200 if st.get("status") == "ok" else 503
        return JSONResponse(status_code=code, content=st)

    @app.post("/api/v1/embed/text")
    async def embed_text(req: TextEmbedRequest):
        st = _require_ready()
        t_pre0 = time.perf_counter()
        prompt = apply_instruction(req.text, req.instruction)
        preprocess_ms = round((time.perf_counter() - t_pre0) * 1000.0, 2)

        vectors, queue_ms, inference_ms = await asyncio.to_thread(
            _run_serialized,
            lambda: runtime_backend.encode_texts([prompt]),
        )
        vec = vectors[0]
        return {
            "model": st["model"],
            "dimension": len(vec),
            "vector": vec,
            "device": st.get("device"),
            "timings": {
                "queue_ms": queue_ms,
                "preprocess_ms": preprocess_ms,
                "inference_ms": inference_ms,
            },
        }

    @app.post("/api/v1/embed/texts")
    async def embed_texts(req: TextsEmbedRequest):
        st = _require_ready()
        t_pre0 = time.perf_counter()
        prompts = [apply_instruction(item, req.instruction) for item in req.texts]
        preprocess_ms = round((time.perf_counter() - t_pre0) * 1000.0, 2)

        if not prompts:
            return {
                "model": st["model"],
                "dimension": st.get("dimension", NATIVE_DIM),
                "vectors": [],
                "device": st.get("device"),
                "timings": {"queue_ms": 0.0, "preprocess_ms": preprocess_ms, "inference_ms": 0.0},
            }

        vectors, queue_ms, inference_ms = await asyncio.to_thread(
            _run_serialized,
            lambda: runtime_backend.encode_texts(prompts),
        )
        dim = len(vectors[0]) if vectors else st.get("dimension", NATIVE_DIM)
        return {
            "model": st["model"],
            "dimension": dim,
            "vectors": vectors,
            "device": st.get("device"),
            "timings": {
                "queue_ms": queue_ms,
                "preprocess_ms": preprocess_ms,
                "inference_ms": inference_ms,
            },
        }

    @app.post("/api/v1/embed/image")
    async def embed_image(file: UploadFile = File(...)):
        st = _require_ready()
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Empty image file.")

        t_pre0 = time.perf_counter()
        try:
            with Image.open(BytesIO(raw)) as img:
                rgb_image = img.convert("RGB")
                received_size = {"width": int(rgb_image.width), "height": int(rgb_image.height)}
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid image payload: {exc}") from exc
        preprocess_ms = round((time.perf_counter() - t_pre0) * 1000.0, 2)

        vec, queue_ms, inference_ms = await asyncio.to_thread(
            _run_serialized,
            lambda: runtime_backend.encode_image(rgb_image),
        )
        return {
            "model": st["model"],
            "dimension": len(vec),
            "vector": vec,
            "device": st.get("device"),
            "received_size": received_size,
            "timings": {
                "queue_ms": queue_ms,
                "preprocess_ms": preprocess_ms,
                "inference_ms": inference_ms,
            },
        }

    return app


app = create_app()
