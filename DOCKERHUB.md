# PodVault

**Self-hosted podcast archiver.** PodVault downloads podcast episodes from RSS feeds (or Apple Podcasts links), organizes them into a clean folder tree, writes complete tags and cover art, and keeps them safe for the long run. You get a web dashboard to manage everything, and every archived podcast gets its own RSS feed you can subscribe to from any podcast app.

Multi-arch image: `linux/amd64` and `linux/arm64` (Intel/AMD servers, Raspberry Pi, Apple Silicon, most NAS devices).

Documentation and examples: https://podvault.dev

Source code: https://github.com/chmedinap/PodVault (MIT License)

> ⚠️ **PodVault is meant for your local or home network, not the public internet.** It hasn't been through any security audit or penetration testing, so don't expose it directly to the internet (port forwarding, a public server, etc.). The optional login keeps other people on your network out; it doesn't make PodVault safe to publish. To reach it from outside, use a VPN such as WireGuard or Tailscale.

## Features

- **Any podcast feed:** RSS URLs or Apple Podcasts links, plus OPML import/export to move subscriptions from or to any podcast app
- **Organized downloads:** configurable folder and file naming, the original RSS metadata saved per episode, cover art embedded
- **Any audio format:** MP3, M4A/M4B (AAC), Ogg/Opus, FLAC… saved with the right extension, with ID3 (MP3) or MP4 tags
- **Web interface:** library, download queue with pause/resume/cancel, per-podcast auto-refresh schedules, activity log, storage health, and an in-browser player
- **Archive safety:** SHA-256 integrity checks, episodes kept even after the publisher deletes them from the feed, JSON export of the inventory, automatic database backups
- **Your own podcast feeds:** one RSS feed per archived podcast, served from your copies, so you can listen in Apple Podcasts, Overcast, Pocket Casts, AntennaPod…
- **Optional login** for the web interface

## Quick start

```bash
docker run -d --name podvault \
  -p 8000:8000 \
  -v ./data:/app/data \
  -e PODVAULT_USERNAME=admin \
  -e PODVAULT_PASSWORD=change-me \
  --restart unless-stopped \
  martitoci/podvault:latest
```

Open http://localhost:8000, add a podcast by URL (or import an OPML file), and start downloading.

### Docker Compose

```yaml
services:
  podvault:
    image: martitoci/podvault:latest
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    environment:
      PODVAULT_USERNAME: admin
      PODVAULT_PASSWORD: change-me
    restart: unless-stopped
```

```bash
docker compose up -d
```

## Data

Everything PodVault stores lives in `/app/data`. Mount it as a volume so your library survives updates:

| Path in the container | Contents |
|---|---|
| `/app/data/podvault_data.db` | SQLite database: episodes, download state, settings, schedules |
| `/app/data/downloads/` | The downloaded episodes |
| `/app/data/feeds.txt` | Your list of feed URLs |
| `/app/data/backups/` | Automatic database backups (the newest 7 are kept) |

To keep the audio on a different disk, mount it separately, e.g. `-v /mnt/media/podcasts:/app/data/downloads`.

## Configuration

All settings are optional environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `PODVAULT_USERNAME` / `PODVAULT_PASSWORD` | unset (no login) | Require this login for the web interface. **Set both if port 8000 is reachable from other machines.** Setting only one blocks every request until both are set. |
| `PODVAULT_BASE_URL` | taken from each request | Public address used in your own podcast feeds, e.g. `https://podvault.example.com` behind a reverse proxy |
| `PODVAULT_FEED_TOKEN` | derived from the password | Fixed access token for feed URLs when the login is on |
| `PODVAULT_BACKUP_ENABLED` | `true` | Automatic database backups |
| `PODVAULT_BACKUP_KEEP` | `7` | How many backups to keep |
| `PODVAULT_BACKUP_INTERVAL_HOURS` | `24` | Minimum time between automatic backups |
| `PODVAULT_BACKUP_DIR` | `/app/data/backups` | Where backups are written |
| `PODVAULT_DEFAULT_DELAY_SECONDS` | `5` | Pause between episode downloads |
| `PODVAULT_DOWNLOAD_TIMEOUT` | `30` | Download timeout (seconds) |
| `PODVAULT_SCHEDULER_POLL_INTERVAL_SECONDS` | `300` | How often auto-refresh checks for due podcasts |
| `PODVAULT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `PODVAULT_LOG_FILE` | unset | Also log to this file |
| `PODVAULT_DB_PATH`, `PODVAULT_DOWNLOADS_FOLDER`, `PODVAULT_FEEDS_FILE` | under `/app/data` | Override individual paths |

With the login on, the browser shows a sign-in page (optionally remembered for 30 days, renewed while you use it) and the sidebar has a Sign out button. Repeated wrong passwords lock the address out for a while. Scripts can still use HTTP Basic auth. The password isn't encrypted over plain HTTP. PodVault isn't meant to be exposed to the internet (see the warning at the top); if you put it on a network you don't fully trust anyway, at least run it behind an HTTPS reverse proxy (Caddy, Traefik, nginx…).

## Listening from a podcast app

Each podcast's page in the web interface shows its PodVault feed URL (`/podcasts/<name>/feed.xml`) with a Copy button. **Archive feeds (OPML)** on the Library page lists all of them at once.

- Only downloaded episodes are listed, served from your own copies, including ones the publisher has removed.
- With the login on, feed URLs include a `?token=...` that only opens the feeds and their audio, never the rest of the interface. Changing the password revokes old feed URLs. Treat them like a password.
- Your phone has to be able to reach PodVault. Set `PODVAULT_BASE_URL` if it's behind a reverse proxy or a different public address.

## Command line

The CLI is available inside the container:

```bash
docker exec podvault podvault --help
docker exec podvault podvault backup                          # back up the database now
docker exec podvault podvault opml-import /app/data/subs.opml # import subscriptions
docker exec podvault podvault verify                          # check every file is still on disk
```

## Updating

```bash
docker pull martitoci/podvault:latest
docker compose up -d        # or: docker rm -f podvault && docker run ... (same command as above)
```

Your data in `/app/data` is untouched by updates, and the database is backed up automatically before feed syncs.

## Health check

The image has a built-in `HEALTHCHECK` on `/healthz`, which needs no login.

## Tags

- `latest`: the most recent release
- `1.3.1`: fixes: newest-first episode order (Download latest now really gets the latest), readable episode descriptions, full dates, podcast names on Schedule
- `1.3.0`: sign-in page with Sign out and "keep me signed in", login lockout
- `1.2.0`: OPML import/export, your own podcast feeds
- `1.1.0`: web login, non-MP3 audio, database backups
- `1.0.0`: first release

## License

MIT. Use, copy and modify it freely, as long as you keep the copyright notice and credit: © Carlos Medina, https://github.com/chmedinap/PodVault
