"""Tests for automatic database backups (`database/backup.py`, Fase 23)."""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from podvault import config, services
from podvault.database import backup


def _make_db(path, rows=1):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    conn.commit()
    conn.close()


def _row_count(path):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        conn.close()


def test_backup_creates_consistent_copy_next_to_db(isolated_env):
    _make_db(config.DB_PATH, rows=3)

    target = backup.backup_database()

    assert target is not None and target.exists()
    assert target.parent == isolated_env / 'backups'
    assert _row_count(target) == 3


def test_backup_without_database_is_a_no_op(isolated_env):
    assert backup.backup_database() is None
    assert backup.list_backups() == []


def test_backup_dir_override(isolated_env, monkeypatch):
    monkeypatch.setattr(config, 'BACKUP_DIR', str(isolated_env / 'elsewhere'))
    _make_db(config.DB_PATH)

    target = backup.backup_database()

    assert target.parent == isolated_env / 'elsewhere'


def test_rotation_keeps_newest_n(isolated_env, monkeypatch):
    monkeypatch.setattr(config, 'BACKUP_KEEP', 2)
    _make_db(config.DB_PATH)
    stamps = iter(['20240101-000000', '20240102-000000', '20240103-000000'])

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.strptime(next(stamps), '%Y%m%d-%H%M%S').replace(tzinfo=timezone.utc)

    with patch.object(backup, 'datetime', FakeDatetime):
        for _ in range(3):
            backup.backup_database()

    names = [p.name for p in backup.list_backups()]
    assert len(names) == 2
    assert names[0].endswith('20240103-000000.db')
    assert names[1].endswith('20240102-000000.db')


def test_maybe_backup_is_throttled(isolated_env):
    _make_db(config.DB_PATH)

    first = backup.maybe_backup_database()
    second = backup.maybe_backup_database()

    assert first is not None
    assert second is None
    assert len(backup.list_backups()) == 1


def test_maybe_backup_runs_again_after_interval(isolated_env):
    _make_db(config.DB_PATH)
    backup.maybe_backup_database()
    later = datetime.now(timezone.utc) + timedelta(hours=config.BACKUP_INTERVAL_HOURS + 1)

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return later

    with patch.object(backup, 'datetime', FakeDatetime):
        assert backup.maybe_backup_database() is not None
    assert len(backup.list_backups()) == 2


def test_maybe_backup_respects_disabled(isolated_env, monkeypatch):
    monkeypatch.setattr(config, 'BACKUP_ENABLED', False)
    _make_db(config.DB_PATH)

    assert backup.maybe_backup_database() is None
    assert backup.list_backups() == []


def test_feed_sync_backs_up_existing_database(isolated_env, feed_df_factory):
    df = feed_df_factory()
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')  # creates the DB; nothing to back up yet
        assert backup.list_backups() == []
        services.add_feed('https://example.com/test_podcast.xml')  # re-sync: back up first

    backups = backup.list_backups()
    assert len(backups) == 1
    conn = sqlite3.connect(str(backups[0]))
    try:
        assert conn.execute('SELECT COUNT(*) FROM "test_podcast"').fetchone()[0] == 2
    finally:
        conn.close()


def test_corrupt_database_is_preserved_before_recreation(isolated_env, feed_df_factory):
    with open(config.DB_PATH, 'wb') as f:
        f.write(b'this is not a sqlite database' * 100)

    df = feed_df_factory()
    with patch('podvault.services.feeds.collect_data', return_value=df):
        result = services.add_feed('https://example.com/test_podcast.xml')

    assert result.feeds and result.feeds[0].success
    preserved = [p for p in backup.list_backups() if p.stem.endswith('-corrupt')]
    assert len(preserved) == 1
    assert preserved[0].read_bytes().startswith(b'this is not a sqlite database')


def test_corrupt_copies_are_never_rotated(isolated_env, monkeypatch):
    monkeypatch.setattr(config, 'BACKUP_KEEP', 1)
    with open(config.DB_PATH, 'wb') as f:
        f.write(b'garbage')
    backup.preserve_corrupt_database()
    _make_db(str(isolated_env / 'fresh.db'))
    import os
    os.replace(isolated_env / 'fresh.db', config.DB_PATH)

    stamps = iter(['20300101-000000', '20300102-000000'])

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.strptime(next(stamps), '%Y%m%d-%H%M%S').replace(tzinfo=timezone.utc)

    with patch.object(backup, 'datetime', FakeDatetime):
        backup.backup_database()
        backup.backup_database()

    names = [p.name for p in backup.list_backups()]
    assert sum(n.endswith('-corrupt.db') for n in names) == 1
    assert sum(not n.endswith('-corrupt.db') for n in names) == 1
