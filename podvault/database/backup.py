"""
Automatic SQLite database backups (Fase 23).

The audio files are protected by checksums (Fase 19), but the database —
every podcast's episode inventory, download state, custom titles,
checksums, schedules, and naming settings — had no protection at all, and
a feed sync rewrites each podcast's table wholesale (`to_sql(if_exists=
'replace')`). This module keeps rotating copies of it.

Backups use SQLite's online backup API (`Connection.backup`), so they're
consistent even while the web server or a background job is writing —
unlike a plain file copy, which can catch a half-written page.

Triggered from three places:
- before a feed sync (`services.feeds.process_feeds_file`), throttled to at
  most one every `config.BACKUP_INTERVAL_HOURS`;
- from the scheduler thread under `podvault serve`, same throttle, so a
  long-running server gets a daily backup even with no manual syncs;
- on demand via `podvault backup`, which ignores the throttle.
"""

import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from podvault import config
from podvault.logging_config import setup_logging

logger = setup_logging(__name__)

_TIMESTAMP_FORMAT = '%Y%m%d-%H%M%S'


def get_backup_dir(db_path: Optional[str] = None) -> Path:
    """`config.BACKUP_DIR` if set, else a `backups/` folder next to the database."""
    if config.BACKUP_DIR:
        return Path(config.BACKUP_DIR)
    return Path(db_path or config.DB_PATH).resolve().parent / 'backups'


def list_backups(db_path: Optional[str] = None) -> List[Path]:
    """This database's backups, newest first."""
    db_path = db_path or config.DB_PATH
    backup_dir = get_backup_dir(db_path)
    if not backup_dir.exists():
        return []
    stem = Path(db_path).stem
    return sorted(backup_dir.glob(f"{stem}-*.db"), key=lambda p: p.name, reverse=True)


def _backup_time(path: Path) -> Optional[datetime]:
    # Name is "<stem>-YYYYmmdd-HHMMSS.db" (optionally with a "-corrupt" suffix)
    parts = path.stem.split('-')
    for i in range(len(parts) - 1):
        try:
            return datetime.strptime(f"{parts[i]}-{parts[i + 1]}", _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _rotate(db_path: str) -> None:
    keep = max(config.BACKUP_KEEP, 1)
    regular = [p for p in list_backups(db_path) if not p.stem.endswith('-corrupt')]
    for old in regular[keep:]:
        try:
            old.unlink()
            logger.info(f"Removed old database backup {old}")
        except OSError as e:
            logger.warning(f"Could not remove old backup {old}: {e}")


def backup_database(db_path: Optional[str] = None) -> Optional[Path]:
    """
    Write a consistent copy of the database to the backup folder and prune
    old copies beyond `config.BACKUP_KEEP`. Returns the new backup's path,
    or None if there was nothing to back up or the backup failed (a backup
    failure is logged, never raised — it must not block a sync).
    """
    db_path = db_path or config.DB_PATH
    if not Path(db_path).exists():
        return None

    backup_dir = get_backup_dir(db_path)
    timestamp = datetime.now(timezone.utc).strftime(_TIMESTAMP_FORMAT)
    target = backup_dir / f"{Path(db_path).stem}-{timestamp}.db"

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(db_path, timeout=config.DB_TIMEOUT)
        try:
            dest = sqlite3.connect(str(target))
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Database backup failed: {e}")
        try:
            target.unlink()
        except OSError:
            pass
        return None

    logger.info(f"Database backed up to {target}")
    _rotate(db_path)
    return target


def maybe_backup_database(db_path: Optional[str] = None) -> Optional[Path]:
    """
    `backup_database()`, but only if backups are enabled and the newest one
    is older than `config.BACKUP_INTERVAL_HOURS`. Cheap to call often.
    """
    if not config.BACKUP_ENABLED:
        return None
    db_path = db_path or config.DB_PATH
    backups = [p for p in list_backups(db_path) if not p.stem.endswith('-corrupt')]
    if backups:
        latest = _backup_time(backups[0])
        if latest and datetime.now(timezone.utc) - latest < timedelta(hours=config.BACKUP_INTERVAL_HOURS):
            return None
    return backup_database(db_path)


def preserve_corrupt_database(db_path: Optional[str] = None) -> Optional[Path]:
    """
    Copy a database file that failed validation aside (as "...-corrupt.db")
    before it gets recreated, so whatever is recoverable from it isn't
    deleted outright. A raw file copy, since the backup API can't read it.
    Never pruned by rotation.
    """
    db_path = db_path or config.DB_PATH
    if not Path(db_path).exists():
        return None
    backup_dir = get_backup_dir(db_path)
    timestamp = datetime.now(timezone.utc).strftime(_TIMESTAMP_FORMAT)
    target = backup_dir / f"{Path(db_path).stem}-{timestamp}-corrupt.db"
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db_path, target)
    except OSError as e:
        logger.error(f"Could not preserve corrupt database {db_path}: {e}")
        return None
    logger.warning(f"Corrupt database copied to {target} before being recreated")
    return target
