from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MergeSettings:
    """相邻字幕合并的暂定实现规则，产品尚未拍板。"""

    max_gap_seconds: float = 0.8
    max_group_duration_seconds: float = 8.0
    max_group_chars: int = 80


@dataclass
class SubtitleCue:
    index: int
    start_seconds: float
    end_seconds: float
    text: str


@dataclass
class SubtitleGroup:
    index: int
    start_seconds: float
    end_seconds: float
    text: str
    cue_indexes: list[int] = field(default_factory=list)


@dataclass
class ParsedSubtitles:
    format: str
    cues: list[SubtitleCue]
    encoding: str = "utf-8"
