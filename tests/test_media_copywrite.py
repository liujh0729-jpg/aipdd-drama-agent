"""[aipdd fork delta] req21 音视频提取文案执行叶聚焦测试。

钉住 ``novelvideo.freezone.media_copywrite`` 的关键决策：三种来源解析、抖音
yt-dlp↔宿主 Chrome CDP 双通路回退、非抖音链接统一 yt-dlp 下载 + ffmpeg 抽音、纯音频
直送 ASR、FunASR 缺公网直链报错、组织直连 fail-closed、yt-dlp 选项（抖音注入
headers）、EG-15c 出网注册。所有网络 / 媒体叶子均打桩，不触网、不下载。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.egress_context import TrustedEgressContext
from novelvideo.freezone import media_copywrite as module
from novelvideo.ports.authz import BillingPrincipal
from novelvideo.ports.model_credentials import CredentialReference

_GATEWAY_MODEL = "gateway:volcengine:whisper-1"


def _organization_context() -> TrustedEgressContext:
    return TrustedEgressContext(
        envelope_id="envelope-1",
        project_id="project-1",
        task_type="freezone_media_copywrite",
        requester_user_id="user-1",
        root_task_id="task-1",
        admission_id="admission-1",
        admitted_at="2026-10-03T04:05:00Z",
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


def _patch_gateway_transcribe(
    monkeypatch: pytest.MonkeyPatch, text: str, captured: dict
) -> None:
    """把网关 ASR 通道换成固定返回，并桩掉拍平调用名解析（不读配置）。"""

    import novelvideo.model_gateway_settings as mgs

    async def fake_gateway(**kwargs):
        captured.update(kwargs)
        return text

    monkeypatch.setattr(module, "_transcribe_via_gateway", fake_gateway)
    monkeypatch.setattr(mgs, "provider_spot_model", lambda p, m: f"{p}::{m}")


# ============================================================
# 参数校验
# ============================================================


async def test_unknown_source_type_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="未知的来源类型"):
        await module.run_freezone_media_copywrite(
            project_dir=tmp_path,
            job_id="j",
            source_type="teleport",
            url="http://x",
            model=_GATEWAY_MODEL,
        )


async def test_empty_url_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        await module.run_freezone_media_copywrite(
            project_dir=tmp_path,
            job_id="j",
            source_type="link",
            url="   ",
            model=_GATEWAY_MODEL,
        )


# ============================================================
# B 类：上传音频直送 ASR
# ============================================================


async def test_upload_audio_transcribes_via_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "in.mp3"
    audio.write_bytes(b"fake-audio-bytes")
    monkeypatch.setattr(
        module, "resolve_static_url_to_path", lambda url, pd: audio
    )
    captured: dict = {}
    _patch_gateway_transcribe(monkeypatch, "识别出的口播文案", captured)

    result = await module.run_freezone_media_copywrite(
        project_dir=tmp_path,
        job_id="job-audio",
        source_type="upload_audio",
        url="/static/projects/p/freezone/_uploads/in.mp3",
        model=_GATEWAY_MODEL,
    )
    assert result.text == "识别出的口播文案"
    assert result.source == "asr"
    assert result.video_path is None  # 纯音频来源无视频
    assert result.audio_path == audio
    assert captured["model"] == "volcengine::whisper-1"
    assert result.text_path.exists()
    assert "识别出的口播文案" in result.text_path.read_text(encoding="utf-8")


# ============================================================
# A 类：非抖音链接统一 yt-dlp 下载 + 抽音
# ============================================================


async def test_link_non_douyin_downloads_and_extracts_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"w" * 2000)

    async def fake_download(url, work_dir, platform):
        assert platform == "youtube"
        return {
            "video_path": video,
            "title": "示例标题",
            "author": "某频道",
            "duration_seconds": 42,
            "video_id": "yt-1",
        }

    async def fake_extract(video_path, dest):
        return wav

    monkeypatch.setattr(module, "_download_via_ytdlp", fake_download)
    monkeypatch.setattr(module, "_extract_audio_16k_wav", fake_extract)
    captured: dict = {}
    _patch_gateway_transcribe(monkeypatch, "youtube 文案", captured)

    result = await module.run_freezone_media_copywrite(
        project_dir=tmp_path,
        job_id="job-yt",
        source_type="link",
        url="https://youtu.be/abc",
        platform="youtube",
        model=_GATEWAY_MODEL,
    )
    assert result.video_path == video
    assert result.audio_path == wav  # 从视频抽出的音频
    assert result.title == "示例标题"
    assert result.author == "某频道"
    assert result.duration_seconds == 42
    assert captured["audio_path"] == wav


# ============================================================
# A 类：抖音双通路
# ============================================================


async def test_douyin_prefers_ytdlp_when_it_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"w" * 2000)

    async def fake_download(url, work_dir, platform):
        assert platform == "douyin"
        return {
            "video_path": video,
            "title": "抖音标题",
            "author": "抖音作者",
            "duration_seconds": 9,
            "video_id": "dy-1",
        }

    detail_called = {"hit": False}

    async def fake_detail(video_id):
        detail_called["hit"] = True
        return {}, "cdp"

    async def fake_extract(video_path, dest):
        return wav

    monkeypatch.setattr(module, "_download_via_ytdlp", fake_download)
    monkeypatch.setattr(module, "_fetch_douyin_detail", fake_detail)
    monkeypatch.setattr(module, "_extract_audio_16k_wav", fake_extract)
    _patch_gateway_transcribe(monkeypatch, "抖音 yt 文案", {})

    result = await module.run_freezone_media_copywrite(
        project_dir=tmp_path,
        job_id="job-dy-yt",
        source_type="link",
        url="https://v.douyin.com/xxx/",
        platform="douyin",
        model=_GATEWAY_MODEL,
    )
    assert detail_called["hit"] is False  # yt-dlp 成功即不触发详情接口 / 宿主 Chrome
    assert result.video_path == video
    assert result.audio_path == wav
    assert result.text == "抖音 yt 文案"


async def test_douyin_falls_back_to_cdp_when_ytdlp_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mp3 = tmp_path / "audio.mp3"
    mp3.write_bytes(b"x" * 2000)

    async def fake_download(url, work_dir, platform):
        raise RuntimeError("yt-dlp douyin extractor broken")

    async def fake_resolve_id(input_str):
        return "7312345678901234567"

    monkeypatch.setattr(module, "_download_via_ytdlp", fake_download)
    monkeypatch.setattr(module, "resolve_douyin_video_id", fake_resolve_id)
    async def fake_detail(video_id):
        return {"aweme_detail": {}}, "cdp"

    monkeypatch.setattr(module, "_fetch_douyin_detail", fake_detail)
    monkeypatch.setattr(
        module,
        "parse_aweme_detail",
        lambda detail: {
            "aweme_id": "7312345678901234567",
            "desc": "口播标题",
            "author": "作者名",
            "duration_seconds": 7,
            "video_url": "http://cdn/v.mp4",
            "music_audio_url": "http://cdn/music.mp3",
            "subtitle_infos": [],
            "stats": {},
        },
    )

    async def fake_resolve_local_audio(*, music_url, video_url, work_dir):
        assert music_url == "http://cdn/music.mp3"
        return mp3

    monkeypatch.setattr(module, "_resolve_local_audio", fake_resolve_local_audio)
    _patch_gateway_transcribe(monkeypatch, "抖音 pw 文案", {})

    result = await module.run_freezone_media_copywrite(
        project_dir=tmp_path,
        job_id="job-dy-pw",
        source_type="link",
        url="https://v.douyin.com/xxx/",
        platform="douyin",
        model=_GATEWAY_MODEL,
    )
    assert result.video_path is None  # CDP 走原声直链，无本地视频
    assert result.audio_path == mp3
    assert result.title == "口播标题"
    assert result.text == "抖音 pw 文案"


async def test_douyin_fallback_reports_progress_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[aipdd fork delta] yt-dlp 未通回退 CDP 时，每一步都上报进度与失败原因。"""

    mp3 = tmp_path / "audio.mp3"
    mp3.write_bytes(b"x" * 2000)

    async def fake_download(url, work_dir, platform):
        raise RuntimeError("yt-dlp broken extractor")

    async def fake_resolve_id(input_str):
        return "7312345678901234567"

    monkeypatch.setattr(module, "_download_via_ytdlp", fake_download)
    monkeypatch.setattr(module, "resolve_douyin_video_id", fake_resolve_id)
    async def fake_detail2(video_id):
        return {"aweme_detail": {}}, "cdp"

    monkeypatch.setattr(module, "_fetch_douyin_detail", fake_detail2)
    monkeypatch.setattr(
        module,
        "parse_aweme_detail",
        lambda detail: {
            "desc": "口播标题",
            "author": "作者名",
            "duration_seconds": 7,
            "video_url": "http://cdn/v.mp4",
            "music_audio_url": "http://cdn/music.mp3",
            "subtitle_infos": [],
            "stats": {},
        },
    )

    async def fake_resolve_local_audio(*, music_url, video_url, work_dir):
        return mp3

    monkeypatch.setattr(module, "_resolve_local_audio", fake_resolve_local_audio)
    _patch_gateway_transcribe(monkeypatch, "抖音 pw 文案", {})

    reports: list[tuple[float, str]] = []

    result = await module.run_freezone_media_copywrite(
        project_dir=tmp_path,
        job_id="job-dy-report",
        source_type="link",
        url="https://v.douyin.com/xxx/",
        platform="douyin",
        model=_GATEWAY_MODEL,
        on_status=lambda progress, message: reports.append((progress, message)),
    )
    assert result.text == "抖音 pw 文案"

    joined = " | ".join(msg for _, msg in reports)
    assert "yt-dlp" in joined  # 先试 yt-dlp
    assert "yt-dlp 未通" in joined and "yt-dlp broken extractor" in joined
    assert "浏览器" in joined  # 回退 CDP/浏览器解析
    assert "ASR 转写中" in joined
    # 进度单调不回退。
    assert [p for p, _ in reports] == sorted(p for p, _ in reports)


async def test_douyin_both_paths_fail_mentions_ytdlp_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """yt-dlp 与 CDP 都失败时，最终异常同时带上 yt-dlp 的失败原因。"""

    async def fake_download(url, work_dir, platform):
        raise RuntimeError("需登录 / 触发风控")

    async def fake_resolve_id(input_str):
        return "7312345678901234567"

    async def fake_detail(video_id):
        raise RuntimeError("未能拦截到抖音详情数据（视频可能已删除），请稍后重试")

    monkeypatch.setattr(module, "_download_via_ytdlp", fake_download)
    monkeypatch.setattr(module, "resolve_douyin_video_id", fake_resolve_id)
    monkeypatch.setattr(module, "_fetch_douyin_detail", fake_detail)

    with pytest.raises(RuntimeError) as excinfo:
        await module.run_freezone_media_copywrite(
            project_dir=tmp_path,
            job_id="job-dy-both-fail",
            source_type="link",
            url="https://v.douyin.com/xxx/",
            platform="douyin",
            model=_GATEWAY_MODEL,
        )
    message = str(excinfo.value)
    assert "未能拦截到抖音详情数据" in message
    assert "此前 yt-dlp 已失败" in message and "需登录 / 触发风控" in message


# ============================================================
# FunASR 缺公网直链
# ============================================================


async def test_funasr_without_public_audio_url_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"w" * 2000)

    async def fake_download(url, work_dir, platform):
        return {
            "video_path": video,
            "title": "T",
            "author": "A",
            "duration_seconds": 5,
            "video_id": "b1",
        }

    async def fake_extract(video_path, dest):
        return wav

    monkeypatch.setattr(module, "_download_via_ytdlp", fake_download)
    monkeypatch.setattr(module, "_extract_audio_16k_wav", fake_extract)

    from novelvideo.freezone.douyin_copywrite import FUNASR_CANDIDATE_ID

    with pytest.raises(RuntimeError, match="公网音频直链"):
        await module.run_freezone_media_copywrite(
            project_dir=tmp_path,
            job_id="job-funasr",
            source_type="link",
            url="https://b23.tv/abc",
            platform="bilibili",
            model=FUNASR_CANDIDATE_ID,
        )


# ============================================================
# 组织 fail-closed
# ============================================================


async def test_org_task_funasr_direct_denied_before_network(tmp_path: Path) -> None:
    from novelvideo.freezone.douyin_copywrite import FUNASR_CANDIDATE_ID

    with pytest.raises(RuntimeError, match="ORG_EGRESS_DENIED"):
        await module.run_freezone_media_copywrite(
            project_dir=tmp_path,
            job_id="job-org",
            source_type="link",
            url="https://b23.tv/abc",
            platform="bilibili",
            model=FUNASR_CANDIDATE_ID,
            egress_context=_organization_context(),
        )


async def test_org_task_doubao_direct_denied_before_network(tmp_path: Path) -> None:
    from novelvideo.freezone.douyin_copywrite import DOUBAO_ASR_CANDIDATE_PREFIX

    with pytest.raises(RuntimeError, match="ORG_EGRESS_DENIED"):
        await module.run_freezone_media_copywrite(
            project_dir=tmp_path,
            job_id="job-org-doubao",
            source_type="upload_audio",
            url="/static/x/a.mp3",
            model=f"{DOUBAO_ASR_CANDIDATE_PREFIX}flash",
            egress_context=_organization_context(),
        )


# ============================================================
# yt-dlp 封装：抖音注入 headers、落盘定位
# ============================================================


def test_ytdlp_injects_douyin_headers_and_locates_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_dlp

    captured: dict = {}

    class FakeYDL:
        def __init__(self, opts):
            captured["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, url, download=True):
            (tmp_path / "source.mp4").write_bytes(b"v")
            return {"title": "T", "uploader": "A", "duration": 10, "id": "x1"}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)

    info = module._download_via_ytdlp_sync("http://x", tmp_path, "douyin")
    assert info["video_path"].name == "source.mp4"
    assert info["title"] == "T"
    assert info["duration_seconds"] == 10
    headers = captured["opts"].get("http_headers")
    assert headers and headers["Referer"] == "https://www.douyin.com/"

    # 非抖音平台不注入抖音专属 headers
    (tmp_path / "source.mp4").unlink()
    module._download_via_ytdlp_sync("http://x", tmp_path, "youtube")
    assert "http_headers" not in captured["opts"]


# ============================================================
# 出网注册
# ============================================================


def test_media_copywrite_leaf_registered_for_network_egress() -> None:
    from novelvideo.task_backend.runners.freezone import (
        FREEZONE_LEAF_EGRESS,
        LeafEgress,
    )

    rule = FREEZONE_LEAF_EGRESS["run_freezone_media_copywrite"]
    assert rule.egress is LeafEgress.NETWORK
    assert rule.eg_id == "EG-15c"
    assert rule.module == "novelvideo.freezone.media_copywrite"
