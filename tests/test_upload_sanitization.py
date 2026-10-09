"""Tests for upload filename sanitization and path traversal prevention.

These exercise the shared helpers used by both the REST API upload route
and the NiceGUI ingest page, so sanitization regressions surface in tests.
"""

from pathlib import Path

from novelvideo.utils.upload_safety import is_safe_upload_target, sanitize_upload_filename


def test_normal_filename() -> None:
    assert sanitize_upload_filename("story.txt") == "story.txt"


def test_strip_unix_path() -> None:
    assert sanitize_upload_filename("/etc/passwd") == "passwd"


def test_strip_relative_traversal() -> None:
    assert sanitize_upload_filename("../../etc/passwd") == "passwd"


def test_strip_windows_path() -> None:
    result = sanitize_upload_filename(r"C:\Users\evil\payload.html")
    assert "/" not in result
    assert "\\" not in result


def test_invalid_names_fallback() -> None:
    assert sanitize_upload_filename("") == "upload.txt"
    assert sanitize_upload_filename(".") == "upload.txt"
    assert sanitize_upload_filename("..") == "upload.txt"
    assert sanitize_upload_filename("/") == "upload.txt"
    assert sanitize_upload_filename(None) == "upload.txt"


def test_traversal_blocked(tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    assert not is_safe_upload_target(upload_dir, "../escape.txt")


def test_absolute_path_blocked(tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    assert not is_safe_upload_target(upload_dir, "/tmp/evil.txt")


def test_sanitized_name_is_safe(tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    safe = sanitize_upload_filename("../../etc/passwd")
    assert is_safe_upload_target(upload_dir, safe)
