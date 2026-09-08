"""
Transaction management utilities for PodVault.

This module provides context managers and utilities for safe database transaction
handling with support for savepoints and automatic rollback on errors.
"""

import sqlite3
import logging
from contextlib import contextmanager
from typing import Optional, Iterator
from podvault.logging_config import setup_logging

logger = setup_logging(__name__)


class TransactionError(Exception):
    """Raised when transaction operations fail."""
    pass


class TransactionState:
    """Tracks the state of a database transaction."""
    
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.in_transaction = False
        self.savepoint_counter = 0
        self.savepoints: list[str] = []
    
    def validate(self) -> bool:
        """
        Validate that the connection is in a valid state.
        
        Returns:
        bool: True if connection is valid, False otherwise
        """
        try:
            # Check if connection is still open
            self.conn.execute("SELECT 1").fetchone()
            return True
        except (sqlite3.Error, AttributeError):
            return False
    
    def is_in_transaction(self) -> bool:
        """
        Check if currently in a transaction.
        
        Returns:
        bool: True if in transaction, False otherwise
        """
        # SQLite doesn't have a direct way to check transaction state
        # We track it ourselves
        return self.in_transaction


@contextmanager
def transaction(conn: sqlite3.Connection, autocommit: bool = True) -> Iterator[sqlite3.Cursor]:
    """
    Context manager for database transactions with automatic rollback on errors.
    
    This context manager ensures that transactions are properly committed or
    rolled back, even if exceptions occur. It also validates transaction state.
    
    Parameters:
    conn: SQLite connection object
    autocommit: If True, automatically commit on successful exit. If False, caller must commit.
    
    Yields:
    sqlite3.Cursor: Database cursor for executing queries
    
    Raises:
    TransactionError: If transaction operations fail
    
    Example:
        >>> with transaction(conn) as cursor:
        ...     cursor.execute("UPDATE episodes SET status = ? WHERE id = ?", ('downloaded', '123'))
        ...     # Transaction automatically committed on exit
        >>> 
        >>> # With manual commit control
        >>> with transaction(conn, autocommit=False) as cursor:
        ...     cursor.execute("UPDATE episodes SET status = ?", ('downloaded',))
        ...     conn.commit()  # Manual commit
    """
    cursor = conn.cursor()
    state = TransactionState(conn)
    
    # Validate connection state
    if not state.validate():
        raise TransactionError("Database connection is not valid")
    
    # Check if already in a transaction
    if state.is_in_transaction():
        logger.warning("Already in a transaction, nesting transactions")
    
    try:
        # Begin transaction
        cursor.execute("BEGIN TRANSACTION")
        state.in_transaction = True
        logger.debug("Transaction started")
        
        yield cursor
        
        # Commit if autocommit is enabled
        if autocommit:
            conn.commit()
            logger.debug("Transaction committed")
            state.in_transaction = False
        
    except sqlite3.Error as e:
        # Rollback on any database error
        try:
            conn.rollback()
            logger.debug("Transaction rolled back due to error")
        except sqlite3.Error as rollback_error:
            logger.error(f"Error during rollback: {rollback_error}")
        state.in_transaction = False
        raise TransactionError(f"Transaction failed: {e}") from e
    except Exception as e:
        # Rollback on any other error
        try:
            conn.rollback()
            logger.debug("Transaction rolled back due to exception")
        except sqlite3.Error as rollback_error:
            logger.error(f"Error during rollback: {rollback_error}")
        state.in_transaction = False
        raise
    finally:
        # Ensure we're not in a transaction state if something went wrong
        if state.in_transaction:
            try:
                conn.rollback()
                logger.warning("Transaction state was inconsistent, rolled back")
            except sqlite3.Error:
                pass
            state.in_transaction = False


@contextmanager
def savepoint(conn: sqlite3.Connection, name: Optional[str] = None) -> Iterator[sqlite3.Cursor]:
    """
    Context manager for database savepoints with automatic rollback on errors.
    
    Savepoints allow partial rollback within a transaction. This is useful when
    you want to rollback only part of a transaction while keeping the rest.
    
    Parameters:
    conn: SQLite connection object
    name: Optional name for the savepoint. If None, auto-generated.
    
    Yields:
    sqlite3.Cursor: Database cursor for executing queries
    
    Raises:
    TransactionError: If savepoint operations fail
    
    Example:
        >>> with transaction(conn) as cursor:
        ...     cursor.execute("UPDATE episodes SET status = ?", ('downloaded',))
        ...     try:
        ...         with savepoint(conn, 'episode_1') as sp_cursor:
        ...             sp_cursor.execute("UPDATE episodes SET status = ? WHERE id = ?", ('error', '123'))
        ...             # If error occurs, only this savepoint is rolled back
        ...     except TransactionError:
        ...         # Main transaction continues
        ...         pass
    """
    cursor = conn.cursor()
    
    # Generate savepoint name if not provided
    if name is None:
        # Use a counter-based name
        savepoint_name = f"sp_{id(conn)}_{hash(cursor)}"
    else:
        # Validate savepoint name (SQLite allows alphanumeric and underscores)
        if not name.replace('_', '').isalnum():
            raise TransactionError(f"Invalid savepoint name: {name}")
        savepoint_name = name
    
    try:
        # Create savepoint
        cursor.execute(f"SAVEPOINT {savepoint_name}")
        logger.debug(f"Savepoint '{savepoint_name}' created")
        
        yield cursor
        
        # Release savepoint on successful exit
        cursor.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        logger.debug(f"Savepoint '{savepoint_name}' released")
        
    except sqlite3.Error as e:
        # Rollback to savepoint on error
        try:
            cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            logger.debug(f"Rolled back to savepoint '{savepoint_name}'")
        except sqlite3.Error as rollback_error:
            logger.error(f"Error rolling back to savepoint '{savepoint_name}': {rollback_error}")
        raise TransactionError(f"Savepoint '{savepoint_name}' failed: {e}") from e
    except Exception as e:
        # Rollback to savepoint on any exception
        try:
            cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            logger.debug(f"Rolled back to savepoint '{savepoint_name}' due to exception")
        except sqlite3.Error as rollback_error:
            logger.error(f"Error rolling back to savepoint '{savepoint_name}': {rollback_error}")
        raise


def validate_transaction_state(conn: sqlite3.Connection) -> bool:
    """
    Validate that the database connection is in a valid transaction state.
    
    Parameters:
    conn: SQLite connection object
    
    Returns:
    bool: True if connection is valid, False otherwise
    
    Example:
        >>> if validate_transaction_state(conn):
        ...     conn.commit()
        ... else:
        ...     logger.error("Invalid transaction state")
    """
    try:
        # Try to execute a simple query to check connection
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        return True
    except (sqlite3.Error, AttributeError) as e:
        logger.error(f"Invalid transaction state: {e}")
        return False


def safe_commit(conn: sqlite3.Connection) -> bool:
    """
    Safely commit a transaction with validation.
    
    Parameters:
    conn: SQLite connection object
    
    Returns:
    bool: True if commit successful, False otherwise
    
    Example:
        >>> if safe_commit(conn):
        ...     logger.info("Transaction committed successfully")
    """
    if not validate_transaction_state(conn):
        logger.error("Cannot commit: invalid transaction state")
        return False
    
    try:
        conn.commit()
        logger.debug("Transaction committed successfully")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error committing transaction: {e}")
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return False


def safe_rollback(conn: sqlite3.Connection) -> bool:
    """
    Safely rollback a transaction with validation.
    
    Parameters:
    conn: SQLite connection object
    
    Returns:
    bool: True if rollback successful, False otherwise
    
    Example:
        >>> if safe_rollback(conn):
        ...     logger.info("Transaction rolled back successfully")
    """
    if not validate_transaction_state(conn):
        logger.error("Cannot rollback: invalid transaction state")
        return False
    
    try:
        conn.rollback()
        logger.debug("Transaction rolled back successfully")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error rolling back transaction: {e}")
        return False

