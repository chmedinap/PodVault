"""
Background scheduler thread for per-podcast auto-refresh (ROADMAP.md Fase 8).

Periodically calls `services.scheduling.run_due_checks()` so podcasts with a
configured check interval get re-synced (and optionally auto-downloaded)
without the user triggering it manually. Deliberately not wired into
FastAPI's app lifespan: `cli.serve()` starts it explicitly, so merely
importing the ASGI app (as tests do via `TestClient`) never spins up a
background thread against whatever database happens to be configured at
import time.

Not a distributed scheduler — a single daemon thread with a plain sleep
loop, same "no new infrastructure" reasoning as the JobRunner in Fase 0.
"""

import threading
from typing import Optional

from podvault import config
from podvault.logging_config import setup_logging

logger = setup_logging(__name__)

_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _loop() -> None:
    from podvault.database.backup import maybe_backup_database
    from podvault.services.scheduling import run_due_checks

    while not _stop_event.is_set():
        try:
            # Throttled to one every BACKUP_INTERVAL_HOURS, so this is a daily backup in practice
            maybe_backup_database()
        except Exception:
            logger.error("Scheduled database backup failed", exc_info=True)
        try:
            result = run_due_checks()
            if result.checked:
                logger.info(f"Scheduler pass checked {len(result.checked)} podcast(s)")
        except Exception:
            logger.error("Scheduler loop iteration failed", exc_info=True)
        _stop_event.wait(config.SCHEDULER_POLL_INTERVAL_SECONDS)


def start_scheduler() -> None:
    """Start the background scheduler thread, if it isn't already running."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name='podcast-scheduler', daemon=True)
    _thread.start()
    logger.info(f"Background scheduler started (polling every {config.SCHEDULER_POLL_INTERVAL_SECONDS}s)")


def stop_scheduler() -> None:
    """Signal the scheduler thread to stop. Used by tests; not needed for normal shutdown (it's a daemon thread)."""
    global _thread
    _stop_event.set()
    _thread = None


def is_running() -> bool:
    """Whether the background scheduler thread is alive (started by `cli.serve()`, not under `TestClient`)."""
    return _thread is not None and _thread.is_alive()
