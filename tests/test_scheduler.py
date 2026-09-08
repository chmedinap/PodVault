"""Tests for the background scheduler thread (`podvault.scheduler`)."""

import time

from podvault import scheduler


def test_start_scheduler_starts_a_daemon_thread(isolated_env, monkeypatch):
    monkeypatch.setattr(scheduler.config, 'SCHEDULER_POLL_INTERVAL_SECONDS', 3600)
    try:
        scheduler.start_scheduler()
        assert scheduler._thread is not None
        assert scheduler._thread.is_alive()
        assert scheduler._thread.daemon
    finally:
        scheduler.stop_scheduler()


def test_start_scheduler_is_idempotent(isolated_env, monkeypatch):
    monkeypatch.setattr(scheduler.config, 'SCHEDULER_POLL_INTERVAL_SECONDS', 3600)
    try:
        scheduler.start_scheduler()
        first_thread = scheduler._thread
        scheduler.start_scheduler()
        assert scheduler._thread is first_thread
    finally:
        scheduler.stop_scheduler()


def test_stop_scheduler_stops_the_thread(isolated_env, monkeypatch):
    monkeypatch.setattr(scheduler.config, 'SCHEDULER_POLL_INTERVAL_SECONDS', 0.05)
    scheduler.start_scheduler()
    thread = scheduler._thread
    scheduler.stop_scheduler()
    thread.join(timeout=2)
    assert not thread.is_alive()
