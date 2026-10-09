# req44 tests
"""[aipdd fork delta req44] AIPDD 数字资产云中转。

这一档的意义是把「本地素材」变成「上游真的能拉走的公网直链」：实测
``POST {站点根}/oss/upload`` 用的就是已连接渠道自己的 ``X-API-Key``，回 ``code:0`` +
无签名裸链（GET 200 / Range 206）。用例把三条硬事实钉住：端点必须拼**站点根**
（``/v1/oss/upload`` 实测 404）、成功码是 **0**（按 ``code!==200`` 判会把成功当失败）、
拿不到公网直链时必须报错而不是静默回 data URL。
"""

from __future__ import annotations

import httpx
import pytest

from novelvideo import config
from novelvideo.model_gateway_settings import (
    get_effective_media_relay_config,
    save_media_relay_config,
)
from novelvideo.storage import media_relay


def _channels() -> list[dict]:
    return [
        {
            "provider": "newapi",
            "baseUrl": "https://other-gw.example/v1",
            "upstreamKey": "sk-other",
        },
        {
            "provider": "aipdd",
            "baseUrl": "https://gw.example/v1",
            "upstreamKey": "sk-aipdd",
        },
    ]


def _patch_channels(monkeypatch: pytest.MonkeyPatch, channels: list[dict]) -> None:
    import novelvideo.model_gateway_settings as mgs

    monkeypatch.setattr(
        mgs, "get_newapi_provider_channels", lambda *args, **kwargs: channels
    )


class _Resp:
    def __init__(self, payload=None, *, status: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status
        self.text = text if text else ("" if payload is None else "json-body")

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return None
        request = httpx.Request("POST", "https://gw.example/oss/upload")
        response = httpx.Response(self.status_code, request=request, text=self.text)
        raise httpx.HTTPStatusError("upload rejected", request=request, response=response)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _fake_client(
    monkeypatch: pytest.MonkeyPatch, responses: list[_Resp]
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    pending = iter(responses)

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, *, headers=None, data=None, files=None, json=None):
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "data": data,
                    "files": files,
                    "json": json,
                }
            )
            return next(pending)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    return calls


def _relay(**kwargs) -> media_relay.AipddCloudRelay:
    return media_relay.AipddCloudRelay(
        origin="https://gw.example/v1", api_key="sk-aipdd", **kwargs
    )


def test_site_origin_strips_the_api_prefix() -> None:
    assert media_relay._site_origin("https://gw.example/v1") == "https://gw.example"
    assert media_relay._site_origin("https://gw.example") == "https://gw.example"
    assert media_relay._site_origin("") == ""


def test_upload_target_prefers_the_aipdd_channel(monkeypatch) -> None:
    _patch_channels(monkeypatch, _channels())
    monkeypatch.setattr(config, "AIPDD_CLOUD_BASE_URL", "")
    monkeypatch.setattr(config, "AIPDD_CLOUD_API_KEY", "")

    origin, api_key = media_relay.resolve_aipdd_cloud_target()

    assert (origin, api_key) == ("https://gw.example", "sk-aipdd")


def test_saved_credentials_beat_env_and_channel(monkeypatch) -> None:
    # 优先级：设置页显式填写 > env > 已连接的 AIPDD 渠道。
    _patch_channels(monkeypatch, _channels())
    monkeypatch.setattr(config, "AIPDD_CLOUD_BASE_URL", "https://env.example/v1")
    monkeypatch.setattr(config, "AIPDD_CLOUD_API_KEY", "sk-env")

    assert media_relay.resolve_aipdd_cloud_target(
        saved_base_url="https://saved.example/v1", saved_api_key="sk-saved"
    ) == ("https://saved.example", "sk-saved")


def test_half_filled_settings_mix_with_the_channel(monkeypatch) -> None:
    # 只填了一半也能跑：缺的一半才回落渠道（用户自己的 Key 永远优先）。
    _patch_channels(monkeypatch, _channels())
    monkeypatch.setattr(config, "AIPDD_CLOUD_BASE_URL", "")
    monkeypatch.setattr(config, "AIPDD_CLOUD_API_KEY", "")

    assert media_relay.resolve_aipdd_cloud_target(saved_api_key="sk-saved") == (
        "https://gw.example",
        "sk-saved",
    )
    assert media_relay.resolve_aipdd_cloud_target(
        saved_base_url="https://manual.example/v1"
    ) == ("https://manual.example", "sk-aipdd")


def test_upload_target_is_empty_without_a_channel(monkeypatch) -> None:
    _patch_channels(monkeypatch, [])
    monkeypatch.setattr(config, "AIPDD_CLOUD_BASE_URL", "")
    monkeypatch.setattr(config, "AIPDD_CLOUD_API_KEY", "")

    assert media_relay.resolve_aipdd_cloud_target() == ("", "")


def test_env_override_beats_channel_lookup(monkeypatch) -> None:
    _patch_channels(monkeypatch, _channels())
    monkeypatch.setattr(config, "AIPDD_CLOUD_BASE_URL", "https://manual.example/v1")
    monkeypatch.setattr(config, "AIPDD_CLOUD_API_KEY", "sk-manual")

    assert media_relay.resolve_aipdd_cloud_target() == (
        "https://manual.example",
        "sk-manual",
    )


def test_relay_requires_a_resolved_channel(monkeypatch) -> None:
    _patch_channels(monkeypatch, [])
    monkeypatch.setattr(config, "AIPDD_CLOUD_BASE_URL", "")
    monkeypatch.setattr(config, "AIPDD_CLOUD_API_KEY", "")

    with pytest.raises(
        media_relay.MediaRelayConfigError, match="AIPDD 参考媒体中转未就绪"
    ):
        media_relay.AipddCloudRelay()


def test_upload_posts_to_site_root_and_returns_public_url(monkeypatch) -> None:
    calls = _fake_client(
        monkeypatch,
        [_Resp({"code": 0, "data": {"url": "https://oss.example/a/b.mp4"}})],
    )

    url = _relay().upload_bytes(b"video-bytes", ext="mp4", resource_type="video")

    assert url == "https://oss.example/a/b.mp4"
    # /v1/oss/upload 实测 404：端点必须拼在站点根。
    assert calls[0]["url"] == "https://gw.example/oss/upload"
    assert calls[0]["headers"] == {"X-API-Key": "sk-aipdd"}
    assert calls[0]["data"] == {
        "bucket_name": media_relay.AIPDD_CLOUD_DEFAULT_BUCKET,
        "prefix": media_relay.AIPDD_CLOUD_DEFAULT_PREFIX,
    }
    filename, payload, content_type = calls[0]["files"]["file"]
    assert str(filename).endswith(".mp4")
    assert payload == b"video-bytes"
    assert content_type == "video/mp4"


def test_upload_accepts_code_zero_as_success(monkeypatch) -> None:
    # 参考实现按 code !== 200 判失败；这里必须认 0。
    _fake_client(monkeypatch, [_Resp({"code": 0, "data": {"url": "https://oss.example/x.png"}})])
    assert _relay().upload_bytes(b"x", ext="png") == "https://oss.example/x.png"


def test_upload_rejects_error_code_with_message(monkeypatch) -> None:
    _fake_client(monkeypatch, [_Resp({"code": 401, "message": "unauthorized"})])

    with pytest.raises(media_relay.MediaRelayConfigError, match="unauthorized"):
        _relay().upload_bytes(b"x")


def test_upload_rejects_non_json_response(monkeypatch) -> None:
    # 渠道地址被站点 SPA 页面接管时的形状（实测另一渠道就回 HTML）。
    _fake_client(monkeypatch, [_Resp(None, text="<!doctype html><html>")])

    with pytest.raises(media_relay.MediaRelayConfigError, match="不是 JSON"):
        _relay().upload_bytes(b"x")


def test_upload_surfaces_http_status_detail(monkeypatch) -> None:
    _fake_client(
        monkeypatch,
        [_Resp(None, status=500, text='{"code":500,"message":"服务器内部错误"}')],
    )

    with pytest.raises(
        media_relay.MediaRelayConfigError, match=r"HTTP 500.*服务器内部错误"
    ):
        _relay().upload_bytes(b"x")


def test_upload_requires_a_public_url(monkeypatch) -> None:
    _fake_client(monkeypatch, [_Resp({"code": 0, "data": {"url": "mediakit://1/2"}})])

    with pytest.raises(media_relay.MediaRelayConfigError, match="公网直链"):
        _relay().upload_bytes(b"x")


def test_object_key_parent_directory_becomes_the_prefix(monkeypatch) -> None:
    calls = _fake_client(
        monkeypatch, [_Resp({"code": 0, "data": {"url": "https://oss.example/x.png"}})]
    )

    _relay().upload_bytes(
        b"x",
        ext="png",
        object_key="relay/tenants/org/projects/proj/objects/deadbeef.png",
    )

    assert calls[0]["data"]["prefix"] == "relay/tenants/org/projects/proj/objects"


def test_unsafe_object_key_is_denied(monkeypatch) -> None:
    _fake_client(monkeypatch, [_Resp({"code": 0, "data": {}})])

    with pytest.raises(media_relay.ServiceEgressDenied):
        _relay().upload_bytes(b"x", object_key="../escape.png")


def test_asset_registration_is_best_effort(monkeypatch) -> None:
    calls = _fake_client(
        monkeypatch,
        [
            _Resp({"code": 0, "data": {"url": "https://oss.example/a.mp4"}}),
            _Resp({"code": 500, "message": "资产服务不可用"}),
        ],
    )

    url = _relay(register_asset=True).upload_bytes(b"x", ext="mp4", resource_type="video")

    assert url == "https://oss.example/a.mp4"  # 登记失败绝不阻断出片
    assert calls[1]["url"] == "https://gw.example/digital_asset"
    assert calls[1]["json"]["type"] == "video"
    assert calls[1]["json"]["url"] == "https://oss.example/a.mp4"


def test_get_media_relay_uses_saved_aipdd_cloud_config(monkeypatch, tmp_path) -> None:
    _patch_channels(monkeypatch, _channels())
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "AIPDD_CLOUD_BASE_URL", "")
    monkeypatch.setattr(config, "AIPDD_CLOUD_API_KEY", "")
    save_media_relay_config(
        provider="aipdd_cloud",
        ttl_seconds=900,
        aipdd_prefix="assets/mystuff/",
        aipdd_register_asset=True,
    )

    relay = media_relay.get_media_relay()

    assert isinstance(relay, media_relay.AipddCloudRelay)
    assert relay._origin == "https://gw.example"
    assert relay._prefix == "assets/mystuff"
    assert relay._register_asset is True


def test_aipdd_cloud_settings_survive_a_provider_switch(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    save_media_relay_config(
        provider="aipdd_cloud",
        ttl_seconds=900,
        aipdd_prefix="assets/mystuff",
        aipdd_register_asset=True,
    )

    saved = get_effective_media_relay_config()

    assert saved.provider == "aipdd_cloud"
    assert saved.aipdd_prefix == "assets/mystuff"
    assert saved.aipdd_register_asset is True


async def test_route_rejects_aipdd_cloud_without_a_channel(monkeypatch) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes.model_gateway import connection

    monkeypatch.setattr(connection, "require_ce_gateway_management", lambda: None)
    monkeypatch.setattr(
        "novelvideo.storage.media_relay.resolve_aipdd_cloud_target",
        lambda **kwargs: ("", ""),
    )
    body = connection.MediaRelayConfigBody.model_validate(
        {"provider": "aipdd_cloud", "ttlSeconds": 1800}
    )

    with pytest.raises(HTTPException, match="AIPDD 参考媒体中转未就绪") as exc:
        await connection.save_media_relay_settings(body)

    assert exc.value.status_code == 400


async def test_route_saves_aipdd_cloud_when_channel_exists(monkeypatch, tmp_path) -> None:
    from novelvideo import config as app_config
    from novelvideo.api.routes.model_gateway import connection

    monkeypatch.setattr(app_config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(connection, "require_ce_gateway_management", lambda: None)
    monkeypatch.setattr(
        "novelvideo.storage.media_relay.resolve_aipdd_cloud_target",
        lambda **kwargs: ("https://gw.example", "sk-aipdd"),
    )
    body = connection.MediaRelayConfigBody.model_validate(
        {
            "provider": "aipdd_cloud",
            "ttlSeconds": 1800,
            "aipddPrefix": "assets/mystuff",
            "registerAsset": True,
        }
    )

    result = await connection.save_media_relay_settings(body)

    assert result["ok"] is True
    assert result["data"]["provider"] == "aipdd_cloud"
    assert result["data"]["aipddPrefix"] == "assets/mystuff"
    assert result["data"]["aipddRegisterAsset"] is True
    assert result["data"]["aipddChannelConfigured"] is True
    assert result["data"]["configured"] is True


# ---------------------------------------------------------------------------
# [aipdd fork delta req44] 本地存储档：图片仍内联，视频/音频必须经 AIPDD 中转
# ---------------------------------------------------------------------------


def test_local_relay_inlines_images_without_touching_the_cloud(monkeypatch) -> None:
    # 空响应表：本地档要是偷偷发了一次上传，这里就会 StopIteration 报错。
    calls = _fake_client(monkeypatch, [])

    url = media_relay.LocalImageRelay(
        saved_base_url="https://gw.example/v1", saved_api_key="sk-aipdd"
    ).upload_bytes(b"png-bytes", ext="png")

    assert url.startswith("data:image/")
    assert calls == []


def test_local_relay_relays_reference_video_through_aipdd(monkeypatch) -> None:
    calls = _fake_client(
        monkeypatch,
        [_Resp({"code": 0, "data": {"url": "https://oss.example/a.mp4"}})],
    )

    url = media_relay.LocalImageRelay(
        saved_base_url="https://gw.example/v1",
        saved_api_key="sk-aipdd",
        aipdd_prefix="assets/mystuff",
    ).upload_bytes(b"video-bytes", ext="mp4", resource_type="video")

    # 本档的命门：交给上游的必须是公网直链，而不是几十 MB 的 data URL。
    assert url == "https://oss.example/a.mp4"
    assert calls[0]["url"] == "https://gw.example/oss/upload"
    assert calls[0]["data"]["prefix"] == "assets/mystuff"


def test_local_relay_refuses_to_inline_reference_video_without_credentials(
    monkeypatch,
) -> None:
    _patch_channels(monkeypatch, [])
    monkeypatch.setattr(config, "AIPDD_CLOUD_BASE_URL", "")
    monkeypatch.setattr(config, "AIPDD_CLOUD_API_KEY", "")
    relay = media_relay.LocalImageRelay()

    with pytest.raises(media_relay.MediaRelayConfigError, match="参考视频"):
        relay.upload_bytes(b"x", ext="mp4", resource_type="video")
    with pytest.raises(media_relay.MediaRelayConfigError, match="参考音频"):
        relay.upload_bytes(b"x", ext="mp3", resource_type="audio")


def test_get_media_relay_local_carries_saved_aipdd_credentials(monkeypatch, tmp_path) -> None:
    _patch_channels(monkeypatch, _channels())
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "AIPDD_CLOUD_BASE_URL", "")
    monkeypatch.setattr(config, "AIPDD_CLOUD_API_KEY", "")
    save_media_relay_config(
        provider="local",
        ttl_seconds=900,
        aipdd_api_key="sk-saved",
        aipdd_base_url="https://saved.example/v1",
        aipdd_prefix="assets/mystuff",
    )

    relay = media_relay.get_media_relay()

    assert isinstance(relay, media_relay.LocalImageRelay)
    delegate = relay._aipdd()
    assert isinstance(delegate, media_relay.AipddCloudRelay)
    assert delegate._origin == "https://saved.example"
    assert delegate._api_key == "sk-saved"
    assert delegate._prefix == "assets/mystuff"


async def test_route_rejects_local_without_any_aipdd_key(monkeypatch) -> None:
    # [用户口径] 选了本地存储就必须填 AIPDD API Key，否则很多地方跑不通。
    from fastapi import HTTPException

    from novelvideo.api.routes.model_gateway import connection

    monkeypatch.setattr(connection, "require_ce_gateway_management", lambda: None)
    monkeypatch.setattr(
        "novelvideo.storage.media_relay.resolve_aipdd_cloud_target",
        lambda **kwargs: ("", ""),
    )
    body = connection.MediaRelayConfigBody.model_validate(
        {"provider": "local", "ttlSeconds": 1800}
    )

    with pytest.raises(HTTPException, match="请填写 AIPDD API Key") as exc:
        await connection.save_media_relay_settings(body)

    assert exc.value.status_code == 400


async def test_route_saves_local_with_an_explicit_aipdd_key(monkeypatch, tmp_path) -> None:
    from novelvideo import config as app_config
    from novelvideo.api.routes.model_gateway import connection

    seen: dict[str, object] = {}

    def fake_resolve(**kwargs):
        seen.update(kwargs)
        return "https://saved.example", str(kwargs.get("saved_api_key") or "")

    monkeypatch.setattr(app_config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(connection, "require_ce_gateway_management", lambda: None)
    monkeypatch.setattr(
        "novelvideo.storage.media_relay.resolve_aipdd_cloud_target", fake_resolve
    )
    body = connection.MediaRelayConfigBody.model_validate(
        {
            "provider": "local",
            "ttlSeconds": 1800,
            "aipddApiKey": "sk-saved",
            "aipddBaseUrl": "https://saved.example/v1",
            "aipddPrefix": "assets/mystuff",
        }
    )

    result = await connection.save_media_relay_settings(body)

    # 设置页填的 Key/地址要原样送到解析器（而不是只认渠道）；地址保留用户填的
    # ``/v1``，到站点根的剥离在解析器里统一做。
    assert seen == {
        "saved_base_url": "https://saved.example/v1",
        "saved_api_key": "sk-saved",
    }
    assert result["data"]["provider"] == "local"
    assert result["data"]["aipddChannelConfigured"] is True
    assert result["data"]["configured"] is True
    assert result["data"]["aipddPrefix"] == "assets/mystuff"
    saved = get_effective_media_relay_config()
    assert saved.aipdd_api_key == "sk-saved"
    assert saved.aipdd_base_url == "https://saved.example/v1"
    assert saved.provider == "local"


def test_status_marks_local_incomplete_without_the_relay(monkeypatch, tmp_path) -> None:
    from novelvideo.model_gateway_settings import build_media_relay_status

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        "novelvideo.storage.media_relay.resolve_aipdd_cloud_target",
        lambda **kwargs: ("", ""),
    )
    save_media_relay_config(provider="local", ttl_seconds=900)

    status = build_media_relay_status()

    assert status["provider"] == "local"
    assert status["aipddChannelConfigured"] is False
    # 本地档不再是「永远已配置」：没中转 Key 就弹黄条。
    assert status["configured"] is False


# ---------------------------------------------------------------------------
# [aipdd fork delta req44] 文件入口：类型必须按扩展名推，否则 video/audio 会绕过守卫
# ---------------------------------------------------------------------------


def test_resource_type_is_inferred_from_the_file_suffix() -> None:
    assert media_relay._resource_type_for_ext(".mp4") == "video"
    assert media_relay._resource_type_for_ext("mov") == "video"
    assert media_relay._resource_type_for_ext("mp3") == "audio"
    assert media_relay._resource_type_for_ext("wav") == "audio"
    assert media_relay._resource_type_for_ext("png") == "image"
    assert media_relay._resource_type_for_ext("") == "image"


def test_local_relay_upload_file_inlines_and_never_uploads(monkeypatch, tmp_path) -> None:
    # 用户口径「能不用云就不用云」：不声明类型的文件入口一律内联，绝不无谓外传。
    # 空响应表 = 本档要是偷偷发了一次上传，这里就会 StopIteration 报错。
    calls = _fake_client(monkeypatch, [])
    clip = tmp_path / "source.mp4"
    clip.write_bytes(b"fake-video-bytes")

    url = media_relay.LocalImageRelay(
        saved_base_url="https://gw.example/v1", saved_api_key="sk-aipdd"
    ).upload_file(clip)

    assert url.startswith("data:")
    assert calls == []


def test_local_relay_upload_file_still_inlines_images(tmp_path) -> None:
    frame = tmp_path / "ref.png"
    frame.write_bytes(b"png-bytes")

    url = media_relay.LocalImageRelay().upload_file(frame)

    assert url.startswith("data:image/png;base64,")


def test_local_relay_names_the_real_medium_in_the_guidance(
    monkeypatch, tmp_path
) -> None:
    # 参考音频传进来的 resource_type 其实是 "video"（Cloudinary 只认 image/video/raw），
    # 文案要是照着它写就成了「参考视频无法内联」，会把用户指向错误的排查方向。
    _patch_channels(monkeypatch, [])
    monkeypatch.setattr(config, "AIPDD_CLOUD_BASE_URL", "")
    monkeypatch.setattr(config, "AIPDD_CLOUD_API_KEY", "")

    with pytest.raises(media_relay.MediaRelayConfigError, match="参考音频"):
        media_relay.LocalImageRelay().upload_bytes(
            b"audio-bytes", ext="mp3", resource_type="video"
        )


def test_aipdd_upload_file_registers_the_real_media_type(monkeypatch, tmp_path) -> None:
    calls = _fake_client(
        monkeypatch,
        [
            _Resp({"code": 0, "data": {"url": "https://oss.example/a.mp4"}}),
            _Resp({"code": 0, "data": {"id": 1006}}),
        ],
    )
    clip = tmp_path / "source.mp4"
    clip.write_bytes(b"video-bytes")

    _relay(register_asset=True).upload_file(clip)

    # 登记类型跟着真实素材走，不能默认把视频登成图片资产。
    assert calls[1]["json"]["type"] == "video"
