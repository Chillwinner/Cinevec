from __future__ import annotations

from io import BytesIO
from typing import Any

from app.infra.clock import now, parse_dt

_REMOTE_SECOND_KEYS = {
    "queue": "queue_seconds",
    "queue_s": "queue_seconds",
    "queue_sec": "queue_seconds",
    "queue_seconds": "queue_seconds",
    "wait": "queue_seconds",
    "waiting": "queue_seconds",
    "preprocess": "preprocess_seconds",
    "preprocess_s": "preprocess_seconds",
    "preprocess_sec": "preprocess_seconds",
    "preprocess_seconds": "preprocess_seconds",
    "preproc": "preprocess_seconds",
    "inference": "inference_seconds",
    "inference_s": "inference_seconds",
    "inference_sec": "inference_seconds",
    "inference_seconds": "inference_seconds",
    "forward": "inference_seconds",
    "compute": "inference_seconds",
    "model": "inference_seconds",
}

_REMOTE_MS_KEYS = {
    "queue_ms": "queue_seconds",
    "preprocess_ms": "preprocess_seconds",
    "preproc_ms": "preprocess_seconds",
    "inference_ms": "inference_seconds",
    "forward_ms": "inference_seconds",
    "compute_ms": "inference_seconds",
}


def start(checkpoint: dict[str, Any], name: str) -> None:
    timings = dict(checkpoint.get("timings") or {})
    item = dict(timings.get(name) or {})
    if not item.get("started_at"):
        item["started_at"] = now().isoformat()
    timings[name] = item
    checkpoint["timings"] = timings


def end(checkpoint: dict[str, Any], name: str) -> None:
    timings = dict(checkpoint.get("timings") or {})
    item = dict(timings.get(name) or {})
    ended = now()
    item.setdefault("started_at", ended.isoformat())
    item["ended_at"] = ended.isoformat()
    started = parse_dt(item.get("started_at"))
    if started:
        if started.tzinfo is None:
            started = started.replace(tzinfo=ended.tzinfo)
        item["seconds"] = round((ended - started).total_seconds(), 4)
    timings[name] = item
    checkpoint["timings"] = timings


def note(checkpoint: dict[str, Any], name: str, **fields: Any) -> None:
    timings = dict(checkpoint.get("timings") or {})
    item = dict(timings.get(name) or {})
    for key, value in fields.items():
        if value is not None:
            item[key] = value
    timings[name] = item
    checkpoint["timings"] = timings


def image_bytes_info(data: bytes) -> dict[str, Any]:
    info: dict[str, Any] = {"bytes": len(data)}
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as frame:
            info["width"] = int(frame.size[0])
            info["height"] = int(frame.size[1])
            info["format"] = str(frame.format or "")
    except Exception:
        pass
    return info


def extract_remote_timings(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Pull queue / preprocess / inference if the encoder JSON already has them.

    The current EmbeddingGemma 2 HTTP contract only returns dimension+vector;
    this stays empty until the service adds those fields. Never invent numbers.
    """
    if not isinstance(payload, dict):
        return {}
    blob = payload.get("timings") if isinstance(payload.get("timings"), dict) else payload
    if not isinstance(blob, dict):
        return {}
    out: dict[str, Any] = {}
    for key, dest in _REMOTE_SECOND_KEYS.items():
        if key in blob and isinstance(blob[key], (int, float)):
            out[dest] = round(float(blob[key]), 4)
    for key, dest in _REMOTE_MS_KEYS.items():
        if key in blob and isinstance(blob[key], (int, float)):
            out[dest] = round(float(blob[key]) / 1000.0, 4)
    nested = blob.get("timings") if blob is payload else None
    if isinstance(nested, dict) and nested is not blob:
        more = extract_remote_timings({"timings": nested})
        for key, value in more.items():
            out.setdefault(key, value)
    return out
