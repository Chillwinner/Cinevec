"""EmbeddingGemma 2 remote HTTP adapter.

The actual service contract is not yet verified. Default request paths follow
the previously confirmed Chinese-CLIP HTTP *shape* only and MUST be treated as
pending. This adapter is independent of ClipHttpVisualEncoder and must not be
mixed into a 512-d Chinese-CLIP space.

Do not construct this encoder against a live 8001 service until the user says
the remote deployment is ready. available() and public_status() do not probe
the network while contract_status is pending_verification.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Literal

from app.domain.errors import DependencyError, NotConfiguredError
from app.domain.model_profile import (
    CONTRACT_PENDING,
    CONTRACT_VERIFIED,
    EG2_DOCUMENT_TEMPLATE,
    EG2_SEARCH_QUERY_INSTRUCTION,
    EG2_SEARCH_QUERY_TEMPLATE,
    FAMILY_EMBEDDINGGEMMA2,
    PREPROCESS_FULL_FRAME,
    PREFIX_BY_UNVERIFIED,
    TaskRole,
    prepare_embeddinggemma2_text,
    reject_dimension_mismatch,
)
from app.infra.pipeline_timing import extract_remote_timings
from app.infra.redact import redact

# 下列路径仅沿用既有 API 形状，默认视为待契约核实，不能当作已接通。
DEFAULT_HEALTH_PATH = "/health"
DEFAULT_TEXT_PATH = "/api/v1/embed/text"
DEFAULT_TEXTS_PATH = "/api/v1/embed/texts"
DEFAULT_IMAGE_PATH = "/api/v1/embed/image"


@dataclass(frozen=True)
class GemmaHttpSignature:
    health_path: str = DEFAULT_HEALTH_PATH
    text_path: str = DEFAULT_TEXT_PATH
    texts_path: str = DEFAULT_TEXTS_PATH
    image_path: str = DEFAULT_IMAGE_PATH
    text_field: str = "text"
    texts_field: str = "texts"
    instruction_field: str = "instruction"
    image_field: str = "file"
    vector_field: str = "vector"
    vectors_field: str = "vectors"
    dimension_field: str = "dimension"
    model_field: str = "model"
    paths_verified: bool = False
    notes: str = "HTTP 路径沿用既有 /health 与 /api/v1/embed/* 形状，待 EmbeddingGemma 2 服务契约核实后再启用。"


def _join(base: str, path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return base.rstrip("/") + path


def _one_vector(vec: Any, expected_dim: Any) -> list[float]:
    if not isinstance(vec, list) or not vec or not isinstance(vec[0], (int, float)):
        raise DependencyError("远程编码器未返回数值向量，已阻断，不会生成假向量。")
    values = [float(x) for x in vec]
    if expected_dim is not None and int(expected_dim) != len(values):
        problem = reject_dimension_mismatch(int(expected_dim), len(values), configured_family=FAMILY_EMBEDDINGGEMMA2)
        raise DependencyError(problem or "维度不一致，已阻断。")
    if all(v == 0.0 for v in values):
        raise DependencyError("远程编码器输出了零向量，已阻断，不会用随机或哈希向量充数。")
    return values


class GemmaHttpClient:
    def __init__(self, base_url: str, timeout: float = 30.0, transport=None, signature: GemmaHttpSignature | None = None) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self._transport = transport
        self.signature = signature or GemmaHttpSignature()

    def health(self) -> dict[str, Any]:
        return self._request("GET", self.signature.health_path)

    def embed_text(self, text: str, instruction: str | None = None, *, omit_instruction: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {self.signature.text_field: text}
        if not omit_instruction:
            body[self.signature.instruction_field] = instruction
        return self._request("POST", self.signature.text_path, json_body=body)

    def embed_texts(self, texts: list[str], instruction: str | None = None, *, omit_instruction: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {self.signature.texts_field: list(texts)}
        if not omit_instruction:
            body[self.signature.instruction_field] = instruction
        return self._request("POST", self.signature.texts_path, json_body=body)

    def embed_image(self, image_bytes: bytes, filename: str = "image.png") -> dict[str, Any]:
        return self._request("POST", self.signature.image_path, file_bytes=image_bytes, filename=filename)

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
            boundary = "----PicaGemmaHttpBoundary"
            filename = filename.replace('"', "").replace("\r", "").replace("\n", "") or "image.png"
            field = self.signature.image_field
            parts = [
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
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


class EmbeddingGemmaHttpVisualEncoder:
    """Independent EmbeddingGemma 2 HTTP adapter. Contract pending until verified."""

    def __init__(
        self,
        base_url: str,
        *,
        expected_dim: int = 768,
        expected_model: str = "google/embeddinggemma-2",
        timeout: float = 30.0,
        transport=None,
        model_id: str | None = None,
        model_version: str | None = None,
        contract_status: str = CONTRACT_PENDING,
        task_prefix_applied_by: str = PREFIX_BY_UNVERIFIED,
        signature: GemmaHttpSignature | None = None,
        preprocess_version: str = PREPROCESS_FULL_FRAME,
    ) -> None:
        if int(expected_dim) != 768:
            raise ValueError(
                f"EmbeddingGemma 2 适配器只接受原生维度 768，收到 {expected_dim}。不会自动改用其他维度。"
            )
        self.client = GemmaHttpClient(base_url, timeout=timeout, transport=transport, signature=signature)
        self.expected_dim = 768
        self.expected_model = expected_model
        self.model_id = model_id or "embeddinggemma-2"
        self.model_version = model_version or "unverified"
        self.contract_status = (contract_status or CONTRACT_PENDING).strip() or CONTRACT_PENDING
        self.task_prefix_applied_by = (task_prefix_applied_by or PREFIX_BY_UNVERIFIED).strip() or PREFIX_BY_UNVERIFIED
        self.preprocess_version = preprocess_version or PREPROCESS_FULL_FRAME
        self.family = FAMILY_EMBEDDINGGEMMA2
        self._health_error: str | None = None
        self._health: dict[str, Any] | None = None
        self._last_text_prefix: dict[str, Any] | None = None
        self._last_embed: dict[str, Any] | None = None

    def _contract_ready(self) -> bool:
        return self.contract_status == CONTRACT_VERIFIED

    def _ensure_health(self) -> dict[str, Any]:
        if not self._contract_ready():
            raise NotConfiguredError(
                "EmbeddingGemma 2 远程契约尚未核实，不会探测或调用模型服务。",
                code="blocked:encoder_contract_pending",
            )
        if self._health is not None and self._health_error is None:
            return self._health
        try:
            data = self.client.health()
        except Exception as exc:  # noqa: BLE001
            self._health_error = redact(str(getattr(exc, "message", exc)))
            raise NotConfiguredError(self._health_error, code="blocked:encoder_health") from exc
        problems = []
        model = str(data.get(self.client.signature.model_field) or data.get("model") or "")
        dimension = data.get(self.client.signature.dimension_field, data.get("dimension"))
        if self.expected_model and model and model != self.expected_model:
            problems.append(f"远程模型 {model} 与配置 {self.expected_model} 不一致，拒绝混用。")
        if dimension is not None:
            mismatch = reject_dimension_mismatch(self.expected_dim, int(dimension), configured_family=FAMILY_EMBEDDINGGEMMA2)
            if mismatch:
                problems.append(mismatch)
        if data.get("status") not in (None, "ok", "healthy", "OK"):
            problems.append(f"远程 health 状态为 {data.get('status')!r}。")
        if problems:
            self._health_error = "；".join(problems)
            raise NotConfiguredError(self._health_error, code="blocked:encoder_signature")
        self._health = data
        self._health_error = None
        return data

    def available(self) -> bool:
        # 未核实前绝不发起 health/embed 请求，避免打到正在部署的 8001。
        if not self._contract_ready():
            return False
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
        sig = self.client.signature
        pending = not self._contract_ready()
        base = {
            "status": "pending_verification" if pending else "blocked",
            "inference_ready": False,
            "family": self.family,
            "backend": "remote_http",
            "architecture": self.expected_model,
            "embed_dim": self.expected_dim,
            "native_dim": 768,
            "contract_status": self.contract_status,
            "task_prefix_applied_by": self.task_prefix_applied_by,
            "query_prefix_template": EG2_SEARCH_QUERY_TEMPLATE,
            "query_instruction": EG2_SEARCH_QUERY_INSTRUCTION,
            "document_prefix_template": EG2_DOCUMENT_TEMPLATE,
            "image_prefix": None,
            "preprocess_version": self.preprocess_version,
            "http_paths_unverified": not sig.paths_verified,
            "http_signature": {
                "health_path": sig.health_path,
                "text_path": sig.text_path,
                "texts_path": sig.texts_path,
                "image_path": sig.image_path,
                "verified": sig.paths_verified,
            },
            "quality_verified": False,
            "comparable_space": False,
            "save_is_not_health": True,
        }
        if pending:
            base["message"] = (
                "EmbeddingGemma 2 配置已登记（原生 768），远程服务契约尚未核实。"
                "保存配置不等于健康检查通过，当前不会探测或调用模型端口。"
                f" 文本任务前缀职责为 {self.task_prefix_applied_by}。"
                f" {sig.notes}"
            )
            return base
        try:
            health = self._ensure_health()
            base.update(
                {
                    "status": "ready",
                    "inference_ready": True,
                    "comparable_space": True,
                    "message": (
                        f"EmbeddingGemma 2 HTTP 契约已按配置核对：model={health.get('model')} "
                        f"dim={health.get('dimension')}。这只表示元信息校验通过，不表示检索质量已验证。"
                    ),
                    "embed_dim": health.get("dimension") or self.expected_dim,
                }
            )
            return base
        except Exception as exc:
            base["message"] = redact(str(getattr(exc, "message", exc)))
            return base

    def _vector_from(self, payload: dict[str, Any]) -> list[float]:
        if not isinstance(payload, dict):
            raise DependencyError("远程编码器返回了无法识别的响应。")
        vec = payload.get(self.client.signature.vector_field)
        dim = payload.get(self.client.signature.dimension_field)
        check = self.expected_dim
        if dim is not None:
            mismatch = reject_dimension_mismatch(check, int(dim), configured_family=FAMILY_EMBEDDINGGEMMA2)
            if mismatch:
                raise DependencyError(mismatch)
        return _one_vector(vec, check)

    def _require_ready(self) -> None:
        if not self._contract_ready():
            raise NotConfiguredError(
                "EmbeddingGemma 2 远程契约尚未核实，拒绝发起编码请求。",
                code="blocked:encoder_contract_pending",
            )
        self._ensure_health()

    def encode_text(self, text: str) -> list[float]:
        return self.encode_text_for_task(text, role="query")

    def encode_text_for_task(self, text: str, role: TaskRole = "query", title: str | None = None) -> list[float]:
        self._require_ready()
        prepared = prepare_embeddinggemma2_text(
            text,
            role=role,
            applied_by=self.task_prefix_applied_by,
            title=title,
        )
        self._last_text_prefix = prepared
        payload = self.client.embed_text(
            prepared["text"],
            instruction=prepared.get("instruction"),
            omit_instruction=bool(prepared.get("omit_instruction")),
        )
        return self._vector_from(payload)

    def encode_image(self, image_bytes: bytes) -> list[float]:
        self._last_embed = None
        t0 = time.perf_counter()
        self._require_ready()
        ready = time.perf_counter() - t0
        t1 = time.perf_counter()
        # 图片不加文本任务前缀；调用方应送完整画面，本适配器不改字节内容。
        payload = self.client.embed_image(image_bytes)
        http = time.perf_counter() - t1
        vector = self._vector_from(payload)
        remote = extract_remote_timings(payload if isinstance(payload, dict) else None)
        processed = payload.get("processed_size") if isinstance(payload, dict) else None
        received = payload.get("received_size") if isinstance(payload, dict) else None
        extra: dict[str, Any] = {}
        if isinstance(processed, dict):
            extra["processed_width"] = processed.get("width")
            extra["processed_height"] = processed.get("height")
            extra["processed_source"] = processed.get("source")
            extra["processed_tokens"] = processed.get("tokens")
            extra["processed_features"] = processed.get("features")
            extra["processed_tensor"] = processed.get("tensor_shape")
        if isinstance(received, dict):
            extra["received_width"] = received.get("width")
            extra["received_height"] = received.get("height")
        if isinstance(payload, dict) and payload.get("device"):
            extra["device"] = payload.get("device")
        self._last_embed = {
            "ready_seconds": round(ready, 4),
            "http_seconds": round(http, 4),
            "request_bytes": len(image_bytes),
            "remote_split": bool(remote),
            **remote,
            **extra,
        }
        return vector

    def last_embed_meta(self) -> dict[str, Any] | None:
        return dict(self._last_embed) if self._last_embed else None

    def last_text_prefix(self) -> dict[str, Any] | None:
        return dict(self._last_text_prefix) if self._last_text_prefix else None


class EmbeddingGemmaHttpSubtitleEncoder:
    def __init__(self, visual: EmbeddingGemmaHttpVisualEncoder) -> None:
        self._visual = visual

    def available(self) -> bool:
        return self._visual.available()

    def status_message(self) -> str:
        return self._visual.status_message()

    def public_status(self) -> dict:
        data = dict(self._visual.public_status())
        data["role"] = "subtitle_document"
        return data

    def encode_text(self, text: str) -> list[float]:
        return self._visual.encode_text_for_task(text, role="document")
