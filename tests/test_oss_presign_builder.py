"""Unit tests for ``novelvideo.utils.oss_client``.

These tests do not call real OSS. They stub the bucket object used by the
client and verify the path mapping and presign contract needed by Seedance2
human-review media uploads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo import config
from novelvideo.utils import oss_client


class FakeBucket:
    def __init__(self) -> None:
        self.sign_calls: list[tuple[str, str, int]] = []
        self.exists_calls: list[str] = []
        self.upload_calls: list[tuple[str, str]] = []
        self.existing_keys: set[str] = set()
        self._counter = 0

    def sign_url(self, method: str, key: str, expires: int, slash_safe: bool = True) -> str:
        self._counter += 1
        self.sign_calls.append((method, key, expires))
        return f"https://fake-oss/{key}?sig={self._counter}&exp={expires}"

    def object_exists(self, key: str) -> bool:
        self.exists_calls.append(key)
        return key in self.existing_keys

    def put_object_from_file(self, key: str, filename: str) -> None:
        self.upload_calls.append((key, filename))
        self.existing_keys.add(key)


@pytest.fixture
def output_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "output"
    root.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(root))
    monkeypatch.setattr(config, "OSS_OBJECT_PREFIX", "output", raising=False)
    return root


@pytest.fixture
def fake_bucket(monkeypatch: pytest.MonkeyPatch) -> FakeBucket:
    oss_client._reset_for_tests()
    bucket = FakeBucket()
    monkeypatch.setattr(oss_client, "get_bucket", lambda: bucket)
    yield bucket
    oss_client._reset_for_tests()


def test_local_path_to_key_maps_output_file_to_prefixed_oss_key(output_root: Path) -> None:
    local = output_root / "admin" / "projA" / "frames" / "ep001" / "beat_01.png"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"frame")

    assert oss_client.local_path_to_key(local) == "output/admin/projA/frames/ep001/beat_01.png"


def test_local_path_to_key_rejects_path_outside_output(
    tmp_path: Path,
    output_root: Path,
) -> None:
    outsider = tmp_path / "state" / "admin" / "projA" / "data.db"
    outsider.parent.mkdir(parents=True)
    outsider.write_bytes(b"x")

    assert oss_client.local_path_to_key(outsider) is None


def test_maybe_presign_existing_output_signs_only_existing_oss_object(
    output_root: Path,
    fake_bucket: FakeBucket,
) -> None:
    local = output_root / "admin" / "projA" / "assets" / "frame.png"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"x")
    fake_bucket.existing_keys.add("output/admin/projA/assets/frame.png")

    url = oss_client.maybe_presign_existing_output(local)

    assert url == "https://fake-oss/output/admin/projA/assets/frame.png?sig=1&exp=900"
    assert fake_bucket.exists_calls == ["output/admin/projA/assets/frame.png"]
    assert fake_bucket.sign_calls == [("GET", "output/admin/projA/assets/frame.png", 900)]


def test_maybe_presign_existing_output_returns_none_when_oss_object_missing(
    output_root: Path,
    fake_bucket: FakeBucket,
) -> None:
    local = output_root / "admin" / "projA" / "assets" / "frame.png"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"x")

    assert oss_client.maybe_presign_existing_output(local) is None
    assert fake_bucket.exists_calls == ["output/admin/projA/assets/frame.png"]
    assert fake_bucket.sign_calls == []
    assert fake_bucket.upload_calls == []


def test_presign_or_upload_output_uploads_missing_oss_object_then_signs(
    output_root: Path,
    fake_bucket: FakeBucket,
) -> None:
    local = output_root / "admin" / "projA" / "frames" / "ep001" / "beat_06.png"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"frame")

    url = oss_client.presign_or_upload_output(local)

    key = "output/admin/projA/frames/ep001/beat_06.png"
    assert url == f"https://fake-oss/{key}?sig=1&exp=900"
    assert fake_bucket.exists_calls == [key]
    assert fake_bucket.upload_calls == [(key, str(local))]
    assert fake_bucket.sign_calls == [("GET", key, 900)]


def test_public_endpoint_overrides_internal_for_signing(monkeypatch: pytest.MonkeyPatch) -> None:
    oss_client._reset_for_tests()
    monkeypatch.setattr(config, "OSS_ENDPOINT", "oss-cn-chengdu-internal.aliyuncs.com")
    monkeypatch.setattr(config, "OSS_PUBLIC_ENDPOINT", "oss-cn-chengdu.aliyuncs.com", raising=False)
    monkeypatch.setattr(config, "OSS_BUCKET", "bucket", raising=False)
    monkeypatch.setattr(config, "OSS_ACCESS_KEY_ID", "ak", raising=False)
    monkeypatch.setattr(config, "OSS_ACCESS_KEY_SECRET", "sk", raising=False)

    endpoint, *_ = oss_client._read_creds()

    assert endpoint == "oss-cn-chengdu.aliyuncs.com"
