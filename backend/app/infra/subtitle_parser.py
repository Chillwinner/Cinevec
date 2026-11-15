from __future__ import annotations

import html
import re
from pathlib import Path

from app.domain.errors import AppError
from app.domain.subtitle import ParsedSubtitles, SubtitleCue

_TAG_RE = re.compile(r"<[^>]+>")
_VTT_NOTE_RE = re.compile(r"^(NOTE|STYLE|REGION)\b")
_TIME_RE = re.compile(
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})"
    r"\s*-->\s*"
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})"
)


def sanitize_cue_text(raw: str) -> str:
    """纯文本清洗：去掉标记并反转义，绝不执行内容。"""
    text = raw.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<(br|BR)\s*/?>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _decode(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "utf-16", "gb18030"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace"), "utf-8-replace"


def _to_seconds(h: str | None, m: str, s: str, frac: str) -> float:
    hours = int(h or 0)
    millis = int(frac.ljust(3, "0")[:3])
    return hours * 3600 + int(m) * 60 + int(s) + millis / 1000.0


def _looks_like_ass(name: str, text: str) -> bool:
    lower = name.lower()
    if lower.endswith(".ass") or lower.endswith(".ssa"):
        return True
    head = text.lstrip()[:400].lower()
    return "[script info]" in head or "v4+ styles" in head or "v4 styles" in head


def parse_subtitle_bytes(data: bytes, filename: str = "file.srt") -> ParsedSubtitles:
    text, encoding = _decode(data)
    if _looks_like_ass(filename, text):
        return ParsedSubtitles(format="ass", cues=_parse_ass(text), encoding=encoding)
    stripped = text.lstrip()
    if stripped.upper().startswith("WEBVTT") or filename.lower().endswith(".vtt"):
        return ParsedSubtitles(format="vtt", cues=_parse_blocks(stripped, skip_webvtt=True), encoding=encoding)
    if filename.lower().endswith(".srt") or _TIME_RE.search(text):
        return ParsedSubtitles(format="srt", cues=_parse_blocks(stripped, skip_webvtt=False), encoding=encoding)
    raise AppError("subtitle_format_unsupported", "无法识别为 SRT 或 VTT 字幕，未进行解析。")


def _ass_time(value: str) -> float:
    match = re.match(r"(\d+):(\d{2}):(\d{2})[.:](\d{1,3})", (value or "").strip())
    if not match:
        return 0.0
    frac = match.group(4)
    if len(frac) <= 2:
        cs = int(frac.ljust(2, "0")[:2]) / 100.0
    else:
        cs = int(frac.ljust(3, "0")[:3]) / 1000.0
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3)) + cs


def _parse_ass(text: str) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line.lower().startswith("dialogue:"):
            continue
        payload = line.split(":", 1)[1]
        parts = payload.split(",", 9)
        if len(parts) < 10:
            continue
        start = _ass_time(parts[1])
        end = _ass_time(parts[2])
        body = re.sub(r"\{[^}]*\}", "", parts[9])
        body = body.replace(r"\N", "\n").replace(r"\n", "\n")
        body = sanitize_cue_text(body)
        if not body:
            continue
        cues.append(SubtitleCue(index=len(cues), start_seconds=start, end_seconds=max(end, start), text=body))
    return cues


def parse_subtitle_file(path: Path) -> ParsedSubtitles:
    return parse_subtitle_bytes(path.read_bytes(), path.name)


def _parse_blocks(text: str, *, skip_webvtt: bool) -> list[SubtitleCue]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if skip_webvtt and lines and lines[0].upper().startswith("WEBVTT"):
        lines = lines[1:]
    cues: list[SubtitleCue] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("NOTE") or _VTT_NOTE_RE.match(line) or line.startswith("STYLE") or line.startswith("REGION"):
            # 跳过 NOTE/STYLE 块直到空行
            if line and _VTT_NOTE_RE.match(line) and not _TIME_RE.search(line):
                i += 1
                while i < len(lines) and lines[i].strip():
                    i += 1
                continue
            i += 1
            continue
        # 可选序号
        if re.fullmatch(r"\d+", line) and i + 1 < len(lines) and _TIME_RE.search(lines[i + 1]):
            i += 1
            line = lines[i].strip()
        match = _TIME_RE.search(line)
        if not match:
            i += 1
            continue
        start = _to_seconds(match.group(1), match.group(2), match.group(3), match.group(4))
        end = _to_seconds(match.group(5), match.group(6), match.group(7), match.group(8))
        i += 1
        payload: list[str] = []
        while i < len(lines) and lines[i].strip():
            # 忽略 VTT 行上的定位设置，已在时间行处理
            payload.append(lines[i])
            i += 1
        body = sanitize_cue_text("\n".join(payload))
        if body:
            cues.append(SubtitleCue(index=len(cues), start_seconds=start, end_seconds=max(end, start), text=body))
        while i < len(lines) and not lines[i].strip():
            i += 1
    return cues
