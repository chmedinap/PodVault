# PodVault

A powerful Python application for downloading, organizing, and managing podcast episodes from RSS feeds. PodVault automatically extracts metadata, organizes files, and updates MP3 ID3 tags with complete episode information.

## Features

- 📻 **RSS Feed Support**: Works with any RSS feed URL
- 🍎 **Apple Podcast Integration**: Automatically converts Apple Podcast links to RSS feeds
- 💾 **SQLite Database**: Stores all episode metadata in a local database
- 📥 **Automatic Downloads**: Download all episodes or just the latest
- 🏷️ **ID3 Tag Management**: Automatically updates MP3 tags with episode metadata
- 🖼️ **Image Handling**: Downloads and embeds podcast/episode cover art
- 📊 **Statistics**: Track download progress and podcast statistics
- 📁 **Organized Storage**: Episodes organized by podcast, year, and date
- 📄 **Metadata Archive**: Saves original RSS XML metadata for each episode
- 🌐 **Web Interface**: Browser dashboard with library, download queue, schedules, activity log, storage health, and an in-browser player
- ⏰ **Auto-Refresh**: Per-podcast schedules that check feeds and download new episodes automatically
- 🛡️ **Archival Safeguards**: SHA-256 integrity checks, episodes kept even after they leave the feed, and JSON export of the library inventory
- 🐳 **Docker**: Ready-to-run `docker compose` setup with persistent data

## Installation

### Prerequisites

- Python 3.7 or higher
- pip (Python package manager)

### Install Dependencies

```bash
# Clone or download the repository
cd PodVault

# Install the package and dependencies
pip install -e .
```

This will install:
- pandas, numpy, feedparser, requests
- mutagen (for ID3 tag management)
- tqdm (for progress bars)
- fastapi, uvicorn, jinja2, python-multipart (for the web interface)

### Docker

If you'd rather not install Python dependencies locally, run PodVault in Docker instead:

```bash
docker compose up --build
```

Then open http://localhost:8000. The database, `downloads/`, and `feeds.txt` are stored under `./data` on the host (mapped to `/app/data` in the container via `docker-compose.yml`), so they survive rebuilds and restarts. To run it in the background: `docker compose up -d --build`; to stop it: `docker compose down`.

Paths can be customized via environment variables (see `docker-compose.yml` or set them directly): `PODVAULT_DB_PATH`, `PODVAULT_DOWNLOADS_FOLDER`, `PODVAULT_FEEDS_FILE`, `PODVAULT_LOG_LEVEL`, `PODVAULT_LOG_FILE`, `PODVAULT_SCHEDULER_POLL_INTERVAL_SECONDS`.

The CLI is still available inside the container, e.g. for `migrate-layout` (deliberately not exposed in the web UI — see [File Organization](#file-organization)):

```bash
docker compose exec podvault podvault migrate-layout
docker compose exec podvault podvault migrate-layout --apply
```

## Quick Start

### 1. Prepare Your Feed List

Create a file named `feeds.txt` in the project root directory. Add one RSS feed URL or Apple Podcast link per line:

```
https://feeds.example.com/podcast.rss
https://podcasts.apple.com/us/podcast/example-podcast/id123456789
https://another-podcast.com/feed.xml
```

**Note**: You can mix RSS feed URLs and Apple Podcast links. The application will automatically detect and convert Apple Podcast links.

See `feeds.txt` for detailed instructions and examples.

### 2. Process Your Feeds

```bash
podvault process-feeds
```

This will:
- Read all feeds from `feeds.txt`
- Automatically detect and convert Apple Podcast links to RSS feeds
- Extract episode metadata from each feed
- Store all information in a SQLite database (`podvault_data.db`)

### 3. List Your Podcasts

```bash
podvault list
```

This shows all podcasts in your database with download statistics.

### 4. Download Episodes

```bash
# Download all episodes for a podcast
podvault download podcast_name

# Or download only the latest episode
podvault download-last podcast_name
```

## Usage Guide

### Command Line Interface

PodVault provides a simple command-line interface for all operations:

#### Process Feeds

```bash
# Process all feeds from feeds.txt
podvault process-feeds

# Or specify a custom feeds file
podvault process-feeds --file my-feeds.txt
```

This command:
- Reads URLs from `feeds.txt`
- Automatically detects Apple Podcast links and converts them to RSS feeds
- Extracts episode metadata from each feed
- Stores all information in a SQLite database (`podvault_data.db`)

#### List Podcasts

```bash
podvault list
```

Shows all podcasts in your database with download statistics.

#### Check Status

```bash
# Show status for all podcasts
podvault status

# Show status for a specific podcast
podvault status podcast_name
```

#### Download Episodes

```bash
# Download all episodes for a podcast
podvault download podcast_name

# Download only the latest episode
podvault download-last podcast_name
```

The download process will:
- Show you how many episodes will be downloaded
- Ask for confirmation before starting
- Download episodes with a delay between each (default: 5 seconds)
- Update ID3 tags automatically
- Download episode and podcast images

#### Verify Downloaded Files

```bash
# Check that every downloaded episode still has its file on disk
podvault verify

# Check a single podcast
podvault verify podcast_name
```

Files can go missing without the database knowing (moved, deleted, an external drive unplugged). `verify` checks each episode marked as downloaded against disk and, if the file is gone, marks it `missing` instead of `downloaded` — the next `download`/`download-last` run will pick it back up automatically.

#### Rename an Episode

```bash
# List a podcast's episodes with their id, current name, and status
podvault episodes podcast_name

# Give an episode a custom display name (updates the ID3 tag and, if already
# downloaded, renames its folder and files to match)
podvault rename podcast_name <episode_id> "New Episode Title"

# Clear the override and go back to the name from the feed
podvault rename podcast_name <episode_id> ""
```

The custom name is stored separately from the feed's original title, so re-processing feeds never overwrites it. If the episode hasn't been downloaded yet, the custom name is simply used the next time it is.

#### Add a Single Feed

```bash
podvault add https://feeds.example.com/podcast.rss
```

This adds the feed to `feeds.txt` and processes it immediately.

#### Naming Configuration

```bash
# Show the current folder/filename templates
podvault config

# Change one or both (only the ones you pass are updated)
podvault config --folder-template "{podcast_name}/{year}/{episode_label} - {episode_title}"
podvault config --filename-template "{episode_number} - {episode_title}"
```

Both templates are plain `str.format`-style strings built from these variables: `{podcast_name}`, `{episode_title}` (respects a custom name from `rename`), `{episode_number}`, `{season_number}`, `{year}`, `{month}`, `{day}`, `{date}`, and `{episode_label}` (episode number zero-padded to 3 digits, or the published date if the feed doesn't number episodes — a ready-made fallback so you don't have to handle that case yourself). A `/` in the folder template is a folder separator; each resulting segment is sanitized independently, and `.`/`..` segments are dropped so a template can't write outside the downloads folder.

Saving is validated against two sample episodes — one with numbers, one without — so a template that only works when a number is present (e.g. `{episode_number:03d}` with no fallback) is rejected up front rather than failing later on whichever real episode lacks one. This is also available from the web UI at `/settings`, with a live preview as you type.

Changing the template only affects future downloads; it doesn't touch files you've already downloaded (see `migrate-layout` above if you want to reorganize them).

#### Auto-Refresh Schedule

```bash
# Show a podcast's current schedule
podvault schedule podcast_name

# Check the feed every 12 hours and download new episodes automatically
podvault schedule podcast_name --interval-hours 12 --auto-download

# Check the feed on a schedule, but leave downloading to you
podvault schedule podcast_name --interval-hours 24 --no-auto-download

# Turn it off
podvault schedule podcast_name --disable
```

This only takes effect while the web server is running (`podvault serve`) — a background thread wakes up periodically (every `PODVAULT_SCHEDULER_POLL_INTERVAL_SECONDS`, default 300s) and re-syncs any podcast whose interval has elapsed, same as running `add` again. It doesn't run from a plain CLI command, since there's no long-running process to host it; `--reload` skips it too (uvicorn's reloader runs the actual server in a subprocess the scheduler thread never reaches). Also configurable per podcast from its detail page in the web UI, with "last check" showing how long since the last check.

#### Web Interface

```bash
podvault serve
```

Opens a browser-based dashboard at http://127.0.0.1:8000 (use `--host`/`--port` to change), with a persistent sidebar across six sections:

- **Library** (`/`) — stat cards (including an **Archive health** card linking to Storage), a cover-art grid with filter pills (All/Incomplete/Missing files/Scheduled) and small corrupt/local-only badges per podcast, and search. Click into a podcast for its full episode list (with status filter pills showing live counts), a pre-flight "download all pending" confirmation (episode count, ~estimated size/time), an inline rename per row (no page navigation needed), and a true single-episode ↓ download/re-download action; click an episode for its own page — rename, re-download, files-on-disk, integrity status, and metadata (duration, ID3-tag completeness, feed GUID) read on demand for just that one file.
- **Queue** (`/queue`) — what's downloading right now, with **estimated** (not byte-level) speed/ETA derived from job timestamps and average episode size, per-job Pause/Resume and "Stop this one", a page-level "Cancel all", and a ✕ to drop a single episode out of "up next" before it's reached. A cancel or pause only ever stops the *next* episode from starting — one already being downloaded finishes and is kept.
- **Schedule** (`/schedule`) — every podcast's auto-refresh interval and auto-download toggle in one table, instead of visiting each podcast individually.
- **Activity** (`/activity`) — filterable job history (downloads, syncs, verify, errors), including scheduled syncs the background thread ran on its own.
- **Storage** (`/storage`) — disk usage by file type, largest podcasts, an **Export library** link, and four health checks: files marked downloaded but missing on disk, files sitting outside the current naming template (dry-run only — moving them for real is still CLI-only), ID3 tag completeness, and file **integrity** (naming each corrupt file, with a "Re-download all N" action) — the last two run as background jobs so they don't rescan the whole library on every page load. A fifth row lists episodes kept after leaving their feed, expandable to review each one.
- **Settings** (`/settings`) — the naming templates with a live preview, plus a read-only Environment panel showing the `PODVAULT_*` variables currently in effect.

Any action that starts a background job (adding a feed, downloading, checking ID3 tags/integrity) shows a small floating **toast** and, while it runs, a **job strip** with progress and a Cancel button — no separate "job progress" page to wait on, and the strip keeps tracking the job even after navigating elsewhere.

**Archival features.** These three matter specifically because this app's job is to *keep* podcasts, not just fetch them once:

- **Integrity checking** — every freshly-downloaded episode gets a SHA-256 checksum stamped at download time. The Storage page's "Integrity" check re-hashes every downloaded file on disk and compares it against that baseline, catching silent corruption (a bad disk, a truncated write, a bad external edit) that a plain "does the file exist" check can't see. A mismatch is flagged (and the episode detail page shows it) without touching the original good checksum, so the record still says when the file was last known-good.
- **Episodes are never deleted, just marked** — if a podcast delists an old episode, or a feed shrinks to only its last N items, PodVault keeps that episode's full record (title, download status, saved file, checksum) instead of losing it on the next sync. Its row is flagged "removed from feed · local only" (shown on the podcast page and the episode's own page) instead of vanishing, and it flips back automatically if the episode ever reappears in the feed.
- **Export** — `GET /podcasts/{name}/export` and `GET /export` download a JSON manifest of a podcast's (or the whole library's) full episode inventory — including episodes removed from their live feed, and each episode's checksum/integrity state — independent of PodVault's own database. Manifest-only by design: the audio files already live in an organized folder tree on disk (see [Naming Configuration](#naming-configuration)), so exporting doesn't duplicate them into a second copy.

Every downloaded episode has a "▶" button that opens a player bar at the bottom of the page — cover art, title, podcast name, a seek bar you can scrub without waiting for the whole file to load, prev/±15s/±30s/next, and a playback-speed toggle. The player doesn't survive navigating to a different page (this app reloads the full page on every link/form, by design — see `ROADMAP.md`), so it doesn't try to fake persistence across pages.

Starting the server also starts the auto-refresh background thread (see [Auto-Refresh Schedule](#auto-refresh-schedule)). See [Docker](#docker) to run it in a container instead. `migrate-layout --apply` is CLI-only by design — it moves real files on disk, so it's not something to trigger from a web click; the Storage page only shows the dry-run plan.

Playback is web-only (there's no CLI equivalent) and only works for episodes already marked `downloaded` — it streams the local file, not the original feed URL, so an episode has to be downloaded before you can listen to it here.

### Getting Help

```bash
podvault --help
podvault <command> --help
```

### File Organization

Episodes are organized in the following structure:

```
downloads/
  └── podcast_name/
      └── 2024/
          └── 01/
              └── 003 - Episode Title/
                  ├── Episode Title - podcast_name.mp3
                  ├── episode_image.jpg (if available)
                  └── episode_metadata.xml (original RSS entry)
```

Each episode gets its own folder named `<episode_number> - <title>` (zero-padded to 3 digits). If a feed doesn't provide an episode number, the folder falls back to `<published_date> - <title>` so episodes still sort chronologically.

This is the *default* layout — both the folder path and the filename are templates you can customize; see [Naming Configuration](#naming-configuration).

Already-downloaded episodes from before a layout change can be moved into the new one with:

```bash
podvault migrate-layout           # dry run: reports what would move
podvault migrate-layout --apply   # actually moves files and updates the database
```

### ID3 Tags

Each downloaded MP3 file automatically includes:

- **Title**: Episode title
- **Artist**: Podcast name (original, before normalization)
- **Album**: Podcast name (same as Artist)
- **Year**: Publication year
- **Track**: Episode number (if available)
- **Disc**: Season number (if available)
- **Comment**: Episode summary/description
- **Cover Art**: Episode image (if available) or podcast cover art
- **Genre**: "Podcast"

### Viewing Statistics

```bash
# Show download status for all podcasts
podvault status

# Show status for a specific podcast
podvault status podcast_name
```

## Configuration

Defaults live in `podvault/config.py`. The deployment-relevant ones can be overridden with environment variables, with no need to edit the file:

| Environment variable | Default | Purpose |
|---|---|---|
| `PODVAULT_DB_PATH` | `podvault_data.db` | Database file location |
| `PODVAULT_DB_TIMEOUT` | `30.0` | Database timeout (seconds) |
| `PODVAULT_DOWNLOADS_FOLDER` | `downloads` | Where episodes are saved |
| `PODVAULT_FEEDS_FILE` | `feeds.txt` | Feed list file |
| `PODVAULT_DEFAULT_DELAY_SECONDS` | `5` | Delay between downloads |
| `PODVAULT_DOWNLOAD_TIMEOUT` | `30` | Download timeout (seconds) |
| `PODVAULT_SCHEDULER_POLL_INTERVAL_SECONDS` | `300` | How often the auto-refresh thread wakes up |
| `PODVAULT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `PODVAULT_LOG_FILE` | *(unset)* | Log to this file in addition to the console |

Other settings (retries, RSS cache size, filename length) are plain constants in `config.py`:

```python
MAX_RETRIES = 3                  # Retry attempts for failed downloads
RETRY_DELAY_BASE = 1             # Base delay for exponential backoff
RETRY_DELAY_MAX = 60             # Maximum delay for exponential backoff
RSS_CACHE_TTL_HOURS = 1          # How long fetched feeds are cached
MAX_FILENAME_LENGTH = 200
```

Naming templates and per-podcast schedules are stored in the database, not in `config.py`; change them with `podvault config` / `podvault schedule` or from the web UI.

## Common Tasks

### Adding a New Podcast

Either:

- Run `podvault add <feed_url>` (or use the add-feed form in the web UI), which appends it to `feeds.txt` and processes it right away; or
- Add the RSS feed URL or Apple Podcast link to `feeds.txt` yourself, then run `podvault process-feeds`

### Re-downloading Episodes

If you need to re-download episodes (e.g., if files were deleted):

1. Run `podvault verify [podcast_name]` — it detects episodes whose file is missing on disk and marks them `missing`
2. Run `podvault download podcast_name` (or `download-last`) — missing episodes are downloaded again automatically

From the web UI you can also re-download a single episode (the ↓ button on the podcast page, or "Re-download" on the episode page), or re-download every corrupt file at once from the Storage page's Integrity check.

### Updating Episode Metadata

If you've updated your database with new episode information:

1. The ID3 tags will be automatically updated when you re-download
2. Simply run `podvault download podcast_name` again to update tags

## Troubleshooting

### "mutagen library not available" Warning

If you see this warning, install mutagen:
```bash
pip install mutagen
```

### Episodes Not Downloading

- Check your internet connection
- Verify the RSS feed URL is correct
- Check the logs for error messages
- Ensure the downloads folder is writable

### ID3 Tags Not Updating

- Make sure mutagen is installed
- Check that the MP3 file exists and is writable
- Review the logs for specific error messages

### Database Errors

- Ensure you have write permissions in the project directory
- Check that the database file isn't locked by another process (for example, a CLI command running while `podvault serve` is writing)
- Make sure `PODVAULT_DB_PATH` points where you expect — in Docker it's `/app/data/podvault_data.db`, i.e. `./data/podvault_data.db` on the host

## File Structure

```
PodVault/
├── README.md                 # This file
├── ARCHITECTURE.md           # Technical documentation
├── ROADMAP.md                # Implementation phases and design decisions
├── feeds.txt                 # Your podcast feed list
├── podvault_data.db          # SQLite database (created automatically)
├── downloads/                # Downloaded episodes (created automatically)
├── data/                     # Docker volume: database, downloads, feeds.txt
├── Dockerfile / docker-compose.yml
├── setup.py                  # Package setup
├── Redesign/                 # UI design mockups
├── tests/                    # pytest suite
└── podvault/                 # Main package
    ├── cli.py                # Command-line interface
    ├── config.py             # Configuration (env-var overridable)
    ├── jobs.py               # Background job runner + job control (cancel/pause/skip)
    ├── scheduler.py          # Auto-refresh background thread
    ├── hashing.py            # SHA-256 checksums for integrity checks
    ├── validation.py         # Input validation
    ├── logging_config.py     # Logging setup
    ├── data/                 # RSS feed parsing and statistics
    ├── database/             # Connections, schema, queries, jobs, settings, scheduling
    ├── download/             # Downloader, ID3 tags, metadata, path/naming utilities
    ├── services/             # Business logic shared by the CLI and web UI
    │                         #   (feeds, downloads, episodes, queue, storage,
    │                         #    integrity, export, playback, settings, ...)
    └── web/                  # FastAPI app, routes, Jinja templates, static fonts
```

The CLI and the web interface are thin layers over `podvault/services/`. See [ARCHITECTURE.md](ARCHITECTURE.md) for details.

## Tips

1. **Backup Your Database**: The database file (`podvault_data.db`) contains all your podcast metadata. Keep it backed up.

2. **Organize Your Feeds**: Keep `feeds.txt` organized with comments:
   ```
   # Technology Podcasts
   https://tech-podcast.com/feed
   
   # News Podcasts
   https://news-podcast.com/feed
   ```

3. **Check Download Space**: Large podcasts can take up significant disk space. Monitor your `downloads/` folder.

4. **Use Delays**: The default delay between downloads (5 seconds) helps avoid overwhelming servers. Adjust it with `PODVAULT_DEFAULT_DELAY_SECONDS` if needed.

## Running Tests

```bash
pip install -e ".[test]"
pytest
```

Tests run against an isolated temp database/downloads folder and mock out network calls, so they're safe to run anytime without touching your real `podvault_data.db` or `downloads/`. See [ARCHITECTURE.md](ARCHITECTURE.md#testing-strategy) for what's covered.

## Support

For technical details and architecture information, see [ARCHITECTURE.md](ARCHITECTURE.md).

## License

This project is provided as-is for personal use.
