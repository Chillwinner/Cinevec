from __future__ import annotations

import importlib.util
import json
import os
import threading
import urllib.error
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps

from app.domain.errors import DependencyError, NotConfiguredError
from app.domain.face import DetectedFace, FACE_DIM, l2_normalize
from app.infra.redact import redact

_MODEL_URLS = {
    "buffalo_sc": "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_sc.zip",
    "buffalo_s": "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip",
    "buffalo_l": "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
}

# Standard ArcFace 112x112 5-point reference landmarks (left eye, right eye, nose, left mouth, right mouth)
_ARCFACE_DST_KPS = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)


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


def _find_onnx_pair(model_dir: Path) -> tuple[Path | None, Path | None]:
    if not model_dir.is_dir():
        return None, None
    det_path: Path | None = None
    rec_path: Path | None = None
    for p in sorted(model_dir.rglob("*.onnx")):
        name = p.name.lower()
        if det_path is None and (name.startswith("det_") or "scrfd" in name):
            det_path = p
        elif rec_path is None and (name.startswith("w600k_") or name.startswith("glintr") or "arcface" in name):
            rec_path = p
    return det_path, rec_path


def _nms_xyxy(boxes, scores, thresh: float = 0.4) -> list[int]:
    if len(scores) == 0:
        return []
    order = scores.argsort()[::-1]
    keep: list[int] = []
    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]
    areas = (x2 - x1).clip(min=0.0) * (y2 - y1).clip(min=0.0)
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = x1[rest].clip(min=x1[i])
        yy1 = y1[rest].clip(min=y1[i])
        xx2 = x2[rest].clip(max=x2[i])
        yy2 = y2[rest].clip(max=y2[i])
        inter = (xx2 - xx1).clip(min=0.0) * (yy2 - yy1).clip(min=0.0)
        denom = areas[i] + areas[rest] - inter + 1e-6
        iou = inter / denom
        order = rest[iou <= thresh]
    return keep


def align_face_112(rgb: Image.Image, kps5) -> Image.Image:
    """Warp an RGB image to an aligned 112x112 ArcFace face chip via 5-point similarity fit."""
    import numpy as np

    # +0.5 accounts for Pillow AFFINE sampling at output pixel centers (dx + 0.5, dy + 0.5)
    src = np.asarray(kps5, dtype=np.float64).reshape(5, 2) + 0.5
    dst = np.asarray(_ARCFACE_DST_KPS, dtype=np.float64).reshape(5, 2) + 0.5
    # Solve sx = a*dx - b*dy + tx, sy = b*dx + a*dy + ty
    a_mat = np.zeros((10, 4), dtype=np.float64)
    rhs = np.zeros((10,), dtype=np.float64)
    for i in range(5):
        dx, dy = dst[i]
        sx, sy = src[i]
        a_mat[2 * i] = (dx, -dy, 1.0, 0.0)
        a_mat[2 * i + 1] = (dy, dx, 0.0, 1.0)
        rhs[2 * i] = sx
        rhs[2 * i + 1] = sy
    params, _, _, _ = np.linalg.lstsq(a_mat, rhs, rcond=None)
    a, b, tx, ty = [float(v) for v in params]
    return rgb.transform(
        (112, 112),
        Image.Transform.AFFINE,
        (a, -b, tx, b, a, ty),
        resample=Image.Resampling.BILINEAR,
    )


class InsightFaceLocalRecognizer:
    """Lazy-loading pure ONNX Runtime SCRFD + ArcFace CPU/GPU recognizer (no insightface/cv2 needed)."""

    def __init__(
        self,
        *,
        model_name: str = "buffalo_sc",
        cache_dir: str | Path = "data/models/insightface",
        det_threshold: float = 0.55,
        min_face_px: int = 24,
        flip_tta: bool = True,
        enabled_getter: Callable[[], bool] | None = None,
    ) -> None:
        self.model_name = (model_name or "buffalo_sc").strip() or "buffalo_sc"
        self.cache_dir = Path(cache_dir)
        self.det_threshold = float(det_threshold)
        self.min_face_px = int(min_face_px)
        self.flip_tta = bool(flip_tta)
        self._enabled_getter = enabled_getter
        self._lock = threading.Lock()
        self._det_session: Any = None
        self._rec_session: Any = None
        self._load_error: str | None = None

    def _is_enabled(self) -> bool:
        if callable(self._enabled_getter):
            return bool(self._enabled_getter())
        return True

    def _deps_installed(self) -> bool:
        return (
            importlib.util.find_spec("onnxruntime") is not None
            and importlib.util.find_spec("numpy") is not None
        )

    def available(self) -> bool:
        return self._is_enabled() and self._deps_installed() and self._load_error is None

    def status_message(self) -> str:
        if not self._is_enabled():
            return "人脸识别已关闭（可在「设置 → 人脸识别」一键开启）。"
        if not self._deps_installed():
            return "未检测到 onnxruntime / numpy 依赖。Docker 部署请设置 PICA_FACE_INSTALL=true，或本地安装 onnxruntime。"
        if self._load_error:
            return f"InsightFace ONNX 初始化失败：{self._load_error}"
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
            "flip_tta": self.flip_tta,
            "deps_installed": deps,
            "loaded": self._det_session is not None and self._rec_session is not None,
            "message": self.status_message(),
        }

    def _ensure_model_files(self) -> tuple[Path, Path]:
        model_dir = self.cache_dir / "models" / self.model_name
        det_path, rec_path = _find_onnx_pair(model_dir)
        if det_path is not None and rec_path is not None:
            return det_path, rec_path

        url = _MODEL_URLS.get(self.model_name)
        if not url:
            raise DependencyError(f"未在本地找到模型包 {self.model_name} 的 ONNX 文件，且无默认下载地址。")

        model_dir.mkdir(parents=True, exist_ok=True)
        zip_part = model_dir / f"{self.model_name}.zip.part"
        zip_final = model_dir / f"{self.model_name}.zip"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "CineVec/1.0"})
            with urllib.request.urlopen(req, timeout=180) as resp, open(zip_part, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
            os.replace(zip_part, zip_final)
            with zipfile.ZipFile(zip_final, "r") as zf:
                root_resolved = model_dir.resolve()
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    rel = Path(member.filename)
                    if rel.is_absolute() or ".." in rel.parts or not member.filename.lower().endswith(".onnx"):
                        continue
                    target = (model_dir / rel.name).resolve()
                    if target.parent != root_resolved:
                        continue
                    with zf.open(member, "r") as src, open(target, "wb") as dst:
                        dst.write(src.read())
        finally:
            for p in (zip_part, zip_final):
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass

        det_path, rec_path = _find_onnx_pair(model_dir)
        if det_path is None or rec_path is None:
            raise DependencyError(f"模型包 {self.model_name} 缺少 SCRFD 检测或 ArcFace 识别 ONNX 文件。")
        return det_path, rec_path

    def _ensure_loaded(self) -> tuple[Any, Any]:
        if not self._is_enabled():
            raise NotConfiguredError(self.status_message(), code="face_disabled")
        with self._lock:
            if self._det_session is not None and self._rec_session is not None:
                return self._det_session, self._rec_session
            if not self._deps_installed():
                raise NotConfiguredError(self.status_message(), code="face_deps_missing")
            try:
                import onnxruntime as ort

                det_path, rec_path = self._ensure_model_files()
                providers = ["CPUExecutionProvider"]
                available = set(ort.get_available_providers())
                if "CUDAExecutionProvider" in available:
                    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
                self._det_session = ort.InferenceSession(str(det_path), providers=providers)
                self._rec_session = ort.InferenceSession(str(rec_path), providers=providers)
                self._load_error = None
                return self._det_session, self._rec_session
            except Exception as exc:  # noqa: BLE001
                self._load_error = redact(str(exc))
                raise DependencyError(f"InsightFace ONNX 加载失败：{self._load_error}") from exc

    def _detect_scrfd(self, det_session: Any, rgb: Image.Image):
        import numpy as np

        orig_w, orig_h = rgb.size
        in_shape = det_session.get_inputs()[0].shape
        in_h = int(in_shape[2]) if len(in_shape) >= 4 and isinstance(in_shape[2], int) and in_shape[2] > 0 else 640
        in_w = int(in_shape[3]) if len(in_shape) >= 4 and isinstance(in_shape[3], int) and in_shape[3] > 0 else 640

        im_ratio = orig_h / max(1, orig_w)
        model_ratio = in_h / max(1, in_w)
        if im_ratio > model_ratio:
            new_h = in_h
            new_w = max(1, int(round(new_h / im_ratio)))
        else:
            new_w = in_w
            new_h = max(1, int(round(new_w * im_ratio)))
        det_scale = new_h / float(orig_h)

        resized = rgb.resize((new_w, new_h), Image.Resampling.BILINEAR)
        canvas = np.zeros((in_h, in_w, 3), dtype=np.float32)
        canvas[:new_h, :new_w, :] = np.asarray(resized, dtype=np.float32)
        blob = ((canvas - 127.5) / 128.0).transpose(2, 0, 1)[None, ...].astype(np.float32)

        input_name = det_session.get_inputs()[0].name
        outputs = det_session.run(None, {input_name: blob})

        strides = (8, 16, 32)
        num_anchors = 2
        by_stride_dim: dict[tuple[int, int], Any] = {}
        expected_rows = {(in_h // s) * (in_w // s) * num_anchors: s for s in strides}
        for raw in outputs:
            arr = np.asarray(raw)
            flat = arr.reshape(-1, arr.shape[-1])
            s = expected_rows.get(int(flat.shape[0]))
            if s is not None:
                by_stride_dim[(s, int(flat.shape[1]))] = flat

        all_boxes = []
        all_scores = []
        all_kps = []
        for s in strides:
            sc = by_stride_dim.get((s, 1))
            bb = by_stride_dim.get((s, 4))
            kp = by_stride_dim.get((s, 10))
            if sc is None or bb is None:
                continue
            sc_1d = sc[:, 0]
            pos = np.where(sc_1d >= self.det_threshold)[0]
            if len(pos) == 0:
                continue
            fh, fw = in_h // s, in_w // s
            centers = np.stack(np.mgrid[:fh, :fw][::-1], axis=-1).astype(np.float32) * float(s)
            centers = np.repeat(centers.reshape(-1, 2), num_anchors, axis=0)
            p = centers[pos]
            dist_b = bb[pos] * float(s)
            x1 = (p[:, 0] - dist_b[:, 0]) / det_scale
            y1 = (p[:, 1] - dist_b[:, 1]) / det_scale
            x2 = (p[:, 0] + dist_b[:, 2]) / det_scale
            y2 = (p[:, 1] + dist_b[:, 3]) / det_scale
            all_boxes.append(np.stack([x1, y1, x2, y2], axis=-1))
            all_scores.append(sc_1d[pos])
            if kp is not None:
                dist_k = kp[pos] * float(s)
                pts = np.zeros_like(dist_k)
                pts[:, 0::2] = (p[:, 0:1] + dist_k[:, 0::2]) / det_scale
                pts[:, 1::2] = (p[:, 1:2] + dist_k[:, 1::2]) / det_scale
                all_kps.append(pts.reshape(-1, 5, 2))
            else:
                all_kps.append(np.zeros((len(pos), 5, 2), dtype=np.float32))

        if not all_boxes:
            return []
        boxes = np.concatenate(all_boxes, axis=0)
        scores = np.concatenate(all_scores, axis=0)
        kps = np.concatenate(all_kps, axis=0)
        keep = _nms_xyxy(boxes, scores, thresh=0.4)
        return [(boxes[i], float(scores[i]), kps[i]) for i in keep]

    def _encode_chip(self, rec_session: Any, chip_rgb: Image.Image) -> list[float]:
        import numpy as np

        arr = np.asarray(chip_rgb, dtype=np.float32)
        blob = ((arr - 127.5) / 127.5).transpose(2, 0, 1)[None, ...].astype(np.float32)
        input_name = rec_session.get_inputs()[0].name
        emb = rec_session.run(None, {input_name: blob})[0][0]
        return l2_normalize([float(v) for v in emb])

    def _encode_arcface(self, rec_session: Any, chip_rgb: Image.Image) -> list[float]:
        emb = self._encode_chip(rec_session, chip_rgb)
        if not self.flip_tta:
            return emb
        emb_flip = self._encode_chip(rec_session, ImageOps.mirror(chip_rgb))
        if len(emb_flip) != len(emb):
            return emb
        return l2_normalize([a + b for a, b in zip(emb, emb_flip)])

    def detect_and_encode(self, image_bytes: bytes) -> list[DetectedFace]:
        if not image_bytes:
            return []
        det_sess, rec_sess = self._ensure_loaded()
        with Image.open(BytesIO(image_bytes)) as img:
            rgb = ImageOps.exif_transpose(img).convert("RGB")
            w, h = rgb.size
            if w <= 0 or h <= 0:
                return []
            detections = self._detect_scrfd(det_sess, rgb)
            results: list[tuple[float, DetectedFace]] = []
            for box, score, kps5 in detections:
                x1, y1, x2, y2 = [float(v) for v in box[:4]]
                bw = max(0.0, x2 - x1)
                bh = max(0.0, y2 - y1)
                if bw < self.min_face_px or bh < self.min_face_px:
                    continue
                if float(kps5.sum()) != 0.0:
                    chip = align_face_112(rgb, kps5)
                else:
                    cx1, cy1 = max(0, int(round(x1))), max(0, int(round(y1)))
                    cx2, cy2 = min(w, int(round(x2))), min(h, int(round(y2)))
                    chip = rgb.crop((cx1, cy1, cx2, cy2)).resize((112, 112), Image.Resampling.BILINEAR)
                emb = self._encode_arcface(rec_sess, chip)
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
