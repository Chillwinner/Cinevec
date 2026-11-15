from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from app.domain.errors import AppError
from app.infra.clock import now

if TYPE_CHECKING:
    from app.application.job_service import JobService
    from app.infra.config import Settings


class JobDispatcher:
    """Single in-process dispatcher. SQLite is the queue; not FastAPI BackgroundTasks."""

    def __init__(self, jobs: JobService, settings: Settings) -> None:
        self._jobs = jobs
        self._settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.worker_id = "dispatcher-1"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pica-dispatcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._jobs.tick_schedule()
            except Exception:
                # 调度失败不中断分发循环
                pass
            if self._jobs.query_priority_active():
                time.sleep(self._settings.worker_poll_seconds)
                continue
            job = self._jobs.claim_next(self.worker_id)
            if job is None:
                time.sleep(self._settings.worker_poll_seconds)
                continue
            try:
                self._jobs.run_claimed(job, self.worker_id)
            except AppError as exc:
                self._jobs.fail(job.id, exc.code, exc.message)
            except Exception as exc:  # 保底：未知错误写入任务，避免卡死
                self._jobs.fail(job.id, "worker_error", f"任务执行出错：{exc}")
            # 一个安全单元结束后再检查查询优先
            _ = now()
