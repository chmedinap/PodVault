"""Tests for the in-process background job runner (`podvault.jobs`)."""

import threading
import time

from podvault.database import jobs as jobs_db
from podvault.database.connection import get_db_connection
from podvault.jobs import JobControl, JobRunner


def _wait_for_completion(runner, job_id, timeout=5):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        with get_db_connection(runner.db_path) as conn:
            job = jobs_db.get_job(conn, job_id)
        if job['status'] in ('success', 'failed'):
            return job
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish in time: {job}")


def test_job_runner_success_reports_progress(isolated_env):
    runner = JobRunner(max_workers=1)
    try:
        def target(x, progress_callback=None, job_control=None):
            if progress_callback:
                progress_callback(1, 1, 'halfway')
            return x * 2

        job_id = runner.submit('multiply', target, 21)
        job = _wait_for_completion(runner, job_id)

        assert job['status'] == 'success'
        assert job['progress'] == 1
        assert job['total'] == 1
    finally:
        runner.shutdown(wait=True)


def test_job_runner_failure_records_error(isolated_env):
    runner = JobRunner(max_workers=1)
    try:
        def target(progress_callback=None, job_control=None):
            raise ValueError('boom')

        job_id = runner.submit('boom_job', target)
        job = _wait_for_completion(runner, job_id)

        assert job['status'] == 'failed'
        assert 'boom' in job['error']
    finally:
        runner.shutdown(wait=True)


def test_job_runner_tracks_podcast_name(isolated_env):
    runner = JobRunner(max_workers=1)
    try:
        def target(progress_callback=None, job_control=None):
            return 'done'

        job_id = runner.submit('download_all', target, podcast_name='my_podcast')
        job = _wait_for_completion(runner, job_id)

        assert job['podcast_name'] == 'my_podcast'
    finally:
        runner.shutdown(wait=True)


# ---------- JobControl (Fase 22): cancel/pause/skip primitives ----------

def test_job_control_starts_uncancelled_and_unpaused():
    control = JobControl()
    assert not control.is_cancelled()
    assert not control.is_paused()


def test_job_control_cancel_is_sticky():
    control = JobControl()
    control.cancel()
    assert control.is_cancelled()


def test_job_control_pause_and_resume_round_trip():
    control = JobControl()
    control.pause()
    assert control.is_paused()
    control.resume()
    assert not control.is_paused()


def test_job_control_skip_tracks_only_the_given_episode():
    control = JobControl()
    control.skip('ep-1')
    assert control.should_skip('ep-1')
    assert not control.should_skip('ep-2')


def test_job_control_wait_while_paused_blocks_until_resumed():
    control = JobControl()
    control.pause()
    waiter_finished = threading.Event()

    def wait_then_signal():
        control.wait_while_paused(poll_interval=0.02)
        waiter_finished.set()

    thread = threading.Thread(target=wait_then_signal)
    thread.start()
    try:
        assert not waiter_finished.wait(timeout=0.2), "wait_while_paused returned before resume() was called"
        control.resume()
        assert waiter_finished.wait(timeout=1), "wait_while_paused did not return after resume()"
    finally:
        thread.join(timeout=1)


def test_job_control_wait_while_paused_returns_early_on_cancel():
    control = JobControl()
    control.pause()
    control.cancel()
    # Cancel should unblock a paused wait even without an explicit resume().
    start = time.time()
    control.wait_while_paused(poll_interval=0.02)
    assert time.time() - start < 1


# ---------- JobRunner integration: cancel/pause/resume/skip via job_id ----------

def test_job_runner_cancel_marks_job_failed_with_cancelled_message(isolated_env):
    runner = JobRunner(max_workers=1)
    try:
        release = threading.Event()

        def target(progress_callback=None, job_control=None):
            job_control.cancel()
            release.wait(timeout=2)
            return 'should not be reported as success'

        job_id = runner.submit('cancellable', target)
        assert runner.cancel(job_id)
        release.set()
        job = _wait_for_completion(runner, job_id)

        assert job['status'] == 'failed'
        assert job['error'] == 'Cancelled by user'
    finally:
        runner.shutdown(wait=True)


def test_job_runner_cancel_of_unknown_job_returns_false(isolated_env):
    runner = JobRunner(max_workers=1)
    try:
        assert runner.cancel('does-not-exist') is False
        assert runner.pause('does-not-exist') is False
        assert runner.resume('does-not-exist') is False
        assert runner.skip('does-not-exist', 'ep-1') is False
    finally:
        runner.shutdown(wait=True)


def test_job_runner_pause_resume_and_skip_reach_the_running_jobs_control(isolated_env):
    runner = JobRunner(max_workers=1)
    try:
        started = threading.Event()
        release = threading.Event()
        observed = {}

        def target(progress_callback=None, job_control=None):
            started.set()
            release.wait(timeout=2)
            observed['paused'] = job_control.is_paused()
            observed['skipped'] = job_control.should_skip('ep-1')
            return 'done'

        job_id = runner.submit('pausable', target)
        assert started.wait(timeout=2)

        assert runner.pause(job_id)
        assert runner.is_paused(job_id)
        assert runner.skip(job_id, 'ep-1')
        release.set()

        job = _wait_for_completion(runner, job_id)
        assert job['status'] == 'success'
        assert observed == {'paused': True, 'skipped': True}
    finally:
        runner.shutdown(wait=True)


def test_job_runner_get_control_is_cleared_after_completion(isolated_env):
    runner = JobRunner(max_workers=1)
    try:
        def target(progress_callback=None, job_control=None):
            return 'done'

        job_id = runner.submit('quick', target)
        _wait_for_completion(runner, job_id)

        assert runner.get_control(job_id) is None
    finally:
        runner.shutdown(wait=True)
