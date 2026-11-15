from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw


def ffmpeg_or_skip():
    import pytest
    from app.domain.errors import NotConfiguredError
    from app.infra.ffmpeg_tools import resolve_ffmpeg

    try:
        return resolve_ffmpeg()
    except NotConfiguredError as exc:
        pytest.skip(str(exc))


def write_png_pattern(path: Path | None, kind: str, size=(96, 64)) -> bytes:
    image = Image.new("RGB", size, (20, 20, 20))
    draw = ImageDraw.Draw(image)
    w, h = size
    if kind == "black":
        image = Image.new("RGB", size, (0, 0, 0))
    elif kind == "white":
        image = Image.new("RGB", size, (255, 255, 255))
    elif kind == "gray":
        image = Image.new("RGB", size, (128, 128, 128))
    elif kind == "checker":
        step = 8
        for y in range(0, h, step):
            for x in range(0, w, step):
                color = (240, 40, 40) if (x // step + y // step) % 2 == 0 else (20, 200, 240)
                draw.rectangle([x, y, x + step - 1, y + step - 1], fill=color)
    elif kind == "checker2":
        step = 8
        for y in range(0, h, step):
            for x in range(0, w, step):
                color = (40, 240, 80) if (x // step + y // step) % 2 == 0 else (240, 200, 20)
                draw.rectangle([x, y, x + step - 1, y + step - 1], fill=color)
    else:
        raise ValueError(kind)
    buf = BytesIO()
    image.save(buf, format="PNG")
    data = buf.getvalue()
    if path is not None:
        path.write_bytes(data)
    return data


def generate_video_from_pngs(pngs: list[Path], dest: Path, fps: int = 5) -> Path:
    """用测试自生成的 PNG 合成小视频，仅用于测试，不是用户数据。"""
    import subprocess

    exe = ffmpeg_or_skip()
    dest.parent.mkdir(parents=True, exist_ok=True)
    lst = dest.with_suffix(".txt")
    lines = []
    for png in pngs:
        lines.append(f"file '{png}'")
        lines.append(f"duration {1.0 / fps:.4f}")
    if pngs:
        lines.append(f"file '{pngs[-1]}'")
    lst.write_text("\n".join(lines), encoding="utf-8")
    result = subprocess.run(
        [
            exe,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-vsync",
            "vfr",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not dest.is_file():
        # concat demuxer 在部分构建上对 PNG 不稳，退回 testsrc/color
        raise RuntimeError(result.stderr[-400:] if result.stderr else "ffmpeg concat failed")
    return dest


def generate_color_video(dest: Path, color: str, duration: float, fps: int = 5, size=(80, 48)) -> Path:
    import subprocess

    exe = ffmpeg_or_skip()
    dest.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    result = subprocess.run(
        [
            exe,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={w}x{h}:d={duration}:r={fps}",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not dest.is_file():
        raise RuntimeError(result.stderr[-400:] if result.stderr else "ffmpeg color failed")
    return dest


def generate_testsrc_video(dest: Path, duration: float, fps: int = 5, size=(80, 48)) -> Path:
    import subprocess

    exe = ffmpeg_or_skip()
    dest.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    result = subprocess.run(
        [
            exe,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={w}x{h}:rate={fps}:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not dest.is_file():
        raise RuntimeError(result.stderr[-400:] if result.stderr else "ffmpeg testsrc failed")
    return dest


def generate_static_pattern_video(dest: Path, kind: str, duration: float, fps: int = 5, size=(80, 48)) -> Path:
    import subprocess

    exe = ffmpeg_or_skip()
    dest.parent.mkdir(parents=True, exist_ok=True)
    png = dest.with_suffix(".src.png")
    write_png_pattern(png, kind, size=size)
    result = subprocess.run(
        [
            exe,
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(png),
            "-t",
            str(duration),
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not dest.is_file():
        raise RuntimeError(result.stderr[-400:] if result.stderr else "ffmpeg still failed")
    return dest
