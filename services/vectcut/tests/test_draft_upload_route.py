# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""``POST /aipdd/drafts/upload`` 与 ``GET /aipdd/drafts`` 的回归测试。

资产库（req15）的「上传文件夹」与「同步草稿目录」依赖这两条路由：上传把平铺
文件 + relative_paths 复原成草稿文件夹（校验路径穿越、数量匹配、目录冲突，失败
回收半成品），列表把草稿目录里的文件夹列给 api 侧做补登记。
"""

from __future__ import annotations

from io import BytesIO

import pytest


@pytest.fixture()
def drafts_client(monkeypatch, tmp_path):
    flask = pytest.importorskip("flask")
    from aipdd import server

    drafts = tmp_path / "drafts"
    drafts.mkdir(parents=True)
    monkeypatch.setattr(server, "default_draft_dir", lambda: drafts)

    app = flask.Flask(__name__)
    server._register_extras(app)
    return app.test_client(), drafts, tmp_path


def _upload_data(
    draft_id: str,
    relative_paths: list[str],
    blobs: list[bytes],
    **extra: object,
) -> dict:
    return {
        "draft_id": draft_id,
        "relative_paths": relative_paths,
        "files": [
            (BytesIO(blob), f"file-{index}") for index, blob in enumerate(blobs)
        ],
        **extra,
    }


def test_upload_lands_files_with_relative_paths(drafts_client) -> None:
    http, drafts, _tmp = drafts_client

    response = http.post(
        "/aipdd/drafts/upload",
        data=_upload_data(
            "up_abc123",
            ["draft_content.json", "materials/clip.mp4"],
            [b'{"ok": true}', b"video-bytes"],
        ),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["output"]["files"] == 2
    assert payload["output"]["size_bytes"] == len(b'{"ok": true}') + len(
        b"video-bytes"
    )
    assert (drafts / "up_abc123" / "draft_content.json").read_bytes() == b'{"ok": true}'
    assert (
        drafts / "up_abc123" / "materials" / "clip.mp4"
    ).read_bytes() == b"video-bytes"


def test_upload_reclaims_partial_files_on_invalid_path(drafts_client) -> None:
    """任一相对路径非法：整份上传作废，已写入的半成品目录被回收。"""
    http, drafts, _tmp = drafts_client

    response = http.post(
        "/aipdd/drafts/upload",
        data=_upload_data("up_mix", ["ok.txt", "/absolute.txt"], [b"ok", b"bad"]),
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["success"] is False
    assert not (drafts / "up_mix").exists()


def test_upload_rejects_path_traversal(drafts_client) -> None:
    http, drafts, _tmp = drafts_client

    response = http.post(
        "/aipdd/drafts/upload",
        data=_upload_data("up_evil", ["../escape.txt"], [b"evil"]),
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert not (drafts / "up_evil").exists()


def test_upload_rejects_mismatched_relative_paths(drafts_client) -> None:
    http, _drafts, _tmp = drafts_client

    response = http.post(
        "/aipdd/drafts/upload",
        data=_upload_data("up_mismatch", ["only-one.txt"], [b"a", b"b"]),
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "relative_paths" in response.get_json()["error"]


def test_upload_rejects_existing_draft(drafts_client) -> None:
    http, drafts, _tmp = drafts_client
    (drafts / "up_taken").mkdir()

    response = http.post(
        "/aipdd/drafts/upload",
        data=_upload_data("up_taken", ["a.txt"], [b"x"]),
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "already exists" in response.get_json()["error"]


@pytest.mark.parametrize("draft_id", ["bad!id", "", "../up"])
def test_upload_rejects_invalid_draft_ids(drafts_client, draft_id: str) -> None:
    http, _drafts, _tmp = drafts_client

    response = http.post(
        "/aipdd/drafts/upload",
        data=_upload_data(draft_id, ["a.txt"], [b"x"]),
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_upload_honors_draft_folder_override(drafts_client) -> None:
    """设置 → 剪映草稿目录：上传落到覆盖目录，默认目录不被动。"""
    http, drafts, tmp = drafts_client
    override = tmp / "override_drafts"
    override.mkdir()

    response = http.post(
        "/aipdd/drafts/upload",
        data=_upload_data(
            "up_override", ["a.txt"], [b"x"], draft_folder=str(override)
        ),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert (override / "up_override" / "a.txt").read_bytes() == b"x"
    assert not (drafts / "up_override").exists()


def test_upload_rejects_relative_draft_folder_override(drafts_client) -> None:
    http, _drafts, _tmp = drafts_client

    response = http.post(
        "/aipdd/drafts/upload",
        data=_upload_data("up_rel", ["a.txt"], [b"x"], draft_folder="relative/dir"),
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "invalid draft_folder" in response.get_json()["error"]


def test_list_drafts_lists_folders(drafts_client) -> None:
    http, drafts, _tmp = drafts_client
    (drafts / "dfd_one").mkdir()
    (drafts / "dfd_one" / "draft_content.json").write_bytes(b"{}")
    (drafts / "dfd_two").mkdir()
    (drafts / ".hidden").mkdir()
    (drafts / "loose.txt").write_bytes(b"x")

    response = http.get("/aipdd/drafts")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert [entry["draft_id"] for entry in payload["drafts"]] == [
        "dfd_one",
        "dfd_two",
    ]
    one = payload["drafts"][0]
    assert one["file_count"] == 1
    assert one["size_bytes"] == 2