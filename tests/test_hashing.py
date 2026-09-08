"""Tests for `podvault.hashing.compute_checksum`."""

import hashlib

from podvault.hashing import compute_checksum


def test_compute_checksum_matches_hashlib(tmp_path):
    file_path = tmp_path / 'episode.mp3'
    content = b'fake mp3 content' * 1000
    file_path.write_bytes(content)

    assert compute_checksum(file_path) == hashlib.sha256(content).hexdigest()


def test_compute_checksum_reads_in_chunks(tmp_path):
    file_path = tmp_path / 'episode.mp3'
    content = b'x' * 10_000
    file_path.write_bytes(content)

    assert compute_checksum(file_path, chunk_size=7) == hashlib.sha256(content).hexdigest()


def test_compute_checksum_different_content_differs(tmp_path):
    a = tmp_path / 'a.mp3'
    b = tmp_path / 'b.mp3'
    a.write_bytes(b'content a')
    b.write_bytes(b'content b')

    assert compute_checksum(a) != compute_checksum(b)
