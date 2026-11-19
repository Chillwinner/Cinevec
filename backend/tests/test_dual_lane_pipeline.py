from __future__ import annotations

import json
import threading
import time
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from app.infra.config import Settings
from app.infra.file_store import LocalFileStore
from app.main import create_app
from tests.conftest import FakeReader
from tests.fakes import MemoryEncoder, MemoryVectorIndex
from tests.media_fixtures import write_png_pattern


class BlockingFirstEncoder(MemoryEncoder):
    def __init__(self, dim: int = 8) -> None:
        super().__init__(dim=dim)
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self._lock = threading.Lock()
        self._image_calls = 0

    def encode_image(self, image_bytes: bytes) -> list[float]:
        with self._lock:
            self._image_calls += 1
            call_num = self._image_calls
        if call_num == 1:
            self.first_started.set()
            self.release_first.wait(timeout=10)
        return super().encode_image(image_bytes)


class FailOnceEncoder(MemoryEncoder):
    def __init__(self, dim: int = 8) -> None:
        super().__init__(dim=dim)
        self._failed = False

    def encode_image(self, image_bytes: bytes) -> list[float]:
        if not self._failed:
            self._failed = True
            raise RuntimeError("simulated embed failure")
        return super().encode_image(image_bytes)


def _setup_platform(client: TestClient) -> dict[str, str]:
    client.post(
        "/api/sources",
        json={
            "name": "platform",
            "endpoint": "http://x",
            "bucket": "pica-dev",
            "role": "storage",
            "is_platform_storage": True,
            "access_key": "k",
            "secret_key": "s",
        },
    )
    tok = client.post("/api/settings/api-tokens", json={"name": "dual-lane"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_dual_lane_preprocess_runs_and_deletes_originals_while_embed_blocks(tmp_path: Path):
    encoder = BlockingFirstEncoder(dim=8)
    index = MemoryVectorIndex(dim=8)
    settings = Settings(sqlite_path=tmp_path / "db.sqlite", worker_enabled=True, worker_poll_seconds=0.05)
    work_root = tmp_path / "work"
    app = create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=FakeReader("ok"),
        worker_enabled=True,
        file_store=LocalFileStore(tmp_path / "store", label="pytest"),
        work_root=work_root,
        encoder=encoder,
        subtitle_encoder=encoder,
        index=index,
    )
    img_bytes = write_png_pattern(None, "checker")

    with TestClient(app) as client:
        headers = _setup_platform(client)
        r1 = client.post(
            "/api/v1/imports",
            headers=headers,
            data={
                "meta": json.dumps(
                    [
                        {
                            "relative_path": "first.png",
                            "copy": False,
                            "vectorize": True,
                            "source_url": "https://t.me/ch/1",
                            "ref": {"ref_id": "tg_1_1", "locator": "https://t.me/ch/1"},
                        }
                    ]
                )
            },
            files=[("files", ("first.png", BytesIO(img_bytes), "image/png"))],
        )
        assert r1.status_code == 201, r1.text
        job1_id = r1.json()["jobs"][0]["id"]

        assert encoder.first_started.wait(timeout=5), "embed-1 never picked up job 1"

        r2 = client.post(
            "/api/v1/imports",
            headers=headers,
            data={
                "meta": json.dumps(
                    [
                        {
                            "relative_path": "second.png",
                            "copy": False,
                            "vectorize": True,
                            "source_url": "https://t.me/ch/2",
                            "ref": {"ref_id": "tg_1_2", "locator": "https://t.me/ch/2"},
                        }
                    ]
                )
            },
            files=[("files", ("second.png", BytesIO(img_bytes), "image/png"))],
        )
        assert r2.status_code == 201, r2.text
        job2_id = r2.json()["jobs"][0]["id"]
        job2_raw = work_root / job2_id / "original.png"

        # While job 1 is blocked inside encode_image, preprocess-1 must finish job 2 preprocess,
        # unlink job 2's raw original file immediately, and put job 2 into embed_pending.
        deadline = time.monotonic() + 5.0
        job2_ready_for_embed = False
        while time.monotonic() < deadline:
            j2 = client.get(f"/api/jobs/{job2_id}").json()
            if j2["stage"] == "embed_pending" and j2["status"] == "waiting" and not job2_raw.exists():
                job2_ready_for_embed = True
                break
            time.sleep(0.05)

        assert job2_ready_for_embed, f"job2 stalled behind blocked embed: {client.get(f'/api/jobs/{job2_id}').json()}"
        assert client.get(f"/api/jobs/{job1_id}").json()["status"] == "running"

        encoder.release_first.set()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            j1 = client.get(f"/api/jobs/{job1_id}").json()
            j2 = client.get(f"/api/jobs/{job2_id}").json()
            if j1["status"] == "completed" and j2["status"] == "completed":
                break
            time.sleep(0.05)

        assert client.get(f"/api/jobs/{job1_id}").json()["status"] == "completed"
        assert client.get(f"/api/jobs/{job2_id}").json()["status"] == "completed"
        assert not (work_root / job1_id).exists()
        assert not (work_root / job2_id).exists()


def test_orphan_work_cleanup_cancel_and_embed_retry_preservation(tmp_path: Path):
    encoder = FailOnceEncoder(dim=8)
    index = MemoryVectorIndex(dim=8)
    settings = Settings(sqlite_path=tmp_path / "db.sqlite", worker_enabled=False)
    work_root = tmp_path / "work"
    app = create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=FakeReader("ok"),
        worker_enabled=False,
        file_store=LocalFileStore(tmp_path / "store", label="pytest"),
        work_root=work_root,
        encoder=encoder,
        subtitle_encoder=encoder,
        index=index,
    )
    img_bytes = write_png_pattern(None, "checker")

    with TestClient(app) as client:
        headers = _setup_platform(client)
        jobs = client.app.state.container.jobs

        # 1) Embed failure preserves encode_units and retries directly in embed_pending
        r1 = client.post(
            "/api/v1/imports",
            headers=headers,
            data={
                "meta": json.dumps(
                    [
                        {
                            "relative_path": "retry_me.png",
                            "copy": False,
                            "vectorize": True,
                            "source_url": "https://t.me/ch/10",
                            "ref": {"ref_id": "tg_1_10", "locator": "https://t.me/ch/10"},
                        }
                    ]
                )
            },
            files=[("files", ("retry_me.png", BytesIO(img_bytes), "image/png"))],
        )
        assert r1.status_code == 201
        job1_id = r1.json()["jobs"][0]["id"]

        claimed1 = jobs.claim_next("preprocess-1", lane="preprocess")
        assert claimed1 is not None and claimed1.id == job1_id
        jobs.run_claimed(claimed1, "preprocess-1", lane="preprocess")
        assert jobs.get(job1_id).stage == "embed_pending"
        assert not (work_root / job1_id / "original.png").exists()

        claimed_e1 = jobs.claim_next("embed-1", lane="embed")
        assert claimed_e1 is not None and claimed_e1.id == job1_id
        jobs.run_claimed(claimed_e1, "embed-1", lane="embed")
        failed = jobs.get(job1_id)
        assert failed.status == "failed"
        assert (work_root / job1_id).is_dir()

        retried = jobs.retry(job1_id)
        assert retried.status == "waiting"
        assert retried.stage == "embed_pending"

        # 2) Cancel removes job work dir immediately
        r2 = client.post(
            "/api/v1/imports",
            headers=headers,
            data={
                "meta": json.dumps(
                    [
                        {
                            "relative_path": "cancel_me.png",
                            "copy": False,
                            "vectorize": True,
                            "source_url": "https://t.me/ch/11",
                            "ref": {"ref_id": "tg_1_11", "locator": "https://t.me/ch/11"},
                        }
                    ]
                )
            },
            files=[("files", ("cancel_me.png", BytesIO(img_bytes), "image/png"))],
        )
        assert r2.status_code == 201
        job2_id = r2.json()["jobs"][0]["id"]
        assert (work_root / job2_id).is_dir()
        jobs.cancel(job2_id)
        assert not (work_root / job2_id).exists()

        # 3) cleanup_orphan_work cleans stale folders but keeps retryable job1 and _incoming
        stale_dir = work_root / "stale-job-999"
        stale_dir.mkdir(parents=True, exist_ok=True)
        (stale_dir / "junk.mp4").write_bytes(b"123")
        incoming_dir = work_root / "_incoming"
        incoming_dir.mkdir(parents=True, exist_ok=True)

        removed = jobs.cleanup_orphan_work(work_root)
        assert removed == 1
        assert not stale_dir.exists()
        assert incoming_dir.is_dir()
        assert (work_root / job1_id).is_dir()

        claimed_e2 = jobs.claim_next("embed-1", lane="embed")
        assert claimed_e2 is not None and claimed_e2.id == job1_id
        jobs.run_claimed(claimed_e2, "embed-1", lane="embed")
        assert jobs.get(job1_id).status == "completed"
        assert not (work_root / job1_id).exists()
