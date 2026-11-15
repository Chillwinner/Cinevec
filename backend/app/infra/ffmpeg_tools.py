from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from app.domain.errors import NotConfiguredError
from app.domain.media import VideoInfo
from app.infra.config import Settings


def resolve_ffmpeg(settings: Settings | None = None) -> str:
    candidates: list[str] = []
    if settings and settings.ffmpeg_path:
        candidates.append(settings.ffmpeg_path)
    which = shutil.which("ffmpeg")
    if which:
        candidates.append(which)
    for known in (
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ):
        candidates.append(known)
    try:
        import imageio_ffmpeg

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    for path in candidates:
        if path and Path(path).is_file():
            return path
    raise NotConfiguredError(
        "本机未找到 FFmpeg 可执行文件。当前未改动系统安装。可在虚拟环境使用 imageio-ffmpeg，或设置环境变量 PICA_FFMPEG_PATH 指向已有二进制。"
    )


def ffmpeg_status(settings: Settings | None = None) -> dict:
    try:
        exe = resolve_ffmpeg(settings)
        result = subprocess.run([exe, "-version"], capture_output=True, text=True, check=False)
        line = (result.stdout or result.stderr or "").splitlines()
        version = line[0] if line else "ok"
        return {"status": "ok", "message": f"已解析到 FFmpeg：{version}", "path": exe}
    except NotConfiguredError as exc:
        return {"status": "unconfigured", "message": exc.message, "path": None}


def probe_video(path: Path, settings: Settings | None = None) -> VideoInfo:
    exe = resolve_ffmpeg(settings)
    result = subprocess.run(
        [exe, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (result.stderr or "") + "\n" + (result.stdout or "")
    duration = 0.0
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
        duration = hours * 3600 + minutes * 60 + seconds
    fps = 0.0
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", text)
    if fps_match:
        fps = float(fps_match.group(1))
    width = height = 0
    size_match = re.search(r", (\d{2,5})x(\d{2,5})", text)
    if size_match:
        width, height = int(size_match.group(1)), int(size_match.group(2))
    approx = int(round(duration * fps)) if duration and fps else 0
    if duration <= 0:
        from app.domain.errors import AppError

        raise AppError("media_probe_failed", f"无法解析视频时长：{path.name}")
    return VideoInfo(
        path=str(path),
        duration_seconds=duration,
        fps=fps or 0.0,
        width=width,
        height=height,
        approx_frames=approx,
    )


def extract_png_at(path: Path, time_seconds: float, settings: Settings | None = None) -> bytes:
    exe = resolve_ffmpeg(settings)
    # -ss 放在输入前做快速定位；单帧管道输出，避免一次抽出全部帧。
    result = subprocess.run(
        [
            exe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(time_seconds, 0):.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        from app.domain.errors import AppError

        err = (result.stderr or b"").decode("utf-8", "ignore")[:300]
        raise AppError("frame_extract_failed", f"抽帧失败（t={time_seconds:.3f}s）：{err or '无输出'}")
    return result.stdout
