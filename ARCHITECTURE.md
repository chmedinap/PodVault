# PodVault Architecture & Technical Documentation

This document provides a detailed technical overview of the PodVault application architecture, design decisions, and implementation details.

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture Design](#architecture-design)
3. [Module Structure](#module-structure)
4. [Data Flow](#data-flow)
5. [Database Schema](#database-schema)
6. [Key Algorithms](#key-algorithms)
7. [Error Handling](#error-handling)
8. [Performance Considerations](#performance-considerations)
9. [Extension Points](#extension-points)

## System Overview

PodVault is a modular Python application designed to:
- Parse RSS feeds and extract podcast metadata
- Store metadata in a SQLite database
- Download podcast episodes with organized file structure
- Update MP3 ID3 tags with complete metadata
- Provide statistics and management capabilities

### Core Principles

1. **Modularity**: Clear separation of concerns across modules
2. **Extensibility**: Easy to add new features or modify behavior
3. **Robustness**: Comprehensive error handling and retry logic
4. **Performance**: Optimized database operations and batch processing
5. **User Experience**: Clear feedback and progress reporting

## Architecture Design

### High-Level Architecture

```
┌─────────────────┐
│   User/CLI      │
└────────┬─────────┘
         │
         ▼
┌─────────────────────────────────────┐
│         Application Layer           │
│  (CLI / notebook.ipynb for devs)  │
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│         Business Logic Layer       │
│  ┌──────────┐  ┌──────────┐       │
│  │   Data   │  │ Download │       │
│  │ Collection│  │ Operations│     │
│  └──────────┘  └──────────┘       │
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│         Data Access Layer           │
│  ┌──────────┐  ┌──────────┐       │
│  │ Database │  │  File    │       │
│  │  Access  │  │  System  │       │
│  └──────────┘  └──────────┘       │
└─────────────────────────────────────┘
```

### Module Dependencies

```
podvault/
├── config.py (no dependencies)
├── cli.py → argparse, sqlite3, all other modules
├── data/
│   ├── collection.py → config, feedparser, requests
│   └── summary.py → pandas
├── database/
│   ├── connection.py → sqlite3, config
│   ├── queries.py → sqlite3
│   └── schema.py → sqlite3
└── download/
    ├── downloader.py → requests, sqlite3, config
    ├── id3_tags.py → mutagen, sqlite3
    ├── metadata.py → sqlite3
    └── utils.py → pathlib, sqlite3
```

**Note**: The `notebook.ipynb` file is provided for developers who want to use PodVault programmatically or for advanced use cases. End users should use the command-line interface (`cli.py`).

## Module Structure

### 1. Configuration (`config.py`)

Centralized configuration management. All constants are defined here to enable easy customization.

**Key Settings:**
- Database paths and timeouts
- Download settings (folder, delays, timeouts)
- Retry configuration (exponential backoff)
- Logging configuration
- File size limits

**Design Decision**: Centralized config allows for easy environment-specific configurations and future environment variable support.

### 2. Data Collection (`data/collection.py`)

Handles RSS feed parsing and data extraction.

#### Key Functions

**`normalize_feed_url(feed_url: str) -> str`**
- Detects Apple Podcast URLs vs RSS feeds
- Converts Apple Podcast links using iTunes Search API
- Returns normalized RSS feed URL

**`get_rss_from_apple_podcast(apple_url: str) -> str`**
- Uses iTunes Search API to resolve Apple Podcast links
- Extracts RSS feed URL from API response
- Handles API errors gracefully

**`collect_data(feed) -> pd.DataFrame`**
- Parses RSS feed using `feedparser`
- Extracts episode metadata:
  - Title, link, published date
  - Summary/description
  - Episode number, season number
  - Episode image URL
  - Podcast image URL
- Normalizes podcast name for database table
- Stores original podcast name in `author_raw`
- Returns cleaned DataFrame with all metadata

**`get_episode_xml_from_rss(rss_url, episode_id, ...) -> str`**
- Downloads RSS feed as XML
- Extracts specific episode entry XML
- Preserves original metadata format
- Used for archival purposes

#### Data Processing Flow

```
RSS Feed URL
    ↓
[feedparser.parse()]
    ↓
Parsed Feed Object
    ↓
[Extract Metadata]
    ↓
[Create DataFrame]
    ↓
[Normalize & Clean]
    ↓
DataFrame with Episode Data
```

### 3. Database Layer (`database/`)

#### Connection Management (`connection.py`)

**`get_db_connection(db_path: str) -> sqlite3.Connection`**
- Context manager for database connections
- Automatic connection cleanup
- Timeout configuration

**`is_valid_database(db_path: str) -> bool`**
- Validates database integrity
- Checks for corruption
- Returns True if database is usable

**`clean_dataframe_for_sqlite(df: pd.DataFrame) -> pd.DataFrame`**
- Removes unsupported data types
- Converts complex types to strings
- Handles NaN values
- Prepares DataFrame for SQLite storage

#### Query Utilities (`queries.py`)

Helper functions for common database operations:
- `table_exists()`: Check if table exists
- `summary_exists()`: Check if summary row exists
- `has_downloaded_episodes()`: Check download status
- `verify_downloaded_files_exist()`: Validate file existence

#### Schema Management (`schema.py`)

Handles database schema evolution and migrations.

**Key Functions:**
- `add_download_columns_to_table()`: Adds download metadata columns
- `add_author_raw_column_to_table()`: Adds original podcast name column
- `add_episode_metadata_columns_to_table()`: Adds episode/season number columns
- `add_podcast_image_url_to_summary()`: Adds image URL columns to summary
- `add_indexes_to_table()`: Creates performance indexes

**Design Decision**: Schema migrations are handled gracefully - columns are added only if they don't exist, ensuring backward compatibility.

### 4. Download Operations (`download/`)

#### Main Downloader (`downloader.py`)

**`download_all_episodes(conn, podcast_name, ...) -> Tuple[int, int]`**
- Retrieves episodes from database
- Downloads with retry logic
- Updates database status
- Downloads images
- Updates ID3 tags
- Returns (successful, failed) counts

**Download Process:**
```
1. Query database for undownloaded episodes
2. For each episode:
   a. Create folder structure
   b. Download MP3 file (with retry)
   c. Download episode image (if available)
   d. Update ID3 tags
   e. Download podcast image (once)
   f. Save episode metadata XML
   g. Update database status
3. Batch commit database updates
4. Update summary statistics
```

**`download_last_episode(conn, podcast_name, ...) -> bool`**
- Downloads only the most recent episode
- Simplified version of `download_all_episodes`
- Returns success/failure

**`_download_with_retry(url, file_path, ...) -> None`**
- Implements exponential backoff retry logic
- Handles transient network failures
- Logs retry attempts
- Raises exception after max retries

**`_download_image(image_url, image_path) -> bool`**
- Downloads image from URL
- Detects file extension from Content-Type or URL
- Saves with correct extension
- Returns success/failure

#### ID3 Tag Management (`id3_tags.py`)

**`update_mp3_id3_tags(file_path, title, artist, ...) -> bool`**
- Updates MP3 file ID3 tags using `mutagen`
- Maps database fields to ID3 tag fields:
  - `title` → TIT2 (Title)
  - `artist` → TPE1 (Artist)
  - `album` → TALB (Album)
  - `year` → TDRC (Date)
  - `track` → TRCK (Track Number)
  - `disc_number` → TPOS (Disc Number)
  - `comment` → COMM (Comment)
  - `cover_image_path` → APIC (Cover Art)
  - `genre` → TCON (Genre)
- Handles encoding (UTF-8)
- Returns success/failure

**`update_episode_id3_tags_from_db(conn, podcast_name, episode_id, file_path, episode_folder) -> bool`**
- Retrieves episode metadata from database
- Uses `get_effective_title()` (see `download/utils.py`) so a `custom_title` override wins over the feed's title, for the TIT2 tag
- Extracts year from published date
- Finds cover image via `find_cover_image_path()` (episode image preferred, podcast image fallback)
- Calls `update_mp3_id3_tags()` with all data
- Comprehensive error handling

**`find_cover_image_path(file_path, episode_folder) -> Optional[Path]`**
- Extracted from the above so the web player's cover-art route (Fase 9) uses the exact same search rule as ID3 tag writing — one place decides what an episode's cover is
- **Image Search Logic:**
  ```
  1. Search episode folder for "<mp3 base name>.*"
     - Finds any extension (.jpg, .png, .gif, .webp)
  2. If not found, search podcast base folder for "podcast_image.*"
     (episode_folder.parent.parent.parent — episode_folder is nested
     podcast/year/month/label, so 3 levels up reaches the podcast folder)
  3. Return the found path, or None
  ```

#### Metadata Updates (`metadata.py`)

**`update_summary(conn, podcast_name, ...) -> None`**
- Calculates podcast statistics using SQL aggregation
- Updates summary table with:
  - Total episodes
  - Downloaded episodes
  - Not downloaded episodes
  - Percentage downloaded
  - Last downloaded date
  - Podcast image URL
  - RSS feed URL
- Uses UPSERT pattern (UPDATE or INSERT)
- Optimized to avoid full table reads

#### Utilities (`utils.py`)

**`sanitize_filename(filename: str) -> str`**
- Removes invalid characters for file systems
- Handles length limits
- Preserves readability

**`parse_episode_date(conn, podcast_name, episode_id, published_date) -> datetime`**
- Parses various date formats
- Caches parsed dates in database
- Handles timezone information

**`show_podcast_summary(conn, podcast_names) -> dict`**
- Displays download statistics
- Formats output for readability

**Naming template engine** (added Fase 7 — see `services/settings.py` for the user-facing side)

- **`episode_template_variables(podcast_name, episode_date, episode_number, season_number, safe_episode_title) -> dict`** — builds the variable set available to templates: `podcast_name`, `episode_title`, `episode_number`, `season_number`, `year`, `month`, `day`, `date`, and the computed `episode_label` (zero-padded episode number, or the published date if absent)
- **`render_naming_template(template, variables) -> str`** — renders a template with `str.format_map`; raises `ValueError` (never `KeyError`/`IndexError`) for an unknown variable or a bad format spec, so callers can treat "the template is broken" as one case
- **`build_episode_folder_path(downloads_folder, podcast_name, episode_date, episode_number, season_number, safe_episode_title, folder_template=None) -> Path`**
  - Renders `folder_template` (defaults to `config.DEFAULT_FOLDER_TEMPLATE`, currently `{podcast_name}/{year}/{month}/{episode_label} - {episode_title}` — reproduces the fixed layout from Fase 1)
  - `/` in the rendered result is a folder separator; every segment is sanitized independently via `sanitize_filename()`, and bare `.`/`..` segments are dropped so a template can't escape `downloads_folder`
  - Shared by `download_all_episodes()`, `download_last_episode()`, `migrate_layout()`, and `rename_episode()` so all four agree on where an episode belongs
- **`build_episode_base_filename(podcast_name, episode_date, episode_number, season_number, safe_episode_title, filename_template=None) -> str`** — same idea for the mp3/image/xml base filename (defaults to `config.DEFAULT_FILENAME_TEMPLATE`, `{episode_title} - {podcast_name}`)

Templates are `str.format` only — never `eval` or arbitrary code — and are configured globally (not per-podcast) via `services.settings.update_settings()`, which validates a candidate template against two sample episodes (one with an episode/season number, one without) before saving, so a template that only works for numbered episodes is rejected up front instead of failing later on a real episode that lacks one.

## Data Flow

### Feed Processing Flow

```
1. User provides feed URL (RSS or Apple Podcast)
   ↓
2. normalize_feed_url() detects type and converts if needed
   ↓
3. collect_data() parses RSS feed
   ↓
4. Extract metadata:
   - Episode titles, links, dates
   - Summaries
   - Episode/season numbers
   - Image URLs
   ↓
5. Create DataFrame with normalized data
   ↓
6. Store in SQLite database (table = normalized podcast name)
   ↓
7. Update summary table with statistics
```

### Download Flow

```
1. Query database for undownloaded episodes
   ↓
2. For each episode:
   a. Create folder: downloads/podcast/year/month/episode_number - title/ (see build_episode_folder_path())
   b. Download MP3 → episode_folder/episode.mp3
   c. Download episode image → episode_folder/episode_image.jpg
   d. Update ID3 tags (reads from database, writes to MP3)
   e. Download podcast image → podcast_folder/podcast_image.jpg (once)
   f. Save metadata XML → episode_folder/episode_metadata.xml
   g. Update database: status = 'downloaded'
   ↓
3. Batch commit database updates (every 10 episodes)
   ↓
4. Update summary statistics
```

### ID3 Tag Update Flow

```
1. Retrieve episode data from database:
   - title, author_raw, episode_number, season_number
   - summary, published, episode_image_url
   ↓
2. Extract year from published date
   ↓
3. Find cover image:
   a. Search episode_folder for "episode_image.*"
   b. If not found, search podcast_folder for "podcast_image.*"
   ↓
4. Convert episode_number/season_number to integers
   ↓
5. Update MP3 file:
   - Open file with mutagen
   - Set all ID3 tag fields
   - Add cover art (if found)
   - Save file
```

## Database Schema

### Episode Tables (One per Podcast)

Each podcast gets its own table named after the normalized podcast name.

**Columns:**
- `title` (TEXT): Episode title
- `link` (TEXT): Episode URL
- `link_direct` (TEXT): Direct audio download URL
- `published` (TEXT): Publication date string
- `published_parsed` (TEXT): ISO format date
- `summary` (TEXT): Episode description
- `id` (TEXT): Episode unique identifier (usually GUID)
- `status` (TEXT): 'downloaded', 'not downloaded', or 'missing' (was downloaded, file gone from disk — see `verify_files()`)
- `author` (TEXT): Normalized podcast name
- `author_raw` (TEXT): Original podcast name (before normalization)
- `episode_number` (INTEGER): Episode number (nullable)
- `season_number` (INTEGER): Season number (nullable)
- `episode_image_url` (TEXT): Episode image URL (nullable)
- `custom_title` (TEXT): User-set display name override (nullable); see `get_effective_title()`
- `Saved_Path` (TEXT): File path where episode is saved
- `Size` (INTEGER): File size in bytes
- `File_name` (TEXT): Filename
- `Checksum` (TEXT): SHA-256 hex digest stamped at download time; the integrity baseline (Fase 19)
- `checksum_verified_at` (TEXT): ISO timestamp of the last successful integrity check against `Checksum`
- `checksum_status` (TEXT): `'ok'`, `'corrupt'`, or NULL (never checked)
- `in_feed` (INTEGER, default 1): whether this episode's id was present in the most recent feed sync; `0` means it was removed from the live feed but is still kept on record (Fase 20)

**Indexes:**
- `idx_status`: On `status` column (for fast queries)
- `idx_published`: On `published` column (for sorting)

### Summary Table

Single table with one row per podcast.

**Columns:**
- `name` (TEXT, PRIMARY KEY): Normalized podcast name
- `num_episodes` (INTEGER): Total episodes
- `num_episodes_downloaded` (INTEGER): Downloaded count
- `num_episodes_not_downloaded` (INTEGER): Not downloaded count
- `pct_episodes_downloaded` (REAL): Percentage downloaded
- `dataframe_name` (TEXT): DataFrame variable name
- `last_episode_downloaded_date` (TEXT): Last download date
- `podcast_image_url` (TEXT): Podcast cover art URL
- `rss_feed_url` (TEXT): RSS feed URL
- `check_interval_hours` (INTEGER, nullable): Auto-refresh interval; `NULL` = disabled (Fase 8 — see `database/scheduling.py`)
- `auto_download` (INTEGER, 0/1): Whether a scheduled check also triggers a `download_all` job
- `last_checked_at` (TEXT, nullable): ISO timestamp of the last scheduled check; `NULL` = never checked (treated as immediately due)

### App Settings Table

Single row (`id = 1`), global (not per-podcast) naming template configuration — see `database/settings.py` and `services/settings.py`.

**Columns:**
- `id` (INTEGER, PRIMARY KEY, `CHECK (id = 1)`): Always `1` — enforces the single-row invariant at the schema level
- `folder_template` (TEXT): See "Naming template engine" under Utilities, above
- `filename_template` (TEXT): Same

## Key Algorithms

### 1. Exponential Backoff Retry

```python
retry_delay = RETRY_DELAY_BASE
for attempt in range(MAX_RETRIES):
    try:
        # Attempt operation
        return success
    except TransientError:
        if attempt < MAX_RETRIES - 1:
            sleep(retry_delay)
            retry_delay = min(retry_delay * 2, RETRY_DELAY_MAX)
```

**Rationale**: Handles transient network failures gracefully without overwhelming servers.

### 2. Batch Database Commits

```python
pending_updates = []
for episode in episodes:
    # Process episode
    pending_updates.append(update)
    if len(pending_updates) >= BATCH_SIZE:
        conn.commit()
        pending_updates = []
conn.commit()  # Final commit
```

**Rationale**: Reduces disk I/O by batching commits. Significantly faster for large downloads.

### 3. SQL Aggregation for Statistics

Instead of:
```python
df = pd.read_sql_query("SELECT * FROM table", conn)
stats = df.groupby('status').count()
```

We use:
```python
cursor.execute("""
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN status = 'downloaded' THEN 1 ELSE 0 END) as downloaded
    FROM table
""")
```

**Rationale**: Calculates statistics in database, avoiding full table reads. 80-95% faster.

### 4. Image Search with Glob Patterns

```python
episode_image_files = list(episode_folder.glob("episode_image.*"))
```

**Rationale**: Finds images regardless of extension (.jpg, .png, .webp, etc.), making the system robust to different image formats.

## Error Handling

### Exception Hierarchy

1. **Network Errors** (`requests.RequestException`):
   - `requests.Timeout`: Network timeout
   - `requests.ConnectionError`: Connection failure
   - `requests.HTTPError`: HTTP error responses
   - **Handling**: Retry with exponential backoff

2. **Database Errors** (`sqlite3.Error`):
   - `sqlite3.OperationalError`: Table/column issues
   - `sqlite3.ProgrammingError`: SQL syntax errors
   - **Handling**: Log error, attempt reconnection, graceful degradation

3. **File System Errors** (`OSError`):
   - Permission errors
   - Disk full errors
   - **Handling**: Log error, skip file, continue processing

4. **Data Errors** (`ValueError`, `TypeError`, `IndexError`):
   - Invalid data formats
   - Missing required fields
   - **Handling**: Log warning, use defaults, continue processing

### Logging Strategy

- **DEBUG**: Detailed information for troubleshooting
- **INFO**: Important operations (downloads, updates)
- **WARNING**: Non-critical issues (missing images, failed retries)
- **ERROR**: Critical failures (database errors, download failures)

Logs include:
- Timestamps
- Function names
- Error messages
- Stack traces (for errors)

## Performance Considerations

### Database Optimizations

1. **Indexes**: Created on frequently queried columns (`status`, `published`)
2. **Batch Commits**: Reduces disk I/O by 90% for large downloads
3. **SQL Aggregation**: Statistics calculated in database, not in Python
4. **Connection Pooling**: (Future) Reuse database connections

### Download Optimizations

1. **Streaming Downloads**: Large files downloaded in chunks
2. **Retry Logic**: Handles transient failures without manual intervention
3. **Delay Between Downloads**: Prevents server overload
4. **Batch Processing**: Multiple operations grouped together

### Memory Optimizations

1. **DataFrame Chunking**: (Future) Process large feeds in chunks
2. **Lazy Loading**: Only load data when needed
3. **Garbage Collection**: Explicit cleanup of large objects

## Extension Points

### Adding New Metadata Fields

1. Add column to database schema in `schema.py`
2. Extract field in `collection.py` → `collect_data()`
3. Update ID3 tags in `id3_tags.py` → `update_mp3_id3_tags()`
4. Run migration to add column to existing tables

### Adding New Feed Sources

1. Create new function in `collection.py` similar to `get_rss_from_apple_podcast()`
2. Add detection logic in `normalize_feed_url()`
3. Integrate with `collect_data()`

### Custom Download Logic

1. Extend `downloader.py` with new functions
2. Follow existing patterns (retry logic, error handling)
3. Update database status appropriately

### Custom ID3 Tag Fields

1. Add field to `update_mp3_id3_tags()` function
2. Map to appropriate ID3 tag frame (see mutagen documentation)
3. Extract data in `update_episode_id3_tags_from_db()`

## Future Enhancements

### Planned Features

1. **Connection Pooling**: Reuse database connections
2. **Resume Downloads**: Resume partial downloads
3. **Checksum Validation**: Verify downloaded file integrity
4. **Progress Reporting**: Real-time download progress
5. **Web Interface**: Browser-based UI
6. **Scheduled Downloads**: Automatic periodic updates
7. **Episode Filtering**: Download by date range, keywords, etc.

### Performance Improvements

1. **Parallel Downloads**: Download multiple episodes simultaneously
2. **Caching**: Cache RSS feed responses
3. **Incremental Updates**: Only fetch new episodes
4. **Database Optimization**: Query plan analysis and optimization

## Testing Strategy

`tests/` (pytest, `pip install -e ".[test]"` then `pytest`) covers the services layer and the web routes built in ROADMAP.md Fase 5. Each test runs against an isolated temp DB/downloads folder (`isolated_env` fixture in `tests/conftest.py`) — network calls (`collect_data`, episode downloads) are mocked so the suite runs offline and fast.

- `test_validation.py` — URL/podcast-name validation, and `validate_and_quote_table_name()` as the SQL-injection guard
- `test_feeds_service.py` — feed ingestion, including a regression test for the re-sync data-loss bug fixed in Fase 4 (`_preserve_download_state()`)
- `test_downloads_service.py` — `require_confirmation=False` doesn't block, progress callback fires, status/Saved_Path update correctly; `download_episode()` fetches exactly one episode and leaves siblings untouched; cancelling/skipping mid-`download_all` keeps already-finished episodes and stops the rest (Fase 22)
- `test_verification_service.py` — `verify_files()` marks episodes `missing` when their file disappears
- `test_migration_service.py` — `migrate_layout()` dry-run vs `apply`, and idempotency on a second run
- `test_episodes_service.py` — `rename_episode()` on downloaded/not-yet-downloaded episodes, and clearing a custom title
- `test_jobs.py` — `JobRunner` success/failure/progress tracking; `JobControl`'s cancel/pause/resume/skip/`wait_while_paused` primitives, and cancel/pause/resume/skip reaching a real running job's control through the runner (Fase 22)
- `test_web_routes.py` — dashboard, podcast detail + pagination, the job-backed routes (add feed, download), `/settings` (page, update, preview), the Fase 22 job-control routes (`/jobs/active/status`, cancel/pause/resume/skip, `/queue/cancel-all`), and archive-health rendering (dashboard stat card, Storage's corrupt-file/local-only listings) via FastAPI's `TestClient`
- `test_naming_templates.py` — the template engine: variable substitution, unknown-variable/bad-format-spec rejection, path-traversal segment dropping, default template matches the pre-Fase-7 hardcoded layout
- `test_settings_service.py` — `services.settings`: defaults on a fresh DB, persistence, dual-sample validation (rejects templates that only work with a number present), `preview()` doesn't persist
- `test_scheduling_service.py` — `services.scheduling`: schedule persistence, `run_due_checks()` skips podcasts with no schedule or not yet due, re-syncs a due one and updates `last_checked_at`, submits a `download_all` job when `auto_download` is on
- `test_scheduler.py` — the background thread itself: starts, is idempotent to call twice, stops cleanly
- `test_playback_service.py` — `services.playback`: resolves audio/cover for a downloaded episode, respects `custom_title`, prefers episode cover over podcast cover, rejects not-yet-downloaded/missing/unknown episodes

Not covered (acceptable gaps for a single-user local app, per ROADMAP.md Fase 5): real network/RSS parsing, real ID3 tag writing against real MP3s, concurrent job execution.

## Scheduler (Fase 8)

`podvault/scheduler.py` is a single daemon thread with a plain sleep loop — same "no new infrastructure" reasoning as the `JobRunner` (Fase 0), not APScheduler/Celery. It calls `services.scheduling.run_due_checks()` every `config.SCHEDULER_POLL_INTERVAL_SECONDS` (default 300s, env `PODVAULT_SCHEDULER_POLL_INTERVAL_SECONDS`), which:

1. Reads every `summary` row with `check_interval_hours IS NOT NULL` (`database/scheduling.get_due_podcasts()`)
2. Filters to the ones where `now - last_checked_at >= check_interval_hours` (or `last_checked_at IS NULL`, i.e. never checked — always due)
3. Re-syncs each one via `services.feeds.process_single_feed()` — the exact same code path `add_feed` uses, so `_preserve_download_state()` (Fase 4) applies and a scheduled re-sync can't wipe out download state any more than a manual one can
4. Updates `last_checked_at` and, for podcasts with `auto_download` on, submits a `download_all` job through the same `JobRunner` background jobs use

**Deliberately not wired into FastAPI's app lifespan.** `cli.serve()` calls `start_scheduler()` explicitly before handing off to uvicorn, so merely importing the ASGI app — which is all `tests/test_web_routes.py`'s `TestClient` does — never starts a background thread against whichever database happened to be configured at import time. The tradeoff: `--reload` doesn't get a scheduler, since uvicorn's reloader runs the actual server in a subprocess the parent-process thread never reaches (that flag is documented as development-only anyway).

## Web Player (Fase 9)

`services/playback.py` → `get_playback_info(podcast_name, episode_id)` resolves an episode's audio file and cover image for the two streaming routes in `web/routes.py`:

- `GET /podcasts/{name}/episodes/{episode_id}/audio` — `FileResponse` over the mp3 at `Saved_Path`. Starlette's `FileResponse` handles `Range` requests on its own (verified with a real 30MB file: a `Range: bytes=0-1023` request correctly returns `206 Partial Content` with a matching `Content-Range` header), which is what lets the browser's `<audio>` element seek without downloading the whole file first.
- `GET /podcasts/{name}/episodes/{episode_id}/cover` — `FileResponse` over whatever `find_cover_image_path()` finds (episode image, falling back to podcast image); 404 if neither exists.

Both 404 (via `HTTPException`) if the episode isn't `status='downloaded'`, has no `Saved_Path`, or the file is missing on disk — there's no fallback to streaming from the original feed URL for undownloaded episodes; that's out of scope.

`podcast_detail.html` renders a "▶" button only for downloaded episodes (`data-podcast`/`data-episode`/`data-title` attributes carry what the JS needs); clicking it points a fixed player bar's `<audio>`/`<img>` at those two routes and calls `.play()` — no full-page reload, and no cross-page persistence (the player only lives for the current page load, same as the rest of this server-rendered UI).

## Web Interface Redesign (Fases 10-18)

A full visual + information-architecture redesign, translated from a Claude Design mockup (`Redesign/Redesign.dc.html`) into this app's existing FastAPI/Jinja2/vanilla-JS stack — no SPA framework, no build step, same philosophy as the rest of the web layer.

**Shell (`web/templates/base.html`, Fase 10):** a persistent sidebar (Library/Queue/Schedule/Activity/Storage/Settings) + sticky topbar replace the old plain `<header>`. IBM Plex Sans/Mono are self-hosted under `web/static/fonts/` and served via `StaticFiles` mounted at `/static` (`web/app.py`) rather than pulled from Google Fonts, keeping the "no CDN dependency" property the rest of the app already had. Nav badge counts and the mockup's "library size" widget were deliberately dropped — both would mean an extra DB query on every single page load, for every page, not just the one page that needs the number.

**New pages, each a thin route in `web/routes.py` over a service function:**

| Route | Service | Notes |
|---|---|---|
| `GET /podcasts/{name}/episodes/{episode_id}` | `services.episodes.get_episode()` | Duration (`mutagen`) and ID3-completeness are read on demand for that one file only — never cached, never scanned across the library. |
| `GET /schedule` | `services.scheduling.list_schedules()` → `database/scheduling.get_all_schedules()` | One query across `summary` for every podcast's schedule, episode counts, and cover image — not N per-podcast queries. |
| `GET /activity` | `database/jobs.list_jobs()` | Reused almost as-is; `services.scheduling.run_due_checks()` was extended to write a `jobs` row for scheduled syncs too (it never did before, so automatic re-syncs were invisible here). |
| `GET /queue` | `services.queue.estimate_progress()` + `services.downloads.preview_pending()` | See below — estimated, not live. |
| `GET /storage` | `services.storage.get_disk_usage()`, `services.verify_files(mark_missing=False)`, `services.migrate_layout(apply=False)` | See below — the ID3 check is the one part of this page that's cached. |

**Queue's speed/ETA are estimates, by design (Fase 16):** `services/queue.py:estimate_progress()` derives seconds-per-episode from `elapsed_time / episodes_completed` (using only the job's existing `created_at` timestamp and current wall-clock time) and bytes-per-second from a podcast's average already-downloaded episode size. Nothing was added to `downloader.py`'s streaming loop — no new progress callback granularity, no extra SQLite writes per chunk. Every value derived this way is labelled "~" in the UI. One wrinkle worth knowing: episode `status` in the database only flips to `downloaded` in batches (`BATCH_COMMIT_SIZE`, not per-file), so `preview_pending()`'s live list can briefly run ahead of a job's `progress` counter between batch commits — the route clamps its index into that list defensively rather than assuming the two always match exactly.

**Storage's ID3-completeness check is a cached background job (Fase 16b):** scanning every downloaded file's tags on every page load doesn't scale, so `services/storage.py:run_id3_check()` runs through the existing `JobRunner` (`job_type='id3_check'`, same mechanism as `download_all`/`add_feed`) and stores its result in the job's `message` column. The Storage page reads back the most recent job of that type (`database/jobs.get_latest_job_by_type()`) instead of re-scanning. "Complete" is defined once, in `download/id3_tags.py:id3_tags_complete()` (`TIT2` + `TPE1` + `APIC` all present), and shared by both this job and the episode detail page's per-file check — one definition, not two.

**Player (Fase 17):** the `<audio>` element and its native `controls` were replaced with custom prev/±15s/play-pause/±30s/next/seek-bar/speed controls, all just calls into the standard `HTMLMediaElement` API (`currentTime`, `playbackRate`) against the same `/audio`/`/cover` routes from Fase 9 — no backend changes. Prev/next read `window.PF_EPISODE_LIST`, a small JSON array `podcast_detail.html` inlines for the currently-visible page of downloaded episodes (same `data-*`-attribute pattern the play buttons already used). Consistent with this app's no-SPA design, the player does not persist across a page navigation — that's an accepted limitation, not a bug.

## Vault Features (Fases 19-21)

Three features that make PodVault behave like an archive rather than a downloader: it needs to know its copies are still good (integrity), it needs to not lose history the moment a feed changes (retention), and it needs a way to get data out of its own schema (export).

**Integrity checking (Fase 19).** `podvault/hashing.py` is a dependency-free leaf module (no imports from `download/` or `database/`) holding `compute_checksum()` — a chunked SHA-256 — so both of those packages can use it without a circular import. `download/downloader.py` stamps a baseline checksum immediately after a *fresh* download, in the same batched update that already writes `Saved_Path`/`Size`/`File_name`; the "file already exists, skip re-download" branch deliberately passes no checksum, and the SQL uses `Checksum = COALESCE(?, Checksum)` so that branch never overwrites an existing baseline with nothing. `services/integrity.py:run_integrity_check()` is the background job (`job_type='integrity_check'`, same `JobRunner`/cached-on-Storage-page pattern as `id3_check`) that re-hashes every downloaded file and compares it against the stored baseline: no baseline yet → hash and store it (counted as "newly hashed", not "verified"); match → refresh `checksum_verified_at`; mismatch → `checksum_status='corrupt'`, leaving the original checksum and verification timestamp untouched so the record still shows when the file was last known-good.

**Feed retention (Fase 20).** `process_single_feed()` writes with `df_clean.to_sql(title, conn, if_exists='replace')` — a full table replace using only the rows in that sync's parsed feed. Before this phase, an episode a podcast delisted (or that fell off a feed exposing only its last N items) was simply never written back on the next sync and vanished from the table — a real, previously-undetected instance of the exact data-loss pattern the Fase 4 bug fix was meant to prevent, just triggered by a shrinking feed instead of a re-processed one. `services/feeds.py:_combine_with_removed_episodes()` fixes this: it flags every episode in the fresh parse `in_feed=1`, then unions in — verbatim, via `SELECT *` — any episode already on record whose id isn't in this fetch, flagged `in_feed=0`. An episode's status, saved path, custom title, and checksum all survive completely untouched; if its id ever reappears in a later fetch, it naturally lands back in the fresh DataFrame and flips to `in_feed=1` with no special-case "resurrection" logic needed. `_TRACKING_COLUMNS` (the list `_preserve_download_state()` carries forward for episodes still present in a sync) had to be extended to include the three checksum columns from Fase 19 — without that, a still-live episode's checksum would have been silently wiped on every re-sync, which would have reintroduced the Fase 4 bug in miniature for exactly the data this phase exists to protect.

**Export (Fase 21).** `services/export.py` builds a JSON-serializable manifest (`export_podcast_manifest()` / `export_library_manifest()`) covering every episode ever seen for a podcast — including `in_feed=False` ones — with its checksum/integrity state, served directly as `JSONResponse` with `Content-Disposition: attachment` at `GET /podcasts/{name}/export` and `GET /export`; no background job needed since it's formatting already-fetched rows, not touching files on disk. Deliberately manifest-only, not a zip of the audio itself — the files already live in an organized, template-driven folder tree (Fase 7), and duplicating potentially many GB into an archive by default isn't something to do without being asked.

## Job Control, Single-Episode Download, and Archive Health (Fase 22)

A second round of changes from the same Claude Design mockup, updated after Fase 18 with a further batch marked "FASE 22." Four related pieces:

**`JobControl` (`podvault/jobs.py`).** One instance per submitted job — a plain `threading.Event`/`Event`/`set`/`Lock` combination, not a new subsystem, matching the "no new infrastructure" reasoning already behind `JobRunner` itself (Fase 0) and `scheduler.py` (Fase 8). `JobRunner.submit()` creates one and passes it to the job target as an unconditional `job_control=` kwarg alongside the existing `progress_callback=`; every job target function in the codebase (including ones that ignore it, like `add_feed`/`run_id3_check`/`run_integrity_check`) had to accept the parameter, since `_run()` always supplies it. `JobRunner.cancel()/pause()/resume()/skip()/is_paused()` look the control up by job id in an in-memory `_controls` dict, cleared when the job finishes — so an action against a finished (or unknown) job id is reported back as "already finished" rather than silently doing nothing.

Checked **only at episode boundaries**, never mid-file-transfer: `download_all_episodes()`'s loop (`download/downloader.py`) calls `job_control.wait_while_paused()` then checks `is_cancelled()`/`should_skip(episode_id)` at the very top of each iteration, before that episode's own download starts. A cancel or pause can therefore never abort a download already in flight — only the *next* one from starting — which is what makes "Finished episodes are kept" true rather than aspirational UI copy. Teaching the byte-streaming download call itself to abort mid-transfer was explicitly out of scope, both for safety (partial files) and because episode-granularity control is all five mockup actions (pause/resume/cancel/skip/cancel-all) actually need.

**True single-episode download.** Before this phase, "re-download episode X" meant `mark_episodes_missing()` + resubmitting a whole-podcast `download_all` job — which also silently re-downloaded any other already-pending/missing episode of that podcast, never actually "just this one." `download/downloader.py:download_one_episode()` is a standalone function (deliberately *not* extracted from `download_all_episodes`'s loop body, to avoid regression risk in the heavily-tested bulk path) that fetches exactly one episode by id, handling both the fresh-download and file-already-exists-refresh-tags branches the bulk loop handles. `services/downloads.py:download_episode()` wraps it with the same validation/error-shape as `download_all`/`download_last`. `POST /podcasts/{name}/episodes/{episode_id}/redownload` was repurposed to submit this as `job_type='download_episode'`, shared by the podcast-detail row's ↓ button (labelled "Download this episode" vs "Re-download this episode" depending on current status) and the episode detail page's "Re-download" button.

**Job strip + toast, replacing the `/jobs/{id}` interstitial.** The old `GET /jobs/{id}` HTML page (`templates/job.html`) is gone; `GET /jobs/{id}/status` (JSON) is unchanged and still polled. Every job-submitting route now redirects back to a normal page (not a jobs page) with `msg`/`ok`/`tone`/`job_id`/`action_label`/`action_url` appended to the query string by `_redirect_with_message()` (extended with keyword-only params for these). This is the only channel used — no route's Jinja context carries toast fields. `base.html`'s script reads those params once per page load, shows a toast (auto-dismissing after 6s, with an optional action link), then cleans the URL via `history.replaceState` so a refresh doesn't re-show it. A job strip polls `/jobs/{job_id}/status` if a `job_id` is known, or `GET /jobs/active/status` (the most-recently-created pending/running job, or `null`) otherwise — this is what lets the strip survive navigating to a page that carries no `job_id` in its URL. **Route registration order matters here:** `/jobs/active/status` is registered *before* `/jobs/{job_id}/status`, otherwise FastAPI's path matching would treat the literal segment `active` as a `{job_id}` value and return a 404 "job not found" instead of ever reaching the active-job handler.

**Archive health surfaced beyond Storage.** `PodcastInfo` (`services/results.py`) gained `corrupt_episodes`/`local_only_episodes`, computed in `_build_podcast_info()` (`services/podcasts.py`) by adding two more `SUM(CASE WHEN ...)` branches to the same per-podcast aggregate query that already computed `missing_episodes`/`total_size_bytes` — no new query, no new table scan. `PodcastListResult` gained `total_corrupt`/`total_local_only` properties for the dashboard's new fifth stat card. Two new listing functions back the Storage page's more specific health rows: `services/integrity.py:list_corrupt_episodes()` (every episode with `checksum_status='corrupt'`, across all podcasts, with its `checksum_verified_at` for a "last good Xd ago" label) and `services/storage.py:list_local_only_episodes()` (every downloaded episode with `in_feed=0`) — both loop `list_podcast_table_names()` the same way `list_downloaded_episodes()` already did for the ID3/integrity jobs. `POST /storage/redownload-corrupt` groups the corrupt list by podcast, marks each affected episode missing, and submits one `download_all` job per podcast (not a per-episode job — matches how every other bulk redownload in this app already works).

**Accessibility pass.** The player bar's controls and every row-level icon action (rename, single-episode download, up-next remove, pause/resume/cancel) were converted from `<span onclick>`-style elements to real `<button>`s with `aria-label`/`title`, and `base.html` gained a `:focus-visible` outline rule covering buttons, links, nav items, and pills — a plain CSS addition, no JS behavior change.

## Deployment (Docker)

`Dockerfile` builds a single `python:3.11-slim` image running `podvault serve --host 0.0.0.0`; `docker-compose.yml` maps one host directory (`./data`) onto `/app/data` in the container. There's no separate worker container — the same in-process `JobRunner` (Fase 0) that backs local usage runs inside the single container, which is enough for a single-user app with at most a couple of concurrent downloads.

**Environment variables** (`podvault/config.py`) let a deployment redirect state without editing code — this is what makes the volume mount meaningful, since the container's default paths (`/app/data/podvault_data.db`, `/app/data/downloads`, `/app/data/feeds.txt`) are set via `ENV` in the Dockerfile, not hardcoded:

| Variable | Overrides | Default |
|---|---|---|
| `PODVAULT_DB_PATH` | `config.DB_PATH` | `podvault_data.db` |
| `PODVAULT_DOWNLOADS_FOLDER` | `config.DOWNLOADS_FOLDER` | `downloads` |
| `PODVAULT_FEEDS_FILE` | `config.FEEDS_FILE` | `feeds.txt` |
| `PODVAULT_LOG_LEVEL` | `config.LOG_LEVEL` | `INFO` |
| `PODVAULT_LOG_FILE` | `config.LOG_FILE` | unset (console only) |
| `PODVAULT_SCHEDULER_POLL_INTERVAL_SECONDS` | `config.SCHEDULER_POLL_INTERVAL_SECONDS` | `300` |

Unset, every one of these matches the original hardcoded default exactly — running the CLI outside Docker is unaffected.

## Security Considerations

1. **Input Validation**: All user inputs validated and sanitized
2. **File Path Safety**: Prevents directory traversal attacks
3. **SQL Injection Prevention**: Parameterized queries only
4. **Network Security**: HTTPS for all downloads
5. **File Permissions**: Appropriate file system permissions

## Conclusion

PodVault is designed with modularity, extensibility, and robustness in mind. The architecture supports easy maintenance and future enhancements while providing a solid foundation for podcast management.

## User Interfaces

### Command-Line Interface (Primary)

The primary interface for end users is the command-line interface (`cli.py`). This provides:
- Simple, intuitive commands
- No programming knowledge required
- Full access to all features
- Clear error messages and help text

### Jupyter Notebook (Developer)

The `notebook.ipynb` file is provided for developers who want to:
- Use PodVault programmatically
- Customize workflows
- Perform advanced data analysis
- Integrate with other tools

**Note**: End users should use the CLI. The notebook is for development and advanced use cases only.

For user-facing documentation, see [README.md](README.md).

