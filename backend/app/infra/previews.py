from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.domain.media import SampleSettings
from app.domain.model_profile import (
    PREPROCESS_CLIP_OFFICIAL_SQUARE,
    PREPROCESS_FULL_FRAME,
    PREPROCESS_LEGACY_CENTER_CROP,
)


def render_preview(image: Image.Image, settings: SampleSettings) -> bytes:
    frame = image.convert("RGB")
    frame.thumbnail((settings.preview_max_edge, settings.preview_max_edge))
    buf = BytesIO()
    frame.save(buf, format="JPEG", quality=settings.preview_quality, optimize=True)
    return buf.getvalue()


def _to_png(frame: Image.Image) -> bytes:
    buf = BytesIO()
    frame.save(buf, format="PNG")
    return buf.getvalue()


def render_legacy_center_crop(image: Image.Image, target: int) -> bytes:
    """历史实现：短边缩放后中心裁剪。仅用于对照旧索引，不得作为新模型默认。"""
    frame = image.convert("RGB")
    target = max(int(target), 1)
    width, height = frame.size
    if width <= 0 or height <= 0:
        raise ValueError("invalid image size")
    if width < height:
        new_size = (target, max(int(round(height * target / width)), 1))
    else:
        new_size = (max(int(round(width * target / height)), 1), target)
    resized = frame.resize(new_size, Image.Resampling.BICUBIC)
    left = max((resized.width - target) // 2, 0)
    top = max((resized.height - target) // 2, 0)
    cropped = resized.crop((left, top, left + target, top + target))
    return _to_png(cropped)


def render_official_square(image: Image.Image, target: int) -> bytes:
    """Chinese-CLIP official image_transform: Resize((res, res), BICUBIC), no CenterCrop."""
    frame = image.convert("RGB")
    target = max(int(target), 1)
    resized = frame.resize((target, target), Image.Resampling.BICUBIC)
    return _to_png(resized)


def render_full_frame(image: Image.Image) -> bytes:
    """Remote models: keep the complete RGB frame; do not apply a 224 crop or square stretch."""
    frame = image.convert("RGB")
    if frame.size[0] <= 0 or frame.size[1] <= 0:
        raise ValueError("invalid image size")
    return _to_png(frame)


def render_model_input(
    image: Image.Image,
    settings: SampleSettings,
    *,
    strategy: str | None = None,
) -> bytes:
    """按模型预处理策略生成模型输入。中等预览仍由 render_preview 独立生成。

    未指定策略时使用 Chinese-CLIP 官方方形 resize，不再默认短边中心裁剪。
    其他模型不得硬套 224。
    """
    chosen = (strategy or PREPROCESS_CLIP_OFFICIAL_SQUARE).strip()
    if chosen == PREPROCESS_LEGACY_CENTER_CROP:
        return render_legacy_center_crop(image, settings.model_input_size)
    if chosen == PREPROCESS_FULL_FRAME:
        return render_full_frame(image)
    if chosen == PREPROCESS_CLIP_OFFICIAL_SQUARE:
        return render_official_square(image, settings.model_input_size)
    raise ValueError(f"未知的模型输入预处理策略：{chosen}")
