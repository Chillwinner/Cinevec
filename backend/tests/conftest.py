from __future__ import annotations


import os

os.environ["PICA_LOAD_DOTENV"] = "0"

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.errors import DependencyError, NotConfiguredError
from app.domain.lifecycle import ListCompleteResult
from app.domain.models import ObjectEntry, Source
from app.infra.config import Settings
from app.main import create_app


class FakeReader:
    def __init__(
        self,
        mode: str = "unconfigured",
        files: dict[str, Path] | None = None,
        listing: list[ObjectEntry] | None = None,
        complete: bool = True,
        list_error: str | None = None,
    ) -> None:
        self.mode = mode
        self.files = files or {}
        self.listing = listing
        self.complete = complete
        self.list_error = list_error
        self.download_count = 0
        self.list_complete_calls = 0

    def probe(self, source: Source, access_key, secret_key) -> None:
        if self.mode == "unconfigured":
            raise NotConfiguredError("测试：存储源未配置完整。")
        if self.mode == "fail":
            from app.domain.errors import DependencyError

            raise DependencyError("测试：连接被拒绝。")

    def list_prefix(self, source: Source, access_key, secret_key, prefix: str):
        if self.mode != "ok":
            self.probe(source, access_key, secret_key)
            return []
        keys = list(self.files.keys()) if self.files else ["a.jpg", "clip.mp4"]
        entries = [ObjectEntry(key=f"{prefix}photos/", is_dir=True)]
        for key in keys:
            entries.append(ObjectEntry(key=f"{prefix}{key}" if not key.startswith(prefix) else key, is_dir=False, size=12))
        if not self.files:
            # 保持与第一阶段测试兼容的默认条目名
            entries = [
                ObjectEntry(key=f"{prefix}photos/", is_dir=True),
                ObjectEntry(key=f"{prefix}a.jpg", is_dir=False, size=12),
                ObjectEntry(key=f"{prefix}clip.mp4", is_dir=False, size=99),
            ]
        return entries

    def download_readonly(self, source: Source, access_key, secret_key, object_key: str, dest_path: Path) -> Path:
        if self.mode != "ok":
            self.probe(source, access_key, secret_key)
        name = object_key.rsplit("/", 1)[-1]
        src = self.files.get(object_key) or self.files.get(name)
        if src is None:
            raise NotConfiguredError("测试源没有为该对象提供只读副本，不会访问用户数据或虚构连接。")
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_path)
        dest_path.chmod(0o444)
        self.download_count += 1
        return dest_path

    def _entries(self) -> list[ObjectEntry]:
        if self.listing is not None:
            return list(self.listing)
        return [
            ObjectEntry(key=key, is_dir=False, size=path.stat().st_size if hasattr(path, "stat") else 1)
            for key, path in self.files.items()
        ]

    def list_complete(self, source: Source, access_key, secret_key, prefix: str = ""):
        self.list_complete_calls += 1
        if self.mode in {"unconfigured", "fail", "authfail"}:
            if self.mode == "unconfigured":
                raise NotConfiguredError("测试：存储源未配置完整，不能当作文件已删除。")
            if self.mode == "authfail":
                raise DependencyError("测试：鉴权失败，不能当作文件已删除。")
            raise DependencyError("测试：网络或连接失败，不能当作文件已删除。")
        if self.mode == "incomplete" or not self.complete:
            return ListCompleteResult(
                entries=self._entries()[:1],
                complete=False,
                pages=1,
                truncated=True,
                error_code="list_incomplete",
                error_message=self.list_error or "测试：列举中途失败，不得判定缺失。",
            )
        entries = [e for e in self._entries() if not prefix or e.key.startswith(prefix)]
        return ListCompleteResult(entries=entries, complete=True, pages=1)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(sqlite_path=tmp_path / "test.db", worker_enabled=False, auth_enabled=False)


@pytest.fixture
def app(settings, tmp_path):
    return create_app(
        settings,
        sqlite_path=tmp_path / "test.db",
        source_reader=FakeReader("unconfigured"),
        worker_enabled=False,
        work_root=tmp_path / "work",
    )


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
