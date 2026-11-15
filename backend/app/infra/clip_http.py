"""Remote Chinese-CLIP HTTP encoder adapter.

Talks only to the confirmed OpenAPI of the existing ViT-B/16 INT8 feature
service (localhost:8001). This is a standalone infra adapter behind the
existing ImageTextEncoder / SubtitleEncoder ports; it is interchangeable
with the local ONNX adapter and must not be mixed into the same vector
space.

Confirmed contract (probed from /openapi.json and live calls, not guessed):
- GET  /health -> {status, model, precision, dimension, milvus}
- POST /api/v1/embed/text  JSON {text} -> {dimension, vector}
- POST /api/v1/embed/texts JSON {texts} -> {count, dimension, vectors}
- POST /api/v1/embed/image multipart file -> {dimension, vector}
- No authentication. Vectors are already L2-normalized; dim=512 for ViT-B-16.
The remote service may also be connected to its own Milvus; this adapter
only calls the embed HTTP API and never writes to that service's collections.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from app.domain.errors import DependencyError, NotConfiguredError
from app.infra.redact import redact

FAMILY = "chinese_clip"
HEALTH_PATH = "/health"
TEXT_PATH = "/api/v1/embed/text"
TEXTS_PATH = "/api/v1/embed/texts"
IMAGE_PATH = "/api/v1/embed/image"


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _vector_from_payload(payload: dict[str, Any], expected_dim: int | None, *, batch: bool = False) -> list[list[float]] | list[float]:
    if not isinstance(payload, dict):
        raise DependencyError("远程编码器返回了无法识别的响应。")
    if batch:
        vectors = payload.get("vectors")
        dim = payload.get("dimension")
        if not isinstance(vectors, list) or not vectors:
            raise DependencyError("远程批量编码响应缺少 vectors。")
        out = []
        for vec in vectors:
            out.append(_one_vector(vec, dim if expected_dim is None else expected_dim))
        return out
    vec = payload.get("vector")
    dim = payload.get("dimension")
    check = expected_dim if expected_dim is not None else dim
    return _one_vector(vec, check)


def _one_vector(vec: Any, expected_dim: Any) -> list[float]:
    if not isinstance(vec, list) or not vec or not isinstance(vec[0], (int, float)):
        raise DependencyError("远程编码器未返回数值向量，已阻断，不会生成假向量。")
    values = [float(x) for x in vec]
    if expected_dim is not None and int(expected_dim) != len(values):
        raise DependencyError(
            f"远程向量维度 {len(values)} 与声明/配置维度 {int(expected_dim)} 不一致，已阻断。"
        )
    if all(v == 0.0 for v in values):
        raise DependencyError("远程编码器输出了零向量，已阻断，不会用随机或哈希向量充数。")
    return values


class ClipHttpClient:
    def __init__(self, base_url: str, timeout: float = 30.0, transport=None) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self._transport = transport

    def health(self) -> dict[str, Any]:
        return self._request("GET", HEALTH_PATH)

    def embed_text(self, text: str) -> dict[str, Any]:
        return self._request("POST", TEXT_PATH, json_body={"text": text})

    def embed_texts(self, texts: list[str]) -> dict[str, Any]:
        return self._request("POST", TEXTS_PATH, json_body={"texts": list(texts)})

    def embed_image(self, image_bytes: bytes, filename: str = "image.png") -> dict[str, Any]:
        return self._request("POST", IMAGE_PATH, file_bytes=image_bytes, filename=filename)

    def _request(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
        file_bytes: bytes | None = None,
        filename: str = "image.png",
    ) -> dict[str, Any]:
        if self._transport is not None:
            return self._transport(method, path, json_body, file_bytes, filename)
        if not self.base_url:
            raise NotConfiguredError("未配置远程图文编码器地址。", code="encoder_unconfigured")
        url = _join(self.base_url, path)
        headers: dict[str, str] = {"Accept": "application/json"}
        data = None
        if file_bytes is not None:
            boundary = "----PicaClipHttpBoundary"
            filename = filename.replace('"', "").replace("\r", "").replace("\n", "") or "image.png"
            parts = [
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
                + file_bytes
                + b"\r\n"
                + f"--{boundary}--\r\n".encode()
            ]
            data = b"".join(parts)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore") if exc.fp else str(exc)
            raise DependencyError(redact(f"远程编码器 HTTP {exc.code}：{detail[:300]}")) from exc
        except urllib.error.URLError as exc:
            raise DependencyError(redact(f"无法连接远程编码器：{exc.reason}")) from exc
        except json.JSONDecodeError as exc:
            raise DependencyError("远程编码器返回了无法解析的响应。") from exc


class ClipHttpVisualEncoder:
    """Image-text port over the confirmed remote HTTP contract."""

    def __init__(
        self,
        base_url: str,
        *,
        expected_dim: int,
        expected_model: str = "ViT-B-16",
        expected_precision: str = "int8_dynamic",
        timeout: float = 30.0,
        transport=None,
        model_id: str | None = None,
        model_version: str | None = None,
    ) -> None:
        self.client = ClipHttpClient(base_url, timeout=timeout, transport=transport)
        self.expected_dim = int(expected_dim)
        self.expected_model = expected_model
        self.expected_precision = expected_precision
        self.model_id = model_id
        self.model_version = model_version
        self._health_error: str | None = None
        self._health: dict[str, Any] | None = None

    def _ensure_health(self) -> dict[str, Any]:
        if self._health is not None and self._health_error is None:
            return self._health
        try:
            data = self.client.health()
        except Exception as exc:  # noqa: BLE001 — surface as blocked
            self._health_error = redact(str(getattr(exc, "message", exc)))
            raise NotConfiguredError(self._health_error, code="blocked:encoder_health") from exc
        problems = []
        model = str(data.get("model") or "")
        precision = str(data.get("precision") or "")
        dimension = data.get("dimension")
        if self.expected_model and model and model != self.expected_model:
            problems.append(f"远程模型 {model} 与配置 {self.expected_model} 不一致，拒绝混用。")
        if self.expected_precision and precision and precision != self.expected_precision:
            problems.append(f"远程精度 {precision} 与配置 {self.expected_precision} 不一致，拒绝混用。")
        if dimension is not None and int(dimension) != self.expected_dim:
            problems.append(f"远程声明维度 {dimension} 与配置 {self.expected_dim} 不一致，拒绝混用。")
        if data.get("status") not in (None, "ok", "healthy", "OK"):
            problems.append(f"远程 /health 状态为 {data.get('status')!r}。")
        if problems:
            self._health_error = "；".join(problems)
            raise NotConfiguredError(self._health_error, code="blocked:encoder_signature")
        self._health = data
        self._health_error = None
        return data

    def available(self) -> bool:
        if not self.client.base_url and self.client._transport is None:
            return False
        try:
            self._ensure_health()
            return True
        except Exception:
            return False

    def status_message(self) -> str:
        return str(self.public_status().get("message") or "")

    def public_status(self) -> dict:
        try:
            health = self._ensure_health()
            ready = True
            message = (
                f"远程 Chinese-CLIP HTTP 已接通：model={health.get('model')} "
                f"precision={health.get('precision')} dim={health.get('dimension')}。"
                "文本与图片接口同属该服务且向量已 L2 归一化，可用于同一空间比较。"
                "本状态只表示接口契约核对通过，不表示检索效果或内存占用已达标。"
            )
        except Exception as exc:
            health = {}
            ready = False
            message = redact(str(getattr(exc, "message", exc)))
        return {
            "status": "ready" if ready else "blocked",
            "message": message,
            "inference_ready": ready,
            "family": FAMILY,
            "backend": "remote_http",
            "architecture": health.get("model") or self.expected_model,
            "precision": health.get("precision") or self.expected_precision,
            "embed_dim": health.get("dimension") or self.expected_dim,
            "quality_verified": False,
            "comparable_space": True if ready else False,
            "normalized": True if ready else None,
            "text_path": TEXT_PATH,
            "image_path": IMAGE_PATH,
            "batch_text_path": TEXTS_PATH,
        }

    def encode_text(self, text: str) -> list[float]:
        self._ensure_health()
        payload = self.client.embed_text(text)
        return _vector_from_payload(payload, self.expected_dim, batch=False)  # type: ignore[return-value]

    def encode_texts(self, texts: list[str]) -> list[list[float]]:
        self._ensure_health()
        payload = self.client.embed_texts(texts)
        return _vector_from_payload(payload, self.expected_dim, batch=True)  # type: ignore[return-value]

    def encode_image(self, image_bytes: bytes) -> list[float]:
        self._ensure_health()
        payload = self.client.embed_image(image_bytes)
        return _vector_from_payload(payload, self.expected_dim, batch=False)  # type: ignore[return-value]


class ClipHttpSubtitleEncoder:
    """Independent subtitle port that may share the remote text endpoint (待验证)."""

    QUALITY_NOTE = (
        "字幕编码器与远程图文服务的 text 接口共用；该路径的语义质量尚未证实（待验证）。"
        "不会用随机或哈希向量充数。"
    )

    def __init__(self, visual: ClipHttpVisualEncoder) -> None:
        self._visual = visual

    def available(self) -> bool:
        return self._visual.available()

    def status_message(self) -> str:
        return str(self.public_status().get("message") or "")

    def public_status(self) -> dict:
        status = dict(self._visual.public_status())
        status["quality_unverified"] = True
        status["quality_verified"] = False
        status["shares_text_encoder"] = True
        if status.get("inference_ready"):
            status["message"] = self.QUALITY_NOTE + " 运行时已接通。"
        else:
            status["message"] = ((status.get("message") or "") + " " + self.QUALITY_NOTE).strip()
        return status

    def encode_text(self, text: str) -> list[float]:
        return self._visual.encode_text(text)
