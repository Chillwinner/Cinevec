from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SampleMode = Literal["interval_seconds", "uniform_total", "frames_per_minute"]


@dataclass
class SampleSettings:
    mode: SampleMode = "interval_seconds"
    interval_seconds: float = 5.0
    uniform_total: int = 20
    frames_per_minute: float = 12.0
    # 下列阈值均为暂定实现值，产品尚未拍板
    black_max_mean: float = 8.0
    white_min_mean: float = 247.0
    blur_max_laplacian_var: float = 18.0
    similar_max_mad: float = 3.5
    preview_max_edge: int = 720
    preview_quality: int = 85
    model_input_size: int = 224


@dataclass
class VideoInfo:
    path: str
    duration_seconds: float
    fps: float
    width: int
    height: int
    approx_frames: int


@dataclass
class SamplePoint:
    index: int
    time_seconds: float


@dataclass
class PreparedFrame:
    index: int
    time_seconds: float
    kept: bool
    drop_reason: str | None
    preview_key: str | None = None
    model_input_key: str | None = None
    extra: dict = field(default_factory=dict)
