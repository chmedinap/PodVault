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
10. [Future Enhancements](#future-enhancements)
11. [Testing Strategy](#testing-strategy)
12. [Scheduler (Fase 8)](#scheduler-fase-8)
13. [Web Player (Fase 9)](#web-player-fase-9)
14. [Web Interface Redesign (Fases 10-18)](#web-interface-redesign-fases-10-18)
15. [Vault Features (Fases 19-21)](#vault-features-fases-19-21)
16. [Job Control, Single-Episode Download, and Archive Health (Fase 22)](#job-control-single-episode-download-and-archive-health-fase-22)
17. [Hardening: Web Login, Audio Formats, Database Backups (Fase 23)](#hardening-web-login-audio-formats-database-backups-fase-23)
18. [OPML and PodVault's Own Feeds (Fase 24)](#opml-and-podvaults-own-feeds-fase-24)
19. [Deployment (Docker)](#deployment-docker)
20. [Security Considerations](#security-considerations)
21. [User Interfaces](#user-interfaces)

## System Overview

PodVault is a modular Python application for *archiving* podcasts. It is designed to:
- Parse RSS feeds (and Apple Podcast links) and extract podcast metadata
- Store metadata in a SQLite database
- Download podcast episodes into a configurable, template-driven folder structure
- Update MP3 ID3 tags with complete metadata
- Keep the archive trustworthy over time: SHA-256 integrity checks, retention of episodes that leave their feed, a portable JSON export, and rotating database backups
- Refresh feeds on a per-podcast schedule and run long work (syncs, downloads, scans) as controllable background jobs
- Move subscriptions in and out via OPML, and serve the archive back as per-podcast RSS feeds any podcast app can subscribe to
- Expose all of this through both a CLI and a server-rendered web interface

### Core Principles

1. **Modularity**: Clear separation of concerns across modules
2. **Extensibility**: Easy to add new features or modify behavior
3. **Robustness**: Comprehensive error handling and retry logic
4. **Performance**: Optimized database operations and batch processing
5. **User Experience**: Clear feedback and progress reporting

## Architecture Design

### High-Level Architecture

```
┌──────────────────────┐      ┌──────────────────────────────────┐
│   CLI (cli.py)       │      │  Web UI (web/: FastAPI + Jinja2) │
└──────────┬───────────┘      └───────┬──────────────────┬───────┘
           │                          │ submits          │
           │                          ▼                  │
           │            ┌──────────────────────────┐     │
           │            │ JobRunner + JobControl   │     │
           │            │ (jobs.py)                │     │
           │            │ Scheduler thread         │     │
           │            │ (scheduler.py)           │     │
           │            └────────────┬─────────────┘     │
           ▼                         ▼                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Service Layer (services/)                      │
│  feeds · downloads · episodes · podcasts · queue · scheduling   │
│  storage · integrity · verification · migration · export        │
│  playback · settings            (results.py = return types)     │
└──────────┬───────────────────────────────┬──────────────────────┘
           ▼                               ▼
┌──────────────────────────┐   ┌──────────────────────────────────┐
│ Data collection (data/)  │   │ Download operations (download/)  │
│ RSS parsing, Apple → RSS │   │ downloader, ID3 tags, metadata,  │
│ feed cache, statistics   │   │ naming templates, hashing.py     │
└──────────┬───────────────┘   └───────────────┬──────────────────┘
           ▼                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│ Data access: database/ (SQLite: per-podcast tables, summary,    │
│ app_settings, jobs) · file system (downloads/)                  │
└─────────────────────────────────────────────────────────────────┘
```

The CLI and the web routes are both thin: they validate/format and delegate to `services/`. The CLI calls services synchronously (with interactive confirmation where relevant); the web layer runs anything slow through the `JobRunner` so requests return immediately. The scheduler thread only exists under `podvault serve`.

### Module Dependencies

```
podvault/
├── config.py → os (no internal dependencies)
├── hashing.py → hashlib (leaf module, no internal dependencies)
├── validation.py → stdlib only
├── logging_config.py → config
├── cli.py → argparse, services, validation; lazily uvicorn + scheduler (for serve)
├── jobs.py → database.jobs, database.connection, ThreadPoolExecutor
├── scheduler.py → config; lazily services.scheduling
├── data/
│   ├── collection.py → config, validation, feedparser, requests
│   └── summary.py → pandas
├── database/
│   ├── connection.py → sqlite3, config
│   ├── queries.py, schema.py, transactions.py → sqlite3
│   ├── jobs.py → job history table
│   ├── scheduling.py → summary-table schedule columns
│   ├── settings.py → app_settings table
│   └── backup.py → sqlite3 online backup API, config
├── download/
│   ├── downloader.py → requests, database, data.collection, hashing
│   ├── id3_tags.py → mutagen, sqlite3
│   ├── metadata.py → sqlite3
│   └── utils.py → pathlib, sqlite3 (naming template engine)
├── services/ → data, database, download, validation, hashing
└── web/
    ├── app.py → fastapi, StaticFiles (/static), auth middleware, /healthz
    ├── auth.py → config (optional HTTP Basic login, plain ASGI middleware)
    ├── routes.py → services, jobs, scheduler, database.jobs
    └── templating.py → jinja2 (+ size/duration filters)
```

## Module Structure

### 1. Configuration (`config.py`)

Centralized configuration management. All constants are defined here to enable easy customization.

**Key Settings:**
- Database paths and timeouts
- Download settings (folder, delays, timeouts)
- Retry configuration (exponential backoff)
- Logging configuration
- File size limits

**Design Decision**: Centralized config allows for easy environment-specific configurations. Deployment-relevant values (paths, timeouts, delay, log level/file, scheduler poll interval) are read from `PODVAULT_*` environment variables via small `_env_str`/`_env_int`/`_env_float` helpers, falling back to the original hardcoded defaults — see [Deployment (Docker)](#deployment-docker) for the full list. User-facing settings that change at runtime (naming templates, per-podcast schedules) live in the database instead, not here.

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
   b. Download audio file (with retry; extension from the URL — .mp3, .m4a, ...)
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

#### Tag Management (`id3_tags.py`)

**`update_mp3_id3_tags(file_path, title, artist, ...) -> bool`**
- Updates the file's tags using `mutagen`, dispatched by extension (Fase 23): ID3 for `.mp3`, MP4 atoms for `.m4a/.m4b/.mp4` (`_update_mp4_tags()` — `©nam`, `©ART`, `©alb`, `©day`, `trkn`, `disk`, `©gen`, `©cmt`, `covr`); returns False without touching the file for other formats. Name kept for API compatibility
- ID3 mapping for MP3:
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

### 5. Service Layer (`services/`)

The single place business logic lives, shared by the CLI and the web routes (extracted from `cli.py` in Fase 0). Each module returns plain dataclasses from `services/results.py` (`PodcastInfo`, `EpisodeInfo`, `DownloadResult`, ...) rather than printing, so the same call can back a terminal command, an HTML page, or a background job. `services/__init__.py` re-exports the public functions, so callers use `services.download_all(...)`, `services.verify_files(...)`, etc.

| Module | Responsibility |
|---|---|
| `feeds.py` | `add_feed`, `process_feeds_file`, `process_single_feed`; preserves download state and retains removed episodes across re-syncs |
| `downloads.py` | `download_all` / `download_last` / `download_episode` (single episode), `preview_pending` |
| `episodes.py` | List/get episodes (incl. on-demand duration + ID3 completeness), `rename_episode` |
| `podcasts.py` | `list_podcasts`, `get_podcast_info`, `get_status` — per-podcast aggregates in one SQL query |
| `queue.py` | `estimate_progress` — estimated speed/ETA for the Queue page |
| `scheduling.py` | Per-podcast schedules, `run_due_checks` (called by the scheduler thread) |
| `storage.py` | Disk usage, ID3-check/retag jobs, local-only (removed-from-feed) episodes |
| `integrity.py` | Checksum re-verification job, `list_corrupt_episodes` |
| `verification.py` | `verify_files` — mark episodes `missing` when their file is gone |
| `migration.py` | `migrate_layout` — move existing files to the current naming template (dry run by default) |
| `export.py` | JSON manifests per podcast / whole library |
| `playback.py` | Resolve audio + cover paths for the web player |
| `settings.py` | Naming templates: get, validate + update, preview |
| `opml.py` | OPML export (original feeds, or PodVault's own) and defensive OPML import |
| `local_feed.py` | PodVault's own RSS feed per podcast, built from downloaded episodes |

Every function that can run as a background job accepts `progress_callback=` and `job_control=` keyword arguments (see [Fase 22](#job-control-single-episode-download-and-archive-health-fase-22)).

### 6. Jobs, Scheduler, and Web Layer

- **`jobs.py`** — `JobRunner` wraps a `ThreadPoolExecutor`, persists every job (type, status, progress, message) to the `jobs` table via `database/jobs.py`, and gives each job a `JobControl` for cancel/pause/resume/skip. `get_job_runner()` returns the process-wide instance.
- **`scheduler.py`** — one daemon thread that periodically calls `services.scheduling.run_due_checks()`; see [Scheduler (Fase 8)](#scheduler-fase-8).
- **`web/app.py`** — builds the FastAPI app, installs the optional login middleware (`web/auth.py`), mounts `/static` (self-hosted IBM Plex fonts), includes the router, and serves the unauthenticated `/healthz`.
- **`web/routes.py`** — sync route handlers over the service layer; anything slow is submitted to the `JobRunner` and the handler redirects back with a toast message. Pages: Library (`/`), podcast and episode detail, Queue, Schedule, Activity, Storage, Settings, plus JSON job-status/control endpoints, audio/cover streaming, and export.
- **`web/templating.py` + `web/templates/`** — Jinja2 environment with size/duration formatting filters; `base.html` holds the shared shell (sidebar, topbar, toast, job strip, player bar) and all CSS/JS — no SPA framework, no build step.

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
   b. Download audio → episode_folder/episode.<ext> (ext from the URL; an existing file under any audio extension is reused)
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

### Jobs Table

One row per background job submitted through the `JobRunner` (downloads, feed syncs including scheduled ones, ID3/integrity checks) — the source for the Activity page, the Queue page, and `/jobs/{id}/status`. See `database/jobs.py`.

**Columns:**
- `id` (TEXT, PRIMARY KEY): Job UUID
- `job_type` (TEXT): e.g. `add_feed`, `download_all`, `download_episode`, `id3_check`, `id3_retag`, `integrity_check`
- `podcast_name` (TEXT, nullable)
- `status` (TEXT): `pending` → `running` → `success` or `failed`
- `progress` / `total` (INTEGER): Progress counters reported by the job's `progress_callback`
- `message` / `error` (TEXT): Result summary (cached scan results for ID3/integrity checks) or failure details
- `created_at` / `updated_at` (TEXT): ISO timestamps (used for Queue's estimated speed/ETA)

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

Already shipped from earlier versions of this list: checksum validation (Fase 19), progress reporting via the job strip/Queue page (Fases 16, 22), the web interface (Fase 4 onward), scheduled downloads (Fase 8), and RSS feed response caching (`RSS_CACHE_*` in `config.py`). Still open:

1. **Connection Pooling**: Reuse database connections
2. **Resume Downloads**: Resume partial downloads (and cancel mid-transfer — job control currently acts only between episodes)
3. **Episode Filtering**: Download by date range, keywords, etc.
4. **Byte-level progress**: Real (not estimated) speed/ETA on the Queue page

### Performance Improvements

1. **Parallel Downloads**: Download multiple episodes simultaneously
2. **Incremental Updates**: Only fetch new episodes
3. **Database Optimization**: Query plan analysis and optimization

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
- `test_queue_service.py` — `services.queue.estimate_progress()` speed/ETA math and `preview_pending()`, deterministic against fake timestamps/sizes (Fase 16)
- `test_storage_service.py` — disk-usage bucketing, largest-podcasts aggregate, and the ID3-completeness job targets (Fase 16b)
- `test_hashing.py` — `compute_checksum()` matches `hashlib` (Fase 19)
- `test_integrity_service.py` — `run_integrity_check()`: baseline hashing, verification, and corruption detection that preserves the last-known-good checksum (Fase 19)
- `test_export_service.py` — `services.export` manifests, including removed-from-feed episodes and checksum state (Fase 21)
- `test_auth.py` — the optional login: off by default, every route (pages, POSTs, static, export) challenged when on, wrong/malformed credentials rejected, `/healthz` always public, half-configured login fails closed, password never rendered (Fase 23)
- `test_audio_formats.py` — extension from URL, existing-file reuse across extensions, `.m4a`/`.ogg` downloads, rename keeps the extension, per-format MIME on `/audio`, tag-writer dispatch (Fase 23)
- `test_backup.py` — consistent copy, `BACKUP_DIR` override, rotation, throttling, disabled flag, backup before a re-sync, corrupt-database preservation (Fase 23)
- `test_opml.py` — nested/case-insensitive outline parsing, DOCTYPE/size/non-OPML rejection, idempotent import that skips invalid URLs, export + round trip, web import/export, and the `PODVAULT_FEEDS_FILE` regression (Fase 24)
- `test_local_feed.py` — only on-disk downloads listed, date ordering, enclosures resolving for URL-shaped GUIDs, base-URL override, local podcast cover, feed token (works for feed paths only, rotates with the password), archive OPML, feed URL on the podcast page (Fase 24)

`isolated_env` also pins `FEEDS_FILE`/`WEB_USERNAME`/`WEB_PASSWORD`/`BACKUP_DIR`/`BACKUP_ENABLED`, so a developer's own `PODVAULT_*` environment can't change test outcomes, and an autouse fixture stubs the per-download fetch of each episode's original RSS XML — before Fase 24 every test download made a real request to the fake `example.com` feed, which made the suite ~6× slower and not actually offline. 231 tests in total, all offline, ~5s.

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

## Hardening: Web Login, Audio Formats, Database Backups (Fase 23)

Three fixes for gaps that could cause real harm rather than inconvenience.

**Optional web login (`web/auth.py`).** `BasicAuthMiddleware` is plain ASGI middleware (not Starlette's `BaseHTTPMiddleware`, which buffers/wraps streaming responses — audio playback goes straight through this way). It reads `config.WEB_USERNAME`/`WEB_PASSWORD` per request (monkeypatchable in tests); with neither set it's a pass-through, so local `podvault serve` is unchanged. With both set, every request except `PUBLIC_PATHS` (`/healthz`) needs a matching `Authorization: Basic` header, compared with `secrets.compare_digest` on both halves. Exactly one set is a misconfiguration and fails closed with a 500 rather than silently serving unprotected. HTTP Basic was chosen over a login form + session cookie: no session store, no login template, no CSRF work for the plain HTML forms, and browsers resend credentials automatically — including for the `<audio>` element's Range requests. It isn't encryption, so internet-facing deployments need an HTTPS reverse proxy. The Dockerfile `HEALTHCHECK` polls `/healthz` (polling `/` would turn unhealthy as soon as a login is on), and `cli.serve()` warns when binding a non-local host with no login.

**Non-MP3 audio.** `download/utils.py` gained `AUDIO_MIME_TYPES`/`AUDIO_EXTENSIONS`, `audio_extension_from_url()` (URL path suffix, query ignored, `.mp3` fallback), `find_existing_audio_file()` (existing audio under *any* extension — so pre-Fase-23 files saved as `.mp3` for m4a URLs are reused, not fetched again), and `episode_audio_path()` combining the two, used by all three download functions. Tag writing dispatches by extension (see Tag Management above); `id3_tags_complete()` checks MP4 atoms for MP4 files and returns None for untagged formats so the Storage ID3 check doesn't count them as incomplete. Duration (`services/episodes.py`) uses `mutagen.File`, rename keeps the audio file's own suffix, `services/storage.py` buckets every audio extension, and `/audio` sends the per-format MIME type. Not done: `Content-Type` sniffing for extension-less URLs (they still save as `.mp3`), Vorbis/FLAC tags.

**Database backups (`database/backup.py`).** `backup_database()` uses `sqlite3.Connection.backup()` — consistent even mid-write, unlike a file copy — into `get_backup_dir()` (`config.BACKUP_DIR`, else `backups/` beside the DB) as `<stem>-YYYYmmdd-HHMMSS.db`, then `_rotate()`s to the newest `BACKUP_KEEP`. Errors are logged, never raised. `maybe_backup_database()` adds the `BACKUP_ENABLED` switch and a `BACKUP_INTERVAL_HOURS` throttle based on the newest backup's timestamp; it's called at the start of `services.feeds.process_feeds_file()` (the path behind add/process-feeds, which rewrites tables with `to_sql(if_exists='replace')`) and on every scheduler tick. That same function used to delete a database failing `is_valid_database()` outright; `preserve_corrupt_database()` now copies it aside first as `...-corrupt.db`, which rotation never prunes. `podvault backup` forces one; `--list` lists them.

## OPML and PodVault's Own Feeds (Fase 24)

**OPML (`services/opml.py`).** `export_opml()` writes OPML 2.0 with one `<outline type="rss">` per podcast, from `summary.rss_feed_url`. With `own_feeds=True` and a `feed_url_for` callback it lists PodVault's own feed URLs instead; that's what `/feeds.opml` serves, so one import in a podcast app subscribes to the whole archive. `parse_opml()` walks every `outline` at any depth (apps nest feeds in folders) and matches `xmlUrl` case-insensitively. Because OPML comes from other software or the internet, it first rejects anything over 5 MB and anything with a DOCTYPE/ENTITY declaration. Real OPML never has one, and both billion-laughs and external-entity attacks need one. `import_opml()` validates each URL, appends only new ones to the feeds file, and syncs just those through `process_feed_urls()`. The web route parses once up front so a bad file gets an immediate error toast instead of a failed job, then runs the import as an `import_opml` job.

**Feeds-file fix.** `config.FEEDS_FILE` (`PODVAULT_FEEDS_FILE`) was defined but never read. `add_feed`, `process_feeds_file`, and the CLI all defaulted to `'feeds.txt'` in the working directory, so in Docker the web UI's "add feed" wrote `/app/feeds.txt`, outside the `/app/data` volume, and the list vanished on the next image rebuild. `services/feeds.py` now defaults to `config.FEEDS_FILE` and gained `read_feeds_file()`, `append_to_feeds_file()`, and `process_feed_urls()`. The last one is the body of `process_feeds_file()` (corrupt-DB handling, backup, per-URL sync) extracted so the OPML import can sync only the URLs it added.

**Own feeds (`services/local_feed.py`).** `build_podcast_feed(name, urls)` emits RSS 2.0 with the iTunes and Atom namespaces. Items are the podcast's `downloaded` episodes whose `Saved_Path` still exists, since an enclosure that 404s makes many apps flag the whole feed. They're sorted newest-first in Python on the parsed date: `ORDER BY published` sorts RFC 822 strings alphabetically, which is wrong, and the rest of the app has the same quirk. Each item has the original GUID (`isPermaLink="false"`, so apps that already know the episode match it), RFC 822 `pubDate`, `enclosure` with length and per-format type, `itunes:episode`/`season`, and episode art when there's episode-specific art. The channel image prefers the local `podcast_image.*`, falling back to the remote URL. The feed sets `itunes:block` so directories don't list a private archive. There's no `itunes:duration`, because reading every file's header on each fetch doesn't scale. The service doesn't build URLs. The web layer passes a `FeedUrls` of callbacks, because only it knows the public base (`PODVAULT_BASE_URL`, else the request's) and the token.

**Routes.** `GET /podcasts/{name}/feed.xml`, `/podcasts/{name}/feed/audio?id=`, `/podcasts/{name}/feed/cover[?id=]`, and `/feeds.opml`. The episode id is a query parameter, not a path segment. Feed GUIDs are very often URLs (`https://example.com/?p=123`), and Starlette routes on the *decoded* path, so an encoded `/` inside `{episode_id}` can't match. The older `/episodes/{episode_id}/...` routes still have that limitation.

**Feed token (`web/auth.py`).** Podcast apps can't complete a browser Basic-auth prompt reliably, and many don't send credentials on enclosure requests at all. So when the login is on, `feed_token()` is `PODVAULT_FEED_TOKEN`, or else `HMAC-SHA256(password, "podvault-feed-v1")[:32]`. Deriving it from the password means rotating the password revokes every feed URL handed out, with no token table to manage. The middleware accepts `?token=` (constant-time compare) only for GET/HEAD on paths matching `_FEED_PATH`; tests check that the token opens nothing else, including the podcast's HTML page, `/export`, and POSTs. The token sits in feed URLs, so it can end up in proxy logs and app sync services, which is why it grants read access to the feeds only.

## Deployment (Docker)

`Dockerfile` builds a single `python:3.11-slim` image running `podvault serve --host 0.0.0.0`; `docker-compose.yml` maps one host directory (`./data`) onto `/app/data` in the container. There's no separate worker container — the same in-process `JobRunner` (Fase 0) that backs local usage runs inside the single container, which is enough for a single-user app with at most a couple of concurrent downloads.

**Environment variables** (`podvault/config.py`) let a deployment redirect state without editing code — this is what makes the volume mount meaningful, since the container's default paths (`/app/data/podvault_data.db`, `/app/data/downloads`, `/app/data/feeds.txt`) are set via `ENV` in the Dockerfile, not hardcoded:

| Variable | Overrides | Default |
|---|---|---|
| `PODVAULT_DB_PATH` | `config.DB_PATH` | `podvault_data.db` |
| `PODVAULT_DB_TIMEOUT` | `config.DB_TIMEOUT` | `30.0` |
| `PODVAULT_DOWNLOADS_FOLDER` | `config.DOWNLOADS_FOLDER` | `downloads` |
| `PODVAULT_FEEDS_FILE` | `config.FEEDS_FILE` | `feeds.txt` |
| `PODVAULT_DEFAULT_DELAY_SECONDS` | `config.DEFAULT_DELAY_SECONDS` | `5` |
| `PODVAULT_DOWNLOAD_TIMEOUT` | `config.DOWNLOAD_TIMEOUT` | `30` |
| `PODVAULT_LOG_LEVEL` | `config.LOG_LEVEL` | `INFO` |
| `PODVAULT_LOG_FILE` | `config.LOG_FILE` | unset (console only) |
| `PODVAULT_SCHEDULER_POLL_INTERVAL_SECONDS` | `config.SCHEDULER_POLL_INTERVAL_SECONDS` | `300` |
| `PODVAULT_USERNAME` / `PODVAULT_PASSWORD` | `config.WEB_USERNAME` / `config.WEB_PASSWORD` | unset (no login) |
| `PODVAULT_BACKUP_ENABLED` | `config.BACKUP_ENABLED` | `true` |
| `PODVAULT_BACKUP_DIR` | `config.BACKUP_DIR` | unset (`backups/` next to the DB → `/app/data/backups`) |
| `PODVAULT_BACKUP_KEEP` | `config.BACKUP_KEEP` | `7` |
| `PODVAULT_BACKUP_INTERVAL_HOURS` | `config.BACKUP_INTERVAL_HOURS` | `24` |
| `PODVAULT_BASE_URL` | `config.BASE_URL` | unset (taken from each request) |
| `PODVAULT_FEED_TOKEN` | `config.FEED_TOKEN` | unset (derived from the password) |

The `HEALTHCHECK` polls the unauthenticated `/healthz`. A multi-arch image (linux/amd64 + linux/arm64) is published as `martitoci/podvault`, built with `docker buildx build --platform linux/amd64,linux/arm64 ... --push` — the Dockerfile itself is architecture-neutral.

Unset, every one of these matches the original hardcoded default exactly — running the CLI outside Docker is unaffected.

## Security Considerations

1. **Input Validation**: All user inputs validated and sanitized
2. **File Path Safety**: Prevents directory traversal attacks (naming templates drop `.`/`..` segments; redirects only to local paths)
3. **SQL Injection Prevention**: Parameterized queries; table names go through `validate_and_quote_table_name()`
4. **Web Access**: Optional HTTP Basic login for every route (Fase 23); off by default, so anyone who can reach the port has full control unless it's set. Basic auth isn't encrypted — use an HTTPS reverse proxy beyond a trusted network
5. **Feed tokens**: PodVault's own feeds (Fase 24) accept a `?token=` instead of the login, for podcast apps. The token is read-only and scoped to feed/audio/artwork GETs, rotates with the password, and travels in URLs, so treat feed URLs as secrets
6. **Untrusted OPML**: Size-capped, and DOCTYPE/ENTITY declarations rejected before parsing
7. **Secrets**: The password is never logged or rendered (the Settings page shows only whether it's set)
8. **File Permissions**: Appropriate file system permissions

## User Interfaces

### Command-Line Interface (`cli.py`)

Covers feed processing, listing/status, downloads, verification, renaming, naming config, schedules, and `migrate-layout`. A few operations are CLI-only by design — notably `migrate-layout --apply`, which moves real files on disk and so isn't exposed as a web click. `podvault serve` starts the web interface and the scheduler thread.

### Web Interface (`podvault serve`)

The day-to-day interface: Library, podcast/episode detail, Queue, Schedule, Activity, Storage, and Settings, plus the in-browser player and export. Playback, job control (pause/resume/skip/cancel), integrity checks, and export are web-only.

### Programmatic Use

The `podvault.services` package is the stable entry point for scripting: every function returns a dataclass from `services/results.py` and never prints or prompts (pass `require_confirmation=False` to `download_all`).

For user-facing documentation, see [README.md](README.md).

## Conclusion

PodVault is designed with modularity, extensibility, and robustness in mind. The architecture supports easy maintenance and future enhancements while providing a solid foundation for podcast management.

