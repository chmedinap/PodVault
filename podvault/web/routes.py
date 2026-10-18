"""
Route handlers for the PodVault web interface.

Handlers are plain `def` (not `async def`): the service layer underneath
is fully synchronous (blocking sqlite3/requests calls), and Starlette
runs sync route handlers in a worker thread automatically, so this
doesn't block the event loop. Anything that can take more than a second
or two — adding a feed, downloading episodes — is submitted to the
JobRunner instead of running inline, so the request returns immediately
and the browser polls `/jobs/{id}/status` for progress. Verifying files
and renaming an episode are local, fast operations, so they just run
inline and redirect back with a status message.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from podvault import config, scheduler, services
from podvault.database import jobs as jobs_db
from podvault.database.backup import get_backup_dir
from podvault.database.connection import get_db_connection
from podvault.database.queries import mark_episodes_missing, table_exists
from podvault.download.utils import AUDIO_MIME_TYPES, TEMPLATE_VARIABLES
from podvault.jobs import get_job_runner
from podvault.services import local_feed, opml
from podvault.services.scheduling import INTERVAL_CHOICES_HOURS
from podvault.web.auth import feed_token
from podvault.web.templating import format_duration, format_size, templates

router = APIRouter()

EPISODES_PER_PAGE = 50
LIBRARY_FILTERS = ('All', 'Incomplete', 'Missing files', 'Scheduled')

# Rough home-broadband guess for the pre-flight "scope confirm" dialog
# (Fase 22): before a bulk download has ever run there's no historical
# per-episode duration to derive a real rate from, so this stays a flat,
# clearly-labelled "~estimated" constant rather than a fake measurement.
ASSUMED_DOWNLOAD_SECONDS_PER_EPISODE = 12


def _filter_podcasts(podcasts, filter_name: str, query: Optional[str]):
    q = (query or '').strip().lower()

    def matches(p) -> bool:
        if q and q not in (p.display_name or '').lower() and q not in p.name.lower():
            return False
        if filter_name == 'Incomplete':
            return (p.downloaded_episodes or 0) < (p.total_episodes or 0)
        if filter_name == 'Missing files':
            return (p.missing_episodes or 0) > 0
        if filter_name == 'Scheduled':
            return p.scheduled
        return True

    return [p for p in podcasts if matches(p)]


def _relative_time(iso_timestamp: Optional[str]) -> str:
    if not iso_timestamp:
        return "never"
    try:
        checked_at = datetime.fromisoformat(iso_timestamp)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return iso_timestamp

    seconds = (datetime.now(timezone.utc) - checked_at).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds / 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = int(minutes / 60)
    if hours < 24:
        return f"{hours}h ago"
    return f"{int(hours / 24)}d ago"


def _next_check_label(last_checked_at: Optional[str], interval_hours: Optional[int]) -> str:
    if not interval_hours:
        return 'off'
    if not last_checked_at:
        return 'now'
    try:
        last_checked = datetime.fromisoformat(last_checked_at)
        if last_checked.tzinfo is None:
            last_checked = last_checked.replace(tzinfo=timezone.utc)
    except ValueError:
        return 'now'

    remaining = (last_checked + timedelta(hours=interval_hours) - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        return 'now'
    hours = remaining / 3600
    if hours < 24:
        return f"in {max(int(hours), 1)}h"
    return f"in {int(hours / 24)}d"


def _job_duration_label(created_at: Optional[str], updated_at: Optional[str]) -> str:
    try:
        created = datetime.fromisoformat(created_at)
        updated = datetime.fromisoformat(updated_at)
    except (TypeError, ValueError):
        return '—'
    seconds = (updated - created).total_seconds()
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s"


def _redirect_with_message(
    url: str, message: str, ok: bool, *,
    tone: Optional[str] = None, job_id: Optional[str] = None,
    action_label: Optional[str] = None, action_url: Optional[str] = None,
) -> RedirectResponse:
    """
    Redirect back to `url` carrying everything the client-side toast/job-strip
    script in base.html needs, all via the query string (Fase 22) — this is
    the only channel used, so no route needs to thread these through its
    Jinja template context.
    """
    params = {'msg': message, 'ok': '1' if ok else '0'}
    if tone:
        params['tone'] = tone
    if job_id:
        params['job_id'] = job_id
    if action_label:
        params['action_label'] = action_label
    if action_url:
        params['action_url'] = action_url
    separator = '&' if '?' in url else '?'
    query = '&'.join(f"{k}={quote(str(v))}" for k, v in params.items())
    return RedirectResponse(url=f"{url}{separator}{query}", status_code=303)


def _public_base_url(request: Request) -> str:
    """Base URL podcast apps should use: PODVAULT_BASE_URL if set (reverse proxy), else this request's."""
    return (config.BASE_URL or str(request.base_url)).rstrip('/')


def _feed_url_builders(request: Request) -> local_feed.FeedUrls:
    """Absolute URLs for PodVault's own feeds, carrying the feed token when a login is on (Fase 24)."""
    base = _public_base_url(request)
    token = feed_token()

    def url(path: str, **params) -> str:
        if token:
            params['token'] = token
        return f"{base}{path}" + (f"?{urlencode(params)}" if params else '')

    return local_feed.FeedUrls(
        feed=lambda name: url(f"/podcasts/{quote(name)}/feed.xml"),
        page=lambda name: f"{base}/podcasts/{quote(name)}",
        audio=lambda name, episode_id: url(f"/podcasts/{quote(name)}/feed/audio", id=episode_id),
        episode_cover=lambda name, episode_id: url(f"/podcasts/{quote(name)}/feed/cover", id=episode_id),
        podcast_cover=lambda name: url(f"/podcasts/{quote(name)}/feed/cover"),
    )


def _safe_redirect_target(path: Optional[str], default: str) -> str:
    """Only accept same-origin relative paths for `return_to`-style form fields, to rule out an open redirect."""
    if path and path.startswith('/') and not path.startswith('//'):
        return path
    return default


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request, msg: Optional[str] = None, ok: Optional[str] = None,
    filter: str = 'All', q: Optional[str] = None,
):
    result = services.list_podcasts()
    visible_podcasts = _filter_podcasts(result.podcasts, filter, q) if result.success else []
    scheduled_count = sum(1 for p in result.podcasts if p.scheduled)

    with get_db_connection() as conn:
        queue_totals = jobs_db.get_queue_totals(conn)

    return templates.TemplateResponse(request, "dashboard.html", {
        "result": result,
        "visible_podcasts": visible_podcasts,
        "filters": LIBRARY_FILTERS,
        "active_filter": filter,
        "query": q or '',
        "scheduled_count": scheduled_count,
        "queue_totals": queue_totals,
        "active_nav": "library",
        "msg": msg,
        "ok": ok == '1',
    })


@router.post("/podcasts")
def add_podcast(feed_url: str = Form(...)):
    feed_url = feed_url.strip()
    if not feed_url:
        return _redirect_with_message("/", "The URL cannot be empty", ok=False)

    job_id = get_job_runner().submit("add_feed", services.add_feed, feed_url)
    return _redirect_with_message("/", "Adding podcast…", ok=True, job_id=job_id)


@router.post("/verify")
def verify_all(return_to: Optional[str] = Form(None)):
    target = _safe_redirect_target(return_to, "/")
    result = services.verify_files(None)
    if result.error:
        return _redirect_with_message(target, result.error, ok=False)
    if result.total_missing:
        return _redirect_with_message(target, f"{result.total_missing} episode(s) marked as missing", ok=False)
    return _redirect_with_message(target, "Everything checks out, no files are missing", ok=True)


EPISODE_FILTERS = ('All', 'Downloaded', 'Missing', 'Pending')


def _filter_episodes(episodes, filter_name: str):
    if filter_name == 'Downloaded':
        return [e for e in episodes if e.status == 'downloaded']
    if filter_name == 'Missing':
        return [e for e in episodes if e.status == 'missing']
    if filter_name == 'Pending':
        return [e for e in episodes if e.status not in ('downloaded', 'missing')]
    return episodes


@router.get("/podcasts/{name}", response_class=HTMLResponse)
def podcast_detail(
    request: Request, name: str, page: int = 1, ep_filter: str = 'All',
    msg: Optional[str] = None, ok: Optional[str] = None,
):
    result = services.list_episodes(name)
    if result.error:
        return _redirect_with_message("/", result.error, ok=False)

    podcast_info = services.get_podcast_info(name)
    filtered_episodes = _filter_episodes(result.episodes, ep_filter)

    total_episodes = len(result.episodes)
    total_filtered = len(filtered_episodes)
    total_pages = max((total_filtered + EPISODES_PER_PAGE - 1) // EPISODES_PER_PAGE, 1)
    page = min(max(page, 1), total_pages)
    start = (page - 1) * EPISODES_PER_PAGE
    episodes_page = filtered_episodes[start:start + EPISODES_PER_PAGE]

    filter_counts = {f: len(_filter_episodes(result.episodes, f)) for f in EPISODE_FILTERS}

    schedule = services.get_podcast_schedule(name)

    pending_count = filter_counts['Missing'] + filter_counts['Pending']
    avg_size = _avg_episode_size(podcast_info)
    estimated_size = format_size(avg_size * pending_count) if avg_size else None
    estimated_seconds = pending_count * ASSUMED_DOWNLOAD_SECONDS_PER_EPISODE
    estimated_time = format_duration(estimated_seconds) if pending_count else None

    return templates.TemplateResponse(request, "podcast_detail.html", {
        "podcast_name": name,
        "podcast_info": podcast_info,
        "episodes": episodes_page,
        "page": page,
        "total_pages": total_pages,
        "total_episodes": total_episodes,
        "total_filtered": total_filtered,
        "ep_filters": EPISODE_FILTERS,
        "filter_counts": filter_counts,
        "active_ep_filter": ep_filter,
        "schedule": schedule,
        "schedule_last_checked": _relative_time(schedule.last_checked_at),
        "interval_choices": INTERVAL_CHOICES_HOURS,
        "pending_count": pending_count,
        "estimated_size": estimated_size,
        "estimated_time": estimated_time,
        "own_feed_url": _feed_url_builders(request).feed(name),
        "active_nav": "library",
        "msg": msg,
        "ok": ok == '1',
    })


@router.post("/podcasts/{name}/schedule")
def update_schedule(
    name: str, interval_hours: str = Form(...), auto_download: bool = Form(False),
    return_to: Optional[str] = Form(None),
):
    target = _safe_redirect_target(return_to, f"/podcasts/{name}")
    parsed_interval = int(interval_hours) if interval_hours and interval_hours != '0' else None
    result = services.set_podcast_schedule(name, parsed_interval, auto_download)
    if result.error:
        return _redirect_with_message(target, result.error, ok=False)
    return _redirect_with_message(target, "Schedule updated", ok=True)


@router.get("/schedule", response_class=HTMLResponse)
def schedule_page(request: Request, msg: Optional[str] = None, ok: Optional[str] = None):
    schedules = services.list_schedules()
    rows = [
        {
            'schedule': s,
            'last_checked_label': _relative_time(s.last_checked_at),
            'next_check_label': _next_check_label(s.last_checked_at, s.interval_hours),
        }
        for s in schedules
    ]
    return templates.TemplateResponse(request, "schedule.html", {
        "rows": rows,
        "interval_choices": INTERVAL_CHOICES_HOURS,
        "scheduler_running": scheduler.is_running(),
        "poll_interval_seconds": config.SCHEDULER_POLL_INTERVAL_SECONDS,
        "active_nav": "schedule",
        "msg": msg,
        "ok": ok == '1',
    })


ACTIVITY_FILTERS = ('All', 'Downloads', 'Syncs', 'Verify', 'Errors')


def _filter_jobs(jobs, filter_name: str):
    if filter_name == 'Downloads':
        return [j for j in jobs if j['job_type'].startswith('download')]
    if filter_name == 'Syncs':
        return [j for j in jobs if j['job_type'] in ('add_feed', 'scheduled_sync')]
    if filter_name == 'Verify':
        return [j for j in jobs if j['job_type'] == 'verify']
    if filter_name == 'Errors':
        return [j for j in jobs if j['status'] == 'failed']
    return jobs


@router.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request, filter: str = 'All'):
    with get_db_connection() as conn:
        jobs = jobs_db.list_jobs(conn, limit=200)
    visible_jobs = _filter_jobs(jobs, filter)

    rows = [
        {
            'job': j,
            'duration_label': _job_duration_label(j['created_at'], j['updated_at']),
            'when_label': _relative_time(j['updated_at']),
        }
        for j in visible_jobs
    ]

    return templates.TemplateResponse(request, "activity.html", {
        "rows": rows,
        "filters": ACTIVITY_FILTERS,
        "active_filter": filter,
        "active_nav": "activity",
    })


DOWNLOAD_JOB_TYPES = ('download_all', 'download_last')


def _avg_episode_size(podcast_info) -> Optional[float]:
    if not podcast_info or not podcast_info.downloaded_episodes:
        return None
    return (podcast_info.total_size_bytes or 0) / podcast_info.downloaded_episodes


@router.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request):
    with get_db_connection() as conn:
        running_jobs = jobs_db.list_jobs_by_status(conn, ['running'], limit=50)
        pending_jobs = jobs_db.list_jobs_by_status(conn, ['pending'], limit=50)
        recent_jobs = jobs_db.list_jobs(conn, limit=200)

    running_jobs = [j for j in running_jobs if j['job_type'] in DOWNLOAD_JOB_TYPES]
    pending_jobs = [j for j in pending_jobs if j['job_type'] in DOWNLOAD_JOB_TYPES]

    active_downloads = []
    for job in running_jobs:
        podcast_info = services.get_podcast_info(job['podcast_name']) if job['podcast_name'] else None
        avg_size = _avg_episode_size(podcast_info)
        estimate = services.estimate_progress(job['created_at'], job['progress'], job['total'] or 0, avg_size)

        # `preview_pending` reflects live DB state, but episode `status` is only
        # flipped to 'downloaded' in batches (see BATCH_COMMIT_SIZE in
        # downloader.py), not after each file — so for batches spanning more
        # than one commit this list can run ahead of `job['progress']`. Index
        # into it defensively rather than assuming the two stay in lockstep.
        pending_episodes = services.preview_pending(job['podcast_name']) if job['podcast_name'] else []
        current_index = min(max(job['progress'] - 1, 0), max(len(pending_episodes) - 1, 0))
        current_episode = pending_episodes[current_index] if pending_episodes else None
        up_next = pending_episodes[current_index + 1:] if pending_episodes else []

        up_next_rows = [
            {
                'episode': ep,
                'estimated_size': format_size(avg_size) if avg_size else '—',
                'estimated_wait': (
                    format_duration(estimate.seconds_per_episode * (i + 1)) if estimate.seconds_per_episode else '~'
                ),
            }
            for i, ep in enumerate(up_next)
        ]

        pct = round((estimate.completed_count / job['total']) * 100) if job['total'] else 0
        active_downloads.append({
            'job': job,
            'current_episode': current_episode,
            'estimate': estimate,
            'pct': pct,
            'speed_label': f"~{format_size(estimate.bytes_per_second)}/s" if estimate.bytes_per_second else '~estimating…',
            'eta_label': f"~{format_duration(estimate.eta_seconds)}" if estimate.eta_seconds is not None else '~estimating…',
            'up_next': up_next_rows,
            'up_next_total_size': format_size(avg_size * len(up_next)) if avg_size else '—',
            'is_paused': get_job_runner().is_paused(job['id']),
        })

    today = datetime.now(timezone.utc).date().isoformat()
    finished_today = [
        j for j in recent_jobs
        if j['job_type'] in DOWNLOAD_JOB_TYPES and j['status'] in ('success', 'failed')
        and (j['updated_at'] or '').startswith(today)
    ]
    finished_rows = [
        {
            'job': j,
            'when_label': _relative_time(j['updated_at']),
        }
        for j in finished_today
    ]

    return templates.TemplateResponse(request, "queue.html", {
        "active_downloads": active_downloads,
        "pending_job_count": len(pending_jobs),
        "finished_rows": finished_rows,
        "any_active": bool(active_downloads) or bool(pending_jobs),
        "active_nav": "queue",
    })


@router.get("/storage", response_class=HTMLResponse)
def storage_page(request: Request, msg: Optional[str] = None, ok: Optional[str] = None):
    disk_usage = services.get_disk_usage()
    largest_podcasts = sorted(
        services.list_podcasts().podcasts, key=lambda p: p.total_size_bytes or 0, reverse=True,
    )[:8]

    verify_result = services.verify_files(None, mark_missing=False)
    migration_result = services.migrate_layout(apply=False)
    planned_moves = [m for m in migration_result.moves if m.status == 'planned']

    corrupt_episodes = services.list_corrupt_episodes()
    corrupt_rows = [
        {**episode, 'last_good_label': _relative_time(episode['verified_at'])}
        for episode in corrupt_episodes
    ]
    local_only_episodes = [
        {'podcast_name': podcast_name, 'episode_id': episode_id, 'title': title}
        for podcast_name, episode_id, title in services.list_local_only_episodes()
    ]

    with get_db_connection() as conn:
        id3_job = jobs_db.get_latest_job_by_type(conn, 'id3_check')
        integrity_job = jobs_db.get_latest_job_by_type(conn, 'integrity_check')

    return templates.TemplateResponse(request, "storage.html", {
        "downloads_folder": config.DOWNLOADS_FOLDER,
        "disk_usage": disk_usage,
        "largest_podcasts": largest_podcasts,
        "max_podcast_size": max((p.total_size_bytes or 0 for p in largest_podcasts), default=0),
        "verify_result": verify_result,
        "planned_moves": planned_moves,
        "id3_job": id3_job,
        "integrity_job": integrity_job,
        "corrupt_rows": corrupt_rows,
        "local_only_episodes": local_only_episodes,
        "active_nav": "storage",
        "msg": msg,
        "ok": ok == '1',
    })


@router.post("/storage/id3-check")
def trigger_id3_check():
    job_id = get_job_runner().submit("id3_check", services.run_id3_check)
    return _redirect_with_message("/storage", "Checking ID3 tags…", ok=True, job_id=job_id)


@router.post("/storage/id3-retag")
def trigger_id3_retag():
    job_id = get_job_runner().submit("id3_retag", services.run_id3_retag)
    return _redirect_with_message("/storage", "Retagging incomplete episodes…", ok=True, job_id=job_id)


@router.post("/storage/integrity-check")
def trigger_integrity_check():
    job_id = get_job_runner().submit("integrity_check", services.run_integrity_check)
    return _redirect_with_message("/storage", "Checking file integrity…", ok=True, job_id=job_id)


@router.post("/storage/redownload-corrupt")
def redownload_corrupt():
    """
    "Re-download all N" for the corrupt-files health row (Fase 22): groups
    the corrupt episodes by podcast, marks each missing, then submits one
    `download_all` job per affected podcast (matches how a bulk redownload
    already works elsewhere — no per-episode job needed here).
    """
    corrupt_episodes = services.list_corrupt_episodes()
    if not corrupt_episodes:
        return _redirect_with_message("/storage", "No corrupt files to re-download.", ok=False)

    by_podcast: dict = {}
    for episode in corrupt_episodes:
        by_podcast.setdefault(episode['podcast_name'], []).append(episode['episode_id'])

    with get_db_connection() as conn:
        for podcast_name, episode_ids in by_podcast.items():
            mark_episodes_missing(conn, podcast_name, episode_ids)

    last_job_id = None
    for podcast_name in by_podcast:
        last_job_id = get_job_runner().submit(
            "download_all", services.download_all, podcast_name,
            podcast_name=podcast_name, require_confirmation=False,
        )
    return _redirect_with_message(
        "/storage", f"Re-downloading {len(corrupt_episodes)} corrupt episode(s) across {len(by_podcast)} podcast(s)…",
        ok=True, job_id=last_job_id, action_label="View queue", action_url="/queue",
    )


@router.get("/podcasts/{name}/export")
def export_podcast(name: str):
    manifest = services.export_podcast_manifest(name)
    if manifest is None:
        return _redirect_with_message("/storage", f"Podcast '{name}' not found in database.", ok=False)
    return JSONResponse(
        manifest,
        headers={"Content-Disposition": f'attachment; filename="{name}_manifest.json"'},
    )


@router.get("/export")
def export_library():
    manifest = services.export_library_manifest()
    return JSONResponse(
        manifest,
        headers={"Content-Disposition": 'attachment; filename="podvault_library_manifest.json"'},
    )


@router.post("/podcasts/{name}/download")
def trigger_download_all(name: str):
    job_id = get_job_runner().submit(
        "download_all", services.download_all, name,
        podcast_name=name, require_confirmation=False,
    )
    return _redirect_with_message(
        f"/podcasts/{name}", "Downloading pending episodes…", ok=True,
        job_id=job_id, action_label="View queue", action_url="/queue",
    )


@router.post("/podcasts/{name}/download-last")
def trigger_download_last(name: str):
    job_id = get_job_runner().submit(
        "download_last", services.download_last, name, podcast_name=name,
    )
    return _redirect_with_message(
        f"/podcasts/{name}", "Downloading the latest episode…", ok=True,
        job_id=job_id, action_label="View queue", action_url="/queue",
    )


@router.post("/podcasts/{name}/verify")
def verify_podcast(name: str):
    result = services.verify_files(name)
    if result.error:
        return _redirect_with_message(f"/podcasts/{name}", result.error, ok=False)
    if result.total_missing:
        return _redirect_with_message(f"/podcasts/{name}", f"{result.total_missing} episode(s) marked as missing", ok=False)
    return _redirect_with_message(f"/podcasts/{name}", "Everything checks out, no files are missing", ok=True)


@router.post("/podcasts/{name}/episodes/{episode_id}/rename")
def rename_episode(name: str, episode_id: str, custom_title: str = Form(""), return_to: Optional[str] = Form(None)):
    target = _safe_redirect_target(return_to, f"/podcasts/{name}")
    result = services.rename_episode(name, episode_id, custom_title)
    if result.error:
        return _redirect_with_message(target, result.error, ok=False)
    message = f"'{result.old_title}' -> '{result.new_title}'"
    if result.detail:
        message += f" ({result.detail})"
    return _redirect_with_message(target, message, ok=True)


@router.get("/podcasts/{name}/episodes/{episode_id}", response_class=HTMLResponse)
def episode_detail(request: Request, name: str, episode_id: str, msg: Optional[str] = None, ok: Optional[str] = None):
    detail = services.get_episode(name, episode_id)
    if detail.error:
        return _redirect_with_message(f"/podcasts/{name}", detail.error, ok=False)

    id3_status_label = {True: 'complete', False: 'incomplete', None: 'not available'}[detail.id3_complete]

    return templates.TemplateResponse(request, "episode_detail.html", {
        "podcast_name": name,
        "episode": detail,
        "id3_status_label": id3_status_label,
        "active_nav": "library",
        "msg": msg,
        "ok": ok == '1',
    })


@router.post("/podcasts/{name}/episodes/{episode_id}/redownload")
def redownload_episode(name: str, episode_id: str, return_to: Optional[str] = Form(None)):
    """
    Fetches THIS episode only (Fase 22) — a true single-episode download via
    `services.download_episode`, replacing the old approach of marking the
    episode missing and re-running the whole `download_all` backlog for its
    podcast. Shared by the podcast-detail row's ↓ action and the episode
    detail page's "Re-download" button.
    """
    target = _safe_redirect_target(return_to, f"/podcasts/{name}/episodes/{episode_id}")
    detail = services.get_episode(name, episode_id)
    if detail.error:
        return _redirect_with_message(target, detail.error, ok=False)

    job_id = get_job_runner().submit(
        "download_episode", services.download_episode, name, episode_id,
        podcast_name=name,
    )
    return _redirect_with_message(
        target, f'Queued "{detail.display_title}" — this episode only.', ok=True,
        job_id=job_id, action_label="View queue", action_url="/queue",
    )


@router.get("/podcasts/{name}/episodes/{episode_id}/audio")
def episode_audio(name: str, episode_id: str):
    info = services.get_playback_info(name, episode_id)
    if not info.success:
        raise HTTPException(status_code=404, detail=info.error)
    return _audio_response(info.audio_path)


@router.get("/podcasts/{name}/episodes/{episode_id}/cover")
def episode_cover(name: str, episode_id: str):
    info = services.get_playback_info(name, episode_id)
    if not info.success or not info.cover_path:
        raise HTTPException(status_code=404, detail="No cover image available")
    return FileResponse(info.cover_path)


def _audio_response(audio_path: str) -> FileResponse:
    # FileResponse handles Range requests, so players can seek without
    # downloading the whole file first.
    media_type = AUDIO_MIME_TYPES.get(Path(audio_path).suffix.lower(), "application/octet-stream")
    return FileResponse(audio_path, media_type=media_type, filename=Path(audio_path).name)


# --- PodVault's own podcast feeds (Fase 24) ---------------------------------
# Episode ids travel as `?id=` rather than in the path: feed GUIDs are often
# URLs, and a "/" can't be routed inside a path segment.

@router.get("/podcasts/{name}/feed.xml")
def own_podcast_feed(request: Request, name: str):
    xml = local_feed.build_podcast_feed(name, _feed_url_builders(request))
    if xml is None:
        raise HTTPException(status_code=404, detail=f"Podcast '{name}' not found")
    return Response(xml, media_type="application/rss+xml; charset=utf-8")


@router.get("/podcasts/{name}/feed/audio")
def own_feed_audio(name: str, id: str):
    info = services.get_playback_info(name, id)
    if not info.success:
        raise HTTPException(status_code=404, detail=info.error)
    return _audio_response(info.audio_path)


@router.get("/podcasts/{name}/feed/cover")
def own_feed_cover(name: str, id: Optional[str] = None):
    if id is not None:
        info = services.get_playback_info(name, id)
        if info.success and info.cover_path:
            return FileResponse(info.cover_path)
        raise HTTPException(status_code=404, detail="No cover image available")
    cover = local_feed.podcast_cover_path(name)
    if cover is None:
        raise HTTPException(status_code=404, detail="No cover image available")
    return FileResponse(str(cover))


@router.get("/feeds.opml")
def own_feeds_opml(request: Request):
    """OPML of every podcast's PodVault feed — import it into a podcast app to subscribe to the whole archive."""
    builders = _feed_url_builders(request)
    return Response(opml.export_opml(own_feeds=True, feed_url_for=builders.feed),
                    media_type="text/x-opml; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="podvault_archive.opml"'})


# --- OPML import/export of the original subscriptions (Fase 24) -------------

@router.get("/export/opml")
def export_subscriptions_opml():
    return Response(opml.export_opml(), media_type="text/x-opml; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="podvault_subscriptions.opml"'})


@router.post("/import/opml")
def import_subscriptions_opml(opml_file: UploadFile = File(...)):
    content = opml_file.file.read(opml.MAX_OPML_BYTES + 1)
    parsed = opml.parse_opml(content)  # validate up front so a bad file gets an immediate message
    if parsed.error:
        return _redirect_with_message("/", parsed.error, ok=False)
    if not parsed.outlines:
        return _redirect_with_message("/", "No podcast feeds found in this OPML file.", ok=False)
    job_id = get_job_runner().submit("import_opml", opml.import_opml, content)
    return _redirect_with_message(
        "/", f"Importing {len(parsed.outlines)} feed(s) from {opml_file.filename or 'OPML'}…",
        ok=True, job_id=job_id, action_label="View activity", action_url="/activity",
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: Optional[str] = None, ok: Optional[str] = None):
    settings = services.get_settings()
    preview = services.preview(settings.folder_template, settings.filename_template)
    env_rows = [
        {'k': 'PODVAULT_DB_PATH', 'v': config.DB_PATH},
        {'k': 'PODVAULT_DOWNLOADS_FOLDER', 'v': config.DOWNLOADS_FOLDER},
        {'k': 'PODVAULT_FEEDS_FILE', 'v': config.FEEDS_FILE},
        {'k': 'PODVAULT_LOG_LEVEL', 'v': config.LOG_LEVEL},
        {'k': 'PODVAULT_LOG_FILE', 'v': config.LOG_FILE or 'not set'},
        {'k': 'PODVAULT_SCHEDULER_POLL_INTERVAL_SECONDS', 'v': str(config.SCHEDULER_POLL_INTERVAL_SECONDS)},
        {'k': 'PODVAULT_DEFAULT_DELAY_SECONDS', 'v': str(config.DEFAULT_DELAY_SECONDS)},
        {'k': 'PODVAULT_DOWNLOAD_TIMEOUT', 'v': str(config.DOWNLOAD_TIMEOUT)},
        {'k': 'PODVAULT_DB_TIMEOUT', 'v': str(config.DB_TIMEOUT)},
        {'k': 'PODVAULT_USERNAME', 'v': config.WEB_USERNAME or 'not set (login disabled)'},
        # Never echo the password itself, only whether one is configured
        {'k': 'PODVAULT_PASSWORD', 'v': 'set' if config.WEB_PASSWORD else 'not set'},
        {'k': 'PODVAULT_BASE_URL', 'v': config.BASE_URL or 'not set (taken from each request)'},
        {'k': 'PODVAULT_FEED_TOKEN', 'v': 'set' if config.FEED_TOKEN else ('derived from password' if config.WEB_PASSWORD else 'not needed (no login)')},
        {'k': 'PODVAULT_BACKUP_ENABLED', 'v': 'yes' if config.BACKUP_ENABLED else 'no'},
        {'k': 'PODVAULT_BACKUP_DIR', 'v': str(get_backup_dir())},
        {'k': 'PODVAULT_BACKUP_KEEP', 'v': str(config.BACKUP_KEEP)},
        {'k': 'PODVAULT_BACKUP_INTERVAL_HOURS', 'v': str(config.BACKUP_INTERVAL_HOURS)},
    ]
    return templates.TemplateResponse(request, "settings.html", {
        "folder_template": settings.folder_template,
        "filename_template": settings.filename_template,
        "preview": preview,
        "variables": TEMPLATE_VARIABLES,
        "default_folder_template": config.DEFAULT_FOLDER_TEMPLATE,
        "default_filename_template": config.DEFAULT_FILENAME_TEMPLATE,
        "env_rows": env_rows,
        "active_nav": "settings",
        "msg": msg,
        "ok": ok == '1',
    })


@router.post("/settings")
def update_settings(folder_template: str = Form(...), filename_template: str = Form(...)):
    result = services.update_settings(folder_template, filename_template)
    if result.error:
        return _redirect_with_message("/settings", result.error, ok=False)
    return _redirect_with_message("/settings", "Templates updated", ok=True)


@router.post("/settings/preview")
def preview_settings(folder_template: str = Form(...), filename_template: str = Form(...)):
    preview = services.preview(folder_template, filename_template)
    return JSONResponse({
        "folder_path": preview.folder_path,
        "filename": preview.filename,
        "error": preview.error,
    })


@router.get("/jobs/active/status")
def active_job_status():
    """
    The most recently created job still pending/running, or null — lets the
    job strip (base.html) keep polling after a navigation to a page that
    carries no `job_id` in its URL (Fase 22).

    Registered before `/jobs/{job_id}/status` so "active" isn't swallowed by
    that route's `{job_id}` path parameter.
    """
    with get_db_connection() as conn:
        jobs = jobs_db.list_jobs_by_status(conn, ['pending', 'running'], limit=1)
    return JSONResponse(jobs[0] if jobs else None)


@router.get("/jobs/{job_id}/status")
def job_status(job_id: str):
    with get_db_connection() as conn:
        job = jobs_db.get_job(conn, job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return JSONResponse(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, return_to: Optional[str] = Form(None)):
    target = _safe_redirect_target(return_to, "/queue")
    found = get_job_runner().cancel(job_id)
    if not found:
        return _redirect_with_message(target, "That job has already finished.", ok=False)
    return _redirect_with_message(target, "Download stopped. Finished episodes are kept.", ok=True)


@router.post("/jobs/{job_id}/pause")
def pause_job(job_id: str, return_to: Optional[str] = Form(None)):
    target = _safe_redirect_target(return_to, "/queue")
    found = get_job_runner().pause(job_id)
    if not found:
        return _redirect_with_message(target, "That job has already finished.", ok=False)
    return _redirect_with_message(target, "Queue paused after the current episode.", ok=True)


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str, return_to: Optional[str] = Form(None)):
    target = _safe_redirect_target(return_to, "/queue")
    found = get_job_runner().resume(job_id)
    if not found:
        return _redirect_with_message(target, "That job has already finished.", ok=False)
    return _redirect_with_message(target, "Queue resumed.", ok=True)


@router.post("/jobs/{job_id}/skip/{episode_id}")
def skip_episode(job_id: str, episode_id: str, return_to: Optional[str] = Form(None)):
    target = _safe_redirect_target(return_to, "/queue")
    found = get_job_runner().skip(job_id, episode_id)
    if not found:
        return _redirect_with_message(target, "That job has already finished.", ok=False)
    return _redirect_with_message(target, "Removed from the queue.", ok=True)


@router.post("/queue/cancel-all")
def cancel_all_downloads():
    with get_db_connection() as conn:
        active_jobs = jobs_db.list_jobs_by_status(conn, ['pending', 'running'], limit=50)
    runner = get_job_runner()
    dropped = 0
    for job in active_jobs:
        if job['job_type'] in DOWNLOAD_JOB_TYPES and runner.cancel(job['id']):
            dropped += 1
    if not dropped:
        return _redirect_with_message("/queue", "Nothing in the queue to cancel.", ok=False)
    return _redirect_with_message(
        "/queue", f"Queue cleared. {dropped} job(s) stopped, downloaded files untouched.", ok=True,
    )
