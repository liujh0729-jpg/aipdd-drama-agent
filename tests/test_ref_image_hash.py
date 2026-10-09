from __future__ import annotations

import json

import pytest

from novelvideo.utils.ref_image_hash import RefImageHasher, file_sha256


def test_ref_image_hasher_roundtrip_and_cache(tmp_path) -> None:
    image = tmp_path / "scene.png"
    image.write_bytes(b"scene-reference-v1")

    hasher = RefImageHasher(tmp_path / "cache")
    digest = hasher.hash(image)

    assert digest == file_sha256(image)
    assert hasher(image) == digest

    cache_file = tmp_path / "cache" / "_ref_image_hash_cache.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8"))
    assert cache[str(image)]["sha256"] == digest
    assert cache[str(image)]["size"] == image.stat().st_size


def test_ref_image_hasher_rehashes_when_same_size_file_changes(tmp_path) -> None:
    image = tmp_path / "scene.png"
    image.write_bytes(b"AAAA")
    hasher = RefImageHasher(tmp_path / "cache")

    first = hasher.hash(image)
    image.write_bytes(b"BBBB")
    second = hasher.hash(image)

    assert first != second
    assert second == file_sha256(image)


def test_ref_image_hasher_rejects_missing_file(tmp_path) -> None:
    hasher = RefImageHasher(tmp_path / "cache")

    with pytest.raises(FileNotFoundError):
        hasher.hash(tmp_path / "missing.png")
