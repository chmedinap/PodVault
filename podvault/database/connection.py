"""
Database connection management and utility functions.
"""

import sqlite3
import os
from contextlib import contextmanager
from typing import Generator
import pandas as pd
from podvault import config


def is_valid_database(db_path: str) -> bool:
    """
    Check if a file is a valid SQLite database.
    
    Parameters:
    db_path: Path to the database file
    
    Returns:
    True if valid database, False otherwise
    """
    if not os.path.exists(db_path):
        return False
    
    try:
        # Try to open and query the database
        test_conn = sqlite3.connect(db_path)
        test_cursor = test_conn.cursor()
        test_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
        test_conn.close()
        return True
    except (sqlite3.DatabaseError, sqlite3.Error):
        return False


@contextmanager
def get_db_connection(db_path: str = None) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager for database connections.
    
    Parameters:
    db_path: Path to database file (defaults to config.DB_PATH)
    
    Yields:
    sqlite3.Connection object
    
    Example:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM table")
    """
    if db_path is None:
        db_path = config.DB_PATH
    
    conn = sqlite3.connect(db_path, timeout=config.DB_TIMEOUT)
    try:
        yield conn
    finally:
        conn.close()


def clean_dataframe_for_sqlite(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert all FeedParserDict and other non-serializable objects to strings.
    
    Parameters:
    df: pandas DataFrame to clean
    
    Returns:
    Cleaned DataFrame with all values serializable to SQLite
    """
    df_clean = df.copy()
    for col in df_clean.columns:
        # Convert any non-serializable objects to strings
        df_clean[col] = df_clean[col].apply(
            lambda x: str(x) if not isinstance(x, (str, int, float, type(None))) else x
        )
    return df_clean




