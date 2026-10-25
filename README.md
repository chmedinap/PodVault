# PodVault

A powerful Python application for downloading, organizing, and managing podcast episodes from RSS feeds. PodVault automatically extracts metadata, organizes files, and updates MP3 ID3 tags with complete episode information.

📖 **Documentation and examples: [podvault.dev](https://podvault.dev)** · 🐳 Docker image: [`martitoci/podvault`](https://hub.docker.com/r/martitoci/podvault) · 📄 [MIT License](LICENSE)

> [!WARNING]
> **PodVault is meant for your local or home network, not the public internet.** It hasn't been through any security audit or penetration testing, so don't expose it directly to the internet (port forwarding, a public server, etc.). The optional login is there to keep other people on your network out, not to make PodVault safe to publish. If you need to reach it from outside, use a VPN such as WireGuard or Tailscale instead.

## Features

- 📻 **RSS Feed Support**: Works with any RSS feed URL
- 🍎 **Apple Podcast Integration**: Automatically converts Apple Podcast links to RSS feeds
- 💾 **SQLite Database**: Stores all episode metadata in a local database
- 📥 **Automatic Downloads**: Download all episodes or just the latest
- 🎧 **Any Podcast Audio Format**: MP3, M4A/M4B (AAC), Ogg/Opus, FLAC… saved with the right extension
- 🏷️ **Tag Management**: Automatically writes episode metadata as ID3 tags (MP3) or MP4 tags (M4A/M4B)
- 🖼️ **Image Handling**: Downloads and embeds podcast/episode cover art
- 📊 **Statistics**: Track download progress and podcast statistics
- 📁 **Organized Storage**: Episodes organized by podcast, year, and date
- 📄 **Metadata Archive**: Saves original RSS XML metadata for each episode
- 🌐 **Web Interface**: Browser dashboard with library, download queue, schedules, activity log, storage health, and an in-browser player
- ⏰ **Auto-Refresh**: Per-podcast schedules that check feeds and download new episodes automatically
- 🛡️ **Archival Safeguards**: SHA-256 integrity checks, episodes kept even after they leave the feed, and JSON export of the library inventory
- 💾 **Automatic Database Backups**: Daily rotating copies of the database, plus `podvault backup` on demand
- 🔒 **Optional Login**: Username/password protection for the web interface
- 🔁 **OPML Import/Export**: Bring your subscriptions from any podcast app, or take them with you
- 📡 **Your Own Podcast Feeds**: Every archived podcast gets its own RSS feed, so you can listen to the archive in any podcast app — even episodes the publisher deleted
- 🐳 **Docker**: Ready-to-run `docker compose` setup with persistent data, and a multi-arch image on Docker Hub

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

A pre-built image (linux/amd64 and linux/arm64) is also published on Docker Hub, so you can skip the build — replace `build: .` with `image: martitoci/podvault:latest` in `docker-compose.yml`, or run it directly:

```bash
docker run -d -p 8000:8000 -v ./data:/app/data martitoci/podvault:latest
```

**If port 8000 is reachable from other machines, turn on the login** by setting `PODVAULT_USERNAME` and `PODVAULT_PASSWORD` (there's a commented-out `environment:` block for this in `docker-compose.yml`; see [Web Login](#web-login)). Database backups are written to `./data/backups` on the host (see [Database Backups](#database-backups)).

Everything else is configured with `PODVAULT_*` environment variables too — see [Configuration](#configuration) for the full list.

The CLI is available inside the container too, for anything you'd rather run from a terminal:

```bash
docker compose exec podvault podvault backup
docker compose exec podvault podvault opml-import /app/data/subscriptions.opml
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

This adds the feed to `feeds.txt` (or wherever `PODVAULT_FEEDS_FILE` points) and processes it immediately.

#### Import and Export OPML

OPML is the standard file podcast apps use to move a subscription list around (Apple Podcasts, Overcast, Pocket Casts, AntennaPod, Podcast Addict… all export and import it).

```bash
# Add every feed from an OPML file exported by another app, and sync the new ones
podvault opml-import subscriptions.opml

# Export the library's original feeds as OPML (to a file, or omit -o to print it)
podvault opml-export -o podvault_subscriptions.opml
```

Importing only adds feeds that aren't already in your feeds file, so importing the same file twice is harmless; folders inside the OPML are flattened, and invalid URLs are skipped and reported. Episodes aren't downloaded automatically: download them as usual, or turn on auto-download in each podcast's schedule. Both are also on the web UI's Library page (**Import** under "Add podcast", and **Export subscriptions (OPML)**).

#### Your Own Podcast Feeds

Every podcast in PodVault gets its own RSS feed listing the episodes you've downloaded, served from your own copies:

```
http://<your-podvault>:8000/podcasts/<podcast_name>/feed.xml
```

Subscribe to it in any podcast app to listen to your archive from your phone — including episodes the publisher has since removed from their feed. The URL is shown (with a Copy button) on each podcast's page in the web UI. **Archive feeds (OPML)** on the Library page (`/feeds.opml`) lists all of them at once, so a podcast app can subscribe to the whole archive in one import.

- Only episodes whose file is actually on disk are listed; new downloads appear the next time the app refreshes the feed.
- Podcast apps can't fill in a browser login, so when the [login](#web-login) is on, feed URLs carry an access token (`?token=...`). The token only opens the feeds and the audio/artwork they link to, never the rest of the web UI. It's derived from your password, so **changing the password revokes every feed URL handed out before** (or set your own with `PODVAULT_FEED_TOKEN`). Treat these URLs like a password.
- Your phone must be able to reach PodVault. If it's behind a reverse proxy or a different public address, set `PODVAULT_BASE_URL` (e.g. `https://podvault.example.com`) so the links inside the feed use that address.
- The feeds are marked `<itunes:block>` so podcast directories won't list them.

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

Changing the template only affects future downloads; it doesn't touch files you've already downloaded.

##### Reorganizing Files You've Already Downloaded

If you change a template after downloading episodes, the older files keep the previous layout. To move them into the current one:

```bash
podvault migrate-layout           # dry run: lists what would move, changes nothing
podvault migrate-layout --apply   # moves the files and updates the database
```

You only need this after changing a template; with the default templates there's nothing to move. The Storage page in the web UI shows the same dry-run list ("file(s) sitting outside the current naming template"), but moving the files is CLI-only on purpose: it touches real files on disk, so run the dry run first and check it before using `--apply`. In Docker: `docker compose exec podvault podvault migrate-layout`.

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

#### Database Backups

```bash
# Back up the database right now and list the backups
podvault backup

# Only list existing backups
podvault backup --list
```

PodVault also backs up the database automatically — before syncing feeds, and once a day from the background thread while `podvault serve` is running — at most once every `PODVAULT_BACKUP_INTERVAL_HOURS` (default 24). Backups go to a `backups/` folder next to the database (`./data/backups` in Docker; override with `PODVAULT_BACKUP_DIR`), named `podvault_data-YYYYmmdd-HHMMSS.db`, and only the newest `PODVAULT_BACKUP_KEEP` (default 7) are kept. They're taken with SQLite's online backup API, so they're consistent even while downloads are running. Set `PODVAULT_BACKUP_ENABLED=false` to turn the automatic ones off.

If the database is ever found corrupted, PodVault recreates it — but first copies the damaged file aside as `...-corrupt.db` in the same folder (these are never deleted by rotation).

To restore, stop PodVault and copy a backup over the database file:

```bash
cp backups/podvault_data-20260101-030000.db podvault_data.db
```

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

##### Web Login

By default the web interface has no login, which is fine on `127.0.0.1`. To require one, set both variables before starting the server:

```bash
PODVAULT_USERNAME=admin PODVAULT_PASSWORD='a-long-password' podvault serve --host 0.0.0.0
```

Every page, action, audio stream, and export then requires that login. Opening any page sends you to a **Sign in** page first, and you come back to the page you asked for afterwards. "Keep me signed in for 30 days" (on by default) keeps you signed in across browser restarts; untick it and the session ends when the browser closes, or after 12 hours at most. Either way the session renews itself while you use PodVault, so it only runs out after that long *without* using it. If it does run out, the next click or form takes you to the sign-in page and back to where you were. **Sign out** is at the bottom of the sidebar. Changing the password signs every browser out. Five wrong passwords in a row from the same address lock it out for 30 seconds, doubling with each further miss up to 15 minutes; behind a reverse proxy everyone shares the proxy's address, so a lockout there affects everyone not already signed in. Scripts and `curl` can still send the credentials as HTTP Basic auth (`curl -u admin:password …`); browsers can't, so Sign out always works (a script that pretends to be a browser with a `Mozilla/…` User-Agent has to use the sign-in page too). Only `/healthz`, used by Docker's health check, and the fonts the sign-in page needs stay open. Setting just one of the two variables is treated as a mistake: `podvault serve` says which one is missing when it starts, the sign-in page explains it, and every request is refused until both are set. `podvault serve` warns you if you bind to a non-local address without a login. The password travels unencrypted over plain HTTP. PodVault isn't meant to be exposed to the internet (see the warning at the top); if you put it on a network you don't fully trust anyway, at least run it behind an HTTPS reverse proxy (Caddy, Traefik, nginx…); the session cookie is marked `Secure` automatically when the proxy reports HTTPS (`X-Forwarded-Proto`).

Starting the server also starts the auto-refresh background thread (see [Auto-Refresh Schedule](#auto-refresh-schedule)). See [Docker](#docker) to run it in a container instead.

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

If you change the templates after downloading, see [Reorganizing Files You've Already Downloaded](#reorganizing-files-youve-already-downloaded).

### Audio Formats and Tags

Each episode is saved with the extension from its download URL: `.mp3`, `.m4a`, `.m4b`, `.aac`, `.ogg`, `.opus`, `.flac`, `.wav`, or `.mp4`. If the URL has none (some hosts use URLs like `/stream/12345`), it's saved as `.mp3`. The web player serves each file with the matching MIME type.

Tags are written for MP3 (ID3) and M4A/M4B/MP4 (MP4 tags). Other formats are downloaded and archived normally, but their tags are left as they came from the publisher.

Each tagged file automatically includes:

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
| `PODVAULT_USERNAME` / `PODVAULT_PASSWORD` | *(unset)* | Require this login for the web interface (both must be set) — see [Web Login](#web-login) |
| `PODVAULT_BACKUP_ENABLED` | `true` | Automatic database backups on/off |
| `PODVAULT_BACKUP_DIR` | `backups/` next to the database | Where backups are written |
| `PODVAULT_BACKUP_KEEP` | `7` | How many backups to keep |
| `PODVAULT_BACKUP_INTERVAL_HOURS` | `24` | Minimum time between automatic backups |
| `PODVAULT_BASE_URL` | *(unset — taken from each request)* | Public address used in [your own podcast feeds](#your-own-podcast-feeds), e.g. behind a reverse proxy |
| `PODVAULT_FEED_TOKEN` | *(derived from the password)* | Fixed access token for feed URLs when the login is on |

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

- Run `podvault add <feed_url>` (or use the add-feed form in the web UI), which appends it to `feeds.txt` and processes it right away;
- Import an OPML file from another podcast app with `podvault opml-import <file>` (or the Import button in the web UI) — see [Import and Export OPML](#import-and-export-opml); or
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

### Tags Not Updating

- Make sure mutagen is installed
- Check the file's format: tags are only written for MP3 and M4A/M4B/MP4 (see [Audio Formats and Tags](#audio-formats-and-tags))
- Check that the audio file exists and is writable
- Review the logs for specific error messages

### A Podcast App Can't Load My PodVault Feed

- Open the feed URL in your phone's browser first: if it doesn't load there, the phone can't reach PodVault (use its LAN address instead of `127.0.0.1`, or set `PODVAULT_BASE_URL` to the public address)
- With the login on, copy the URL from the podcast's page in the web UI, including the `?token=...` part. If you changed the password since, copy it again, because the old token no longer works
- Episodes only appear once they're downloaded

### Database Errors

- Ensure you have write permissions in the project directory
- Check that the database file isn't locked by another process (for example, a CLI command running while `podvault serve` is writing)
- Make sure `PODVAULT_DB_PATH` points where you expect — in Docker it's `/app/data/podvault_data.db`, i.e. `./data/podvault_data.db` on the host
- If the database is damaged or something went wrong after a sync, restore the latest copy from `backups/` (see [Database Backups](#database-backups))

### The Web Interface Keeps Asking for a Password

- Check that your browser accepts cookies for PodVault's address: the sign-in is kept in a `podvault_session` cookie
- If you changed `PODVAULT_PASSWORD` or `PODVAULT_USERNAME`, everyone has to sign in again; that's intended
- Check that `PODVAULT_USERNAME` and `PODVAULT_PASSWORD` are set the way you think (the Settings page shows the username and whether a password is set, never the password itself)
- If the sign-in page says "Login is misconfigured", only one of the two variables is set — set both, or neither

## File Structure

```
PodVault/
├── README.md                 # This file
├── ARCHITECTURE.md           # Technical documentation
├── ROADMAP.md                # Implementation phases and design decisions
├── LICENSE                   # MIT License
├── DOCKERHUB.md              # Docker Hub overview (short README)
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
    ├── dates.py              # Parsing/sorting feed publication dates
    ├── validation.py         # Input validation
    ├── logging_config.py     # Logging setup
    ├── data/                 # RSS feed parsing and statistics
    ├── database/             # Connections, schema, queries, jobs, settings, scheduling, backups
    ├── download/             # Downloader, audio tags, metadata, path/naming utilities
    ├── services/             # Business logic shared by the CLI and web UI
    │                         #   (feeds, downloads, episodes, queue, storage,
    │                         #    integrity, export, playback, settings, ...)
    └── web/                  # FastAPI app, optional login, routes, Jinja templates, static fonts
```

The CLI and the web interface are thin layers over `podvault/services/`. See [ARCHITECTURE.md](ARCHITECTURE.md) for details.

## Tips

1. **Backup Your Database**: The database file (`podvault_data.db`) contains all your podcast metadata. PodVault keeps rotating local copies automatically (see [Database Backups](#database-backups)), but those live on the same disk. Copy the `backups/` folder somewhere else from time to time too.

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

Documentation, guides and examples live at **[podvault.dev](https://podvault.dev)**. For technical details and architecture information, see [ARCHITECTURE.md](ARCHITECTURE.md).

## License

PodVault is released under the [MIT License](LICENSE). Anyone can use, copy, modify and distribute it, including in their own projects, as long as they keep the copyright notice and credit: © Carlos Medina, [github.com/chmedinap/PodVault](https://github.com/chmedinap/PodVault).
