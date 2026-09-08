"""
CRUD for the single-row `app_settings` table (naming templates today;
a natural place for other global preferences later).
"""

import sqlite3
from typing import Dict

from podvault import config
from podvault.database.schema import create_app_settings_table_if_not_exists


def get_app_settings(conn: sqlite3.Connection) -> Dict[str, str]:
    """Return {'folder_template': ..., 'filename_template': ...}, seeding defaults on first call."""
    create_app_settings_table_if_not_exists(conn)
    cursor = conn.execute(
        f"SELECT folder_template, filename_template FROM {config.APP_SETTINGS_TABLE_NAME} WHERE id = 1"
    )
    row = cursor.fetchone()
    if row is None:
        return {
            'folder_template': config.DEFAULT_FOLDER_TEMPLATE,
            'filename_template': config.DEFAULT_FILENAME_TEMPLATE,
        }
    return {'folder_template': row[0], 'filename_template': row[1]}


def update_app_settings(conn: sqlite3.Connection, folder_template: str, filename_template: str) -> None:
    """Persist new naming templates."""
    create_app_settings_table_if_not_exists(conn)
    conn.execute(
        f"""
        INSERT INTO {config.APP_SETTINGS_TABLE_NAME} (id, folder_template, filename_template)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            folder_template = excluded.folder_template,
            filename_template = excluded.filename_template
        """,
        (folder_template, filename_template),
    )
    conn.commit()
