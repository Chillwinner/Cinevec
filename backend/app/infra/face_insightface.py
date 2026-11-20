from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from app.domain.errors import DependencyError, NotConfiguredError
from app.domain.face import DetectedFace, FACE_DIM, l2_normalize
from app.infra.redact import redact


class DisabledFaceRecognizer:
    """Zero-footprint stub used when face recognition is turned off or unconfigured."""

    def __init__(self, message: str = "人脸识别默认关闭，可在设置或 PICA_FACE_ENABLED=true 中开启。") -> None:
        self._message = message

    def available(self) -> bool:
        return False

    def status_message(self) -> str:
        return self._message

    def public_status(self) -> dict[str, Any]:
        return {
            "status": "disabled",
            "enabled": False,
            "available": False,
            "backend": "disabled",
            "model": None,
            "dim": FACE_DIM,
            "message": self._message,
        }

    def detect_and_encode(self, image_bytes: bytes) -> list[DetectedFace]:
        raise NotConfiguredError(self._message, code="face_disabled")


class InsightFaceLocalRecognizer:
    """Lazy-loading InsightFace (SCRFD + ArcFace ONNX) CPU/GPU recognizer."""

    def __init__(
        self,
        *,
        model_name: str = "buffalo_s",
        cache_dir: str | Path = "data/models/insightface",
        det_threshold: float = 0.55,
        min_face_px: int = 24,
        enabled_getter: Callable[[], bool] | None = None,
    ) -> None:
        self.model_name = (model_name or "buffalo_s").strip() or "buffalo_s"
        self.cache_dir = Path(cache_dir)
        self.det_threshold = float(det_threshold)
        self.min_face_px = int(min_face_px)
        self._enabled_getter = enabled_getter
        self._lock = threading.Lock()
        self._app: Any = None
        self._load_error: str | None = None

    def _is_enabled(self) -> bool:
        if callable(self._enabled_getter):
            return bool(self._enabled_getter())
        return True

    def _deps_installed(self) -> bool:
        return (
            importlib.util.find_spec("insightface") is not None
            and importlib.util.find_spec("onnxruntime") is not None
        )

    def available(self) -> bool:
        return self._is_enabled() and self._deps_installed() and self._load_error is None

    def status_message(self) -> str:
        if not self._is_enabled():
            return "人脸识别已关闭（可在「设置 → 人脸识别」一键开启）。"
        if not self._deps_installed():
            return "未检测到 insightface / onnxruntime 依赖。Docker 部署请设置 PICA_FACE_INSTALL=true，或本地安装 insightface 与 onnxruntime。"
        if self._load_error:
            return f"InsightFace 模型初始化失败：{self._load_error}"
        return f"InsightFace 本地 ONNX 已就绪（模型包：{self.model_name}，512 维）。"

    def public_status(self) -> dict[str, Any]:
        enabled = self._is_enabled()
        deps = self._deps_installed()
        ready = enabled and deps and self._load_error is None
        status = "ok" if ready else ("disabled" if not enabled else ("error" if self._load_error else "unconfigured"))
        return {
            "status": status,
            "enabled": enabled,
            "available": ready,
            "backend": "local_insightface",
            "model": self.model_name,
            "dim": FACE_DIM,
            "deps_installed": deps,
            "loaded": self._app is not None,
            "message": self.status_message(),
        }

    def _ensure_loaded(self) -> Any:
        if not self._is_enabled():
            raise NotConfiguredError(self.status_message(), code="face_disabled")
        with self._lock:
            if self._app is not None:
                return self._app
            if not self._deps_installed():
                raise NotConfiguredError(self.status_message(), code="face_deps_missing")
            try:
                from insightface.app import FaceAnalysis

                self.cache_dir.mkdir(parents=True, exist_ok=True)
                app = FaceAnalysis(
                    name=self.model_name,
                    root=str(self.cache_dir),
                    providers=["CPUExecutionProvider"],
                )
                app.prepare(ctx_id=-1, det_size=(640, 640))
                self._app = app
                self._load_error = None
                return self._app
            except Exception as exc:  # noqa: BLE001
                self._load_error = redact(str(exc))
                raise DependencyError(f"InsightFace 加载失败：{self._load_error}") from exc

    def detect_and_encode(self, image_bytes: bytes) -> list[DetectedFace]:
        if not image_bytes:
            return []
        app = self._ensure_loaded()
        import numpy as np

        with Image.open(BytesIO(image_bytes)) as img:
            rgb = img.convert("RGB")
            w, h = rgb.size
            arr = np.asarray(rgb, dtype=np.uint8)
        if w <= 0 or h <= 0:
            return []
        # InsightFace expects BGR uint8 HxWxC
        bgr = arr[:, :, ::-1].copy()
        raw_faces = app.get(bgr) or []
        results: list[tuple[float, DetectedFace]] = []
        for face in raw_faces:
            score = float(getattr(face, "det_score", 0.0) or 0.0)
            if score < self.det_threshold:
                continue
            bbox_raw = getattr(face, "bbox", None)
            emb_raw = getattr(face, "normed_embedding", None)
            if emb_raw is None:
                emb_raw = getattr(face, "embedding", None)
            if bbox_raw is None or emb_raw is None:
                continue
            x1, y1, x2, y2 = [float(v) for v in bbox_raw[:4]]
            bw = max(0.0, x2 - x1)
            bh = max(0.0, y2 - y1)
            if bw < self.min_face_px or bh < self.min_face_px:
                continue
            emb = l2_normalize([float(v) for v in emb_raw])
            if len(emb) != FACE_DIM:
                continue
            norm_box = [
                max(0.0, min(1.0, x1 / w)),
                max(0.0, min(1.0, y1 / h)),
                max(0.0, min(1.0, x2 / w)),
                max(0.0, min(1.0, y2 / h)),
            ]
            area_score = ((norm_box[2] - norm_box[0]) * (norm_box[3] - norm_box[1])) * score
            results.append((area_score, DetectedFace(bbox=norm_box, det_score=score, embedding=emb)))
        results.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in results]


class FaceHttpRecognizer:
    """Optional remote HTTP face recognition adapter (`/health`, `/api/v1/faces/detect`)."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        enabled_getter: Callable[[], bool] | None = None,
        transport: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = float(timeout or 30.0)
        self._enabled_getter = enabled_getter
        self._transport = transport

    def _is_enabled(self) -> bool:
        return bool(self._enabled_getter()) if callable(self._enabled_getter) else True

    def available(self) -> bool:
        return self._is_enabled() and (bool(self.base_url) or self._transport is not None)

    def status_message(self) -> str:
        if not self._is_enabled():
            return "人脸识别已关闭。"
        if not self.base_url and self._transport is None:
            return "未配置远程人脸识别服务地址 PICA_FACE_HTTP_BASE_URL。"
        return f"远程人脸识别服务：{self.base_url or 'injected'}"

    def public_status(self) -> dict[str, Any]:
        enabled = self._is_enabled()
        ready = self.available()
        return {
            "status": "ok" if ready else ("disabled" if not enabled else "unconfigured"),
            "enabled": enabled,
            "available": ready,
            "backend": "remote_http",
            "model": "remote_face_512d",
            "dim": FACE_DIM,
            "message": self.status_message(),
        }

    def detect_and_encode(self, image_bytes: bytes) -> list[DetectedFace]:
        if not self._is_enabled():
            raise NotConfiguredError("人脸识别未开启。", code="face_disabled")
        if self._transport is not None:
            payload = self._transport("POST", "/api/v1/faces/detect", image_bytes)
        else:
            if not self.base_url:
                raise NotConfiguredError("未配置 PICA_FACE_HTTP_BASE_URL。", code="face_unconfigured")
            boundary = "----PicaFaceBoundary"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="frame.jpg"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n"
            ).encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()
            req = urllib.request.Request(
                f"{self.base_url}/api/v1/faces/detect",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "ignore") if exc.fp else str(exc)
                raise DependencyError(redact(f"远程人脸服务 HTTP {exc.code}：{detail[:300]}")) from exc
            except urllib.error.URLError as exc:
                raise DependencyError(redact(f"无法连接远程人脸服务：{exc.reason}")) from exc

        faces_out: list[DetectedFace] = []
        for item in payload.get("faces") or []:
            emb = l2_normalize([float(x) for x in (item.get("embedding") or [])])
            if len(emb) != FACE_DIM:
                continue
            bbox = [float(x) for x in (item.get("bbox") or [0, 0, 1, 1])[:4]]
            score = float(item.get("det_score") or 1.0)
            faces_out.append(DetectedFace(bbox=bbox, det_score=score, embedding=emb))
        return faces_out
