"""[aipdd fork delta] 抖音文案提取执行叶的聚焦测试。

钉住 ``novelvideo.freezone.douyin_copywrite`` 的纯函数与关键决策：链接解析、
详情结构解析、字幕脚手架剥离、ASR 候选清单（网关快照过滤 + 内置 FunASR /
豆包语音）、候选 id 解析、豆包语音凭证与音频参数、组织任务直连 fail-closed、
产物 txt 渲染口径。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from novelvideo.egress_context import TrustedEgressContext
from novelvideo.freezone import chrome_cdp
from novelvideo.freezone import douyin_copywrite as module
from novelvideo.freezone.douyin_cookie_capture import CookieCaptureResult
from novelvideo.ports.authz import BillingPrincipal
from novelvideo.ports.model_credentials import CredentialReference


def _organization_context() -> TrustedEgressContext:
    return TrustedEgressContext(
        envelope_id="envelope-1",
        project_id="project-1",
        task_type="freezone_douyin_copywrite",
        requester_user_id="user-1",
        root_task_id="task-1",
        admission_id="admission-1",
        admitted_at="2026-09-29T04:05:00Z",
        membership_id="membership-1",
        authz_version=11,
        billing_principal=BillingPrincipal(kind="organization", id="org-1"),
        credential=CredentialReference(
            source="organization",
            credential_id="credential-1",
            key_version=7,
            org_id="org-1",
        ),
    )


# ============================================================
# 链接解析
# ============================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("7312345678901234567", "7312345678901234567"),
        ("https://www.douyin.com/video/7312345678901234567", "7312345678901234567"),
        (
            "https://www.douyin.com/share/video/7312345678901234567",
            "7312345678901234567",
        ),
        (
            "7.53 复制打开抖音，看看【某作者的作品】"
            " https://www.douyin.com/video/7312345678901234567 复制此链接",
            "7312345678901234567",
        ),
    ],
)
async def test_resolve_video_id_from_raw_id_and_links(raw: str, expected: str) -> None:
    assert await module.resolve_douyin_video_id(raw) == expected


async def test_resolve_video_id_rejects_non_douyin_host_spoofing_shortlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O11：把内网主机拼上 ``v.douyin.com`` 子串不得绕过 host 校验、绝不触网。"""

    import httpx

    class _ExplodingClient:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("host 校验未拦下伪装链接，不应发起网络请求")

    monkeypatch.setattr(httpx, "AsyncClient", _ExplodingClient)

    for spoof in (
        "http://169.254.169.254/?x=v.douyin.com",
        "http://127.0.0.1:9001/a?v.douyin.com",
        "v.douyin.com@10.0.0.1/",
    ):
        with pytest.raises(ValueError):
            await module.resolve_douyin_video_id(spoof)


async def test_resolve_video_id_rejects_unknown_input() -> None:
    with pytest.raises(ValueError):
        await module.resolve_douyin_video_id("看看这个视频")
    with pytest.raises(ValueError):
        await module.resolve_douyin_video_id("")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 带随机口令 + 中文描述 + 尾部提示语的整段分享文案，只抠出 https 短链。
        (
            "7.99 c@n.qr 09/02 :3pm Ago:/ 请允许一切发生 # 心理暗示# 口播 "
            "https://v.douyin.com/6014qSZRXys/ 复制此链接，打开Dou音搜索，直接观看视频！",
            "https://v.douyin.com/6014qSZRXys/",
        ),
        # 已是纯链接：原样返回。
        ("https://v.douyin.com/abc/", "https://v.douyin.com/abc/"),
        # 无协议短链混在文案里：补全 https。
        ("看这个 v.douyin.com/xyz/ 谢谢", "https://v.douyin.com/xyz/"),
        # 尾随半角标点会被剥除。
        ("https://v.douyin.com/abc/).", "https://v.douyin.com/abc/"),
        # 既无 https 也无短链：原样返回（交由下游判断）。
        ("纯文字没有链接", "纯文字没有链接"),
        ("", ""),
    ],
)
def test_extract_share_url(raw: str, expected: str) -> None:
    assert module.extract_share_url(raw) == expected


def _patch_httpx_redirect(
    monkeypatch: pytest.MonkeyPatch,
    *,
    final_url: str = "",
    error: Exception | None = None,
) -> None:
    """把 ``httpx.AsyncClient`` 换成固定重定向目标的替身。"""

    import httpx

    class _FakeResponse:
        def __init__(self, url: str) -> None:
            self.url = url

    class _FakeAsyncClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def get(self, _url: str, headers=None) -> "_FakeResponse":
            if error is not None:
                raise error
            return _FakeResponse(final_url)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)


async def test_resolve_video_id_follows_short_link_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx_redirect(
        monkeypatch, final_url="https://www.douyin.com/video/999888777"
    )
    resolved = await module.resolve_douyin_video_id(
        "复制打开抖音 v.douyin.com/abcDEF123/ 看看"
    )
    assert resolved == "999888777"


async def test_resolve_video_id_accepts_modal_id_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx_redirect(
        monkeypatch, final_url="https://www.douyin.com/?modal_id=888777666"
    )
    resolved = await module.resolve_douyin_video_id("https://v.douyin.com/xyz987/")
    assert resolved == "888777666"


async def test_resolve_video_id_short_link_network_error_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    _patch_httpx_redirect(monkeypatch, error=httpx.ConnectError("boom"))
    with pytest.raises(RuntimeError):
        await module.resolve_douyin_video_id("https://v.douyin.com/xyz987/")


# ============================================================
# 详情结构解析
# ============================================================


def _detail_payload(
    *,
    urls: list[str] | None = None,
    music_title: str = "",
    subtitle_infos: list[dict] | None = None,
) -> dict:
    video_info: dict = {
        "duration": 15000,
        "play_addr": {
            "url_list": list(
                urls or ["https://v3-web.douyinvod.com/cdn/video/e.mp4"]
            )
        },
    }
    if subtitle_infos is not None:
        video_info["subtitle"] = {"subtitleInfos": subtitle_infos}
    aweme: dict = {
        "aweme_id": "7312345678901234567",
        "desc": "测试标题",
        "author": {"nickname": "测试作者"},
        "statistics": {
            "digg_count": 1200,
            "comment_count": 34,
            "collect_count": 5,
            "share_count": 6,
        },
        "video": video_info,
    }
    if music_title:
        aweme["music"] = {
            "title": music_title,
            "play_url": {"url_list": ["https://p9.douyinstatic.com/original.mp3"]},
        }
    return {"aweme_detail": aweme}


def test_parse_detail_extracts_structured_fields() -> None:
    info = module.parse_aweme_detail(_detail_payload())
    assert info["aweme_id"] == "7312345678901234567"
    assert info["desc"] == "测试标题"
    assert info["author"] == "测试作者"
    assert info["duration_seconds"] == 15
    assert info["video_url"] == "https://v3-web.douyinvod.com/cdn/video/e.mp4"
    assert info["stats"] == {
        "digg_count": 1200,
        "comment_count": 34,
        "collect_count": 5,
        "share_count": 6,
    }
    assert info["music_audio_url"] == ""
    assert info["subtitle_infos"] == []


def test_parse_detail_accepts_aweme_list_shape() -> None:
    payload = {"aweme_list": [_detail_payload()["aweme_detail"]]}
    info = module.parse_aweme_detail(payload)
    assert info["aweme_id"] == "7312345678901234567"


def test_parse_detail_reads_video_level_subtitle_fallback() -> None:
    infos = [{"LanguageCodeName": "zh-CN", "Url": "https://v.douyin.com/sub.vtt"}]
    info = module.parse_aweme_detail(_detail_payload(subtitle_infos=infos))
    assert info["subtitle_infos"] == infos


def test_parse_detail_prefers_top_level_subtitle_infos() -> None:
    top = [{"Url": "https://top.example.com/sub.vtt"}]
    payload = _detail_payload(subtitle_infos=[{"Url": "https://video-level.vtt"}])
    payload["aweme_detail"]["subtitle_infos"] = top
    info = module.parse_aweme_detail(payload)
    assert info["subtitle_infos"] == top


def test_parse_detail_extracts_original_sound_music_url() -> None:
    payload = _detail_payload(music_title="@作者创作的原声")
    info = module.parse_aweme_detail(payload)
    assert info["music_audio_url"] == "https://p9.douyinstatic.com/original.mp3"


def test_parse_detail_ignores_licensed_music_without_original_sound() -> None:
    payload = _detail_payload(music_title="授权背景音乐")
    assert module.parse_aweme_detail(payload)["music_audio_url"] == ""


def test_find_best_video_url_prefers_mixed_audio_stream() -> None:
    urls = [
        "https://cdn.example.com/plain-clip.mp4",
        "https://cdn.example.com/mixed-audio-clip.mp4",
    ]
    assert module._find_best_video_url(urls) == urls[1]


def test_find_best_video_url_falls_back_to_first() -> None:
    urls = ["https://cdn.example.com/one.mp4", "https://cdn.example.com/two.mp4"]
    assert module._find_best_video_url(urls) == urls[0]
    assert module._find_best_video_url([]) == ""


# ============================================================
# 字幕脚手架剥离
# ============================================================


def test_parse_subtitle_text_strips_vtt_scaffolding() -> None:
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
大家好

00:00:02.000 --> 00:00:04.000
<c.color>欢迎来到本视频</c>

1
2
<c>only-tag-no-cjk</c>
"""
    assert module.parse_subtitle_text(vtt) == "大家好\n欢迎来到本视频"
    assert module.parse_subtitle_text("") == ""


# ============================================================
# ASR 候选清单（网关快照过滤 + 内置 FunASR）
# ============================================================


def _patch_channels(
    monkeypatch: pytest.MonkeyPatch,
    *,
    channels: list[dict],
    snapshots: dict[str, list[str]],
) -> None:
    from novelvideo import model_gateway_settings, newapi_provisioner

    monkeypatch.setattr(
        model_gateway_settings, "get_newapi_provider_channels", lambda: list(channels)
    )
    monkeypatch.setattr(
        newapi_provisioner,
        "upstream_passthrough_models",
        lambda provider, from_snapshot=True: list(snapshots.get(provider, [])),
    )


def test_list_asr_models_filters_gateway_snapshot_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_channels(
        monkeypatch,
        channels=[{"provider": "volc"}, {"provider": "comfyui"}, {"provider": "aipdd"}],
        snapshots={
            "volc": ["doubao-seed-asr-1.0", "doubao-seedream-3.0-t2i", "gpt-4o"],
            "aipdd": ["whisper-1"],
        },
    )
    ids = [item["id"] for item in module.list_douyin_asr_models()]
    assert "gateway:volc:doubao-seed-asr-1.0" in ids
    assert "gateway:aipdd:whisper-1" in ids
    assert module.FUNASR_CANDIDATE_ID in ids
    assert not any("seedream" in item or "gpt-4o" in item for item in ids)
    assert not any(item.startswith("gateway:comfyui:") for item in ids)


def test_list_asr_models_keeps_funasr_when_channel_config_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import model_gateway_settings

    def _boom():
        raise RuntimeError("channel config unavailable")

    monkeypatch.setattr(model_gateway_settings, "get_newapi_provider_channels", _boom)
    models = module.list_douyin_asr_models()
    ids = [item["id"] for item in models]
    # [aipdd fork delta] 渠道配置读不到时：内置 FunASR + 豆包语音候选仍在。
    assert ids[0] == module.FUNASR_CANDIDATE_ID
    assert set(ids) == {
        module.FUNASR_CANDIDATE_ID,
        *(
            f"{module.DOUBAO_ASR_CANDIDATE_PREFIX}{v}"
            for v in module._DOUBAO_ASR_VARIANTS
        ),
    }


def test_list_asr_models_tolerates_single_channel_snapshot_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import model_gateway_settings, newapi_provisioner

    monkeypatch.setattr(
        model_gateway_settings,
        "get_newapi_provider_channels",
        lambda: [{"provider": "volc"}, {"provider": "aipdd"}],
    )

    def _upstream(provider: str, from_snapshot: bool = True) -> list[str]:
        if provider == "volc":
            raise RuntimeError("snapshot broken")
        return ["whisper-1"]

    monkeypatch.setattr(newapi_provisioner, "upstream_passthrough_models", _upstream)
    ids = [item["id"] for item in module.list_douyin_asr_models()]
    assert "gateway:aipdd:whisper-1" in ids
    assert not any(item.startswith("gateway:volc:") for item in ids)


def test_list_asr_models_deduplicates_repeated_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_channels(
        monkeypatch,
        channels=[{"provider": "volc"}],
        snapshots={"volc": ["doubao-seed-asr-1.0", "doubao-seed-asr-1.0"]},
    )
    ids = [item["id"] for item in module.list_douyin_asr_models()]
    assert ids.count("gateway:volc:doubao-seed-asr-1.0") == 1


def test_list_asr_models_flags_unconfigured_direct_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 凭证/渠道未配置：gateway 候选仍就绪（来自已配置渠道快照），
    # funasr / doubao_asr 候选 ready=False，供工具页在提交入口提醒去设置。
    _patch_channels(
        monkeypatch,
        channels=[{"provider": "volc"}],
        snapshots={"volc": ["doubao-seed-asr-1.0"]},
    )

    def _missing(*_args: object) -> tuple[str, str]:
        raise RuntimeError("未配置")

    monkeypatch.setattr(module, "_doubao_asr_credentials", _missing)
    monkeypatch.setattr(module, "_funasr_platform_config", _missing)
    by_id = {item["id"]: item for item in module.list_douyin_asr_models()}
    assert by_id["gateway:volc:doubao-seed-asr-1.0"]["ready"] is True
    assert by_id[module.FUNASR_CANDIDATE_ID]["ready"] is False
    assert all(
        by_id[f"{module.DOUBAO_ASR_CANDIDATE_PREFIX}{variant}"]["ready"] is False
        for variant in module._DOUBAO_ASR_VARIANTS
    )


def test_list_asr_models_flags_configured_direct_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_channels(
        monkeypatch,
        channels=[{"provider": "volc"}],
        snapshots={"volc": ["doubao-seed-asr-1.0"]},
    )
    monkeypatch.setattr(
        module, "_doubao_asr_credentials", lambda: "app-api-key"
    )
    monkeypatch.setattr(
        module, "_funasr_platform_config", lambda: ("https://api.aipdd.work", "key")
    )
    by_id = {item["id"]: item for item in module.list_douyin_asr_models()}
    assert by_id[module.FUNASR_CANDIDATE_ID]["ready"] is True
    assert all(
        by_id[f"{module.DOUBAO_ASR_CANDIDATE_PREFIX}{variant}"]["ready"] is True
        for variant in module._DOUBAO_ASR_VARIANTS
    )


# ============================================================
# 候选 id 解析
# ============================================================


def test_resolve_candidate_defaults_to_builtin_funasr() -> None:
    expected = {
        "id": module.FUNASR_CANDIDATE_ID,
        "kind": "funasr",
        "provider": "aipdd",
        "model": module.FUNASR_TASK_TYPE_CODE,
        "display": module.FUNASR_DISPLAY_NAME,
    }
    assert module.resolve_douyin_asr_candidate("") == expected
    assert module.resolve_douyin_asr_candidate("  ") == expected
    assert module.resolve_douyin_asr_candidate(module.FUNASR_TASK_TYPE_CODE) == expected


def test_resolve_candidate_parses_gateway_id() -> None:
    assert module.resolve_douyin_asr_candidate(
        "gateway:volc:doubao-seed-asr-1.0"
    ) == {
        "id": "gateway:volc:doubao-seed-asr-1.0",
        "kind": "gateway",
        "provider": "volc",
        "model": "doubao-seed-asr-1.0",
        "display": "doubao-seed-asr-1.0",
    }


def test_resolve_candidate_parses_doubao_openspeech_id() -> None:
    resolved = module.resolve_douyin_asr_candidate(
        f"{module.DOUBAO_ASR_CANDIDATE_PREFIX}seedasr"
    )
    assert resolved == {
        "id": f"{module.DOUBAO_ASR_CANDIDATE_PREFIX}seedasr",
        "kind": "doubao_asr",
        "provider": "volcengine",
        "model": "seedasr",
        "display": "Doubao ASR 2.0",
    }


def test_resolve_candidate_rejects_unknown_doubao_variant() -> None:
    with pytest.raises(ValueError):
        module.resolve_douyin_asr_candidate(
            f"{module.DOUBAO_ASR_CANDIDATE_PREFIX}unknown"
        )


def test_resolve_candidate_keeps_colons_inside_model_name() -> None:
    resolved = module.resolve_douyin_asr_candidate("gateway:volc:org:asr-v2")
    assert resolved["provider"] == "volc"
    assert resolved["model"] == "org:asr-v2"


@pytest.mark.parametrize("bad", ["gateway:", "gateway:volc", "volc:asr", "unknown"])
def test_resolve_candidate_rejects_unrecognized_ids(bad: str) -> None:
    with pytest.raises(ValueError):
        module.resolve_douyin_asr_candidate(bad)


# ============================================================
# FunASR 平台配置与组织 fail-closed
# ============================================================


def test_funasr_platform_config_strips_v1_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import model_gateway_settings

    monkeypatch.setattr(
        model_gateway_settings,
        "get_newapi_provider_channel",
        lambda provider: {
            "baseUrl": "https://api.aipdd.work/v1/",
            "upstreamKey": "sk-x",
        },
    )
    base_url, key = module._funasr_platform_config()
    assert base_url == "https://api.aipdd.work"
    assert key == "sk-x"


def test_funasr_platform_config_requires_channel_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import model_gateway_settings

    monkeypatch.setattr(
        model_gateway_settings, "get_newapi_provider_channel", lambda provider: {}
    )
    with pytest.raises(RuntimeError):
        module._funasr_platform_config()


async def test_org_task_funasr_direct_is_denied_before_any_network(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="ORG_EGRESS_DENIED"):
        await module.run_freezone_douyin_copywrite(
            project_dir=tmp_path,
            job_id="job-org",
            video="7312345678901234567",
            model=module.FUNASR_CANDIDATE_ID,
            egress_context=_organization_context(),
        )


async def test_org_task_doubao_direct_is_denied_before_any_network(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="ORG_EGRESS_DENIED"):
        await module.run_freezone_douyin_copywrite(
            project_dir=tmp_path,
            job_id="job-org-doubao",
            video="7312345678901234567",
            model=f"{module.DOUBAO_ASR_CANDIDATE_PREFIX}flash",
            egress_context=_organization_context(),
        )


# ============================================================
# 豆包语音（openspeech）凭证与音频参数
# ============================================================


def test_doubao_asr_credentials_reads_api_key_from_channel_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import model_gateway_settings

    monkeypatch.setattr(
        model_gateway_settings,
        "get_newapi_provider_channel",
        lambda provider: {"settings": {"asr": {"apiKey": "ch-api-key"}}},
    )
    # 豆包语音统一单个 API Key（官方：任意接口 X-Api-Key header、无需 appid）
    assert module._doubao_asr_credentials() == "ch-api-key"


def test_doubao_asr_credentials_ignore_env_when_db_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # [aipdd fork delta] 豆包语音凭证只读设置数据库（渠道 settings.asr.apiKey）：
    # DB 为空时即便环境变量配了 VOLCENGINE_ASR_* 也不再回填（避免密钥 env/DB 双真
    # 源），直接报错。
    from novelvideo import model_gateway_settings

    monkeypatch.setattr(
        model_gateway_settings, "get_newapi_provider_channel", lambda provider: {}
    )
    monkeypatch.setenv("VOLCENGINE_ASR_API_KEY", "env-key")
    with pytest.raises(RuntimeError, match="豆包语音凭证"):
        module._doubao_asr_credentials()


def test_doubao_asr_credentials_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 旧字段 appId/accessToken 不再被读取——只有 apiKey 非空才算已配置。
    from novelvideo import model_gateway_settings

    monkeypatch.setattr(
        model_gateway_settings,
        "get_newapi_provider_channel",
        lambda provider: {"settings": {"asr": {"appId": "a", "accessToken": "t"}}},
    )
    with pytest.raises(RuntimeError, match="豆包语音凭证"):
        module._doubao_asr_credentials()


def test_doubao_audio_payload_prefers_public_url() -> None:
    payload = module._doubao_audio_payload(
        audio_url="https://cdn.example.com/audio.m4a?sig=1", audio_path=None
    )
    assert payload == {
        "format": "m4a",
        "url": "https://cdn.example.com/audio.m4a?sig=1",
    }


def test_doubao_audio_payload_encodes_local_file(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    payload = module._doubao_audio_payload(audio_url="", audio_path=audio)
    assert payload["format"] == "wav"
    assert payload["data"] == "UklGRg=="


def test_doubao_audio_payload_requires_any_source() -> None:
    with pytest.raises(RuntimeError):
        module._doubao_audio_payload(audio_url="", audio_path=None)


# ============================================================
# 产物 txt 渲染口径
# ============================================================


def test_render_copywrite_file_matches_reference_layout() -> None:
    rendered = module._render_copywrite_file(
        video_id="7312345678901234567",
        title="测试标题",
        author="测试作者",
        stats={
            "digg_count": 1200,
            "comment_count": 34,
            "collect_count": 5,
            "share_count": 6,
        },
        source_label="语音识别（doubao-seed-asr-1.0）",
        model_label="doubao-seed-asr-1.0",
        text="这是口播文案",
    )
    assert rendered.startswith("视频ID: 7312345678901234567")
    assert "标题: 测试标题" in rendered
    assert "作者: 测试作者" in rendered
    assert "互动: 点赞1,200 评论34 收藏5 分享6" in rendered
    assert "文案来源: 语音识别（doubao-seed-asr-1.0）" in rendered
    assert "模型: doubao-seed-asr-1.0" in rendered
    assert rendered.endswith("【口播文案】\n这是口播文案\n")


def test_render_copywrite_file_omits_model_line_for_subtitle_fallback() -> None:
    rendered = module._render_copywrite_file(
        video_id="1",
        title="",
        author="",
        stats={},
        source_label="视频内置字幕",
        model_label="",
        text="字幕文案",
    )
    assert "模型:" not in rendered
    assert "标题:" not in rendered
    assert "作者:" not in rendered
    assert rendered.endswith("字幕文案\n")


# ============================================================
# 裸 CDP 详情拦截（替代 Playwright）：只用宿主机真实 Chrome，不装浏览器
# ============================================================


def test_chrome_cdp_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # [aipdd fork delta] 默认值与 compose 注入保持一致；环境变量可覆盖，
    # 主机名形式在交给 CDP 前解析成宿主可达 IP（Chrome 只接受 IP / localhost 的 Host 头）。
    monkeypatch.setenv(module._CHROME_CDP_URL_ENV, "http://127.0.0.1:9333")
    assert module.chrome_cdp_url() == "http://127.0.0.1:9333"
    monkeypatch.setattr(
        "novelvideo.utils.chrome_cdp.socket.gethostbyname",
        lambda _host: "192.168.65.254",
    )
    monkeypatch.delenv(module._CHROME_CDP_URL_ENV, raising=False)
    assert module.chrome_cdp_url() == "http://192.168.65.254:9222"


# ----------------------------------------------------------
# chrome_cdp 纯函数：URL 判定 / ws host 重写 / 响应体解码
# ----------------------------------------------------------

_DETAIL_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/?x=1"
_DETAIL_PAYLOAD = {"aweme_detail": {"desc": "hello"}}


def test_is_detail_url_only_matches_marker() -> None:
    assert chrome_cdp._is_detail_url(_DETAIL_URL) is True
    assert chrome_cdp._is_detail_url("https://www.douyin.com/video/123") is False
    assert chrome_cdp._is_detail_url("") is False


def test_rewrite_ws_host_aligns_to_reachable_netloc() -> None:
    # Chrome 常返回 ws://localhost，容器里连不通：对齐到本端实际访问的 host:port。
    got = chrome_cdp._rewrite_ws_host(
        "ws://localhost:9222/devtools/browser/abc", "http://192.168.5.2:9222"
    )
    assert got == "ws://192.168.5.2:9222/devtools/browser/abc"


def test_rewrite_ws_host_https_uses_wss() -> None:
    got = chrome_cdp._rewrite_ws_host(
        "ws://localhost:9222/devtools/browser/abc", "https://h.example:9222"
    )
    assert got == "wss://h.example:9222/devtools/browser/abc"


def test_decode_response_body_text_base64_and_bad() -> None:
    import base64

    assert chrome_cdp._decode_response_body(
        {"body": '{"aweme_detail": {}}', "base64Encoded": False}
    ) == {"aweme_detail": {}}
    b64 = base64.b64encode(json.dumps({"aweme_detail": 1}).encode()).decode()
    assert chrome_cdp._decode_response_body({"body": b64, "base64Encoded": True}) == {
        "aweme_detail": 1
    }
    assert chrome_cdp._decode_response_body({"body": "not-json", "base64Encoded": False}) is None
    assert chrome_cdp._decode_response_body({}) is None


# ----------------------------------------------------------
# fetch_douyin_detail：注入假 WebSocket / version，验证端到端捕获与错误分因
# ----------------------------------------------------------


class _FakeSocket:
    """按 CDP 命令脚本回放响应与 Network 事件的假 WebSocket。"""

    def __init__(self) -> None:
        self._out: asyncio.Queue[str] = asyncio.Queue()
        self.sent_methods: list[str] = []
        # 事件/命令的时间线，用于钉住「先 loadingFinished 再取 body」。
        self.timeline: list[str] = []
        # WebSocket 上下文退出次数：收尾漏关会每拦一次详情就漏一条连接。
        self.ctx_exits = 0

    async def send(self, text: str) -> None:
        msg = json.loads(text)
        mid = msg["id"]
        method = msg["method"]
        params = msg.get("params") or {}
        self.sent_methods.append(method)
        if method == "Target.createTarget":
            self._emit({"id": mid, "result": {"targetId": "T1"}})
        elif method == "Target.attachToTarget":
            self._emit({"id": mid, "result": {"sessionId": "S1"}})
        elif method == "Network.getResponseBody":
            self.timeline.append(method)
            self._emit(
                {
                    "id": mid,
                    "result": {
                        "body": json.dumps(_DETAIL_PAYLOAD),
                        "base64Encoded": False,
                    },
                }
            )
        else:
            self._emit({"id": mid, "result": {}})
        # 导航到视频页后，页面自发详情 XHR —— 以 responseReceived + loadingFinished
        # 两段事件回放（真实 Chrome 只有加载结束后才取到 body）。
        if method == "Page.navigate" and "/video/" in str(params.get("url", "")):
            self._emit(
                {
                    "method": "Network.responseReceived",
                    "sessionId": "S1",
                    "params": {
                        "requestId": "R1",
                        "response": {"url": _DETAIL_URL, "status": 200},
                    },
                }
            )
            self.timeline.append("Network.responseReceived")
            self._emit(
                {
                    "method": "Network.loadingFinished",
                    "sessionId": "S1",
                    "params": {"requestId": "R1"},
                }
            )
            self.timeline.append("Network.loadingFinished")

    def _emit(self, obj: dict[str, object]) -> None:
        self._out.put_nowait(json.dumps(obj))

    def __aiter__(self):
        return self._read()

    async def _read(self):
        while True:
            yield await self._out.get()


class _FakeSocketCtx:
    def __init__(self, sock: _FakeSocket) -> None:
        self._sock = sock

    async def __aenter__(self) -> _FakeSocket:
        return self._sock

    async def __aexit__(self, *_exc: object) -> bool:
        self._sock.ctx_exits += 1
        return False


async def _fake_version(_url: str) -> dict[str, str]:
    return {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/x"}


async def test_fetch_douyin_detail_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chrome_cdp, "_WARMUP_SECONDS", 0.0)
    sock = _FakeSocket()
    detail = await chrome_cdp.fetch_douyin_detail(
        "http://192.168.5.2:9222",
        "123",
        timeout=10.0,
        ws_connect=lambda _u: _FakeSocketCtx(sock),
        version_getter=_fake_version,
    )
    assert detail == _DETAIL_PAYLOAD
    # 关键命令都发过：建目标、附加、开网络、导航视频页、取响应体。
    for m in ("Target.createTarget", "Target.attachToTarget", "Network.enable",
              "Page.navigate", "Network.getResponseBody"):
        assert m in sock.sent_methods
    # 时序回归钉：body 必须排在 loadingFinished 之后（提前取会撞 no data）。
    assert sock.timeline == [
        "Network.responseReceived",
        "Network.loadingFinished",
        "Network.getResponseBody",
    ]
    # 收尾回归钉：客户端与 WebSocket 必须成对退出（只 aclose 客户端 = 每次漏一条连接，
    # 而且异常曾在 except Exception 里被静默吞掉，单测不钉就没人知道）。
    assert sock.ctx_exits == 1


async def test_fetch_raises_unreachable_when_version_fails() -> None:
    async def _boom(_url: str) -> dict[str, str]:
        raise OSError("connection refused")

    with pytest.raises(chrome_cdp.CdpUnavailable):
        await chrome_cdp.fetch_douyin_detail(
            "http://192.168.5.2:9222",
            "123",
            ws_connect=lambda _u: _FakeSocketCtx(_FakeSocket()),
            version_getter=_boom,
        )


async def test_fetch_raises_unreachable_when_ws_connect_fails() -> None:
    def _bad_connect(_u: str):
        raise OSError("ws handshake refused")

    with pytest.raises(chrome_cdp.CdpUnavailable):
        await chrome_cdp.fetch_douyin_detail(
            "http://192.168.5.2:9222",
            "123",
            timeout=5.0,
            ws_connect=_bad_connect,
            version_getter=_fake_version,
        )


async def test_await_detail_raises_no_detail_when_silent() -> None:
    class _EmptyClient:
        def __init__(self) -> None:
            self.events: asyncio.Queue[dict[str, object]] = asyncio.Queue()

    loop = asyncio.get_running_loop()
    with pytest.raises(chrome_cdp.CdpNoDetail):
        await chrome_cdp._await_detail(_EmptyClient(), "S1", deadline=loop.time() + 0.2)


# ----------------------------------------------------------
# douyin_copywrite._intercept_douyin_detail：把 CDP 异常映射成可操作文案
# ----------------------------------------------------------


async def test_intercept_returns_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(cdp_url: str, video_id: str, *, timeout: float) -> dict[str, object]:
        return {"aweme_detail": {"desc": "ok"}}

    monkeypatch.setattr(module, "fetch_douyin_detail", _fake)
    assert await module._intercept_douyin_detail("123") == {"aweme_detail": {"desc": "ok"}}


async def test_intercept_maps_unreachable_to_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(cdp_url: str, video_id: str, *, timeout: float) -> dict[str, object]:
        raise chrome_cdp.CdpUnavailable("refused")

    monkeypatch.setattr(module, "fetch_douyin_detail", _fake)
    with pytest.raises(RuntimeError, match="调试端口") as ei:
        await module._intercept_douyin_detail("123")
    # 明确告知用「本地真实 Chrome、不额外安装浏览器」。
    assert "不额外安装任何浏览器" in str(ei.value)
    # 指引要给可执行命令：只说「以 --remote-debugging-port 启动 Chrome」会撞上
    # 「日常 Chrome 已在运行时 open -a 的参数被现有进程忽略」这条死路。
    assert "scripts/douyin-cdp-chrome.sh" in str(ei.value)
    assert "open -a" in str(ei.value)


async def test_intercept_maps_nodetail_to_risk_control(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(cdp_url: str, video_id: str, *, timeout: float) -> dict[str, object]:
        raise chrome_cdp.CdpNoDetail("silent")

    monkeypatch.setattr(module, "fetch_douyin_detail", _fake)
    with pytest.raises(RuntimeError, match="未能拦截到抖音详情"):
        await module._intercept_douyin_detail("123")


async def test_intercept_body_unavailable_is_not_blamed_on_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拦到详情但取不到 body：不能再让用户去查登录/风控。"""

    async def _fake(cdp_url: str, video_id: str, *, timeout: float) -> dict[str, object]:
        raise chrome_cdp.CdpNoDetail("已拦到 1 条详情响应（HTTP 200）但 CDP 取不到响应体")

    monkeypatch.setattr(module, "fetch_douyin_detail", _fake)
    with pytest.raises(RuntimeError) as ei:
        await module._intercept_douyin_detail("123")
    assert "刷新该视频页" in str(ei.value)
    assert "触发风控" not in str(ei.value)


# ----------------------------------------------------------
# _fetch_douyin_detail：HTTP 主链路 → CDP 兜底，以及「首次处理后自动记录 Cookie」
# ----------------------------------------------------------


def _patch_detail_channels(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cookie: str = "",
    http_result: dict[str, object] | None = None,
    http_error: Exception | None = None,
    cdp_result: dict[str, object] | None = None,
    cdp_error: Exception | None = None,
) -> dict[str, list[str]]:
    """把详情三通道（Cookie / CDP / 抓 Cookie）全换成记录调用的替身。"""

    calls: dict[str, list[str]] = {"http": [], "cdp": [], "capture": []}

    monkeypatch.setattr(module, "douyin_web_cookie", lambda: cookie)

    async def _http(video_id: str) -> dict[str, object]:
        calls["http"].append(video_id)
        if http_error is not None:
            raise http_error
        return dict(http_result or {"aweme_detail": {"desc": "http"}})

    async def _cdp(video_id: str) -> dict[str, object]:
        calls["cdp"].append(video_id)
        if cdp_error is not None:
            raise cdp_error
        return dict(cdp_result or {"aweme_detail": {"desc": "cdp"}})

    async def _capture(*, cdp_url: str, **_kw: object) -> CookieCaptureResult:
        calls["capture"].append(cdp_url)
        return CookieCaptureResult(saved=True, cookie_count=2, cookie_names=("sessionid",))

    monkeypatch.setattr(module, "fetch_douyin_detail_via_http", _http)
    monkeypatch.setattr(module, "_intercept_douyin_detail", _cdp)
    monkeypatch.setattr(module, "capture_douyin_cookie", _capture)
    return calls


async def test_http_detail_success_skips_cdp_and_keeps_manual_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主链路本来就通：不开浏览器、也不拿浏览器那份覆盖用户手贴的凭据。"""

    calls = _patch_detail_channels(monkeypatch, cookie="sessionid=MANUAL")
    payload, via = await module._fetch_douyin_detail("123")
    assert via == "http"
    assert payload["aweme_detail"]["desc"] == "http"  # type: ignore[index]
    assert calls["http"] == ["123"]
    assert calls["cdp"] == []
    assert calls["capture"] == []


async def test_expired_cookie_falls_back_to_cdp_and_renews_from_chrome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """库里的被平台明确拒过 = 已过期：CDP 兜底走通后正好换成浏览器里新鲜的那份。"""

    calls = _patch_detail_channels(
        monkeypatch,
        cookie="sessionid=STALE",
        http_error=module.DouyinHttpRejected("详情返回里没有目标 aweme_id"),
    )
    _payload, via = await module._fetch_douyin_detail("123")
    assert via == "cdp"
    assert calls["capture"] == [module.chrome_cdp_url()]


async def test_missing_cookie_records_chrome_login_state_after_cdp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """需求本尊：首次「没配 Cookie 也能提取」成功后，登录态要顺手记进设置库。"""

    calls = _patch_detail_channels(monkeypatch, cookie="")
    _payload, via = await module._fetch_douyin_detail("123")
    assert via == "cdp"
    assert calls["http"] == []
    assert calls["capture"] == [module.chrome_cdp_url()]


async def test_unusable_http_channel_does_not_void_manual_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「通道用不了」（网络层/未就绪）≠「凭据被拒」：不能据此覆盖用户手贴的 Cookie。"""

    calls = _patch_detail_channels(
        monkeypatch,
        cookie="sessionid=MANUAL",
        http_error=module.DouyinHttpUnavailable("httpx not installed"),
    )
    _payload, via = await module._fetch_douyin_detail("123")
    assert via == "cdp"
    assert calls["cdp"] == ["123"]
    assert calls["capture"] == []


async def test_both_channels_failed_concatenates_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两路都失败时拼成因：只贴一边会把用户支去开浏览器或白刷 Cookie。"""

    _patch_detail_channels(
        monkeypatch,
        cookie="sessionid=STALE",
        http_error=module.DouyinHttpRejected("风控拦截"),
        cdp_error=RuntimeError("未能拦截到抖音详情数据"),
    )
    with pytest.raises(RuntimeError) as ei:
        await module._fetch_douyin_detail("123")
    assert "HTTP 主链路也不可用" in str(ei.value)
    assert "风控拦截" in str(ei.value)


async def test_missing_cookie_error_points_at_settings_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_detail_channels(
        monkeypatch, cookie="", cdp_error=RuntimeError("CDP 连不上")
    )
    with pytest.raises(RuntimeError, match="未配置抖音 Cookie"):
        await module._fetch_douyin_detail("123")


async def test_cookie_capture_crash_cannot_break_a_successful_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自动记录是 best-effort：它炸了自己也不能让已成功的提取翻车。"""

    _patch_detail_channels(monkeypatch, cookie="")

    async def _boom(*_a: object, **_kw: object) -> CookieCaptureResult:
        raise RuntimeError("unexpected capture bug")

    monkeypatch.setattr(module, "capture_douyin_cookie", _boom)
    payload, via = await module._fetch_douyin_detail("123")
    assert via == "cdp"
    assert payload["aweme_detail"]["desc"] == "cdp"  # type: ignore[index]


# ----------------------------------------------------------
# chrome_cdp._await_detail：body 必须在 loadingFinished 之后取
# ----------------------------------------------------------


class _FakeCdpClient:
    """预置事件队列的最小 CDP 客户端替身，记录 getResponseBody 调用。"""

    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        for event in events:
            self.events.put_nowait(event)
        self.body_calls: list[str] = []
        self.body_results: list[object] = []

    async def call(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, object]:
        assert method == "Network.getResponseBody", method
        self.body_calls.append(str((params or {}).get("requestId")))
        result = self.body_results.pop(0) if self.body_results else None
        if isinstance(result, Exception):
            raise result
        return dict(result or {})  # type: ignore[arg-type]


def _detail_received(request_id: str = "R1") -> dict[str, object]:
    return {
        "sessionId": "S1",
        "method": "Network.responseReceived",
        "params": {
            "requestId": request_id,
            "response": {
                "url": "https://www.douyin.com/aweme/v1/web/aweme/detail/?x=1",
                "status": 200,
            },
        },
    }


def _detail_finished(request_id: str = "R1") -> dict[str, object]:
    return {
        "sessionId": "S1",
        "method": "Network.loadingFinished",
        "params": {"requestId": request_id},
    }


def _detail_body(aweme_id: str = "123") -> dict[str, object]:
    return {
        "body": json.dumps({"aweme_detail": {"aweme_id": aweme_id, "desc": "hi"}}),
        "base64Encoded": False,
    }


async def test_await_detail_does_not_fetch_body_before_loading_finished() -> None:
    client = _FakeCdpClient([_detail_received()])
    loop = asyncio.get_running_loop()
    with pytest.raises(chrome_cdp.CdpNoDetail, match="预算内未拦到"):
        await chrome_cdp._await_detail(client, "S1", deadline=loop.time() + 0.2)
    # 回归钉：responseReceived 阶段不能提前 getResponseBody（会撞 no data）。
    assert client.body_calls == []


async def test_await_detail_fetches_body_after_loading_finished() -> None:
    client = _FakeCdpClient([_detail_received(), _detail_finished()])
    client.body_results = [_detail_body()]
    loop = asyncio.get_running_loop()
    payload = await chrome_cdp._await_detail(
        client, "S1", deadline=loop.time() + 1.0, expected_video_id="123"
    )
    assert payload == {"aweme_detail": {"aweme_id": "123", "desc": "hi"}}
    assert client.body_calls == ["R1"]


async def test_await_detail_retries_body_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chrome_cdp, "_BODY_RETRY_INTERVAL_SECONDS", 0.0)
    client = _FakeCdpClient([_detail_received(), _detail_finished()])
    client.body_results = [
        RuntimeError("CDP Network.getResponseBody 报错: No data found for resource"),
        _detail_body(),
    ]
    loop = asyncio.get_running_loop()
    payload = await chrome_cdp._await_detail(client, "S1", deadline=loop.time() + 1.0)
    assert payload["aweme_detail"] == {"aweme_id": "123", "desc": "hi"}
    assert client.body_calls == ["R1", "R1"]


async def test_await_detail_reports_body_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chrome_cdp, "_BODY_RETRY_INTERVAL_SECONDS", 0.0)
    client = _FakeCdpClient([_detail_received(), _detail_finished()])
    client.body_results = [RuntimeError("No data found") for _ in range(chrome_cdp._BODY_RETRIES)]
    loop = asyncio.get_running_loop()
    with pytest.raises(chrome_cdp.CdpNoDetail, match="取不到响应体"):
        await chrome_cdp._await_detail(client, "S1", deadline=loop.time() + 0.3)


async def test_await_detail_discards_foreign_aweme_id() -> None:
    client = _FakeCdpClient([_detail_received(), _detail_finished()])
    client.body_results = [_detail_body(aweme_id="999999")]
    loop = asyncio.get_running_loop()
    # 重试分支点推荐位后可能拦到别的视频，返回别人的文案比报错更糟。
    with pytest.raises(chrome_cdp.CdpNoDetail, match="其它视频"):
        await chrome_cdp._await_detail(
            client, "S1", deadline=loop.time() + 0.3, expected_video_id="123"
        )


# ----------------------------------------------------------
# 豆包语音 openspeech v3：submit/query HTTP 流程
# ----------------------------------------------------------


class _FakeOpenspeechResponse:
    def __init__(
        self,
        *,
        status_header: str = "",
        payload: dict[str, object] | None = None,
        http_status: int = 200,
    ) -> None:
        self.headers: dict[str, str] = {}
        if status_header:
            self.headers["X-Api-Status-Code"] = status_header
        self.status_code = http_status
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self) -> dict[str, object]:
        return self._payload


def _patch_openspeech(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[_FakeOpenspeechResponse],
) -> list[tuple[str, dict[str, str], object]]:
    """把 httpx.AsyncClient 换成记录 (url, headers, json) 的假客户端。"""

    import httpx

    calls: list[tuple[str, dict[str, str], object]] = []
    queue = list(responses)

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def post(
            self, url: str, *, headers: dict[str, str] | None = None, json: object = None
        ) -> _FakeOpenspeechResponse:
            calls.append((url, dict(headers or {}), json))
            return queue.pop(0)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(module, "_doubao_asr_credentials", lambda: "api-key")
    monkeypatch.setattr(module, "_DOUBAO_ASR_POLL_SECONDS", 0.0)
    return calls


async def test_doubao_asr_query_sends_empty_json_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """query 不带 body 会被网关 json.Unmarshal("") 拒（45000000）——必须带 ``{}``。"""

    calls = _patch_openspeech(
        monkeypatch,
        [
            _FakeOpenspeechResponse(status_header="20000000", payload={}),
            _FakeOpenspeechResponse(status_header="20000002", payload={}),
            _FakeOpenspeechResponse(
                status_header="20000000", payload={"result": {"text": "你好今天天气怎么样"}}
            ),
        ],
    )
    text = await module._transcribe_via_doubao_asr(
        variant="seedasr",
        audio_url="https://example.com/a.mp3",
        audio_path=None,
        job_id="job-1",
    )
    assert text == "你好今天天气怎么样"
    submit_url, submit_headers, submit_body = calls[0]
    assert submit_url.endswith("/submit")
    assert submit_headers["X-Api-Sequence"] == "-1"
    assert submit_body == {
        "user": {"uid": "novelvideo"},
        "audio": {"format": "mp3", "url": "https://example.com/a.mp3"},
        "request": {"model_name": "bigmodel", "enable_itn": True, "enable_punc": True},
    }
    queries = calls[1:]
    assert len(queries) == 2
    for _url, headers, body in queries:
        assert headers["X-Api-Request-Id"] == submit_headers["X-Api-Request-Id"]
        assert headers["X-Api-Resource-Id"] == "volc.seedasr.auc"
        assert body == {}


async def test_doubao_asr_query_error_surfaces_http_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_openspeech(
        monkeypatch,
        [
            _FakeOpenspeechResponse(status_header="20000000", payload={}),
            _FakeOpenspeechResponse(
                status_header="",
                payload={"header": {"code": 45000000, "message": "unexpected end of JSON input"}},
                http_status=400,
            ),
        ],
    )
    with pytest.raises(RuntimeError) as ei:
        await module._transcribe_via_doubao_asr(
            variant="seedasr",
            audio_url="https://example.com/a.mp3",
            audio_path=None,
            job_id="job-1",
        )
    message = str(ei.value)
    assert "豆包语音识别查询失败" in message
    assert "HTTP 400" in message
    assert len(calls) == 2


async def test_doubao_asr_flash_is_single_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_openspeech(
        monkeypatch,
        [
            _FakeOpenspeechResponse(
                status_header="20000000", payload={"result": {"text": "同步结果"}}
            )
        ],
    )
    text = await module._transcribe_via_doubao_asr(
        variant="flash",
        audio_url="https://example.com/a.mp3",
        audio_path=None,
        job_id="job-1",
    )
    assert text == "同步结果"
    assert len(calls) == 1 and calls[0][0].endswith("/recognize/flash")


# ============================================================
# 网关 ASR 报错可诊断化（_gateway_transcription_detail / _transcribe_via_gateway）
# ============================================================


def _status_error(status: int, *, json_body=None, text: str = "") -> Exception:
    import httpx

    request = httpx.Request("POST", "http://gateway.test/v1/audio/transcriptions")
    body_kwargs = {"json": json_body} if json_body is not None else {"text": text}
    response = httpx.Response(
        status,
        **body_kwargs,
        request=request,
    )
    return httpx.HTTPStatusError("upstream failed", request=request, response=response)


def test_gateway_detail_carries_status_and_upstream_message() -> None:
    exc = _status_error(
        503,
        json_body={
            "error": {
                "code": "model_not_found",
                "message": "No available channel for model ap-funasr under group default (distributor)",
            }
        },
    )
    detail = module._gateway_transcription_detail(exc)
    # 以前这里只会得到一句 "NewAPI audio transcription failed"，看不出上游为什么失败。
    assert "HTTP 503" in detail
    assert "No available channel for model ap-funasr" in detail
    # 最可行动的一类：网关侧没有可用渠道 → 给出渠道归属与可执行出口（导航名必须是真实栏目）。
    assert "设置 → 模型与渠道" in detail
    assert "同步模型清单" in detail


def test_gateway_detail_falls_back_to_raw_body_for_non_json() -> None:
    exc = _status_error(401, text="Authorization header is required")
    detail = module._gateway_transcription_detail(exc)
    assert "HTTP 401" in detail
    assert "Authorization header is required" in detail


def test_gateway_detail_truncates_oversized_message() -> None:
    exc = _status_error(500, json_body={"error": {"message": "x" * 900}})
    detail = module._gateway_transcription_detail(exc)
    assert len(detail) < 400


async def test_transcribe_via_gateway_raises_diagnosable_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """网关返回 5xx 时，任务侧拿到的报错要带上游状态与原因。"""

    import httpx

    from novelvideo.config import get_newapi_runtime_credentials

    monkeypatch.setattr(
        get_newapi_runtime_credentials.__module__ + ".get_newapi_runtime_credentials",
        lambda: ("test-key", "http://gateway.test/v1"),
    )

    error = _status_error(
        503,
        json_body={
            "error": {
                "code": "model_not_found",
                "message": "No available channel for model ap-funasr under group default (distributor)",
            }
        },
    )

    class _FakeResponse:
        status_code = 503

        def raise_for_status(self) -> None:
            raise error

    class _FakeAsyncClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def post(self, *_args, **_kwargs) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"id3")

    with pytest.raises(RuntimeError) as excinfo:
        await module._transcribe_via_gateway(
            audio_path=audio, model="ap-funasr", job_id="job-gw", egress_context=None
        )
    message = str(excinfo.value)
    assert "网关 ASR 转写失败" in message
    assert "HTTP 503" in message
    assert "No available channel" in message


async def test_transcribe_via_gateway_transport_error_is_named(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连接类异常也给出类型名，而不是统一一句英文兜底。"""

    import httpx

    from novelvideo.config import get_newapi_runtime_credentials

    monkeypatch.setattr(
        get_newapi_runtime_credentials.__module__ + ".get_newapi_runtime_credentials",
        lambda: ("test-key", "http://gateway.test/v1"),
    )

    class _FakeAsyncClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"id3")

    with pytest.raises(RuntimeError, match="ConnectError"):
        await module._transcribe_via_gateway(
            audio_path=audio, model="ap-funasr", job_id="job-gw2", egress_context=None
        )
