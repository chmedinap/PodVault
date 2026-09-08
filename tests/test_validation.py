"""
Tests for input validation, in particular `validate_and_quote_table_name`
which is the app's SQL-injection guard for the one place table names are
interpolated into raw SQL strings (podcast names become table names).
"""

import pytest

from podvault.database.queries import validate_and_quote_table_name
from podvault.validation import validate_feed_url, validate_file_path, validate_podcast_name


def test_validate_feed_url_accepts_https():
    is_valid, error = validate_feed_url('https://example.com/feed.xml')
    assert is_valid
    assert error is None


def test_validate_feed_url_rejects_missing_scheme():
    is_valid, error = validate_feed_url('example.com/feed.xml')
    assert not is_valid


def test_validate_feed_url_rejects_non_http_scheme():
    is_valid, error = validate_feed_url('file:///etc/passwd')
    assert not is_valid


def test_validate_feed_url_rejects_empty():
    is_valid, error = validate_feed_url('')
    assert not is_valid


def test_validate_podcast_name_accepts_normal_name():
    is_valid, error = validate_podcast_name('my_podcast')
    assert is_valid


def test_validate_podcast_name_rejects_empty():
    is_valid, error = validate_podcast_name('')
    assert not is_valid


def test_validate_file_path_rejects_traversal():
    is_valid, error = validate_file_path('../../etc/passwd')
    assert not is_valid


def test_validate_and_quote_table_name_quotes_valid_name():
    assert validate_and_quote_table_name('my_podcast') == '"my_podcast"'


def test_validate_and_quote_table_name_rejects_sql_injection_attempt():
    with pytest.raises(ValueError):
        validate_and_quote_table_name('my_podcast"; DROP TABLE summary; --')


def test_validate_and_quote_table_name_rejects_uppercase_and_dashes():
    with pytest.raises(ValueError):
        validate_and_quote_table_name('My-Podcast')


def test_validate_and_quote_table_name_rejects_empty():
    with pytest.raises(ValueError):
        validate_and_quote_table_name('')
