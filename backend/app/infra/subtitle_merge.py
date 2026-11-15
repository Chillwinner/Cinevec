from __future__ import annotations

from app.domain.subtitle import MergeSettings, SubtitleCue, SubtitleGroup


def merge_adjacent(cues: list[SubtitleCue], settings: MergeSettings | None = None) -> list[SubtitleGroup]:
    """按暂定间隔规则合并相邻字幕，同时保留各条原文（调用方仍保存 cues）。"""
    settings = settings or MergeSettings()
    if not cues:
        return []
    groups: list[SubtitleGroup] = []
    current = [cues[0]]

    def _flush(block: list[SubtitleCue]) -> None:
        text = " ".join(item.text for item in block)
        groups.append(
            SubtitleGroup(
                index=len(groups),
                start_seconds=block[0].start_seconds,
                end_seconds=block[-1].end_seconds,
                text=text,
                cue_indexes=[item.index for item in block],
            )
        )

    for cue in cues[1:]:
        prev = current[-1]
        gap = cue.start_seconds - prev.end_seconds
        duration = cue.end_seconds - current[0].start_seconds
        chars = len(" ".join(item.text for item in current)) + 1 + len(cue.text)
        can_merge = (
            gap <= settings.max_gap_seconds
            and duration <= settings.max_group_duration_seconds
            and chars <= settings.max_group_chars
        )
        if can_merge:
            current.append(cue)
        else:
            _flush(current)
            current = [cue]
    _flush(current)
    return groups
