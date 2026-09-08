"""
In-process background job runner.

Runs long operations (feed processing, downloads) on a worker thread so a
caller (a future web request handler) doesn't block waiting for them, and
records progress/status in the `jobs` table so it can be polled afterwards.

Not a distributed task queue: jobs run in this same process and are lost if
the process restarts mid-job. That's an intentional trade-off for a
single-user, single-process app — see ROADMAP.md Fase 0.
"""

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional, Set

from podvault import config
from podvault.database.connection import get_db_connection
from podvault.database import jobs as jobs_db
from podvault.logging_config import setup_logging

logger = setup_logging(__name__)


class JobControl:
    """
    Cooperative cancel/pause/skip signalling for one running job (Fase 22).

    Deliberately coarse-grained: a download job checks this *between*
    episodes (see `download_all_episodes`'s loop), not mid-file-transfer —
    "Finished episodes are kept" in the UI copy is true because a cancel or
    pause never interrupts a download already in flight, only the next one
    from starting. That keeps this a plain in-memory flag/set instead of
    needing to teach the byte-streaming download itself how to abort safely.
    """

    def __init__(self):
        self._cancelled = threading.Event()
        self._paused = threading.Event()
        self._skip_ids: Set[str] = set()
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def skip(self, episode_id: str) -> None:
        with self._lock:
            self._skip_ids.add(episode_id)

    def should_skip(self, episode_id: str) -> bool:
        with self._lock:
            return episode_id in self._skip_ids

    def wait_while_paused(self, poll_interval: float = 0.5) -> None:
        """Block the calling (worker) thread while paused, waking early on cancel."""
        while self.is_paused() and not self.is_cancelled():
            time.sleep(poll_interval)


class JobRunner:
    """Executes callables on a bounded thread pool and tracks them in the `jobs` table."""

    def __init__(self, db_path: Optional[str] = None, max_workers: int = 2):
        self.db_path = db_path or config.DB_PATH
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='job')
        self._controls: dict = {}
        self._controls_lock = threading.Lock()

    def submit(
        self,
        job_type: str,
        target: Callable[..., Any],
        *args: Any,
        podcast_name: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """
        Schedule `target(*args, progress_callback=..., job_control=..., **kwargs)`
        to run in the background.

        `target` receives a `progress_callback(current, total, message=None)`
        keyword argument it may call to report progress, and a `job_control`
        (`JobControl`) keyword argument it may check to cooperatively cancel,
        pause, or skip individual items; callers that don't need either can
        ignore them (every job target accepts both for consistency, even if
        some — `add_feed`, the ID3/integrity checks — don't act on `job_control`).

        Returns the job id immediately; poll `/jobs/{id}/status` or
        `podvault.database.jobs.get_job()` for progress.
        """
        job_id = str(uuid.uuid4())
        control = JobControl()
        with self._controls_lock:
            self._controls[job_id] = control

        with get_db_connection(self.db_path) as conn:
            jobs_db.create_job(conn, job_id, job_type, podcast_name=podcast_name)

        self._executor.submit(self._run, job_id, target, args, kwargs, control)
        return job_id

    def _run(self, job_id: str, target: Callable[..., Any], args: tuple, kwargs: dict, control: JobControl) -> None:
        with get_db_connection(self.db_path) as conn:
            jobs_db.mark_job_running(conn, job_id)

        def progress_callback(current: int, total: int, message: Optional[str] = None) -> None:
            with get_db_connection(self.db_path) as conn:
                jobs_db.update_job_progress(conn, job_id, current, total, message)

        try:
            result = target(*args, progress_callback=progress_callback, job_control=control, **kwargs)
            with get_db_connection(self.db_path) as conn:
                if control.is_cancelled():
                    jobs_db.mark_job_failed(conn, job_id, "Cancelled by user")
                else:
                    jobs_db.mark_job_success(conn, job_id, message=str(result) if result is not None else None)
        except Exception as e:
            logger.error(f"Job {job_id} ({target}) failed: {e}")
            logger.debug(traceback.format_exc())
            with get_db_connection(self.db_path) as conn:
                jobs_db.mark_job_failed(conn, job_id, str(e))
        finally:
            with self._controls_lock:
                self._controls.pop(job_id, None)

    def get_control(self, job_id: str) -> Optional[JobControl]:
        with self._controls_lock:
            return self._controls.get(job_id)

    def cancel(self, job_id: str) -> bool:
        control = self.get_control(job_id)
        if control is None:
            return False
        control.cancel()
        return True

    def pause(self, job_id: str) -> bool:
        control = self.get_control(job_id)
        if control is None:
            return False
        control.pause()
        return True

    def resume(self, job_id: str) -> bool:
        control = self.get_control(job_id)
        if control is None:
            return False
        control.resume()
        return True

    def skip(self, job_id: str, episode_id: str) -> bool:
        control = self.get_control(job_id)
        if control is None:
            return False
        control.skip(episode_id)
        return True

    def is_paused(self, job_id: str) -> bool:
        control = self.get_control(job_id)
        return control.is_paused() if control else False

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)


_default_runner: Optional[JobRunner] = None


def get_job_runner() -> JobRunner:
    """Return the process-wide JobRunner, creating it on first use."""
    global _default_runner
    if _default_runner is None:
        _default_runner = JobRunner()
    return _default_runner
