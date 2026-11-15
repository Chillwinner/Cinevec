from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.errors import NotConfiguredError
from app.domain.models import Source
from app.infra.config import Settings
from app.infra.file_store import PlatformResolvingFileStore
from app.main import create_app
from tests.conftest import FakeReader


class _MemMetadata:
    def __init__(self) -> None:
        self.source = None
        self.creds = (None, None)

    def get_platform_storage(self):
        return self.source

    def get_source_credentials(self, source_id: str):
        return self.creds


def test_resolving_store_picks_up_platform_source_without_rebuild():
    meta = _MemMetadata()
    store = PlatformResolvingFileStore(meta)
    assert store.available() is False
    assert "未接通" in store.status_message()
    with pytest.raises(NotConfiguredError, match="未接通"):
        store.put_bytes("previews/x.jpg", b"abc", "image/jpeg")

    now = datetime.now()
    meta.source = Source(
        id="stor-1",
        name="pica-dev-storage",
        endpoint="http://127.0.0.1:9000",
        bucket="pica-dev",
        region="us-east-1",
        role="storage",
        is_platform_storage=True,
        access_key_configured=True,
        secret_configured=True,
        last_probe_status="ok",
        last_probe_message="连接成功",
        created_at=now,
        updated_at=now,
    )
    meta.creds = ("ak-test", "sk-test")
    assert store.available() is True
    assert "pica-dev" in store.status_message()
    assert "凭证不回显" in store.status_message()


def test_retry_failed_index_job_without_reimport(tmp_path: Path):
    settings = Settings(sqlite_path=tmp_path / "db.sqlite", worker_enabled=False)
    app = create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=FakeReader("ok"),
        worker_enabled=False,
    )
    with TestClient(app) as client:
        source_id = client.post(
            "/api/sources",
            json={
                "name": "ok-source",
                "endpoint": "http://127.0.0.1:9000",
                "bucket": "media",
                "access_key": "k",
                "secret_key": "s",
            },
        ).json()["id"]
        imported = client.post(
            "/api/imports",
            json={"source_id": source_id, "items": [{"object_key": "clip.mp4", "vectorize": True}]},
        ).json()
        job_id = imported["jobs"][0]["id"]
        # 人工标失败后应能重试回到 waiting，不新增资产
        jobs = client.app.state.container.jobs
        jobs.fail(job_id, "filestore_unconfigured", "测试用失败")
        retried = client.post(f"/api/jobs/{job_id}/retry")
        assert retried.status_code == 200
        body = retried.json()
        assert body["id"] == job_id
        assert body["status"] == "waiting"
        assert body["error_code"] is None
        assets = client.get("/api/assets").json()["items"]
        assert len(assets) == 1
        # 非失败任务不能重试
        denied = client.post(f"/api/jobs/{job_id}/retry")
        assert denied.status_code == 400
        assert denied.json()["error"]["code"] == "invalid_state"
