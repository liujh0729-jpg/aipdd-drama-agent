"""[aipdd fork delta] 火山 AI MediaKit 生成式视频超分客户端全链路测试（respx mock）。"""

from __future__ import annotations

from pathlib import Path

import pytest
import respx
from httpx import Response

from novelvideo.generators import mediakit
from novelvideo.storage import media_relay

BASE = "https://mediakit.test/api/v1"
UPLOAD_URL = "https://upload.test/media-put"
CDN_URL = "https://cdn.test/enhanced.mp4"
TASK_ID = "amk-tool-enhance-video-generative-xyz"
ERASE_TASK_ID = "amk-tool-erase-video-subtitle-pro-xyz"
ERASE_CDN_URL = "https://cdn.test/erased.mp4"


def _upload_response() -> Response:
    return Response(
        200,
        json={
            "success": True,
            "result": {
                "file_id": "fid-abc-123",
                "method": "PUT",
                "upload_headers": [{"key": "x-token", "value": "tok"}],
                "upload_url": UPLOAD_URL,
            },
        },
    )


@pytest.mark.asyncio
async def test_enhance_video_file_full_chain_local_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 本地存储档：走 mediakit:// 原生上传分支。
    monkeypatch.setattr(media_relay, "media_relay_is_local", lambda: True)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"tiny-mp4-bytes")
    out = tmp_path / "out" / "enhanced.mp4"

    with respx.mock(assert_all_called=True) as router:
        router.post(f"{BASE}/tools-sync/request-media-upload-url").mock(
            return_value=_upload_response()
        )
        put_route = router.put(UPLOAD_URL).mock(return_value=Response(204))
        submit_route = router.post(f"{BASE}/tools/enhance-video-generative").mock(
            return_value=Response(
                200, json={"success": True, "task_id": TASK_ID}
            )
        )
        router.get(f"{BASE}/tasks/{TASK_ID}").mock(
            side_effect=[
                Response(200, json={"success": True, "status": "running"}),
                Response(
                    200,
                    json={
                        "success": True,
                        "status": "completed",
                        "result": {
                            "video_url": CDN_URL,
                            "duration": 12,
                            "fps": 30,
                            "resolution": "1080p",
                        },
                    },
                ),
            ]
        )
        router.get(CDN_URL).mock(
            return_value=Response(200, content=b"enhanced-video-bytes")
        )

        meta = await mediakit.enhance_video_file(
            source_path=src,
            output_path=out,
            resolution="1080p",
            api_key="mk-test-key",
            base_url=BASE,
            poll_interval=0.01,
        )

    assert meta["backend"] == "mediakit"
    assert meta["provider"] == "volcengine_mediakit"
    assert meta["mediakit_task_id"] == TASK_ID
    assert meta["source_media"] == "mediakit_upload"
    assert out.read_bytes() == b"enhanced-video-bytes"

    # PUT 上传必须带上 upload_headers 里的自定义头。
    put_request = put_route.calls.last.request
    assert put_request.headers.get("x-token") == "tok"
    # 提交任务的鉴权头。
    submit_request = submit_route.calls.last.request
    assert submit_request.headers.get("authorization") == "Bearer mk-test-key"


@pytest.mark.asyncio
async def test_enhance_video_file_uses_cloud_relay_public_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 云端中转档：relay 上传拿公网 URL，喂给 MediaKit 抓取。
    monkeypatch.setattr(media_relay, "media_relay_is_local", lambda: False)

    class _FakeRelay:
        def upload_bytes(self, data, **_kwargs):
            return "https://relay.example/video.mp4"

    monkeypatch.setattr(
        media_relay, "get_media_relay", lambda: _FakeRelay()
    )
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"tiny-mp4-bytes")
    out = tmp_path / "enhanced.mp4"

    with respx.mock(assert_all_called=True) as router:
        submit_route = router.post(f"{BASE}/tools/enhance-video-generative").mock(
            return_value=Response(200, json={"success": True, "task_id": TASK_ID})
        )
        router.get(f"{BASE}/tasks/{TASK_ID}").mock(
            return_value=Response(
                200,
                json={
                    "success": True,
                    "status": "completed",
                    "result": {"video_url": CDN_URL},
                },
            )
        )
        router.get(CDN_URL).mock(
            return_value=Response(200, content=b"enhanced-video-bytes")
        )

        meta = await mediakit.enhance_video_file(
            source_path=src,
            output_path=out,
            resolution="4k",
            api_key="mk-test-key",
            base_url=BASE,
            poll_interval=0.01,
        )

    assert meta["source_media"] == "relay_public_url"
    assert meta["resolution"] == "4k"
    import json

    body = json.loads(submit_route.calls.last.request.content)
    assert body["video_url"] == "https://relay.example/video.mp4"


@pytest.mark.asyncio
async def test_enhance_video_file_failed_task_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media_relay, "media_relay_is_local", lambda: True)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"tiny-mp4-bytes")
    out = tmp_path / "enhanced.mp4"

    with respx.mock(assert_all_called=True) as router:
        router.post(f"{BASE}/tools-sync/request-media-upload-url").mock(
            return_value=_upload_response()
        )
        router.put(UPLOAD_URL).mock(return_value=Response(204))
        router.post(f"{BASE}/tools/enhance-video-generative").mock(
            return_value=Response(200, json={"success": True, "task_id": TASK_ID})
        )
        router.get(f"{BASE}/tasks/{TASK_ID}").mock(
            return_value=Response(
                200,
                json={
                    "success": True,
                    "status": "failed",
                    "error": {"code": "ContentRejected", "message": "nsfw"},
                },
            )
        )

        with pytest.raises(mediakit.MediaKitError, match="ContentRejected"):
            await mediakit.enhance_video_file(
                source_path=src,
                output_path=out,
                resolution="1080p",
                api_key="mk-test-key",
                base_url=BASE,
                poll_interval=0.01,
            )


@pytest.mark.asyncio
async def test_enhance_video_file_requires_api_key(tmp_path: Path) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    with pytest.raises(mediakit.MediaKitError, match="MediaKit API Key"):
        await mediakit.enhance_video_file(
            source_path=src,
            output_path=tmp_path / "out.mp4",
            api_key="",
            base_url=BASE,
        )


def test_mediakit_configured_is_purely_key_based(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[aipdd fork delta] 引擎策略固定：有 Key 即算已配置；仅 env
    VIDEO_ENHANCE_BACKEND=ffmpeg 作为运维总开关，不再读用户可选的 backend 模式。"""
    monkeypatch.delenv("VIDEO_ENHANCE_BACKEND", raising=False)
    monkeypatch.setattr(mediakit, "mediakit_api_key", lambda: "mk-x")
    assert mediakit.mediakit_configured() is True
    # 运维级强制走本地：唯一能让 configured 变 False 的开关。
    monkeypatch.setenv("VIDEO_ENHANCE_BACKEND", "ffmpeg")
    assert mediakit.mediakit_configured() is False
    # 其它 backend 取值（含旧的 strict mediakit）不再影响判据。
    monkeypatch.setenv("VIDEO_ENHANCE_BACKEND", "mediakit")
    assert mediakit.mediakit_configured() is True
    # 无 Key 则不算已配置。
    monkeypatch.delenv("VIDEO_ENHANCE_BACKEND", raising=False)
    monkeypatch.setattr(mediakit, "mediakit_api_key", lambda: "")
    assert mediakit.mediakit_configured() is False


def test_normalize_media_ref_accepts_both_forms() -> None:
    assert mediakit._normalize_media_ref("fid-1") == "mediakit://fid-1"
    assert mediakit._normalize_media_ref("mediakit://fid-2") == "mediakit://fid-2"


# ---- [aipdd fork delta] 字幕擦除（精细化版）客户端全链路 ----


@pytest.mark.asyncio
async def test_erase_video_file_subtitle_local_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 本地存储档 + 智能自动检测：mediakit:// 上传，mode=Subtitle，默认 v5。
    monkeypatch.setattr(media_relay, "media_relay_is_local", lambda: True)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"tiny-mp4-bytes")
    out = tmp_path / "out" / "erased.mp4"

    with respx.mock(assert_all_called=True) as router:
        router.post(f"{BASE}/tools-sync/request-media-upload-url").mock(
            return_value=_upload_response()
        )
        router.put(UPLOAD_URL).mock(return_value=Response(204))
        submit_route = router.post(
            f"{BASE}/tools/erase-video-subtitle-pro"
        ).mock(
            return_value=Response(200, json={"success": True, "task_id": ERASE_TASK_ID})
        )
        router.get(f"{BASE}/tasks/{ERASE_TASK_ID}").mock(
            return_value=Response(
                200,
                json={
                    "success": True,
                    "status": "completed",
                    "result": {"video_url": ERASE_CDN_URL, "duration": 12},
                },
            )
        )
        router.get(ERASE_CDN_URL).mock(
            return_value=Response(200, content=b"erased-video-bytes")
        )

        meta = await mediakit.erase_video_file(
            source_path=src,
            output_path=out,
            mode="Subtitle",
            api_key="mk-test-key",
            base_url=BASE,
            poll_interval=0.01,
        )

    assert meta["backend"] == "mediakit"
    assert meta["erase_mode"] == "Subtitle"
    assert meta["mediakit_task_id"] == ERASE_TASK_ID
    assert meta["source_media"] == "mediakit_upload"
    assert out.read_bytes() == b"erased-video-bytes"

    import json

    body = json.loads(submit_route.calls.last.request.content)
    assert body["mode"] == "Subtitle"
    assert body["model_version"] == "v5"
    assert "erase_ratio_location" not in body
    assert body["video_url"].startswith("mediakit://")
    assert submit_route.calls.last.request.headers.get(
        "authorization"
    ) == "Bearer mk-test-key"


@pytest.mark.asyncio
async def test_erase_video_file_box_text_mode_cloud_relay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 云端中转 + 框选擦除：relay 拿公网 URL，mode=Text + erase_ratio_location 透传。
    monkeypatch.setattr(media_relay, "media_relay_is_local", lambda: False)

    class _FakeRelay:
        def upload_bytes(self, data, **_kwargs):
            return "https://relay.example/video.mp4"

    monkeypatch.setattr(media_relay, "get_media_relay", lambda: _FakeRelay())
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"tiny-mp4-bytes")
    out = tmp_path / "erased.mp4"

    with respx.mock(assert_all_called=True) as router:
        submit_route = router.post(
            f"{BASE}/tools/erase-video-subtitle-pro"
        ).mock(
            return_value=Response(200, json={"success": True, "task_id": ERASE_TASK_ID})
        )
        router.get(f"{BASE}/tasks/{ERASE_TASK_ID}").mock(
            return_value=Response(
                200,
                json={
                    "success": True,
                    "status": "completed",
                    "result": {"video_url": ERASE_CDN_URL},
                },
            )
        )
        router.get(ERASE_CDN_URL).mock(
            return_value=Response(200, content=b"erased-video-bytes")
        )

        meta = await mediakit.erase_video_file(
            source_path=src,
            output_path=out,
            mode="Text",
            erase_ratio_location=[
                {
                    "top_left_x": 0.62,
                    "top_left_y": 0.86,
                    "bottom_right_x": 0.97,
                    "bottom_right_y": 0.96,
                }
            ],
            api_key="mk-test-key",
            base_url=BASE,
            poll_interval=0.01,
        )

    assert meta["source_media"] == "relay_public_url"
    assert meta["erase_mode"] == "Text"
    import json

    body = json.loads(submit_route.calls.last.request.content)
    assert body["video_url"] == "https://relay.example/video.mp4"
    assert body["mode"] == "Text"
    assert body["erase_ratio_location"][0]["bottom_right_x"] == 0.97


@pytest.mark.asyncio
async def test_erase_video_file_failed_task_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media_relay, "media_relay_is_local", lambda: True)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"tiny-mp4-bytes")
    out = tmp_path / "erased.mp4"

    with respx.mock(assert_all_called=True) as router:
        router.post(f"{BASE}/tools-sync/request-media-upload-url").mock(
            return_value=_upload_response()
        )
        router.put(UPLOAD_URL).mock(return_value=Response(204))
        router.post(f"{BASE}/tools/erase-video-subtitle-pro").mock(
            return_value=Response(200, json={"success": True, "task_id": ERASE_TASK_ID})
        )
        router.get(f"{BASE}/tasks/{ERASE_TASK_ID}").mock(
            return_value=Response(
                200,
                json={
                    "success": True,
                    "status": "failed",
                    "error": {"code": "InvalidVideo", "message": "too long"},
                },
            )
        )

        with pytest.raises(mediakit.MediaKitError, match="InvalidVideo"):
            await mediakit.erase_video_file(
                source_path=src,
                output_path=out,
                mode="Subtitle",
                api_key="mk-test-key",
                base_url=BASE,
                poll_interval=0.01,
            )


@pytest.mark.asyncio
async def test_erase_video_file_requires_api_key(tmp_path: Path) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    with pytest.raises(mediakit.MediaKitError, match="MediaKit API Key"):
        await mediakit.erase_video_file(
            source_path=src,
            output_path=tmp_path / "out.mp4",
            api_key="",
            base_url=BASE,
        )
