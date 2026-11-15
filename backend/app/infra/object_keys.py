from __future__ import annotations

from pathlib import PurePosixPath

from app.domain.errors import AppError

ORIGINALS_PREFIX = "originals"
PREVIEWS_PREFIX = "previews"
PLUGIN_PREFIX = "plugin"


def sanitize_relative_path(value: str, *, fallback_name: str = "") -> str:
    """Keep a relative object path. Reject traversal, absolute paths, and empty names."""
    raw = (value or "").strip().replace("\\", "/")
    if not raw:
        raw = (fallback_name or "").strip().replace("\\", "/")
    if not raw:
        raise AppError("invalid", "上传文件缺少相对路径或文件名。")
    if raw.startswith("/") or raw.startswith("~") or ":" in raw.split("/")[0]:
        raise AppError("invalid", "拒绝绝对路径或盘符路径。")
    parts: list[str] = []
    for part in PurePosixPath(raw).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise AppError("invalid", "拒绝包含上级目录的路径。")
        cleaned = part.replace("\x00", "").strip()
        if not cleaned or cleaned in {".", ".."}:
            raise AppError("invalid", "路径分段无效。")
        parts.append(cleaned)
    if not parts:
        raise AppError("invalid", "上传文件名无效。")
    return "/".join(parts)


def hosted_original_key(batch_id: str, relative_path: str, *, fallback_name: str = "") -> str:
    batch = sanitize_relative_path(batch_id)
    relative = sanitize_relative_path(relative_path, fallback_name=fallback_name)
    return f"{ORIGINALS_PREFIX}/{batch}/{relative}"


def plugin_object_key(ref_id: str) -> str:
    ident = sanitize_relative_path(ref_id)
    if "/" in ident:
        raise AppError("invalid", "ref_id 不能包含路径分隔。")
    return f"{PLUGIN_PREFIX}/{ident}"


def suffix_for(key: str, kind: str) -> str:
    name = (key or "").rsplit("/", 1)[-1]
    if "." in name:
        return "." + name.rsplit(".", 1)[-1].lower()
    return ".jpg" if kind == "image" else ".mp4"


def display_name_from_key(object_key: str) -> str:
    name = (object_key or "").rstrip("/").split("/")[-1]
    return name or object_key
