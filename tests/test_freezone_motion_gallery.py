# SPDX-License-Identifier: Elastic-2.0
# [aipdd fork delta] 动效大片图册：目录接口与素材托管端点的契约测试。
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.api.routes import motion as motion_routes
from novelvideo.freezone import motion_media, motion_templates


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(freezone_routes.router, prefix="/api/v1")
    app.include_router(motion_routes.router, prefix="/api/v1")

    async def fake_resolve(*_args, **_kwargs):
        return None

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", fake_resolve)
    app.dependency_overrides[freezone_routes.get_api_user] = lambda: {
        "id": "owner_1",
        "username": "admin",
    }
    # 素材缓存根跟着临时状态卷走，测试不碰真实 STATE_DIR。
    monkeypatch.setattr(motion_media, "STATE_DIR", tmp_path)
    return TestClient(app)


def test_motion_cases_envelope_keeps_data_a_bare_list(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)

    payload = client.get("/api/v1/projects/58/freezone/motion/cases").json()

    cases = motion_templates.load_motion_cases()
    assert payload["ok"] is True
    # 老客户端只读 data，并且按数组用；换成对象会当场崩。
    assert isinstance(payload["data"], list)
    assert len(payload["data"]) == len(cases)
    assert [case["id"] for case in payload["data"]] == [case["id"] for case in cases]
    # 图册的展示口径全挂在兄弟键上：分类顺序、方法卡、渲染契约、素材前缀。
    assert payload["categories"]
    assert payload["methods"]
    assert payload["render_contract"]
    assert payload["asset_base"] == "/api/v1/motion-gallery"
    assert payload["videos_available"] is False
    assert payload["version"] == motion_templates.get_motion_manifest_version()
    first = payload["data"][0]
    assert first["poster_ready"] is motion_media.has_poster(first["id"])
    for field in ("label", "category", "brief", "prompt_cn", "cover", "tags", "media"):
        assert field in first


def test_motion_case_prompt_carries_the_render_contract(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    case = motion_templates.load_motion_cases()[0]

    payload = client.get(
        f"/api/v1/projects/58/freezone/motion/cases/{case['id']}/prompt"
    ).json()

    # 图册复制、提示词库和渲染任务必须拿到逐字相同的文本，所以拼装只有一处。
    assert payload["data"]["prompt"] == motion_templates.compose_motion_prompt(case["id"])
    assert payload["data"]["prompt"].startswith(case["prompt_cn"])
    manifest = motion_templates.load_motion_manifest()
    assert manifest["render_contract"] in payload["data"]["prompt"]

    assert (
        client.get("/api/v1/projects/58/freezone/motion/cases/no_such/prompt").status_code
        == 404
    )


def test_bundled_poster_is_served_and_unknown_case_is_404(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    case = motion_templates.load_motion_cases()[0]
    assert motion_media.has_poster(case["id"]) is True
    poster = motion_media.poster_path(case["id"]).read_bytes()

    response = client.get(f"/api/v1/motion-gallery/{case['id']}/poster.webp")

    assert response.status_code == 200
    assert response.content == poster
    assert response.headers["content-type"] == motion_media.sniff_content_type(poster)
    assert client.get("/api/v1/motion-gallery/no_such/poster.webp").status_code == 404


@pytest.mark.parametrize(
    "case_id,filename",
    [
        ("no_such", "poster.webp"),
        ("M43", "not_in_manifest.mp4"),
        ("..", "poster.webp"),
        ("M43", "..%2F..%2F..%2Fetc%2Fpasswd"),
    ],
)
def test_media_endpoint_only_serves_manifest_entries(
    monkeypatch, tmp_path, case_id: str, filename: str
) -> None:
    """清单是唯一 allowlist：越界路径与没登记的文件名一律取不到。"""
    client = _client(monkeypatch, tmp_path)

    response = client.get(f"/api/v1/motion-gallery/{case_id}/{filename}")

    # 越过 router 的路径段会被 Starlette 直接判 404；清单对不上是端点自己判 404。
    assert response.status_code == 404


def test_cached_video_is_served_with_range_support(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    case = motion_templates.load_motion_cases()[0]
    blob = b"ftyp" + b"0" * 1_020  # 假原片：只测分片协议，不测解码
    target = motion_media.video_cache_path(case["id"], case["media"]["file"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(blob)
    url = f"/api/v1/motion-gallery/{case['id']}/{case['media']['file']}"

    full = client.get(url)
    assert full.status_code == 200
    assert full.content == blob
    assert full.headers["accept-ranges"] == "bytes"

    partial = client.get(url, headers={"Range": "bytes=100-199"})
    assert partial.status_code == 206
    assert partial.content == blob[100:200]
    assert partial.headers["content-range"] == f"bytes 100-199/{len(blob)}"

    tail = client.get(url, headers={"Range": "bytes=-10"})
    assert tail.status_code == 206
    assert tail.content == blob[-10:]

    bad = client.get(url, headers={"Range": "bytes=99999-"})
    assert bad.status_code == 416
    assert bad.headers["content-range"] == f"bytes */{len(blob)}"


def test_videos_available_flag_turns_on_with_a_cached_original(
    monkeypatch, tmp_path
) -> None:
    client = _client(monkeypatch, tmp_path)
    cases_url = "/api/v1/projects/58/freezone/motion/cases"
    assert client.get(cases_url).json()["videos_available"] is False

    case = motion_templates.load_motion_cases()[0]
    target = motion_media.video_cache_path(case["id"], case["media"]["file"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")

    assert client.get(cases_url).json()["videos_available"] is True


def test_video_ready_is_per_case(monkeypatch, tmp_path) -> None:
    """没配镜像时只有真下过的那条能播：逐条给前端，别拿全局标志给所有案例都摆播放按钮。"""
    client = _client(monkeypatch, tmp_path)
    cases_url = "/api/v1/projects/58/freezone/motion/cases"
    first, second = motion_templates.load_motion_cases()[:2]
    target = motion_media.video_cache_path(first["id"], first["media"]["file"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"ftyp")

    body = client.get(cases_url).json()
    ready = {case["id"]: case["video_ready"] for case in body["data"]}
    assert ready[first["id"]] is True
    assert ready[second["id"]] is False

    monkeypatch.setenv("MOTION_GALLERY_MIRROR", "https://mirror.example/motion/")
    body = client.get(cases_url).json()
    assert all(case["video_ready"] is True for case in body["data"])


@pytest.mark.parametrize(
    ("filename", "head", "expected"),
    [
        ("a.mp4", b"\x00\x00\x00\x20ftypisom" + b"0" * 8, True),
        ("a.mp4", b"<!DOCTYPE html><html>", False),
        ("a.webm", b"\x1a\x45\xdf\xa3\x01\x02\x03\x04" + b"0" * 8, True),
        ("a.webm", b"<html>", False),
        # 认不出容器的扩展名放行：体检不能反过来把真素材挡在门外。
        ("a.avi", b"RIFF\x00\x00\x00\x00AVI LIST", True),
    ],
)
def test_unhashed_download_must_at_least_look_like_a_video(
    tmp_path: Path, filename: str, head: bytes, expected: bool
) -> None:
    """清单没带 sha256 时的最低限度体检：镜像回 200 + 错误页不该被永久缓存。"""
    path = tmp_path / filename
    path.write_bytes(head)
    assert motion_media._looks_like_video_container(path, filename) is expected


def test_mirror_env_is_honoured_for_availability(monkeypatch) -> None:
    monkeypatch.setenv("MOTION_GALLERY_MIRROR", "https://mirror.example/motion/")

    assert motion_media.mirror_base() == "https://mirror.example/motion"
    assert motion_media.videos_available() is True


def test_asset_base_can_point_at_an_external_cdn(monkeypatch) -> None:
    from novelvideo.freezone import route_helpers

    monkeypatch.delenv("MOTION_GALLERY_ASSET_BASE", raising=False)
    assert route_helpers.get_freezone_motion_asset_base() == "/api/v1/motion-gallery"

    external = "https://cdn.example.com/motion"
    monkeypatch.setenv("MOTION_GALLERY_ASSET_BASE", external)
    assert route_helpers.get_freezone_motion_asset_base() == external
