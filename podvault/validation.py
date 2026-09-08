"""
Input validation utilities for PodVault.

This module provides validation functions for URLs, podcast names, and file paths
to prevent errors and security issues.
"""

import re
import os
from urllib.parse import urlparse
from pathlib import Path
from typing import Tuple, Optional


# Validation constants
MAX_PODCAST_NAME_LENGTH = 100
MIN_PODCAST_NAME_LENGTH = 1
MAX_URL_LENGTH = 2048
MAX_FILE_PATH_LENGTH = 260  # Windows path limit, reasonable for cross-platform
ALLOWED_URL_SCHEMES = ('http', 'https')
ALLOWED_PODCAST_NAME_CHARS = re.compile(r'^[a-zA-Z0-9_\-\s\.]+$')


class ValidationError(Exception):
    """Raised when input validation fails."""
    pass


def validate_feed_url(url: str) -> Tuple[bool, Optional[str]]:
    """
    Validate a feed URL format and basic accessibility.
    
    Parameters:
    url: str - URL to validate
    
    Returns:
    Tuple[bool, Optional[str]]: (is_valid, error_message)
        - If valid: (True, None)
        - If invalid: (False, error_message)
    
    Example:
        >>> is_valid, error = validate_feed_url("https://feeds.example.com/podcast.rss")
        >>> if not is_valid:
        ...     print(f"Invalid URL: {error}")
    """
    if not url:
        return False, "URL cannot be empty"
    
    if not isinstance(url, str):
        return False, f"URL must be a string, got {type(url).__name__}"
    
    # Check length
    if len(url) > MAX_URL_LENGTH:
        return False, f"URL exceeds maximum length of {MAX_URL_LENGTH} characters"
    
    # Check for basic URL format
    if not url.strip():
        return False, "URL cannot be whitespace only"
    
    # Parse URL
    try:
        parsed = urlparse(url.strip())
    except Exception as e:
        return False, f"Invalid URL format: {e}"
    
    # Check scheme
    if not parsed.scheme:
        return False, "URL must include a scheme (http:// or https://)"
    
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        return False, f"URL scheme must be one of {ALLOWED_URL_SCHEMES}, got '{parsed.scheme}'"
    
    # Check netloc (domain)
    if not parsed.netloc:
        return False, "URL must include a domain name"
    
    # Check for suspicious patterns (basic security check)
    if '..' in parsed.path or '//' in parsed.path:
        return False, "URL contains suspicious path patterns"
    
    return True, None


def validate_podcast_name(name: str) -> Tuple[bool, Optional[str]]:
    """
    Validate a podcast name for use as a database table name and filename.
    
    Parameters:
    name: str - Podcast name to validate
    
    Returns:
    Tuple[bool, Optional[str]]: (is_valid, error_message)
        - If valid: (True, None)
        - If invalid: (False, error_message)
    
    Example:
        >>> is_valid, error = validate_podcast_name("My Podcast")
        >>> if not is_valid:
        ...     print(f"Invalid name: {error}")
    """
    if not name:
        return False, "Podcast name cannot be empty"
    
    if not isinstance(name, str):
        return False, f"Podcast name must be a string, got {type(name).__name__}"
    
    # Check length
    if len(name) < MIN_PODCAST_NAME_LENGTH:
        return False, f"Podcast name must be at least {MIN_PODCAST_NAME_LENGTH} character(s)"
    
    if len(name) > MAX_PODCAST_NAME_LENGTH:
        return False, f"Podcast name exceeds maximum length of {MAX_PODCAST_NAME_LENGTH} characters"
    
    # Check for only whitespace
    if not name.strip():
        return False, "Podcast name cannot be whitespace only"
    
    # Check for allowed characters (alphanumeric, underscore, hyphen, space, period)
    # This is more restrictive than what normalize() might produce, but safer
    if not ALLOWED_PODCAST_NAME_CHARS.match(name):
        return False, (
            "Podcast name contains invalid characters. "
            "Only letters, numbers, underscores, hyphens, spaces, and periods are allowed."
        )
    
    # Check for SQLite reserved words (basic check)
    sqlite_reserved = {
        'select', 'insert', 'update', 'delete', 'create', 'drop', 'alter',
        'table', 'index', 'view', 'trigger', 'database', 'schema'
    }
    if name.lower() in sqlite_reserved:
        return False, f"Podcast name cannot be a SQLite reserved word: '{name}'"
    
    return True, None


def validate_file_path(file_path: str, must_exist: bool = False, must_be_file: bool = False) -> Tuple[bool, Optional[str]]:
    """
    Validate a file path for safety and basic requirements.
    
    Parameters:
    file_path: str - File path to validate
    must_exist: bool - If True, path must exist (default: False)
    must_be_file: bool - If True, path must be a file (not directory) (default: False)
    
    Returns:
    Tuple[bool, Optional[str]]: (is_valid, error_message)
        - If valid: (True, None)
        - If invalid: (False, error_message)
    
    Example:
        >>> is_valid, error = validate_file_path("feeds.txt", must_exist=True)
        >>> if not is_valid:
        ...     print(f"Invalid path: {error}")
    """
    if not file_path:
        return False, "File path cannot be empty"
    
    if not isinstance(file_path, str):
        return False, f"File path must be a string, got {type(file_path).__name__}"
    
    # Check length
    if len(file_path) > MAX_FILE_PATH_LENGTH:
        return False, f"File path exceeds maximum length of {MAX_FILE_PATH_LENGTH} characters"
    
    # Check for only whitespace
    if not file_path.strip():
        return False, "File path cannot be whitespace only"
    
    # Check for path traversal attempts
    if '..' in file_path or file_path.startswith('/') and not os.path.isabs(file_path):
        # Allow absolute paths, but be cautious
        normalized = os.path.normpath(file_path)
        if '..' in normalized:
            return False, "File path contains path traversal patterns (..)"
    
    # Check if path exists (if required)
    if must_exist:
        if not os.path.exists(file_path):
            return False, f"File path does not exist: {file_path}"
        
        # Check if it's a file (if required)
        if must_be_file:
            if not os.path.isfile(file_path):
                return False, f"Path exists but is not a file: {file_path}"
    
    # Check for null bytes (security issue)
    if '\x00' in file_path:
        return False, "File path contains null bytes"
    
    return True, None


def sanitize_podcast_name(name: str) -> str:
    """
    Sanitize a podcast name to make it safe for use as a database table name.
    
    This function normalizes the name and ensures it's safe, but does not
    validate it. Use validate_podcast_name() for validation.
    
    Parameters:
    name: str - Podcast name to sanitize
    
    Returns:
    str - Sanitized podcast name
    
    Example:
        >>> sanitized = sanitize_podcast_name("My Podcast!")
        >>> print(sanitized)
        "My_Podcast"
    """
    if not name or not isinstance(name, str):
        return "unknown_podcast"
    
    # Remove leading/trailing whitespace
    name = name.strip()
    
    # Replace spaces and special characters with underscores
    name = re.sub(r'[^\w\-\.]', '_', name)
    
    # Remove multiple consecutive underscores
    name = re.sub(r'_+', '_', name)
    
    # Remove leading/trailing underscores
    name = name.strip('_')
    
    # Ensure it's not empty after sanitization
    if not name:
        return "unknown_podcast"
    
    # Ensure it doesn't start with a number (SQLite table name requirement)
    if name[0].isdigit():
        name = f"podcast_{name}"
    
    return name

