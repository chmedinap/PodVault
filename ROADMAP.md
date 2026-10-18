# Roadmap: Web Interface + Docker

**Date:** 2026-09-05
**Goal:** add a web administration interface to PodVault (add podcasts by URL, customize names via ID3 tags, verify files on disk, new per-episode folder structure) and then package everything in Docker.

This document assumes the following design decisions (recommendations marked with 💡; if any of them don't convince you, we can change them without affecting the rest of the plan):

- 💡 **Web backend:** FastAPI. Pure Python, integrates directly with the current code (`sqlite3`, `pandas`, `mutagen`), has native async support (useful for not blocking the UI while downloading), and generates OpenAPI docs for free.
- 💡 **Frontend:** server-rendered with Jinja2 + HTMX (some lightweight JS/CSS for progress polling). Avoids maintaining a separate SPA build and keeps the Dockerfile much simpler. If you want a richer UI later on (drag&drop, complex inline editing), migrating to React is an incremental step, not a redesign.
- 💡 **Download concurrency:** a simple in-process job runner (worker thread(s) + a `jobs` table in SQLite for status/progress), instead of Celery+Redis. For single-user use this is enough and avoids operating extra services.
- 💡 **Access:** no login by default (local/home network use). A place to add basic auth later is left open, before exposing the app to the internet.
- The current CLI (`podvault ...`) **stays**; the web app and the CLI will share the same services layer (this also resolves recommendation 5.1 from `review.md`, which already flagged mixing business logic with the CLI).

---

## Phase 0 — Base refactor (blocks everything else)

**Why first:** today the logic lives glued to `cli.py`/`downloader.py`. Without separating this out, every web screen would end up reimplementing or duplicating download/processing logic.

- Create `podvault/services/` with pure functions that no longer depend on `argparse` or `print()`:
  - `add_feed(url)`, `process_all_feeds()`, `list_podcasts()`, `get_status(podcast_name=None)`, `download_all(podcast_name)`, `download_last(podcast_name)`
- `cli.py` becomes a thin wrapper over `services/`.
- Create a `jobs` table (`id`, `type`, `podcast_name`, `status`, `progress`, `total`, `message`, `created_at`, `updated_at`) and a minimal `JobRunner` (thread + queue) to run `download_all`/`process_all_feeds` in the background and be able to check its progress from the web.

**Estimated effort:** 1-2 days.

---

## Phase 1 — New per-episode folder structure

**Current structure** ([downloader.py:409](podvault/download/downloader.py#L409)):
```
downloads/podcast_name/2024/2024-01-15 - Episode Title/
```

**Requested structure:**
```
downloads/podcast_name/2024/01/03 - Episode Title/
    Episode Title - podcast_name.mp3
    episode_image.jpg
    episode_metadata.xml
```
(interpreting "# episode" as the episode number; if an episode doesn't carry `episode_number` in the feed, a fallback like `s-n` or the publish date is used, so the folder's alphabetical order doesn't break)

- Modify path construction in `download_all_episodes` and `download_last_episode` (`downloader.py`) to add the month level and use the episode in the folder name.
- **Migration script** (`podvault migrate-layout --dry-run`): walks `Saved_Path` of already-downloaded episodes, computes the new path, moves files, and updates `Saved_Path` in the DB.
  - ⚠️ This moves real files on disk — the command must support `--dry-run` (only reports what it would do) and run in dry-run mode by default; the real run (`--apply`) is triggered explicitly by you, not the web interface, and only after confirming the dry run looks right. I'm not automating this inside the web flow to avoid accidentally moving your entire library.

**Estimated effort:** 1 day (path change) + 1 day (migration script + tests).

---

## Phase 2 — Verifying files on disk

- Extend what already exists in `database/queries.py` (`verify_downloaded_files_exist()`) so that, besides detecting missing files, it can **fix the state**: if `status = 'downloaded'` but the file isn't there, mark it as `missing` (new state) instead of `downloaded`.
- Endpoint + "Verify files" button (global or per podcast) that triggers this check as a background job and shows a report: how many are OK, how many are missing, with an option to "re-download missing ones".

**Estimated effort:** 0.5-1 day.

---

## Phase 3 — Custom naming via ID3 tags

- Add a `custom_title` (nullable) column to each podcast's table: if set, it's used for the file/folder name and the `TIT2` tag; otherwise, the original feed title is used (current behavior).
- On the episode's page (web), an editable "Custom name" field that, on save:
  1. Rewrites the ID3 tag (`update_mp3_id3_tags`, already exists in `id3_tags.py`).
  2. Renames the episode's folder/file if it's already downloaded, and updates `Saved_Path`.
  3. Uses the existing validations in `validation.py`/`utils.sanitize_filename` to avoid invalid names or path traversal.

**Estimated effort:** 1 day.

---

## Phase 4 — Web interface (MVP) ✅ IMPLEMENTED

Minimum screens:

1. **Add podcast** — URL input (RSS or Apple Podcasts), calls `services.add_feed()`, shows the result/errors inline.
2. **Dashboard** — list of podcasts with stats (reuses `data/summary.py` and the `summary` table).
3. **Podcast detail** — episode table: status (downloaded/missing/pending), individual download button, custom-name field, path on disk.
4. **Download progress** — progress bar via polling to `/jobs/{id}`.
5. **File verification** — report from Phase 2.

**Estimated effort:** 3-5 days (depends on how polished you want it visually).

**Actual implementation:** `podvault/web/` (FastAPI + Jinja2). HTMX was dropped from the originally recommended stack: progress polling is already solved with `fetch()` + vanilla JS in `templates/job.html`, with no need for an extra library. `podvault serve [--host] [--port]` starts the server.

**Critical bug found and fixed during this phase:** `process_feeds_file`/`add_feed` saved feed data with `df.to_sql(title, conn, if_exists='replace')`, which **replaces the entire table** on every sync. Since `collect_data()` always builds a fresh DataFrame with `status='not downloaded'` and without the tracking columns (`Saved_Path`, `custom_title`, etc.), re-processing an already-known feed (e.g. "refresh this podcast to see new episodes") silently wiped the download state, saved paths, and custom names of every existing episode. Fixed by adding `_preserve_download_state()` in `services/feeds.py`: before writing, it merges (by episode `id`) the new DataFrame with the tracking state already stored in the table, preserving it for episodes that already existed. Validated with real data: downloaded + renamed episode → re-sync of the same feed → state, path, and custom name stay intact; same for episodes marked `missing`.

---

## Phase 5 — Minimal tests before Docker ✅ IMPLEMENTED

`review.md` already flags that there's no test suite — it's the project's biggest gap. Before packaging into Docker it's worth having at least:

- Tests for the `services/` layer (mocking network and filesystem).
- Tests for the critical web routes (`add_feed`, `download`, `verify`) with FastAPI's `TestClient`.
- A smoke test for the folder migration script (Phase 1) against a temp directory.

Exhaustive coverage isn't necessary, but it is worth covering the paths that move real files or write to the DB, since those are the most expensive to debug in production.

**Estimated effort:** 1-2 days.

**Actual implementation:** 44 tests in `tests/` (`pip install -e ".[test]"` + `pytest`), all running against an isolated temp directory (`isolated_env` fixture) and without touching the real network (`collect_data()` and episode downloads are mocked). Coverage: validation/SQL-injection guard, the feeds service (includes a regression test for the Phase 4 re-sync bug), downloads (`require_confirmation=False`, progress callback), file verification, folder migration (dry-run/apply/idempotency), episode renaming (with and without a file on disk), `JobRunner`, and the most critical web routes (dashboard, paginated detail, add/download as a background job, verify, rename). See details in `ARCHITECTURE.md` → Testing Strategy.

---

## Phase 6 — Dockerization ✅ IMPLEMENTED

- `Dockerfile`: `python:3.11-slim` image, installs the package (`pip install .`), runs `uvicorn podvault.web.app:app --host 0.0.0.0 --port 8000`.
- `docker-compose.yml` with volumes to persist state outside the container:
  ```yaml
  volumes:
    - ./data/podvault_data.db:/app/podvault_data.db
    - ./data/downloads:/app/downloads
    - ./data/feeds.txt:/app/feeds.txt
  ```
- Move configurable paths (`DB_PATH`, `DOWNLOADS_FOLDER`, etc.) to environment variables in `config.py` (this also resolves recommendation 5.6 from `review.md`, and is nearly mandatory for Docker volumes to make sense).
- `.dockerignore` (exclude `downloads/`, `*.db`, `.git`, `__pycache__`).
- A single container is enough since the job runner is in-process; there's no need to split "web" and "worker" unless the downloads volume grows a lot.

**Estimated effort:** 1 day.

**Actual implementation:** a single `./data:/app/data` volume (instead of three separate mounts) — avoids the classic Docker problem where bind-mounting a *file* that doesn't yet exist on the host (`podvault_data.db`) makes Docker create it as a directory instead of a file. `config.py` now reads `PODVAULT_DB_PATH`/`PODVAULT_DOWNLOADS_FOLDER`/`PODVAULT_FEEDS_FILE`/`PODVAULT_LOG_LEVEL`/`PODVAULT_LOG_FILE` from environment variables (with the same defaults as always if unset, without breaking usage outside Docker). Full detail in `ARCHITECTURE.md` → Deployment (Docker).

⚠️ **Docker wasn't available in the environment where I implemented this** (a sandbox with no Docker daemon), so I couldn't run `docker build`/`docker compose up` directly. I validated everything I could another way: installed the package with `pip install .` (non-editable, same as the Dockerfile does), confirmed the HTML templates get packaged correctly (`package_data`), and ran the `podvault serve` binary from a directory outside the repo with the same environment variables the container uses — added a real feed and downloaded an episode, confirming that everything (DB, downloads folder) shows up exactly where the env vars point. I also validated the `docker-compose.yml` syntax with a YAML parser. The only thing left is to actually run `docker compose up --build` on your machine for the final end-to-end confirmation.

---

# Extension: Advanced settings, auto-refresh, and playback

Added on 2026-09-05, at the user's request, on top of the already-implemented base (Phases 0-6). Three new features, fairly independent of each other — the proposed order (7 → 8 → 9) is by how invasive each one is to the existing code, not by hard dependency.

## Phase 7 — Settings: customizable folder and file naming ✅ IMPLEMENTED

**Goal:** replace the fixed folder scheme (`podcast/year/month/label`, hardcoded since Phase 1) with templates the user defines from a new **Settings** screen on the web.

**Design:**
- New `app_settings` table (single row) with `folder_template` and `filename_template`. Service `services/settings.py`: `get_settings()` / `update_settings(folder_template, filename_template)`.
- Variables available in the templates (via `str.format`, never `eval`/arbitrary code — a security risk otherwise): `{podcast_name}`, `{episode_title}` (already respects `custom_title` from Phase 3), `{episode_number}`, `{season_number}`, `{year}`, `{month}`, `{day}`, `{date}`, and `{episode_label}` (a computed variable: zero-padded number, or date if the feed doesn't number episodes — the *current* behavior as the default, for anyone who doesn't want to think about the fallback).
- Defaults = today's scheme, so nothing changes if you don't touch the settings:
  - `folder_template = "{podcast_name}/{year}/{month}/{episode_label} - {episode_title}"`
  - `filename_template = "{episode_title} - {podcast_name}"`
- Refactor: `build_episode_folder_path()`/`build_episode_base_filename()` (`download/utils.py`) no longer have the scheme hardcoded and instead render the configured template. Each folder segment is still sanitized separately (`sanitize_filename`), and the template is validated to disallow `..`/absolute paths or unknown variables (rejected on save, with a preview before confirming) — this is a basic template-sanity validation, not a migration mechanism.
- With no production data (this app only has test data), no preservation strategy for already-downloaded content is needed: changing the template applies from the next download onward, and that's it. `migrate_layout()` (Phase 1) still exists as a CLI command for anyone who ever wants to reorganize already-downloaded files, but it isn't part of this phase's scope.
- UI: a `/settings` screen with the two fields + a live preview against a sample episode before saving.

**Estimated effort:** 2-3 days.

**Risk:** medium — touches the function that downloads, migration, and renaming all depend on. Worth adding new tests for custom templates (path traversal, unknown variable, empty template).

**Actual implementation:** besides what was designed above, I added `podvault config` (CLI) as the equivalent of `/settings` for anyone who'd rather not use the web UI. Validation on save renders against **two** samples (one with an episode/season number, one without) — so a template that only works when a number is present (e.g. `{episode_number:03d}` with no fallback) gets rejected on save instead of breaking later on some real episode that lacks one.

While touching this function, I also fixed two pre-existing bugs:
1. **In this same phase's first draft**: my initial `DEFAULT_FOLDER_TEMPLATE` was `{podcast_name}/{year}/{month}/{episode_label}` — without `{episode_title}` — which dropped the episode title from the folder name (the real scheme since Phase 1 always included "number/date **- title**"). A test caught it by starting to fail (`test_rename_downloaded_episode_moves_files`) because the folder wasn't being renamed when the episode was renamed.
2. **`migrate_layout()` (Phase 1) never looked at `custom_title`**: it computed the destination folder using the original feed title, ignoring any renaming done in Phase 3. It now uses `get_effective_title()` like the rest of the code.

## Phase 8 — Per-podcast auto-refresh schedule ✅ IMPLEMENTED

**Goal:** each podcast configures how often its feed is checked for new episodes, with the option to download them automatically.

**Design:**
- New columns on `summary`: `check_interval_hours` (NULL = disabled), `auto_download` (bool), `last_checked_at`.
- `services/scheduling.py`: `set_podcast_schedule(podcast_name, interval_hours, auto_download)` and `run_due_checks()` — walks podcasts with a configured interval where `now - last_checked_at >= interval`, re-syncs the feed (same path as `add_feed`, reusing the `rss_feed_url` already stored in `summary`), and, if `auto_download` is on, enqueues a `download_all` job.
- 💡 **Recommendation:** a simple background thread with a loop every few minutes (started with `podvault serve`, via a FastAPI startup event) that calls `run_due_checks()`. No APScheduler/Celery — same reasoning as Phase 0's homegrown `JobRunner`. This **only runs in server mode** (with `serve`), it wouldn't make sense on a one-off CLI invocation.
- The automatic re-sync reuses `_preserve_download_state()` (the Phase 4 fix) — no risk of it stomping on already-downloaded or renamed episodes.
- UI: on each podcast's detail page, a "Refresh every: Never / 6h / 12h / 24h / weekly" selector + a "download new ones automatically" checkbox, and a "last checked: X ago" indicator.

**Estimated effort:** 2-3 days.

**Risk:** low-medium — the new part (the loop) is isolated; the only delicate bit is not blocking FastAPI's event loop (running it on a separate thread, not an async task).

**Actual implementation:** the thread is started from `cli.serve()` explicitly, **not** from FastAPI's lifespan as the original recommendation suggested — this way, importing the FastAPI `app` (which is all `TestClient`-based tests do) never fires off a background thread against whatever database happens to be active at that moment. `--reload` deliberately skips the scheduler (uvicorn's reloader subprocess never reaches it), with a warning to the user. `process_single_feed()` (previously private, `_process_single_feed`) was made public in `services/feeds.py` so it could be reused here without duplicating logic. `podvault schedule <podcast>` (CLI) is the equivalent of the web selector.

Validated with a real server: configured `planet_money` with a 1h interval + auto-download, started `podvault serve` with `PODVAULT_SCHEDULER_POLL_INTERVAL_SECONDS=2`, and without touching anything else the scheduler detected the podcast as "never checked" (due immediately), re-synced the feed, and enqueued a real `download_all` job — confirmed by `last_checked_at` updated in the DB and the job showing up in the `jobs` table with the correct `podcast_name`.

## Phase 9 — In-browser playback with metadata ✅ IMPLEMENTED

**Goal:** listen to an already-downloaded episode straight from the web, seeing cover art/title/description while it plays — without having to open it with another app.

**Design:**
- New routes: `GET /podcasts/{name}/episodes/{episode_id}/audio` (streams the mp3) and `GET /podcasts/{name}/episodes/{episode_id}/cover` (image — reuses the episode→podcast lookup from `id3_tags.py`).
- 💡 **Recommendation:** use Starlette's `FileResponse` for audio — supports `Range` requests out of the box, so the browser's progress bar can seek forward/back without downloading the whole file first.
- UI: a "▶" button per already-downloaded episode; a fixed player at the bottom of the page (`<audio controls>` + cover art + title + podcast), updated by JS on play click, without reloading the page.
- Only episodes with `status='downloaded'` are playable — no direct streaming from the original feed URL is added for undownloaded episodes; that's out of scope.

**Estimated effort:** 1-2 days.

**Risk:** low — it's additive, doesn't touch existing download/naming/scheduling logic.

**Actual implementation:** extracted `find_cover_image_path()` from `id3_tags.py` into a standalone, reusable function (it used to live inline inside `update_episode_id3_tags_from_db`) — so the ID3 tag and the web cover-art route use the exact same lookup rule, without duplicating logic. `services/playback.py` resolves audio/cover/metadata; found and fixed a real bug while writing it: it wasn't calling `add_download_columns_to_table()` before querying `custom_title`, so requesting playback for an episode of a freshly-added podcast (never downloaded, column didn't exist yet) threw a raw `sqlite3.OperationalError` instead of a clean 404.

Validated with a real 30MB mp3 served by a real server: a full request (200, `audio/mpeg`, exact size), a request with `Range: bytes=0-1023` (206 Partial Content, correct `Content-Range`) — confirms the `FileResponse` recommendation for seek/scrubbing actually works, not just in theory. Also tested cover art serving a real JPEG image, and the "▶" button rendering only for downloaded episodes.

---

## Recommended order and dependencies

```
Phase 0 (refactor + jobs)
  ├─> Phase 1 (folders)          ─┐
  ├─> Phase 2 (verification)      ├─> Phase 4 (web MVP) ─> Phase 5 (tests) ─> Phase 6 (Docker)
  └─> Phase 3 (ID3 tags)         ─┘

Phase 6 (Docker) ─> Phase 7 (configurable naming) ─> Phase 8 (scheduling) ─> Phase 9 (playback)
```

Phases 1, 2, and 3 don't depend on each other — they can be done in parallel or in whatever order you prefer, but they all depend on Phase 0. Phases 7, 8, and 9 also don't hard-depend on each other — the proposed order is by invasiveness (7 touches shared code, 8 adds new infrastructure, 9 is isolated), not by real blocking. If you'd rather do playback first because it's the most visible, reordering is no problem.

**Total estimate (Phases 0-6):** ~9-13 days of focused work. **Extension (Phases 7-9):** ~5-8 additional days.

## Status before the redesign

**Phases 0-9 complete.** Everything planned in this document was implemented, tested (automated tests + manual validation with real data and real servers at each phase), and documented in `README.md`/`ARCHITECTURE.md`.

---

# Extension: Full visual redesign (Phases 10-18)

A designer used Claude Design (a mockup tool separate from this repo) to produce a full visual + information-architecture redesign of the web interface, delivered as `Redesign/Redesign.dc.html` — a mockup file with its own templating syntax, not part of the real app; it's a spec to translate, not to port literally. `Redesign/ios-frame.jsx` and `Redesign/support.js` are scaffolding exclusive to the mockup tool (a decorative iPhone frame and Claude Design's own rendering runtime) — irrelevant to the implementation.

The redesign proposes a persistent-sidebar layout and restyles every existing screen (Library/dashboard, podcast detail, Settings, player), plus five screens that didn't exist before: episode detail, global Schedule, Activity (job history), Queue (live downloads), and Storage (disk usage + health checks).

**Decisions confirmed with the user before implementing:** build the full redesign (all 9 phases), Queue uses **estimated** speed/ETA (not live/byte-level) without touching `downloader.py`'s hot loop, and Storage's ID3-tag-completeness check runs as a **cached background job** (reusing the existing `JobRunner`) instead of scanning on every page load.

## Phase 10 — Design system foundation ✅ IMPLEMENTED

**Goal:** replace the plain `<header>` with a persistent-sidebar + topbar shell, using the mockup's dark palette and typography, as the foundation for every following screen.

**Actual implementation:**
- `base.html`: dark tokens (`--bg:#0b0d10`, `--panel:#14171c`, etc.), sidebar with nav (Library/Queue/Schedule/Activity/Storage/Settings) + a footer with the version, sticky topbar with a search box and a "+ Add podcast" button.
- **Self-hosted IBM Plex Sans/Mono**, not via the Google Fonts CDN (unlike the mockup) — the `.woff2` files were downloaded from `@fontsource` (jsdelivr) into `podvault/web/static/fonts/` and served via `StaticFiles` mounted at `/static` (`web/app.py`). Keeps the "no CDN dependency, local/home network use" property the app already had.
- **No live badges in the nav** (the mockup shows counters like "18" next to Queue) — omitted to avoid adding a DB query on every single page load, on every page. The mockup's "Library size" widget was omitted for the same reason.
- Reusable CSS components added: `.stat-card`, `.chip`/`.chip-ok`/`.chip-warn`/`.chip-dim`, `.pill`/`.pill-active`, `.cover-placeholder`, `.grid-cards`, `.toggle` (switch), `.pill-radio` (hidden radios + pill-styled label).
- `cover_gradient(name)` (Jinja global, `web/templating.py`): a stable diagonal gradient derived from the name's hash — same podcast, same color, no real cover art needed.
- The mockup's "Mobile" screen (a phone-layout preview inside a fake iPhone frame) was dropped entirely — translated into responsive media queries (`@media (max-width: 860px)`: collapsible sidebar with an overlay, 2-column grid) instead of a separate route/screen.
- The player bar was moved from `podcast_detail.html` to `base.html` (shared across every page).

## Phase 11 — Library restyle (`dashboard.html`) ✅ IMPLEMENTED

**Goal:** stat cards + cover-art grid + filter pills, replacing the old plain table.

**Actual implementation:**
- `PodcastInfo` (`services/results.py`) extended with `display_name`, `missing_episodes`, `total_size_bytes`, `podcast_image_url`, `scheduled`. `services/podcasts.py:_build_podcast_info()` computes this per podcast with two queries (one to `summary`, another aggregate one against the podcast's own table with `MAX(author_raw)`/`SUM(CASE WHEN status=...)`) — `author_raw` (the feed's original title before normalizing to a table name) is used as the "display name" since the schema never stored a "pretty" title separate from the table name.
- Filters (`?filter=All|Incomplete|Missing files|Scheduled`) applied in the route over the already-fetched list — no extra query per filter.
- "Scheduled" reuses `services.list_schedules()` (built for Phase 14, but needed here too) instead of N per-podcast schedule queries.
- "In queue" stat: `database/jobs.py:get_queue_totals()` — `SUM(total - progress)` over `pending`/`running` jobs (an approximation: a `pending` job that hasn't started iterating yet contributes 0 until its first progress tick).

## Phase 12 — Podcast detail restyle ✅ IMPLEMENTED

**Goal:** a bigger header (cover art/stats/actions), schedule as a side-card, episode rows with a status chip + action icons instead of a plain table.

**Actual implementation:**
- `services.get_podcast_info(name)` (new, reuses Phase 11's `_build_podcast_info()`) for the header's stats.
- **The rename form was moved off this page** — the pencil icon now leads to the new episode detail page (Phase 13), which is where renaming now lives.
- Episode filter (`?ep_filter=All|Downloaded|Missing|Pending`) applied before pagination.
- The schedule side-card was restyled with interval pills (hidden `input[type=radio]` + `.pill-radio-label`) and a toggle instead of a `<select>`/checkbox — same fields/endpoint as before.
- `EpisodeInfo` extended with `episode_number`, `published`, `size_bytes` for the new rows.

## Phase 13 — Episode detail (new) ✅ IMPLEMENTED

**Goal:** a new per-episode page with metadata, files on disk, and actions (play, copy path, re-download).

**Actual implementation:**
- New route `GET /podcasts/{name}/episodes/{episode_id}` → `services.get_episode()` (new, `EpisodeDetail` dataclass).
- **Duration**: read on demand with `mutagen.mp3.MP3(saved_path).info.length` for just that one file, with no caching and no library scan.
- **ID3 tags complete**: a new predicate `id3_tags_complete()` (`download/id3_tags.py`) — `TIT2` (title) + `TPE1` (artist) + `APIC` (cover art) present. Shared with the Storage job (Phase 16b) so both use the same definition of "complete".
- **"Reveal in folder" replaced with "Copy path"** (button + `navigator.clipboard.writeText`) — a process running on a server can't open the file explorer of whoever is looking at the page in their browser.
- **"Re-download"**: a per-episode re-download didn't exist; implemented by marking the episode as `missing` (`mark_episodes_missing`) and enqueuing a normal `download_all` job — note: this also retries any other pending/missing episode of that same podcast, not just this one (same behavior as the rest of the app, not a special case).
- The rename form (moved from Phase 12) and the new rename/redownload routes accept an optional `return_to` (validated as a same-origin relative path, never `//host` — rules out an open redirect) to return to this same page instead of always going back to the podcast's detail page.

## Phase 14 — Schedule (new, global) ✅ IMPLEMENTED

**Goal:** one row per podcast with interval, auto-download, last/next check — instead of having to visit each podcast individually.

**Actual implementation:**
- `database/scheduling.py:get_all_schedules()` — a single query against `summary` (which already has `check_interval_hours`/`auto_download`/`last_checked_at` from Phase 8, plus `num_episodes`/`num_episodes_downloaded`/`podcast_image_url` for each row's thumbnail) — no N per-podcast queries.
- `services.list_schedules()` wraps this in `ScheduleInfo` (extended with `podcast_name`/`total_episodes`/`downloaded_episodes`/`podcast_image_url`).
- New `scheduler.is_running()` — checks whether the scheduler thread is alive, for the "scheduler running/stopped" indicator.
- Each row is its own `<form>` that auto-submits when any pill/toggle changes (plain JS, no AJAX) — reuses the existing `POST /podcasts/{name}/schedule` endpoint unchanged, with a `return_to=/schedule` to come back to this screen instead of the podcast's detail page.

## Phase 15 — Activity (new) ✅ IMPLEMENTED

**Goal:** a filterable job history (Downloads/Syncs/Verify/Errors).

**Actual implementation:**
- Reuses `database/jobs.py:list_jobs()` almost unchanged; filters applied in the route.
- **A real gap I found and fixed:** `run_due_checks()` (scheduler, Phase 8) never created a `jobs` row when re-syncing a feed — automatic syncs were invisible in any history. Added `jobs_db.create_job()`/`mark_job_running()`/`mark_job_success()`/`mark_job_failed()` around that call (no `JobRunner`/thread-pool needed — it already runs on the scheduler thread, it just needed the audit-trail row).
- No new "warn" tier — just `success`/`failed` (✓/✕), so as not to invent a job state that doesn't exist in the schema.
- Added `DownloadResult.__str__()` (`services/results.py`) so a download job's `message` reads cleanly ("3 of 3 episode(s) downloaded") instead of the raw dataclass repr `JobRunner` used to store (`str(result)`).

## Phase 16 — Queue (new) ✅ IMPLEMENTED

**Goal:** a "downloading now" card with progress/speed/ETA, an "up next" list, and a "finished today" list — **estimated speed/ETA, zero changes to `downloader.py`.**

**Actual implementation:**
- `services/queue.py` (new, standalone module): `estimate_progress(created_at, progress, total, avg_episode_size_bytes)` — derives seconds-per-episode from `elapsed / episodes_completed` (using only the job's `created_at` plus the current wall clock) and bytes/second from the average size of that podcast's already-downloaded episodes. No new callback in the download loop, no extra SQLite writes per chunk. Every such value is labelled "~" in the UI.
- `services.preview_pending(podcast_name)` — the same query (same `published DESC` order, same `WHERE status IN (...)`) `download_all_episodes()` uses internally, but filtered on the Python side from `list_episodes()` instead of a second SQL query.
- **A non-trivial detail:** an episode's `status` in the DB only flips to `downloaded` in batches (`BATCH_COMMIT_SIZE=10`), not one at a time — so `preview_pending()` can run ahead of the job's real `progress` between commits. The route indexes into the pending list defensively (clamping the index) instead of assuming the two always stay in sync.
- Dedicated tests in `tests/test_queue_service.py`: the estimation math is fully deterministic against fake timestamps/sizes, with no real downloads.

## Phase 16b — Storage (new) ✅ IMPLEMENTED

**Goal:** a volume-usage bar + largest podcasts + three health checks.

**Actual implementation:**
- `services/storage.py` (new module): `get_disk_usage()` — a single `os.walk()` over `config.DOWNLOADS_FOLDER`, bucketed by extension (`.mp3`→audio, images/`.xml`→art & metadata, everything else→orphaned) + `shutil.disk_usage()` for the volume's free space. No caching in v1.
- Check (a) "downloaded but the file is missing": `services.verify_files(None, mark_missing=False)` — read-only; this screen's own button ("Verify all files") still uses the existing mutating path (`mark_missing=True` by default).
- Check (b) "outside the naming template": `services.migrate_layout(apply=False)` (already existed) — the count of `planned` moves is shown directly; "Show plan" is a native `<details>` element with no extra JS.
- Check (c) "ID3 tags complete" — **cached background job**, as agreed with the user: `job_type='id3_check'` run through the existing `JobRunner` (same pattern as `download_all`/`add_feed`), with the result stored in the job's `message` ("598 of 612 complete"). The screen reads the most recent job of that type via `database/jobs.py:get_latest_job_by_type()` — no new columns. "Recheck" enqueues a new job; "Retag" runs `update_episode_id3_tags_from_db()` (unchanged) as another background job, only for the incomplete files.

## Phase 17 — Player enhancement ✅ IMPLEMENTED

**Goal:** replace `<audio controls>` with custom controls (previous/-15s/play-pause/+30s/next, a seek bar, playback speed) — restyle + JS only, no new backend.

**Actual implementation:**
- Everything against the native `HTMLMediaElement` API (`currentTime`, `playbackRate`, `play()`/`pause()`) over the existing `/audio` (supports `Range`) and `/cover` routes, unchanged.
- Previous/next need the podcast's episode list on the client side: `podcast_detail.html` injects `window.PF_EPISODE_LIST` (an array of `{id, title}` for the current page's downloaded episodes, in display order) via an inline `<script>` — the same pattern the play buttons already used (`data-*` attributes).
- **An explicit limitation, not a bug to fix:** this app is a full-page-reload, no-SPA app, by design (see above). Navigating to another page destroys the `<audio>` element — the player bar is present on every page (via Phase 10's shell) but doesn't survive a page change. Keeping playback persistent across navigations would require client-side page swapping, deliberately out of scope.

## Phase 18 — Settings restyle + Environment panel ✅ IMPLEMENTED

**Goal:** restyle the already-functional Settings screen + a new read-only panel with environment variables.

**Actual implementation:**
- Restyle only — `get_settings()`/`update_settings()`/`preview()` unchanged.
- New "Environment" panel: reads directly from the `config` attributes that are already sourced from environment variables (`PODVAULT_DB_PATH`, `PODVAULT_DOWNLOADS_FOLDER`, `PODVAULT_FEEDS_FILE`, `PODVAULT_LOG_LEVEL`, `PODVAULT_LOG_FILE`, `PODVAULT_SCHEDULER_POLL_INTERVAL_SECONDS`, `PODVAULT_DEFAULT_DELAY_SECONDS`, `PODVAULT_DOWNLOAD_TIMEOUT`, `PODVAULT_DB_TIMEOUT`) — no new service function, they're passed straight from the route.
- Variable chips ("Insert: `{podcast_name}` `{episode_title}` ...") are now clickable and insert the token into the folder field. "Restore defaults" fills the fields with `config.DEFAULT_FOLDER_TEMPLATE`/`DEFAULT_FILENAME_TEMPLATE` on the client side (you still need to hit "Save" to persist) — there's no "reset" route on the backend, none was needed.

---

# Extension: Vault features (Phases 19-21)

Added at the user's request: three features aimed specifically at the app's role as a podcast *archive*, not just a downloader — the previous 18 phases made the app pleasant to browse and administer, but didn't address what happens when a file quietly corrupts on disk, or a podcast delists an old episode from its live feed.

**Decisions confirmed with the user before implementing:** track every episode ever seen, even after it disappears from the live feed, with an "in feed" / "removed from feed, local only" indicator; add real file-integrity checking (not just presence-checking); add an export capability.

## Phase 19 — Integrity (checksums) ✅ IMPLEMENTED

**Goal:** `verify_files()` only ever confirmed a downloaded episode's file still *exists* — it can't tell a healthy mp3 from one that's been silently truncated or corrupted (bit rot, a bad external edit, a failed disk). A vault needs to know its copies are still good, not just present.

**Actual implementation:**
- `podvault/hashing.py` (new, dependency-free leaf module — no `download/`/`database/` imports, so both can depend on it without a cycle): `compute_checksum(file_path)` — chunked SHA-256, safe for large audio files.
- Three new columns (via `add_download_columns_to_table()`, extended): `Checksum`, `checksum_verified_at`, `checksum_status` ('ok' / 'corrupt' / NULL-never-checked).
- **Baseline hashing at download time**, not as an afterthought: `download_all_episodes()`/`download_last_episode()` (`download/downloader.py`) hash a file immediately after a *fresh* download and stamp it as the baseline — cheap relative to the network time already spent. The "file already exists, skip re-download" branch deliberately does **not** re-hash (`update_download_info()`/`_commit_batch_updates()` use `Checksum = COALESCE(?, Checksum)` so passing no checksum leaves whatever was already stored untouched) — re-hashing a library's already-verified files on every re-sync would be wasted I/O for no new information.
- **Background verification job** (`services/integrity.py:run_integrity_check()`, `job_type='integrity_check'`, same `JobRunner`/cached-on-Storage-page pattern as the ID3 check): re-hashes every downloaded episode still on disk.
  - No stored checksum yet (downloaded before this column existed) → hash now, store as the new baseline, counted as "newly hashed" (not "verified" — there was nothing to compare against).
  - Matches → refresh `checksum_verified_at`, stays `'ok'`.
  - Differs → `checksum_status='corrupt'`, but the *original* checksum and `checksum_verified_at` are left alone, so the record still says when it was last known-good instead of quietly adopting the bad read as the new baseline.
- Episode detail page shows the integrity status (verified/CHECKSUM MISMATCH/not yet checked) and the first 16 hex chars of the stored SHA-256; Storage page gets a fourth health-check row ("Integrity (checksums)") with a "Check now"/"Recheck" button, mirroring the ID3 row exactly.

**Risk:** low — additive, no existing column semantics changed; the one thing worth watching is the hot download loop, which only gained one hash computation per freshly-downloaded file (no change to the byte-streaming/retry logic itself).

## Phase 20 — Feed retention: episodes are never deleted, just marked ✅ IMPLEMENTED

**Goal:** store every episode a feed has ever listed, even after the podcast removes it — with a visible "in feed" vs. "removed from feed, local only" indicator — instead of silently losing the record the moment a feed shrinks.

**A real, pre-existing bug this fixes:** `process_single_feed()` wrote every sync with `df_clean.to_sql(title, conn, if_exists='replace')` — a full table replace using *only* the rows in that fetch's parsed feed. `_preserve_download_state()` (Phase 4) already carried tracking columns forward for episodes still present, but any episode **missing** from a fresh parse — delisted by the podcast, or a feed that only ever exposes its last N items — was simply never written back and vanished from the table on the next sync. For an app whose whole point is not losing podcast history, this was the most consequential gap in the redesign so far.

**Actual implementation:**
- New `in_feed INTEGER DEFAULT 1` column (added alongside Phase 19's columns in the same `add_download_columns_to_table()` migration — existing rows default to "assumed present," safe since a real sync immediately corrects it either way).
- `services/feeds.py:_combine_with_removed_episodes()`: marks every episode in the freshly-parsed DataFrame `in_feed=1`, then unions in — verbatim, via `SELECT *`, with `in_feed=0` — any episode already stored under that podcast's table whose id isn't in this fetch. Nothing about a removed episode's row is touched beyond that one flag: its download status, saved path, checksum, and custom title all survive exactly as they were.
- If an episode's id reappears in a later fetch (the podcast un-deletes it, or a feed glitch resolves), it naturally shows up in the fresh DataFrame again and flips back to `in_feed=1` — no special-case code needed for "resurrection."
- `_TRACKING_COLUMNS` (the list `_preserve_download_state()` carries forward for episodes still present) was extended to include `Checksum`/`checksum_verified_at`/`checksum_status` — these didn't exist when that list was first written, and without adding them here, Phase 19's checksums would have been silently wiped on every re-sync of a still-live episode, which would have been a second, self-inflicted version of the exact bug this phase exists to fix.
- UI: a small purple "local only" tag next to the title on the podcast detail page's episode rows (shown only for removed episodes, to avoid cluttering the common case); the episode detail page shows an explicit chip either way ("in feed" / "removed from feed · local only"), per the user's request for a two-state indicator, not just a warning for the exception.

**Risk:** medium — this changes what gets written to the episode table on every single feed sync, which is about as core a code path as this app has. Covered by dedicated regression tests (episode removed → preserved + flagged; episode reappears → flag flips back; still-present episode's checksum survives a resync) on top of the existing Phase 4 regression tests, which all still pass unchanged.

## Phase 21 — Export ✅ IMPLEMENTED

**Goal:** a way to get a podcast's (or the whole library's) full inventory out of PodVault's own sqlite schema, in a portable form.

**Actual implementation:**
- `services/export.py`: `export_podcast_manifest(name)` / `export_library_manifest()` — JSON-serializable dicts covering every episode ever seen for a podcast (including `in_feed=False` ones), with id, titles, status, dates, on-disk path, size, and the full checksum/integrity state from Phase 19.
- Routes `GET /podcasts/{name}/export` and `GET /export`, both plain synchronous `JSONResponse`s with a `Content-Disposition: attachment` header so the browser downloads them directly — no background job needed, since building the manifest is just formatting already-fetched rows, not scanning files on disk.
- **Deliberately manifest-only, not a zip of the audio files.** The files already live in an organized, naming-template-driven folder tree on disk (Phase 7); duplicating potentially many GB of audio into an archive on every export by default isn't something to do without being asked. The manifest is what makes that existing folder tree self-describing and portable on its own — a listing you can keep independent of the database, or hand to another tool, without re-copying what it describes. A "bundle the files too" option is a reasonable future addition if it turns out to be wanted, not something this phase closes the door on.
- UI: an "Export library" link on the Storage page (next to "Verify all files"), and an "Export" link in each podcast detail page's action row.

**Risk:** low — read-only, additive, no existing routes or schema touched.

---

## Phase 22 — Job control, true single-episode download, and archive-health surfacing ✅ IMPLEMENTED

**Goal:** a second round of changes from the same Claude Design mockup (`Redesign/Redesign.dc.html`, updated after Phase 18 and explicitly marked "FASE 22" inline), building UX and functionality on top of everything shipped so far. Confirmed with the user: implement whatever the updated mockup specifies.

**Job control (cancel/pause/resume/skip one item), checked only between episodes:**
- `podvault/jobs.py`: new `JobControl` (a plain `threading.Event`/`Event`/`set`/`Lock` — no new infrastructure, consistent with this project's existing job-runner design) — `cancel()`/`is_cancelled()`, `pause()`/`resume()`/`is_paused()`, `skip(episode_id)`/`should_skip(episode_id)`, and `wait_while_paused()` (blocks the worker thread, waking early on cancel). `JobRunner.submit()` now creates one `JobControl` per job and passes it to every job target as an unconditional `job_control=` kwarg (every existing job target function was updated to accept and, where relevant, act on it); `JobRunner.cancel()/pause()/resume()/skip()/is_paused()` look it up by job id.
- **Deliberately coarse-grained, checked only at episode boundaries, never mid-file-transfer:** `download_all_episodes()`'s loop (`download/downloader.py`) checks `job_control.wait_while_paused()` / `is_cancelled()` / `should_skip(episode_id)` at the top of each iteration, before that episode's own download starts. This is why "Finished episodes are kept" is literally true in the UI copy — a cancel or pause can never abort a download already in flight, only prevent the *next* one from starting. Teaching the byte-streaming download itself to abort mid-transfer was explicitly out of scope.
- Routes: `GET /jobs/active/status` (the most recent job still pending/running, or `null` — registered *before* `GET /jobs/{job_id}/status` so the literal path segment "active" isn't swallowed by that route's `{job_id}` parameter), `POST /jobs/{job_id}/cancel`, `POST /jobs/{job_id}/pause`, `POST /jobs/{job_id}/resume`, `POST /jobs/{job_id}/skip/{episode_id}`, `POST /queue/cancel-all` (cancels every pending/running download-type job). All redirect back with a toast message via `_redirect_with_message()`.
- Queue page: per-active-job Pause/Resume toggle and "Stop this one" (cancel), a page-level "Cancel all", and a per-"up next" row ✕ ("Remove from queue") wired to the skip route.

**True single-episode download, replacing "mark missing + re-run the whole backlog":**
- `download/downloader.py:download_one_episode()` (new, standalone — deliberately *not* a refactor of `download_all_episodes`'s loop into a shared helper, to avoid regression risk in the heavily-tested bulk-download path) and `services/downloads.py:download_episode()` fetch exactly one episode by id, whether it's not-yet-downloaded, missing, or already downloaded (a real re-download).
- `POST /podcasts/{name}/episodes/{episode_id}/redownload` was repurposed to submit `job_type='download_episode'` via this new function instead of `mark_episodes_missing()` + a whole-podcast `download_all` — the old approach also re-downloaded any other pending/missing episodes of that podcast, which was never actually "just this one." Shared by the podcast-detail row's ↓ button (dynamically labelled "Download this episode" / "Re-download this episode") and the episode detail page's "Re-download" button.

**Job strip + toast notifications, replacing the `/jobs/{id}` full-page interstitial:**
- The old `GET /jobs/{id}` HTML page (`templates/job.html`) is retired; `GET /jobs/{id}/status` (JSON) stays. Every job-submitting route now redirects back to a sensible page (not a jobs page) carrying `msg`/`ok`/`tone`/`job_id`/`action_label`/`action_url` as query parameters (`_redirect_with_message()` extended to build this).
- **Entirely client-side** (`base.html`): a script reads those query parameters once on page load, shows a toast (auto-dismissing after 6s, with an optional action link), and cleans the URL via `history.replaceState`. A job strip polls `/jobs/{job_id}/status` — or `/jobs/active/status` when no `job_id` is in the current URL, so the strip survives navigating to a different page — and shows progress plus a Cancel button; it also fires a completion toast the first time it observes a tracked job leave `pending`/`running`. This was a deliberate scope reduction: no route needed its Jinja context threaded with toast/job fields, since the query string is the only channel used.

**Archive health surfaced where it matters, not just on Storage:**
- `PodcastInfo` gained `corrupt_episodes`/`local_only_episodes` (computed in `services/podcasts.py:_build_podcast_info()` via the same per-podcast aggregate query that already computed `missing_episodes`/`total_size_bytes`); `PodcastListResult` gained `total_corrupt`/`total_local_only`.
- Dashboard: a fifth stat card ("Archive health", linking to `/storage`) and small per-podcast-card badges ("N corrupt" / "N local") shown only when non-zero.
- Storage: `services/integrity.py:list_corrupt_episodes()` and `services/storage.py:list_local_only_episodes()` (new) let the page name specific corrupt files ("podcast/episode — last good Xd ago") instead of just a count, with a "Re-download all N" action (`POST /storage/redownload-corrupt`: groups corrupt episodes by podcast, marks each missing, submits one `download_all` job per affected podcast); a new expandable "N episode(s) kept after leaving their feed" health row lists each one, linking to its episode detail page.

**Other mockup-driven changes:**
- Podcast detail page: a pre-flight "scope confirm" dialog before "Download all pending" (count/`~estimated` size via the podcast's average episode size/`~estimated` time via a flat, clearly-labelled `ASSUMED_DOWNLOAD_SECONDS_PER_EPISODE` constant — there's no historical per-episode duration to derive a real rate from before a download has ever run, so this stays an honest guess rather than a fake measurement); episode-filter pills now show live counts; the pencil (rename) action expands an inline input+Save/Cancel row instead of navigating away.
- Accessibility pass: the player bar's controls (prev/±/play-pause/next/speed) and every row-level icon action (rename, single-episode download, up-next remove, cancel/pause/resume) are real `<button>` elements with `aria-label`/`title`, plus a `:focus-visible` outline rule added to `base.html`'s CSS for all interactive controls.

**Risk:** low-to-medium — the `job_control=` kwarg is now unconditionally passed to *every* job target (including ones that ignore it, like `add_feed`/`run_id3_check`/`run_integrity_check`), so every existing and new job-target function signature had to be checked/updated; missing one would fail loudly with a `TypeError` at job-submit time (caught during this phase's own test run, fixed before shipping). The single-episode download path is a standalone reimplementation, not shared code with the bulk path, by design.

---

## Phase 23 — Hardening: web login, non-MP3 audio, database backups ✅ IMPLEMENTED

**Goal:** close the three gaps that could cause real harm rather than mere inconvenience, found in a review after Phase 22 and the first public Docker Hub image (`martitoci/podvault`): the web UI had no login at all while Docker binds it to `0.0.0.0`; every episode was saved as `.mp3` and tagged with ID3 regardless of its real format; and the database — the one thing integrity checksums (Phase 19) don't protect — had no backups, despite a `ENABLE_BACKUP = True` flag in `config.py` that nothing read.

**1. Optional web login (`podvault/web/auth.py`).**
- HTTP Basic auth as plain ASGI middleware (not `BaseHTTPMiddleware`, so streamed audio responses pass through untouched), enabled when both `PODVAULT_USERNAME` and `PODVAULT_PASSWORD` are set; off by default so `podvault serve` on 127.0.0.1 is unchanged. Covers every route including static files, job-control POSTs, audio, and export; credentials compared with `secrets.compare_digest`.
- Basic auth over a login page + session: no session store, no login template, no CSRF work for the app's plain HTML forms, and browsers resend it automatically, including on the `<audio>` element's Range requests.
- Only one of the two variables set **fails closed** (500 with an explanation on every request) instead of silently running unprotected.
- New unauthenticated `GET /healthz`; the Dockerfile `HEALTHCHECK` now polls it instead of `/`, which would 401 once a login is on.
- `cli.serve()` warns when binding to a non-local host without a login. The Settings Environment panel shows the username and whether a password is set — never the password.

**2. Non-MP3 audio (`download/utils.py`, `download/id3_tags.py`).**
- `audio_extension_from_url()` picks the saved extension from the download URL's path (query string ignored; `.mp3` fallback when there's no recognised one). `find_existing_audio_file()` recognises an already-downloaded file under *any* audio extension, so pre-Phase-23 `.mp3`-named m4a files are reused, not downloaded a second time. All three download paths (`download_all_episodes`, `download_last_episode`, `download_one_episode`) go through `episode_audio_path()`.
- Tags: MP3 keeps ID3; `.m4a/.m4b/.mp4` get the equivalent MP4 atoms (`©nam`, `©ART`, `©alb`, `©day`, `trkn`, `disk`, `©gen`, `©cmt`, `covr`); other formats are archived but not tagged (`update_mp3_id3_tags` returns False without touching the file, and `id3_tags_complete` returns None so they don't show as "incomplete" on Storage). Public function names kept for API compatibility.
- Elsewhere: episode duration uses `mutagen.File` (any format), rename keeps the file's own extension, Storage's disk-usage buckets count every audio extension, and the `/audio` route sends a per-format MIME type (`audio/mp4`, `audio/ogg`, …) instead of always `audio/mpeg`.
- Deliberately not done: sniffing `Content-Type` after download for extension-less URLs (would mean renaming files post-download and changing `_download_with_retry`'s contract), and Vorbis/FLAC tag writing.

**3. Database backups (`podvault/database/backup.py`).**
- `backup_database()` uses SQLite's online backup API (consistent while the server or a job is writing) into `<db dir>/backups/<stem>-YYYYmmdd-HHMMSS.db`, then prunes to the newest `PODVAULT_BACKUP_KEEP` (7). Failures are logged, never raised — a backup must not block a sync.
- `maybe_backup_database()` throttles to one per `PODVAULT_BACKUP_INTERVAL_HOURS` (24) and is called before every `process_feeds_file()` (the path behind `add`/`process-feeds`, which rewrites tables wholesale) and on every scheduler tick under `serve` — so in practice a daily backup.
- `process_feeds_file()` used to `os.remove()` a database that failed validation; it now first copies it aside as `...-corrupt.db` (`preserve_corrupt_database()`, never rotated).
- `podvault backup` (forces one, ignoring the throttle) and `podvault backup --list`. Settings: `PODVAULT_BACKUP_ENABLED/DIR/KEEP/INTERVAL_HOURS`; the old `ENABLE_BACKUP` name is kept as an alias.

**Tests:** `test_auth.py`, `test_audio_formats.py`, `test_backup.py` (32 new). `isolated_env` now pins login/backup settings so a developer's own `PODVAULT_*` environment can't leak into the suite. MP4 tag writing was additionally verified by hand against a real AAC `.m4a` (generated with macOS `afconvert`), since the suite has no real audio fixtures.

**Risk:** low. Auth is opt-in. The extension change only affects new downloads (existing files are found by `find_existing_audio_file`, and `Saved_Path` already pointed at them). Backups only read the database.

---

## Phase 24 — OPML import/export and PodVault's own podcast feeds ✅ IMPLEMENTED

**Goal:** two features from the post-Phase-22 review: moving subscriptions in and out with the format every podcast app speaks, and letting podcast apps play the archive itself, including episodes the publisher deleted (Phase 20).

**OPML (`services/opml.py`).**
- Export the original subscriptions (`GET /export/opml`, `podvault opml-export [-o file]`) and import another app's export (`POST /import/opml` as an `import_opml` job, `podvault opml-import file`).
- Import is idempotent: only URLs not already in the feeds file are added and synced, invalid ones are skipped and reported, nested folders are flattened.
- Defensive parsing: 5 MB cap and DOCTYPE/ENTITY rejected before ElementTree sees the bytes.

**Own feeds (`services/local_feed.py`).**
- `GET /podcasts/{name}/feed.xml`: RSS 2.0 + iTunes namespace, downloaded on-disk episodes only, original GUIDs, enclosures served from PodVault (`/feed/audio?id=`), local artwork (`/feed/cover`), `itunes:block`. `GET /feeds.opml` lists every podcast's feed for a one-step subscribe.
- Episode ids are query parameters because GUIDs are often URLs, and a `/` can't be routed in a path segment.
- With the login on, feed URLs carry a `?token=`: `PODVAULT_FEED_TOKEN` or an HMAC of the password, so changing the password revokes old URLs. It's accepted only for GET/HEAD on feed paths.
- `PODVAULT_BASE_URL` sets the public address used in feed links (reverse proxies).
- UI: the podcast page shows the feed URL with a Copy button; the Library's "Add podcast" card gained OPML Import, "Export subscriptions (OPML)", and "Archive feeds (OPML)".

**Fixes found along the way.**
- `PODVAULT_FEEDS_FILE` was never read: everything defaulted to `'feeds.txt'` in the cwd, so in Docker the web UI wrote outside the data volume and the feed list was lost on rebuild. Now honoured everywhere (`read_feeds_file`/`append_to_feeds_file`/`process_feed_urls` in `services/feeds.py`).
- Feed items are sorted by parsed date: SQL `ORDER BY published` sorts RFC 822 strings alphabetically.
- Test suite: every test download made a real HTTP request (fetching the episode's original RSS XML from the fake `example.com` feed). An autouse fixture now stubs it, taking the suite from ~25s to ~5s and making it actually offline.

**Tests:** `test_opml.py`, `test_local_feed.py` (18 new). Also checked by hand: `feedparser` parses a generated feed cleanly (`bozo` False) with accented titles, URL-shaped GUIDs, HTML summaries, and tokenized enclosures.

**Risk:** low. Additive routes and services. The feeds-file change only alters behaviour where `PODVAULT_FEEDS_FILE` was set (Docker), which is the fix. Feed tokens expose read-only audio access to anyone holding a feed URL, by design.

---

## Current status

**Phases 0-24 complete.** Everything planned is implemented: the original web interface + Docker (0-6), configurable naming/scheduling/playback (7-9), the full visual redesign (10-18), the vault-specific features (19-21), job control + true single-episode download + archive-health surfacing (22), hardening (23: web login, non-MP3 audio, database backups), and OPML + PodVault's own podcast feeds (24). It's tested (231 automated tests: 98 before the redesign, 132 after it, 153 with the vault features, 181 with Phase 22, 213 with Phase 23, now 231 with Phase 24) and documented here and in `ARCHITECTURE.md`/`README.md`. There's no pending next step from this roadmap; any new work needs a new phase.
