from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Any


def safe_rmtree(folder: Path) -> None:
    def _on_error(func, path, _exc_info):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            func(path)
        except OSError:
            pass

    if folder.exists():
        shutil.rmtree(folder, onerror=_on_error)


def safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            try:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
            path.unlink(missing_ok=True)
    except OSError:
        pass


def remember_encode_unit(checkpoint: dict[str, Any], unit_index: int, local_path: str | Path) -> None:
    units = [item for item in (checkpoint.get("encode_units") or []) if int(item.get("unit_index", -1)) != int(unit_index)]
    units.append({"unit_index": int(unit_index), "local_path": str(local_path)})
    checkpoint["encode_units"] = units
    checkpoint["keep_work"] = True


def local_path_for(checkpoint: dict[str, Any] | None, unit_index: int) -> str | None:
    if not checkpoint:
        return None
    for item in checkpoint.get("encode_units") or []:
        if int(item.get("unit_index", -1)) == int(unit_index):
            path = item.get("local_path")
            return str(path) if path else None
    return None


def cleanup_encode_work(checkpoint: dict[str, Any] | None) -> None:
    if not checkpoint:
        return
    folders: set[Path] = set()
    work = checkpoint.get("work_path")
    if work:
        safe_unlink(Path(work))
        folders.add(Path(work).parent)
    for item in checkpoint.get("encode_units") or []:
        path = item.get("local_path")
        if path:
            p = Path(path)
            if p.parent.name == "encode":
                folders.add(p.parent)
                folders.add(p.parent.parent)
            else:
                folders.add(p.parent)
    for folder in sorted(folders, key=lambda item: len(str(item)), reverse=True):
        safe_rmtree(folder)
