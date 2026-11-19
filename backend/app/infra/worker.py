from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from app.domain.errors import AppError
from app.infra.clock import now

if TYPE_CHECKING:
    from app.application.job_service import JobService
    from app.infra.config import Settings


class JobDispatcher:
    """Dual-lane in-process dispatcher. SQLite is the queue; not FastAPI BackgroundTasks."""

    def __init__(self, jobs: JobService, settings: Settings, *, work_root: Path | None = None) -> None:
        self._jobs = jobs
        self._settings = settings
        self._work_root = work_root
        self._stop = threading.Event()
        self._preprocess_thread: threading.Thread | None = None
        self._embed_thread: threading.Thread | None = None
        self.worker_id = "dispatcher-1"

    def start(self) -> None:
        self._stop.clear()
        if self._work_root is not None:
            try:
                self._jobs.cleanup_orphan_work(self._work_root)
            except Exception:
                pass
        if not (self._preprocess_thread and self._preprocess_thread.is_alive()):
            self._preprocess_thread = threading.Thread(
                target=self._loop,
                args=("preprocess-1", "preprocess"),
                name="pica-preprocess",
                daemon=True,
            )
            self._preprocess_thread.start()
        if not (self._embed_thread and self._embed_thread.is_alive()):
            self._embed_thread = threading.Thread(
                target=self._loop,
                args=("embed-1", "embed"),
                name="pica-embed",
                daemon=True,
            )
            self._embed_thread.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in (self._preprocess_thread, self._embed_thread):
            if thread:
                thread.join(timeout=2)

    def _loop(self, worker_id: str, lane: str) -> None:
        while not self._stop.is_set():
            if lane == "preprocess":
                try:
                    self._jobs.tick_schedule()
                except Exception:
                    # 调度失败不中断分发循环
                    pass
            if self._jobs.query_priority_active():
                time.sleep(self._settings.worker_poll_seconds)
                continue
            job = self._jobs.claim_next(worker_id, lane=lane)
            if job is None:
                time.sleep(self._settings.worker_poll_seconds)
                continue
            try:
                self._jobs.run_claimed(job, worker_id, lane=lane)
            except AppError as exc:
                self._jobs.fail(job.id, exc.code, exc.message)
            except Exception as exc:  # 保底：未知错误写入任务，避免卡死
                self._jobs.fail(job.id, "worker_error", f"任务执行出错：{exc}")
            # 一个安全单元结束后再检查查询优先
            _ = now()
