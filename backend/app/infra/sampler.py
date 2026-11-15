from __future__ import annotations

from pathlib import Path

from app.domain.media import SamplePoint, SampleSettings, VideoInfo
from app.infra.config import Settings
from app.infra.ffmpeg_tools import extract_png_at, probe_video


def build_sample_plan(info: VideoInfo, settings: SampleSettings) -> list[SamplePoint]:
    duration = max(info.duration_seconds, 0.0)
    if duration <= 0:
        return []
    mode = settings.mode
    if mode == "uniform_total":
        count = max(int(settings.uniform_total), 1)
        if info.approx_frames:
            count = min(count, info.approx_frames)
        if count == 1:
            times = [0.0]
        else:
            times = [min(duration * i / count, max(duration - 1e-3, 0.0)) for i in range(count)]
    elif mode == "frames_per_minute":
        rate = max(float(settings.frames_per_minute), 0.001)
        interval = 60.0 / rate
        times = _interval_times(duration, interval)
    else:
        times = _interval_times(duration, max(float(settings.interval_seconds), 0.001))
    return [SamplePoint(index=i, time_seconds=t) for i, t in enumerate(times)]


def _interval_times(duration: float, interval: float) -> list[float]:
    times = []
    t = 0.0
    # 包含 0，按间隔取样，不超过时长。短于一个间隔的视频只会有第 0 秒一帧。
    while t < duration - 1e-6:
        times.append(round(t, 4))
        t += interval
    return times or [0.0]


class FFmpegFrameSampler:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def probe(self, path: Path) -> VideoInfo:
        return probe_video(path, self._settings)

    def plan(self, info: VideoInfo, settings: SampleSettings) -> list[SamplePoint]:
        return build_sample_plan(info, settings)

    def extract_one(self, path: Path, point: SamplePoint) -> bytes:
        return extract_png_at(path, point.time_seconds, self._settings)
