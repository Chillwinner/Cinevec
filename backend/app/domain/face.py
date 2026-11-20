from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from io import BytesIO
from typing import Iterable, Sequence

from PIL import Image, ImageOps

FACE_DIM = 512


@dataclass(frozen=True)
class DetectedFace:
    """Single detected face bounding box (normalized 0..1) and 512-d unit embedding."""

    bbox: list[float]
    det_score: float
    embedding: list[float]
    crop_jpeg: bytes | None = None


@dataclass
class FaceSettings:
    enabled: bool = False
    cluster_threshold: float = 0.40
    det_threshold: float = 0.55
    min_face_px: int = 24
    sync_person_tags: bool = True


def l2_normalize(vec: Sequence[float]) -> list[float]:
    values = [float(x) for x in vec]
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= 1e-12:
        return values
    return [v / norm for v in values]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for a, b in zip(left, right):
        fa = float(a)
        fb = float(b)
        dot += fa * fb
        na += fa * fa
        nb += fb * fb
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom <= 1e-12:
        return 0.0
    return dot / denom


def average_normalized_vectors(vectors: Iterable[Sequence[float]]) -> list[float] | None:
    acc: list[float] | None = None
    count = 0
    for raw in vectors:
        unit = l2_normalize(raw)
        if not unit:
            continue
        if acc is None:
            acc = list(unit)
        elif len(unit) == len(acc):
            for idx, val in enumerate(unit):
                acc[idx] += val
        count += 1
    if acc is None or count == 0:
        return None
    return l2_normalize(acc)


def encode_vector_blob(vec: Sequence[float]) -> bytes:
    if not vec:
        return b""
    return struct.pack(f"<{len(vec)}f", *(float(x) for x in vec))


def decode_vector_blob(blob: bytes | memoryview | None) -> list[float]:
    if not blob:
        return []
    raw = bytes(blob)
    if len(raw) % 4 != 0:
        return []
    count = len(raw) // 4
    return list(struct.unpack(f"<{count}f", raw))


def crop_face_jpeg(image_bytes: bytes, bbox: Sequence[float], *, size: int = 160, margin: float = 0.22) -> bytes:
    """Crop normalized [x1, y1, x2, y2] bbox into a centered square face thumbnail JPEG."""
    with Image.open(BytesIO(image_bytes)) as img:
        rgb = ImageOps.exif_transpose(img).convert("RGB")
        w, h = rgb.size
        if len(bbox) >= 4 and w > 0 and h > 0:
            x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
            # Support normalized coordinates (0..1.5) or pixel coordinates
            if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
                px1, py1, px2, py2 = x1 * w, y1 * h, x2 * w, y2 * h
            else:
                px1, py1, px2, py2 = x1, y1, x2, y2
            bw = max(4.0, px2 - px1)
            bh = max(4.0, py2 - py1)
            side = max(bw, bh) * (1.0 + margin * 2.0)
            cx = (px1 + px2) * 0.5
            cy = (py1 + py2) * 0.5
            left = max(0, int(round(cx - side * 0.5)))
            top = max(0, int(round(cy - side * 0.5)))
            right = min(w, int(round(cx + side * 0.5)))
            bottom = min(h, int(round(cy + side * 0.5)))
            if right > left and bottom > top:
                rgb = rgb.crop((left, top, right, bottom))
        rgb = rgb.resize((size, size), Image.Resampling.LANCZOS)
        out = BytesIO()
        rgb.save(out, format="JPEG", quality=88)
        return out.getvalue()
