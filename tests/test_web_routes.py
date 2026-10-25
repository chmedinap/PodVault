"""
Smoke tests for the web interface via FastAPI's TestClient. These cover
the routes ROADMAP.md Fase 5 calls out as highest-value: adding a feed
and downloading go through the JobRunner (background thread), so a
regression that makes them block would hang these tests instead of the
whole app in production.
"""

import threading
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from podvault import services
from podvault.database import jobs as jobs_db
from podvault.database.connection import get_db_connection
from podvault.jobs import get_job_runner
from podvault.web.app import app


def _job_id_from_redirect(location: str) -> str:
    """Job-submitting routes redirect with `?...&job_id=<id>` (Fase 22), not a `/jobs/{id}` path."""
    return parse_qs(urlparse(location).query)['job_id'][0]


def _poll_job(client, job_id, timeout=5):
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        status = client.get(f'/jobs/{job_id}/status').json()
        if status['status'] in ('success', 'failed'):
            return status
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish in time: {status}")


def test_dashboard_with_no_podcasts(isolated_env):
    client = TestClient(app)
    resp = client.get('/')
    assert resp.status_code == 200
    assert 'PodVault' in resp.text


def test_dashboard_lists_podcast(isolated_env, feed_df_factory):
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app)
    resp = client.get('/')
    assert resp.status_code == 200
    assert 'test_podcast' in resp.text


def test_podcast_detail_pagination(isolated_env, feed_df_factory):
    episodes = [
        {'id': f'ep-{i}', 'title': f'Episode {i}', 'published': f'2024-01-{(i % 28) + 1:02d}'}
        for i in range(1, 61)
    ]
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory(episodes=episodes)):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast')
    assert resp.status_code == 200
    assert 'Page 1 of 2' in resp.text


def test_podcast_detail_unknown_podcast_redirects_to_dashboard(isolated_env):
    client = TestClient(app, follow_redirects=False)
    resp = client.get('/podcasts/does_not_exist')
    assert resp.status_code == 303
    assert resp.headers['location'].startswith('/?msg=')


def test_add_podcast_runs_as_background_job(isolated_env, feed_df_factory):
    client = TestClient(app, follow_redirects=False)
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        resp = client.post('/podcasts', data={'feed_url': 'https://example.com/test_podcast.xml'})

    assert resp.status_code == 303
    assert 'job_id=' in resp.headers['location']
    job_id = _job_id_from_redirect(resp.headers['location'])

    status = _poll_job(client, job_id)
    assert status['status'] == 'success'


def test_add_podcast_rejects_blank_url(isolated_env):
    client = TestClient(app, follow_redirects=False)
    resp = client.post('/podcasts', data={'feed_url': '   '})
    assert resp.status_code == 303
    assert resp.headers['location'].startswith('/?')
    assert 'ok=0' in resp.headers['location']


def test_download_last_runs_as_background_job(isolated_env, feed_df_factory, mock_download):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app, follow_redirects=False)
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        resp = client.post('/podcasts/test_podcast/download-last')
        assert resp.status_code == 303
        job_id = _job_id_from_redirect(resp.headers['location'])
        status = _poll_job(client, job_id)

    assert status['status'] == 'success'
    assert status['podcast_name'] == 'test_podcast'


def test_job_status_unknown_job_is_404(isolated_env):
    client = TestClient(app)
    resp = client.get('/jobs/does-not-exist/status')
    assert resp.status_code == 404


def test_rename_episode_route(isolated_env, feed_df_factory):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Original', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app, follow_redirects=False)
    resp = client.post('/podcasts/test_podcast/episodes/ep-1/rename', data={'custom_title': 'New Name'})
    assert resp.status_code == 303
    assert 'ok=1' in resp.headers['location']

    episode = services.list_episodes('test_podcast').episodes[0]
    assert episode.custom_title == 'New Name'


def test_verify_route(isolated_env, feed_df_factory):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Original', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app, follow_redirects=False)
    resp = client.post('/podcasts/test_podcast/verify')
    assert resp.status_code == 303
    assert 'ok=1' in resp.headers['location']


def test_settings_page_shows_current_templates(isolated_env):
    client = TestClient(app)
    resp = client.get('/settings')
    assert resp.status_code == 200
    from podvault import config
    assert config.DEFAULT_FOLDER_TEMPLATE in resp.text


def test_settings_update_route(isolated_env):
    client = TestClient(app, follow_redirects=False)
    resp = client.post('/settings', data={
        'folder_template': '{podcast_name}/{year}',
        'filename_template': '{episode_title}',
    })
    assert resp.status_code == 303
    assert 'ok=1' in resp.headers['location']

    settings = services.get_settings()
    assert settings.folder_template == '{podcast_name}/{year}'


def test_settings_update_route_rejects_bad_template(isolated_env):
    client = TestClient(app, follow_redirects=False)
    resp = client.post('/settings', data={
        'folder_template': '{not_a_real_variable}',
        'filename_template': '{episode_title}',
    })
    assert resp.status_code == 303
    assert 'ok=0' in resp.headers['location']


def test_settings_preview_route(isolated_env):
    client = TestClient(app)
    resp = client.post('/settings/preview', data={
        'folder_template': '{podcast_name}/{year}',
        'filename_template': '{episode_title}',
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data['error'] is None
    assert data['folder_path'] == 'sample_podcast/2024'


def test_podcast_detail_shows_schedule_section(isolated_env, feed_df_factory):
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast')
    assert resp.status_code == 200
    assert 'Auto-refresh' in resp.text
    assert 'never' in resp.text.lower()


def test_schedule_update_route(isolated_env, feed_df_factory):
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app, follow_redirects=False)
    resp = client.post('/podcasts/test_podcast/schedule', data={
        'interval_hours': '12',
        'auto_download': 'true',
    })
    assert resp.status_code == 303
    assert 'ok=1' in resp.headers['location']

    schedule = services.get_podcast_schedule('test_podcast')
    assert schedule.interval_hours == 12
    assert schedule.auto_download is True


def test_schedule_update_route_disable(isolated_env, feed_df_factory):
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.add_feed('https://example.com/test_podcast.xml')
    services.set_podcast_schedule('test_podcast', interval_hours=12, auto_download=True)

    client = TestClient(app, follow_redirects=False)
    resp = client.post('/podcasts/test_podcast/schedule', data={'interval_hours': '0'})
    assert resp.status_code == 303

    schedule = services.get_podcast_schedule('test_podcast')
    assert schedule.interval_hours is None


def _add_and_download_one(feed_df_factory, mock_download):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)


def test_episode_audio_route_serves_downloaded_file(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)

    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast/episodes/ep-1/audio')
    assert resp.status_code == 200
    assert resp.content == b'fake mp3 content'
    assert resp.headers['content-type'] == 'audio/mpeg'


def test_episode_audio_route_supports_range_requests(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)

    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast/episodes/ep-1/audio', headers={'Range': 'bytes=0-3'})
    assert resp.status_code == 206
    assert resp.content == b'fake'
    assert resp.headers['content-range'].startswith('bytes 0-3/')


def test_episode_audio_route_404_for_not_downloaded_episode(isolated_env, feed_df_factory):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast/episodes/ep-1/audio')
    assert resp.status_code == 404


def test_episode_cover_route_404_when_no_image(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)

    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast/episodes/ep-1/cover')
    assert resp.status_code == 404


def test_episode_cover_route_serves_podcast_image(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)

    episode = services.list_episodes('test_podcast').episodes[0]
    podcast_folder = Path(episode.saved_path).parent.parent.parent.parent
    (podcast_folder / 'podcast_image.png').write_bytes(b'fake png bytes')

    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast/episodes/ep-1/cover')
    assert resp.status_code == 200
    assert resp.content == b'fake png bytes'


def test_dashboard_filter_missing_files(isolated_env, feed_df_factory, mock_download):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)
    episode = services.list_episodes('test_podcast').episodes[0]
    Path(episode.saved_path).unlink()
    services.verify_files('test_podcast')

    client = TestClient(app)
    resp = client.get('/?filter=Missing files')
    assert resp.status_code == 200
    assert 'test_podcast' in resp.text

    resp = client.get('/?filter=Incomplete')
    assert resp.status_code == 200
    assert 'test_podcast' not in resp.text.split('id="add-podcast"')[1]


def test_podcast_detail_episode_filter(isolated_env, feed_df_factory, mock_download):
    df = feed_df_factory(episodes=[
        {'id': 'ep-1', 'title': 'Pending Episode', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Downloaded Episode', 'published': '2024-01-02'},
    ])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_last('test_podcast')

    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast?ep_filter=Downloaded')
    assert resp.status_code == 200
    assert 'Downloaded Episode' in resp.text
    assert 'Pending Episode' not in resp.text


def test_episode_detail_page(isolated_env, feed_df_factory, mock_download):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)

    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast/episodes/ep-1')
    assert resp.status_code == 200
    assert 'Episode One' in resp.text


def test_episode_detail_page_unknown_episode_redirects(isolated_env, feed_df_factory):
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app, follow_redirects=False)
    resp = client.get('/podcasts/test_podcast/episodes/does-not-exist')
    assert resp.status_code == 303
    assert resp.headers['location'].startswith('/podcasts/test_podcast?')


def test_rename_episode_with_return_to(isolated_env, feed_df_factory):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Original', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app, follow_redirects=False)
    resp = client.post(
        '/podcasts/test_podcast/episodes/ep-1/rename',
        data={'custom_title': 'New Name', 'return_to': '/podcasts/test_podcast/episodes/ep-1'},
    )
    assert resp.status_code == 303
    assert resp.headers['location'].startswith('/podcasts/test_podcast/episodes/ep-1?')


def test_rename_episode_rejects_unsafe_return_to(isolated_env, feed_df_factory):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Original', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app, follow_redirects=False)
    resp = client.post(
        '/podcasts/test_podcast/episodes/ep-1/rename',
        data={'custom_title': 'New Name', 'return_to': '//evil.example.com'},
    )
    assert resp.status_code == 303
    assert resp.headers['location'].startswith('/podcasts/test_podcast?')


def test_storage_page_empty(isolated_env):
    client = TestClient(app)
    resp = client.get('/storage')
    assert resp.status_code == 200
    assert 'never checked' in resp.text


def test_storage_page_shows_largest_podcasts(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)
    client = TestClient(app)
    resp = client.get('/storage')
    assert resp.status_code == 200
    assert 'test_podcast' in resp.text


def test_storage_id3_check_runs_as_background_job(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)
    client = TestClient(app, follow_redirects=False)
    resp = client.post('/storage/id3-check')
    assert resp.status_code == 303
    job_id = _job_id_from_redirect(resp.headers['location'])
    status = _poll_job(client, job_id)
    assert status['status'] == 'success'
    assert 'complete' in status['message']

    resp = client.get('/storage')
    assert 'of 1 complete' in resp.text


def test_storage_integrity_check_runs_as_background_job(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)
    client = TestClient(app, follow_redirects=False)
    resp = client.post('/storage/integrity-check')
    assert resp.status_code == 303
    job_id = _job_id_from_redirect(resp.headers['location'])
    status = _poll_job(client, job_id)
    assert status['status'] == 'success'
    assert 'verified' in status['message']

    resp = client.get('/storage')
    assert 'Integrity (checksums)' in resp.text


def test_storage_lists_corrupt_episodes_by_name(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)
    with get_db_connection() as conn:
        conn.execute("UPDATE test_podcast SET checksum_status = 'corrupt' WHERE id = 'ep-1'")
        conn.commit()

    client = TestClient(app)
    resp = client.get('/storage')
    assert resp.status_code == 200
    assert 'test_podcast/ep-1' in resp.text
    assert 'Re-download all 1' in resp.text


def test_storage_lists_local_only_episodes_for_review(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)
    with get_db_connection() as conn:
        conn.execute("UPDATE test_podcast SET in_feed = 0 WHERE id = 'ep-1'")
        conn.commit()

    client = TestClient(app)
    resp = client.get('/storage')
    assert resp.status_code == 200
    assert '1 episode(s) kept after leaving their feed' in resp.text
    assert '/podcasts/test_podcast/episodes/ep-1' in resp.text


def test_storage_redownload_corrupt_submits_download_all_per_podcast(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)
    with get_db_connection() as conn:
        conn.execute("UPDATE test_podcast SET checksum_status = 'corrupt' WHERE id = 'ep-1'")
        conn.commit()

    client = TestClient(app, follow_redirects=False)
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        resp = client.post('/storage/redownload-corrupt')
        assert resp.status_code == 303
        job_id = _job_id_from_redirect(resp.headers['location'])
        status = _poll_job(client, job_id)

    assert status['status'] == 'success'
    assert status['job_type'] == 'download_all'


def test_storage_redownload_corrupt_with_nothing_corrupt(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)
    client = TestClient(app, follow_redirects=False)
    resp = client.post('/storage/redownload-corrupt')
    assert resp.status_code == 303
    assert 'ok=0' in resp.headers['location']


def test_dashboard_shows_archive_health_stat_card(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)
    with get_db_connection() as conn:
        conn.execute("UPDATE test_podcast SET checksum_status = 'corrupt' WHERE id = 'ep-1'")
        conn.commit()

    client = TestClient(app)
    resp = client.get('/')
    assert resp.status_code == 200
    assert 'Archive health' in resp.text
    assert '1 corrupt' in resp.text


def test_episode_detail_shows_integrity_and_feed_status(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)
    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast/episodes/ep-1')
    assert resp.status_code == 200
    assert 'verified' in resp.text
    assert 'in feed' in resp.text


def test_export_podcast_route_returns_manifest(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)
    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast/export')
    assert resp.status_code == 200
    assert resp.headers['content-type'] == 'application/json'
    assert 'attachment' in resp.headers['content-disposition']
    data = resp.json()
    assert data['podcast_name'] == 'test_podcast'
    assert data['episode_count'] == 1


def test_export_podcast_route_unknown_podcast_redirects(isolated_env):
    client = TestClient(app, follow_redirects=False)
    resp = client.get('/podcasts/does_not_exist/export')
    assert resp.status_code == 303
    assert resp.headers['location'].startswith('/storage?')


def test_export_library_route_returns_manifest(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)
    client = TestClient(app)
    resp = client.get('/export')
    assert resp.status_code == 200
    data = resp.json()
    assert data['podcast_count'] == 1


def test_podcast_detail_shows_local_only_badge(isolated_env, feed_df_factory):
    df = feed_df_factory(episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-02'},
    ])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    shrunk_df = feed_df_factory(episodes=[{'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-02'}])
    with patch('podvault.services.feeds.collect_data', return_value=shrunk_df):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast')
    assert resp.status_code == 200
    assert 'local only' in resp.text


def test_queue_page_empty(isolated_env):
    client = TestClient(app)
    resp = client.get('/queue')
    assert resp.status_code == 200
    assert 'No downloads in progress' in resp.text


def test_queue_page_shows_running_download_job(isolated_env, feed_df_factory):
    episodes = [
        {'id': f'ep-{i}', 'title': f'Episode {i}', 'published': f'2024-01-{i:02d}'}
        for i in range(1, 4)
    ]
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory(episodes=episodes)):
        services.add_feed('https://example.com/test_podcast.xml')

    with get_db_connection() as conn:
        jobs_db.create_job(conn, 'job-1', 'download_all', podcast_name='test_podcast')
        jobs_db.mark_job_running(conn, 'job-1')
        jobs_db.update_job_progress(conn, 'job-1', 1, 3, 'Episode 1')

    client = TestClient(app)
    resp = client.get('/queue')
    assert resp.status_code == 200
    assert 'test_podcast' in resp.text
    assert 'job-1'[:8] in resp.text


def test_jobs_active_status_is_null_with_nothing_running(isolated_env):
    client = TestClient(app)
    resp = client.get('/jobs/active/status')
    assert resp.status_code == 200
    assert resp.json() is None


def test_jobs_active_status_returns_most_recent_running_job(isolated_env):
    with get_db_connection() as conn:
        jobs_db.create_job(conn, 'job-1', 'download_all')
        jobs_db.mark_job_running(conn, 'job-1')

    client = TestClient(app)
    resp = client.get('/jobs/active/status')
    assert resp.status_code == 200
    assert resp.json()['id'] == 'job-1'


def _submit_blocking_job(job_type='download_all', podcast_name=None):
    """Submit a real job that blocks until released, so its JobControl stays
    registered in the runner's in-memory `_controls` map — cancel/pause/resume/
    skip only find a control for jobs still tracked there (Fase 22)."""
    started = threading.Event()
    release = threading.Event()

    def target(progress_callback=None, job_control=None):
        started.set()
        release.wait(timeout=2)
        return 'done'

    job_id = get_job_runner().submit(job_type, target, podcast_name=podcast_name)
    assert started.wait(timeout=2)
    return job_id, release


def test_cancel_pause_resume_skip_routes_control_the_running_jobs_control(isolated_env):
    job_id, release = _submit_blocking_job()
    client = TestClient(app, follow_redirects=False)

    resp = client.post(f'/jobs/{job_id}/pause', data={'return_to': '/queue'})
    assert resp.status_code == 303
    assert 'paused' in resp.headers['location'].lower()
    assert get_job_runner().is_paused(job_id)

    resp = client.post(f'/jobs/{job_id}/resume', data={'return_to': '/queue'})
    assert resp.status_code == 303
    assert 'resumed' in resp.headers['location'].lower()
    assert not get_job_runner().is_paused(job_id)

    resp = client.post(f'/jobs/{job_id}/skip/ep-1', data={'return_to': '/queue'})
    assert resp.status_code == 303
    assert get_job_runner().get_control(job_id).should_skip('ep-1')

    resp = client.post(f'/jobs/{job_id}/cancel', data={'return_to': '/queue'})
    assert resp.status_code == 303
    assert 'stopped' in resp.headers['location'].lower()
    assert get_job_runner().get_control(job_id).is_cancelled()

    release.set()
    status = _poll_job(client, job_id)
    assert status['status'] == 'failed'
    assert status['error'] == 'Cancelled by user'


def test_job_control_routes_report_already_finished_for_unknown_job(isolated_env):
    client = TestClient(app, follow_redirects=False)
    for route in ('cancel', 'pause', 'resume'):
        resp = client.post(f'/jobs/does-not-exist/{route}')
        assert resp.status_code == 303
        assert 'ok=0' in resp.headers['location']

    resp = client.post('/jobs/does-not-exist/skip/ep-1')
    assert resp.status_code == 303
    assert 'ok=0' in resp.headers['location']


def test_queue_cancel_all_cancels_active_download_jobs(isolated_env):
    job_id, release = _submit_blocking_job(podcast_name='test_podcast')
    client = TestClient(app, follow_redirects=False)

    resp = client.post('/queue/cancel-all')
    assert resp.status_code == 303
    assert 'ok=1' in resp.headers['location']

    release.set()
    status = _poll_job(client, job_id)
    assert status['status'] == 'failed'
    assert status['error'] == 'Cancelled by user'


def test_queue_cancel_all_with_nothing_active(isolated_env):
    client = TestClient(app, follow_redirects=False)
    resp = client.post('/queue/cancel-all')
    assert resp.status_code == 303
    assert 'ok=0' in resp.headers['location']


def test_schedule_page_lists_podcasts(isolated_env, feed_df_factory):
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app)
    resp = client.get('/schedule')
    assert resp.status_code == 200
    assert 'test_podcast' in resp.text
    assert 'scheduler stopped' in resp.text


def test_schedule_page_update_returns_to_schedule(isolated_env, feed_df_factory):
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.add_feed('https://example.com/test_podcast.xml')

    client = TestClient(app, follow_redirects=False)
    resp = client.post('/podcasts/test_podcast/schedule', data={
        'interval_hours': '24', 'auto_download': 'true', 'return_to': '/schedule',
    })
    assert resp.status_code == 303
    assert resp.headers['location'].startswith('/schedule?')

    schedule = services.get_podcast_schedule('test_podcast')
    assert schedule.interval_hours == 24


def test_redownload_episode_runs_as_background_job(isolated_env, feed_df_factory, mock_download):
    _add_and_download_one(feed_df_factory, mock_download)
    episode = services.list_episodes('test_podcast').episodes[0]
    assert Path(episode.saved_path).exists()

    client = TestClient(app, follow_redirects=False)
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        resp = client.post('/podcasts/test_podcast/episodes/ep-1/redownload')
        assert resp.status_code == 303
        job_id = _job_id_from_redirect(resp.headers['location'])
        status = _poll_job(client, job_id)

    assert status['status'] == 'success'


def test_podcast_detail_shows_play_button_only_for_downloaded(isolated_env, feed_df_factory, mock_download):
    df = feed_df_factory(episodes=[
        {'id': 'ep-1', 'title': 'Downloaded Episode', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Pending Episode', 'published': '2024-01-02'},
    ])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_last('test_podcast')

    client = TestClient(app)
    resp = client.get('/podcasts/test_podcast')
    assert resp.status_code == 200
    # Count actual <button> elements, not the JS below that also mentions the class name.
    assert resp.text.count('class="secondary play-button"') == 1


def test_episode_detail_shows_feed_html_description_as_text(isolated_env, feed_df_factory):
    df = feed_df_factory(episodes=[{
        'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01',
        'summary': 'First line.<br /><br />Read <a href="https://example.com">our book</a> &amp; more<script>x()</script>',
    }])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    resp = TestClient(app).get('/podcasts/test_podcast/episodes/ep-1')
    assert resp.status_code == 200
    assert 'Read our book &amp; more' in resp.text
    assert '&lt;br' not in resp.text and '&lt;a href' not in resp.text
    assert '<script>x()' not in resp.text


def test_podcast_detail_shows_full_dates(isolated_env, feed_df_factory):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Episode One', 'published': 'Fri, 03 Jan 2025 10:00:00 +0000'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    resp = TestClient(app).get('/podcasts/test_podcast')
    assert '2025-01-03' in resp.text
    assert 'Fri, 03 Ja<' not in resp.text


def test_schedule_page_shows_podcast_display_name(isolated_env, feed_df_factory):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01', 'author_raw': 'The Test Show'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    resp = TestClient(app).get('/schedule')
    assert resp.status_code == 200
    assert 'The Test Show' in resp.text
