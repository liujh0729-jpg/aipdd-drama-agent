from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.api.schemas import (
    FreezoneImageToVideoRequest,
    FreezoneKeyframeVideoRequest,
    FreezoneVideoEditRequest,
    FreezoneVideoExtendRequest,
    FreezoneVideoGenRequest,
    FreezoneVideoOmniGenRequest,
)


def _catalog(*modes: str) -> dict:
    return {
        "catalog_id": "catalog-video",
        "supportedModes": list(modes),
        "referenceImageMax": 9,
        "resolutionOptions": ["720p"],
        "minDuration": 1,
        "maxDuration": 15,
    }


def _wan_catalog() -> dict:
    catalog = _catalog("all_reference")
    catalog.update(
        {
            "referenceImageMax": 10,
            "referenceVideoMax": 5,
            "referenceAudioMax": 5,
            "referenceFileMax": 1,
            "referenceLinkMax": 1,
            "referenceFileTypes": ["pdf", "docx", "md"],
            "maxDuration": 30,
        }
    )
    return catalog


async def _install_route_fakes(
    monkeypatch,
    tmp_path: Path,
    capabilities: dict,
    *,
    backend: str = "newapi_seedance-2.0-mini",
) -> dict:
    captured: dict = {}

    async def resolve_project(_project, _user):
        return (
            SimpleNamespace(requester_user_id="user-1"),
            "admin",
            "project",
            tmp_path,
            str(tmp_path / "output"),
        )

    async def resolve_backend(_model, *, requester_user_id):
        assert requester_user_id == "user-1"
        return backend

    async def resolve_request(
        _media_type,
        _model,
        _params,
        *,
        mode,
        requester_user_id,
    ):
        assert requester_user_id == "user-1"
        captured["request_mode"] = mode
        return None, {}, capabilities

    async def start(**kwargs):
        captured["start"] = kwargs
        return {"job_id": "job-test"}

    async def validate_audio(reference_items, *, capabilities, backend):
        captured["validated_audio_items"] = reference_items

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", resolve_project)
    monkeypatch.setattr(freezone_routes, "_resolve_catalog_video_backend", resolve_backend)
    monkeypatch.setattr(freezone_routes, "_resolve_catalog_request", resolve_request)
    monkeypatch.setattr(freezone_routes, "_start_or_enqueue_freezone_video_gen", start)
    monkeypatch.setattr(
        freezone_routes,
        "_validate_catalog_reference_audio_items",
        validate_audio,
    )
    monkeypatch.setattr(
        freezone_routes,
        "_resolve_url_list",
        lambda _project_dir, urls: [str(url) for url in urls if url],
    )
    return captured


@pytest.mark.asyncio
async def test_video_edit_forwards_configured_independent_audio(
    monkeypatch, tmp_path: Path
) -> None:
    capabilities = _catalog("video_edit")
    capabilities.update(
        {
            "referenceVideoMax": 1,
            "referenceAudioMax": 2,
            "referenceAudioMinSeconds": 1,
            "referenceAudioMaxSeconds": 15,
        }
    )
    captured = await _install_route_fakes(monkeypatch, tmp_path, capabilities)

    await freezone_routes.freezone_video_edit(
        "project",
        FreezoneVideoEditRequest(
            video_url="https://example.com/source.mp4",
            image_urls=["https://example.com/style.png"],
            audio_urls=["https://example.com/music.mp3"],
            model="catalog-video",
            gen_mode="videoEdit",
        ),
        {"username": "admin"},
    )

    assert captured["request_mode"] == "videoEdit"
    assert captured["start"]["gen_mode"] == "video_edit"
    assert captured["start"]["aspect_ratio"] == "auto"
    assert captured["start"]["duration_seconds"] is None
    assert captured["start"]["reference_items"] == [
        {
            "type": "video",
            "path": "https://example.com/source.mp4",
            "role": "视频编辑源",
        },
        {
            "type": "image",
            "path": "https://example.com/style.png",
            "role": "图片参考",
        },
        {
            "type": "audio",
            "path": "https://example.com/music.mp3",
            "role": "配乐参考",
        },
    ]
    assert captured["validated_audio_items"] == captured["start"]["reference_items"]


@pytest.mark.asyncio
async def test_video_extend_uses_source_video_auto_ratio_and_requested_duration(
    monkeypatch, tmp_path: Path
) -> None:
    capabilities = _catalog("video_extend")
    capabilities["referenceVideoMax"] = 1
    captured = await _install_route_fakes(monkeypatch, tmp_path, capabilities)

    await freezone_routes.freezone_video_extend(
        "project",
        FreezoneVideoExtendRequest(
            video_url="https://example.com/source.mp4",
            prompt="继续生成五秒，人物走出房门",
            duration_seconds=7,
            model="catalog-video",
        ),
        {"username": "admin"},
    )

    assert captured["request_mode"] == "videoExtend"
    assert captured["start"]["gen_mode"] == "video_extend"
    assert captured["start"]["aspect_ratio"] == "auto"
    assert captured["start"]["duration_seconds"] == 7
    assert captured["start"]["reference_items"] == [
        {
            "type": "video",
            "path": "https://example.com/source.mp4",
            "role": "视频延长源",
        }
    ]
    assert "视频延长要求" in captured["start"]["prompt"]


@pytest.mark.asyncio
async def test_omni_video_forwards_document_reference(
    monkeypatch, tmp_path: Path
) -> None:
    captured = await _install_route_fakes(monkeypatch, tmp_path, _wan_catalog())

    await freezone_routes.freezone_video_omni_gen(
        "project",
        FreezoneVideoOmniGenRequest(
            prompt="根据产品说明生成广告",
            references=[
                {
                    "type": "file",
                    "url": "https://example.com/product.PDF?download=1",
                    "role": "产品说明",
                }
            ],
            model="catalog-video",
            duration_seconds=30,
        ),
        {"username": "admin"},
    )

    assert captured["request_mode"] == "allReference"
    assert captured["start"]["duration_seconds"] == 30
    assert captured["start"]["reference_items"] == [
        {
            "type": "file",
            "path": "https://example.com/product.PDF?download=1",
            "role": "产品说明",
        }
    ]


@pytest.mark.asyncio
async def test_omni_video_forwards_public_link_reference(
    monkeypatch, tmp_path: Path
) -> None:
    captured = await _install_route_fakes(monkeypatch, tmp_path, _wan_catalog())

    await freezone_routes.freezone_video_omni_gen(
        "project",
        FreezoneVideoOmniGenRequest(
            prompt="根据页面内容生成新闻短片",
            references=[
                {
                    "type": "link",
                    "url": "https://example.com/news/wan-3",
                    "role": "网页参考",
                }
            ],
            model="catalog-video",
        ),
        {"username": "admin"},
    )

    assert captured["start"]["reference_items"] == [
        {
            "type": "link",
            "path": "https://example.com/news/wan-3",
            "role": "网页参考",
        }
    ]


@pytest.mark.asyncio
async def test_omni_video_rejects_file_and_link_together(
    monkeypatch, tmp_path: Path
) -> None:
    await _install_route_fakes(monkeypatch, tmp_path, _wan_catalog())

    with pytest.raises(HTTPException, match="mutually exclusive"):
        await freezone_routes.freezone_video_omni_gen(
            "project",
            FreezoneVideoOmniGenRequest(
                prompt="生成介绍视频",
                references=[
                    {"type": "file", "url": "https://example.com/brief.pdf"},
                    {"type": "link", "url": "https://example.com/brief"},
                ],
                model="catalog-video",
            ),
            {"username": "admin"},
        )


@pytest.mark.asyncio
async def test_video_edit_rejects_audio_when_catalog_cap_is_zero(
    monkeypatch, tmp_path: Path
) -> None:
    capabilities = _catalog("video_edit")
    capabilities.update({"referenceVideoMax": 1, "referenceAudioMax": 0})
    await _install_route_fakes(monkeypatch, tmp_path, capabilities)

    with pytest.raises(HTTPException, match="at most 0 audio references"):
        await freezone_routes.freezone_video_edit(
            "project",
            FreezoneVideoEditRequest(
                video_url="https://example.com/source.mp4",
                audio_urls=["https://example.com/music.mp3"],
                model="catalog-video",
                gen_mode="videoEdit",
            ),
            {"username": "admin"},
        )


@pytest.mark.asyncio
async def test_image_to_video_uses_one_reference_image_not_first_frame(
    monkeypatch, tmp_path: Path
) -> None:
    captured = await _install_route_fakes(
        monkeypatch,
        tmp_path,
        _catalog("image_to_video"),
    )

    await freezone_routes.freezone_video_i2v(
        "project",
        FreezoneImageToVideoRequest(
            image_urls=["https://example.com/ref.png"],
            model="catalog-video",
            gen_mode="imageToVideo",
        ),
        {"username": "admin"},
    )

    assert captured["request_mode"] == "imageToVideo"
    assert captured["start"]["gen_mode"] == "image_reference"
    assert captured["start"]["requested_gen_mode"] == "imageToVideo"
    assert captured["start"]["reference_items"] == [
        {"type": "image", "path": "https://example.com/ref.png", "role": "图片参考"}
    ]


@pytest.mark.asyncio
async def test_image_to_video_rejects_more_than_one_image(monkeypatch, tmp_path: Path) -> None:
    await _install_route_fakes(monkeypatch, tmp_path, _catalog("image_to_video"))

    with pytest.raises(HTTPException, match="exactly one image"):
        await freezone_routes.freezone_video_i2v(
            "project",
            FreezoneImageToVideoRequest(
                image_urls=["https://example.com/a.png", "https://example.com/b.png"],
                model="catalog-video",
                gen_mode="imageToVideo",
            ),
            {"username": "admin"},
        )


@pytest.mark.asyncio
async def test_image_to_video_does_not_use_image_reference_capability(
    monkeypatch, tmp_path: Path
) -> None:
    await _install_route_fakes(monkeypatch, tmp_path, _catalog("image_reference"))

    with pytest.raises(HTTPException, match="image_to_video"):
        await freezone_routes.freezone_video_i2v(
            "project",
            FreezoneImageToVideoRequest(
                image_urls=["https://example.com/ref.png"],
                model="catalog-video",
                gen_mode="imageToVideo",
            ),
            {"username": "admin"},
        )


@pytest.mark.asyncio
async def test_image_reference_does_not_use_image_to_video_capability(
    monkeypatch, tmp_path: Path
) -> None:
    await _install_route_fakes(monkeypatch, tmp_path, _catalog("image_to_video"))

    with pytest.raises(HTTPException, match="image_reference"):
        await freezone_routes.freezone_video_i2v(
            "project",
            FreezoneImageToVideoRequest(
                image_urls=["https://example.com/a.png", "https://example.com/b.png"],
                model="catalog-video",
                gen_mode="imageReference",
            ),
            {"username": "admin"},
        )


@pytest.mark.asyncio
async def test_image_reference_keeps_multi_image_reference_protocol(
    monkeypatch, tmp_path: Path
) -> None:
    captured = await _install_route_fakes(
        monkeypatch,
        tmp_path,
        _catalog("image_reference"),
    )

    await freezone_routes.freezone_video_i2v(
        "project",
        FreezoneImageToVideoRequest(
            image_urls=["https://example.com/a.png", "https://example.com/b.png"],
            model="catalog-video",
            gen_mode="imageReference",
        ),
        {"username": "admin"},
    )

    assert captured["request_mode"] == "imageReference"
    assert captured["start"]["gen_mode"] == "image_reference"
    assert captured["start"]["requested_gen_mode"] == "imageReference"
    assert [item["path"] for item in captured["start"]["reference_items"]] == [
        "https://example.com/a.png",
        "https://example.com/b.png",
    ]


@pytest.mark.asyncio
async def test_video_edit_rejects_model_without_catalog_capability(
    monkeypatch, tmp_path: Path
) -> None:
    await _install_route_fakes(
        monkeypatch,
        tmp_path,
        _catalog("text_to_video", "first_frame", "image_reference"),
    )

    with pytest.raises(HTTPException, match="video_edit"):
        await freezone_routes.freezone_video_edit(
            "project",
            FreezoneVideoEditRequest(
                video_url="https://example.com/input.mp4",
                model="happyhorse-1.1",
                gen_mode="videoEdit",
            ),
            {"username": "admin"},
        )


def test_image_to_video_requires_an_explicit_new_mode() -> None:
    with pytest.raises(ValidationError, match="gen_mode"):
        FreezoneImageToVideoRequest(
            image_urls=["https://example.com/ref.png"],
            model="catalog-video",
        )


def test_fixed_video_routes_reject_cross_route_modes() -> None:
    with pytest.raises(ValidationError, match="gen_mode"):
        FreezoneVideoGenRequest(prompt="rain", gen_mode="allReference")
    with pytest.raises(ValidationError, match="gen_mode"):
        FreezoneVideoOmniGenRequest(prompt="rain", gen_mode="textToVideo")
    with pytest.raises(ValidationError, match="gen_mode"):
        FreezoneVideoEditRequest(video_url="video.mp4", gen_mode="firstFrame")


def test_keyframe_route_requires_explicit_mode() -> None:
    with pytest.raises(ValidationError, match="gen_mode"):
        FreezoneKeyframeVideoRequest(
            first_frame_url="https://example.com/ref.png",
            model="catalog-video",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_mode", "first_url", "last_url", "expected_mode", "expected_image_path"),
    [
        (
            "firstFrame",
            "https://example.com/first.png",
            None,
            "first_frame",
            "https://example.com/first.png",
        ),
        (
            "firstLastFrame",
            "https://example.com/first.png",
            None,
            "first_last_frame",
            "https://example.com/first.png",
        ),
        (
            "firstLastFrame",
            "https://example.com/first.png",
            "https://example.com/last.png",
            "first_last_frame",
            "https://example.com/first.png",
        ),
        (
            "firstLastFrame",
            None,
            "https://example.com/last.png",
            "first_last_frame",
            None,
        ),
    ],
)
async def test_keyframe_route_preserves_selected_mode_for_all_frame_combinations(
    monkeypatch,
    tmp_path: Path,
    requested_mode: str,
    first_url: str | None,
    last_url: str | None,
    expected_mode: str,
    expected_image_path: str | None,
) -> None:
    captured = await _install_route_fakes(
        monkeypatch,
        tmp_path,
        _catalog("first_frame", "first_last_frame"),
    )

    await freezone_routes.freezone_video_keyframes(
        "project",
        FreezoneKeyframeVideoRequest(
            first_frame_url=first_url,
            last_frame_url=last_url,
            model="catalog-video",
            gen_mode=requested_mode,
        ),
        {"username": "admin"},
    )

    assert captured["request_mode"] == requested_mode
    assert captured["start"]["gen_mode"] == expected_mode
    assert captured["start"]["requested_gen_mode"] == requested_mode
    assert captured["start"]["aspect_ratio"] == "auto"
    assert captured["start"]["duration_seconds"] == 5
    assert captured["start"]["last_frame_path"] == last_url
    first_items = [
        item for item in captured["start"]["reference_items"] if item["role"] == "首帧"
    ]
    assert (first_items[0]["path"] if first_items else None) == expected_image_path


@pytest.mark.asyncio
async def test_text_to_video_preserves_selected_ratio_and_duration(
    monkeypatch, tmp_path: Path
) -> None:
    captured = await _install_route_fakes(
        monkeypatch,
        tmp_path,
        _catalog("text_to_video"),
    )

    await freezone_routes.freezone_video_gen(
        "project",
        FreezoneVideoGenRequest(
            prompt="rainy street",
            aspect_ratio="9:16",
            duration_seconds=9,
            model="catalog-video",
        ),
        {"username": "admin"},
    )

    assert captured["start"]["aspect_ratio"] == "9:16"
    assert captured["start"]["duration_seconds"] == 9


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_url", "last_url", "message"),
    [
        (None, None, "requires first_frame_url"),
        (
            "https://example.com/first.png",
            "https://example.com/last.png",
            "does not accept last_frame_url",
        ),
    ],
)
async def test_first_frame_mode_rejects_invalid_frame_combinations(
    monkeypatch,
    tmp_path: Path,
    first_url: str | None,
    last_url: str | None,
    message: str,
) -> None:
    await _install_route_fakes(monkeypatch, tmp_path, _catalog("first_frame"))

    with pytest.raises(HTTPException, match=message):
        await freezone_routes.freezone_video_keyframes(
            "project",
            FreezoneKeyframeVideoRequest(
                first_frame_url=first_url,
                last_frame_url=last_url,
                model="catalog-video",
                gen_mode="firstFrame",
            ),
            {"username": "admin"},
        )


@pytest.mark.asyncio
async def test_keyframes_rejects_frames_beyond_declared_slots_with_facts(
    monkeypatch, tmp_path: Path
) -> None:
    """[req44] 图槽不够时不能只丢一句「最多 N 张」：申报、后果、出路都要摊开。

    实拉取证（2026-10-08）：渠道给 LTX 2.5 的 ``image_to_video`` 绑定只有一个图槽
    （``image``「首帧图片」），``/v1/models`` 也写着 ``maxInputImages: 1``。首尾帧页签
    按产品口径放开是对的（不拿渠道接线不足当能力边界），但放它提交出去只会被上游默默
    截成一张、照常计费 —— 拦是对的，文案必须让人看懂该动哪里。
    """

    capabilities = _catalog("first_last_frame")
    capabilities["referenceImageMax"] = 1
    await _install_route_fakes(monkeypatch, tmp_path, capabilities)

    with pytest.raises(HTTPException) as exc:
        await freezone_routes.freezone_video_keyframes(
            "project",
            FreezoneKeyframeVideoRequest(
                first_frame_url="https://example.com/first.png",
                last_frame_url="https://example.com/last.png",
                model="ap-ltx-2.5",
                gen_mode="firstLastFrame",
            ),
            {"username": "admin"},
        )

    assert exc.value.status_code == 400
    detail = str(exc.value.detail)
    assert "首尾帧" in detail
    assert "参考图上限只有 1 张" in detail
    # 渠道自己的申报原样带出来当证据（数字来自目录条目，不是文案里写死的）。
    assert "渠道 /v1/models 对它的申报是：参考图上限 1" in detail
    assert "出路" in detail and "首帧生成视频" in detail
    assert "ap-ltx-2.5" in detail




# ---------------------------------------------------------------------------
# [aipdd fork delta] 网关声明了引用输入上限（referenceAudioMax / referenceVideoMax）
# 但没写 supportedModes 的非 Seedance2 模型（如 ap-minimax-h3）：过去被「只支持
# Seedance 2.0」硬闸 400，现按引用容量放行全能参考。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_omni_gen_allows_non_seedance_model_with_declared_reference_audio(
    monkeypatch, tmp_path: Path
) -> None:
    capabilities = {
        "catalog_id": "catalog-minimax",
        "referenceImageMax": 9,
        "referenceAudioMax": 3,
        "resolutionOptions": ["768p"],
        "minDuration": 1,
        "maxDuration": 15,
    }
    captured = await _install_route_fakes(
        monkeypatch, tmp_path, capabilities, backend="ap-minimax-h3"
    )

    await freezone_routes.freezone_video_omni_gen(
        "project",
        FreezoneVideoOmniGenRequest(
            prompt="生成带背景音乐的视频",
            references=[
                {"type": "audio", "url": "https://example.com/bgm.mp3", "role": "背景"}
            ],
            model="catalog-minimax",
            gen_mode="allReference",
        ),
        {"username": "admin"},
    )

    assert captured["request_mode"] == "allReference"
    assert captured["start"]["reference_items"][0]["type"] == "audio"


@pytest.mark.asyncio
async def test_omni_gen_rejects_non_seedance_model_without_reference_capability(
    monkeypatch, tmp_path: Path
) -> None:
    # 没声明引用容量、又非 seedance2 → 仍走历史硬闸拒绝。
    capabilities = {
        "catalog_id": "catalog-plain",
        "resolutionOptions": ["720p"],
    }
    await _install_route_fakes(
        monkeypatch, tmp_path, capabilities, backend="ap-some-other-video"
    )
    with pytest.raises(HTTPException, match="only supports Seedance"):
        await freezone_routes.freezone_video_omni_gen(
            "project",
            FreezoneVideoOmniGenRequest(
                prompt="生成视频",
                references=[{"type": "audio", "url": "https://example.com/bgm.mp3"}],
                model="catalog-plain",
                gen_mode="allReference",
            ),
            {"username": "admin"},
        )


def test_catalog_omni_reference_capable_reads_declared_limits() -> None:
    assert freezone_routes._catalog_omni_reference_capable({"referenceAudioMax": 3}) is True
    assert freezone_routes._catalog_omni_reference_capable({"referenceVideoMax": 1}) is True
    # 只有图片引用上限不算全能参考（那是图片参考 / image_reference 管的）。
    assert freezone_routes._catalog_omni_reference_capable({"referenceImageMax": 9}) is False
    assert freezone_routes._catalog_omni_reference_capable({"referenceAudioMax": 0}) is False
    assert freezone_routes._catalog_omni_reference_capable({}) is False
    assert freezone_routes._catalog_omni_reference_capable(None) is False


def test_catalog_omni_reference_capable_also_reads_binding_mode_evidence() -> None:
    """[req44] 渠道挂了全能参考的在线绑定（``bindingModes`` 含 all_reference）同样算具备容量。

    前端 `isVideoModeSupportedByModel` 已按同一份证据放开全能参考页签，后端 omni 闸必须
    同源，否则就是「看得见入口、提交被只 seedance2 的历史硬闸拦下」。依旧**只加不减**：
    没带这份证据的条目行为一字不变，形状不对（不是序列）也不表态。
    """

    assert (
        freezone_routes._catalog_omni_reference_capable({"bindingModes": ["all_reference"]})
        is True
    )
    assert (
        freezone_routes._catalog_omni_reference_capable(
            {"bindingModes": ["first_frame", "text_to_video"]}
        )
        is False
    )
    assert freezone_routes._catalog_omni_reference_capable({"bindingModes": []}) is False
    assert freezone_routes._catalog_omni_reference_capable({"bindingModes": None}) is False
    assert (
        freezone_routes._catalog_omni_reference_capable({"bindingModes": "all_reference"})
        is False
    )


def _ratio_catalog() -> dict:
    catalog = _catalog("all_reference")
    catalog.update(
        {
            "referenceVideoMax": 3,
            "referenceImageMax": 9,
            "ratioOptions": ["16:9", "9:16", "1:1"],
        }
    )
    return catalog


@pytest.mark.asyncio
async def test_omni_gen_aspect_ratio_follows_source_video_within_catalog_tiers(
    monkeypatch, tmp_path: Path
) -> None:
    """[req42] 换人/换产品/对口型的画幅按源视频判定，且只落在目录声明的档位里。

    旧口径把 ``auto`` 原样传到建单体：AIPDD 收不到 ratio 时固定输出 864x480 横屏，
    竖屏素材产物和原视频看着完全无关。画幅是素材里已经写着的答案。
    """

    from novelvideo.api.routes.freezone import catalog as catalog_module

    captured = await _install_route_fakes(monkeypatch, tmp_path, _ratio_catalog())
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake-video")
    calls: dict = {}

    async def fake_probe(path: str, allowed=None) -> str:
        calls["path"] = path
        calls["allowed"] = list(allowed or [])
        return "9:16"

    monkeypatch.setattr(catalog_module, "probe_video_aspect_ratio", fake_probe)

    await freezone_routes.freezone_video_omni_gen(
        "project",
        FreezoneVideoOmniGenRequest(
            prompt="把画面里的产品换成这张图里的",
            aspect_ratio="auto",
            references=[
                {"type": "video", "url": str(source)},
                {"type": "image", "url": "https://example.com/product.png"},
            ],
            model="catalog-video",
        ),
        {"username": "admin"},
    )

    assert captured["start"]["aspect_ratio"] == "9:16"
    assert calls["path"] == str(source)
    assert calls["allowed"] == ["16:9", "9:16", "1:1"]


@pytest.mark.asyncio
async def test_omni_gen_allows_empty_prompt_with_source_video(
    monkeypatch, tmp_path: Path
) -> None:
    """[req42] 画布智能剪辑就是「空提示词 + 源视频 + 参考图」提交的：旧口径直接 400，
    换产品流程在画布里根本跑不起来；现在放行，并由后端补出「保留源视频、只换产品」
    的任务指令。"""

    captured = await _install_route_fakes(monkeypatch, tmp_path, _ratio_catalog())
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake-video")
    image = tmp_path / "product.png"
    image.write_bytes(b"fake-image")

    async def fake_probe(path: str, allowed=None) -> str:
        return "9:16"

    from novelvideo.api.routes.freezone import catalog as catalog_module

    monkeypatch.setattr(catalog_module, "probe_video_aspect_ratio", fake_probe)

    await freezone_routes.freezone_video_omni_gen(
        "project",
        FreezoneVideoOmniGenRequest(
            prompt="",
            source_tool="replace-product",
            references=[
                {"type": "video", "url": str(source)},
                {"type": "image", "url": str(image)},
            ],
            model="catalog-video",
        ),
        {"username": "admin"},
    )

    prompt = captured["start"]["prompt"]
    assert "任务：把源视频画面中的产品替换为参考图所示的产品" in prompt
    assert captured["start"]["source_tool"] == "replace-product"


@pytest.mark.asyncio
async def test_omni_gen_still_requires_prompt_without_a_source_video(
    monkeypatch, tmp_path: Path
) -> None:
    """没有源视频当画面基准时，空提示词依旧拒——不能凭参考图开一条无主题的文生视频。"""

    await _install_route_fakes(monkeypatch, tmp_path, _ratio_catalog())
    image = tmp_path / "product.png"
    image.write_bytes(b"fake-image")

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_omni_gen(
            "project",
            FreezoneVideoOmniGenRequest(
                prompt="",
                source_tool="replace-product",
                references=[{"type": "image", "url": str(image)}],
                model="catalog-video",
            ),
            {"username": "admin"},
        )
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_omni_gen_keeps_explicit_aspect_ratio_without_probing(
    monkeypatch, tmp_path: Path
) -> None:
    """用户显式选了画幅就照用，不再自作聪明探测素材。"""

    from novelvideo.api.routes.freezone import catalog as catalog_module

    captured = await _install_route_fakes(monkeypatch, tmp_path, _ratio_catalog())
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake-video")

    def _boom(*_args, **_kwargs):
        raise AssertionError("显式画幅不该触发探测")

    monkeypatch.setattr(catalog_module, "probe_video_aspect_ratio", _boom)

    await freezone_routes.freezone_video_omni_gen(
        "project",
        FreezoneVideoOmniGenRequest(
            prompt="横屏出片",
            aspect_ratio="16:9",
            references=[{"type": "video", "url": str(source)}],
            model="catalog-video",
        ),
        {"username": "admin"},
    )

    assert captured["start"]["aspect_ratio"] == "16:9"


@pytest.mark.asyncio
async def test_omni_gen_falls_back_when_source_probe_fails(
    monkeypatch, tmp_path: Path
) -> None:
    """探测失败退回 16:9 并放行，不能把一次 ffprobe 抖动变成整单被拒。"""

    from novelvideo.api.routes.freezone import catalog as catalog_module

    captured = await _install_route_fakes(monkeypatch, tmp_path, _ratio_catalog())
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake-video")

    async def _fail(_path: str, _allowed=None) -> str:
        raise RuntimeError("moov atom not found")

    monkeypatch.setattr(catalog_module, "probe_video_aspect_ratio", _fail)

    await freezone_routes.freezone_video_omni_gen(
        "project",
        FreezoneVideoOmniGenRequest(
            prompt="继续",
            aspect_ratio="auto",
            references=[{"type": "video", "url": str(source)}],
            model="catalog-video",
        ),
        {"username": "admin"},
    )

    assert captured["start"]["aspect_ratio"] == "16:9"


@pytest.mark.asyncio
async def test_omni_gen_does_not_probe_remote_or_missing_reference(
    monkeypatch, tmp_path: Path
) -> None:
    """只探本地素材文件：直链与不存在的路径一律不喂给 ffprobe。"""

    from novelvideo.api.routes.freezone import catalog as catalog_module

    def _boom(*_args, **_kwargs):
        raise AssertionError("不该对远端/缺失素材起探测")

    monkeypatch.setattr(catalog_module, "probe_video_aspect_ratio", _boom)

    remote = await catalog_module._resolve_omni_aspect_ratio(
        "auto",
        [{"type": "video", "path": "https://example.com/a.mp4"}],
        {"ratioOptions": ["9:16"]},
    )
    missing = await catalog_module._resolve_omni_aspect_ratio(
        "auto",
        [{"type": "video", "path": str(tmp_path / "nope.mp4")}],
        {"ratioOptions": ["9:16"]},
    )
    no_video = await catalog_module._resolve_omni_aspect_ratio(
        "auto",
        [{"type": "image", "path": "https://example.com/a.png"}],
        {"ratioOptions": ["9:16"]},
    )
    assert (remote, missing, no_video) == ("16:9", "16:9", "16:9")


# ---------------------------------------------------------------- req43 能力绑定闸门
#
# 事实来自渠道自己的 /v1/capabilities，测试直接注入判定结果，不打真实渠道。
#
# 【已更正的口径】闸的判据是「这条提交模式要走的哪一条绑定」，不是「这个模型名下面
# 任何绑定」：同一个模型名可以挂着文生 / 首尾帧 / ref2va 几条入参集完全不同的绑定，
# 按模型名取并集在两个方向都会错。绑定事实也**不再用来把目录里的参考上限归零**——
# 渠道接线不足不等于模型没能力，拿它藏入口是把渠道的缺陷当成用户的能力边界。

_VIDEO_REF = [{"type": "video", "url": "https://example.com/source.mp4"}]
_AUDIO_REF = [{"type": "audio", "url": "https://example.com/voice.mp3"}]


def _omni_facts(**overrides) -> list:
    """线上实况的缩样：全能参考模式认到的就是那条接错的 ref2va 绑定。"""

    facts = {
        "mode": "all_reference",
        "bindings": ["aipdd_minimax_h3_ref2va_test"],
        "taskKinds": ["first_last_frame_to_video"],
        "acceptsReferenceVideo": False,
        "acceptsReferenceAudio": False,
        "acceptsReferenceImage": True,
        "inputUiTypes": ["image_url", "number", "textarea"],
        "inputParams": [
            "aipdd_minimax_h3_ref2va_test: 提示词 / 首帧图片 / 尾帧图片 / 时长（秒）"
        ],
    }
    facts.update(overrides)
    return [("aipdd", facts)]


def _patch_omni_facts(monkeypatch, facts_list) -> dict:
    """注入判定结果，并把路由传下来的 ``mode`` 记下来（闸门必须按模式问）。"""

    seen: dict = {}

    def _fake(_model, *, mode=None):
        seen["mode"] = mode
        return facts_list

    monkeypatch.setattr(
        "novelvideo.api.routes.freezone.catalog._omni_reference_binding_facts",
        _fake,
    )
    return seen


async def _send_omni(monkeypatch, tmp_path, references, model, capabilities=None):
    captured = await _install_route_fakes(
        monkeypatch,
        tmp_path,
        _wan_catalog() if capabilities is None else capabilities,
    )
    await freezone_routes.freezone_video_omni_gen(
        "project",
        FreezoneVideoOmniGenRequest(prompt="换产品", references=references, model=model),
        {"username": "admin"},
    )
    return captured


@pytest.mark.asyncio
async def test_omni_video_forwards_reference_video_when_binding_takes_it(
    monkeypatch, tmp_path
) -> None:
    _patch_omni_facts(monkeypatch, _omni_facts(acceptsReferenceVideo=True))
    captured = await _send_omni(monkeypatch, tmp_path, _VIDEO_REF, "doubao-seedance-2-0")
    assert captured["start"]["reference_items"][0]["type"] == "video"


@pytest.mark.asyncio
async def test_omni_video_without_binding_facts_does_not_block(
    monkeypatch, tmp_path
) -> None:
    """渠道没暴露能力接口（没读过事实）时不表态：闸门不能变成新的猜测源。"""

    _patch_omni_facts(monkeypatch, [])
    captured = await _send_omni(monkeypatch, tmp_path, _VIDEO_REF, "catalog-video")
    assert "start" in captured


@pytest.mark.asyncio
async def test_omni_video_rejects_reference_video_when_binding_cannot_take_it(
    monkeypatch, tmp_path
) -> None:
    captured = await _install_route_fakes(monkeypatch, tmp_path, _wan_catalog())
    seen = _patch_omni_facts(monkeypatch, _omni_facts())

    with pytest.raises(HTTPException, match="递不进上游") as exc:
        await freezone_routes.freezone_video_omni_gen(
            "project",
            FreezoneVideoOmniGenRequest(
                prompt="换产品", references=_VIDEO_REF, model="ap-minimax-h3"
            ),
            {"username": "admin"},
        )

    assert exc.value.status_code == 400
    # 路由要把本次的提交模式一路传到底：问错绑定就得出错答案。
    assert seen == {"mode": "allReference"}
    detail = exc.value.detail
    # 报错要给证据（该模式关联到哪条绑定、它实际吃什么入参）和出路，不能只说「不支持」。
    assert "aipdd_minimax_h3_ref2va_test" in detail
    assert "首帧图片" in detail
    assert "尾帧图片" in detail
    assert "first_last_frame_to_video" in detail
    # 必须说是哪条提交模式递不进去，并拿渠道自己在 /v1/models 的申报作对照。
    assert "全能参考" in detail
    assert "参考视频上限 5" in detail
    assert "申报与绑定入参冲突" in detail
    assert "与原视频毫不相干" in detail
    # 同渠道没有可用备选时，不能指向一份不存在的「上面那份出路」。
    assert "或直接改用上面出路" not in detail
    # 拒单发生在提交之前：一次都没打上游，不烧额度。
    assert "start" not in captured


def test_omni_reference_block_text_labels_each_binding_when_several_are_matched(
    monkeypatch,
) -> None:
    """该模式关联到多条绑定时逐条前缀 code：运维要对着渠道配置一条一条排查。"""

    from novelvideo.api.routes.freezone import catalog as catalog_module

    # 出路候选走渠道自己的绑定清单：这里固定为空，让本测只验「措辞」。
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_provider_capability_bindings",
        lambda _provider: [],
    )

    facts = _omni_facts() + [
        (
            "other",
            {
                "bindings": ["other_h3_ref2va"],
                "taskKinds": ["reference_to_video"],
                "acceptsReferenceVideo": False,
                "inputParams": ["other_h3_ref2va: 提示词 / 参考图片"],
            },
        )
    ]
    text = catalog_module._omni_reference_input_block_text(
        model="ap-minimax-h3",
        kind="video",
        label="参考视频",
        feature="替换视频产品 / 替换视频人物",
        consequence="产物将是一条与原视频毫不相干的片子",
        facts_list=facts,
        mode="all_reference",
    )
    assert "这些绑定实际收到的入参只有" in text
    assert "other_h3_ref2va: 提示词 / 参考图片" in text
    assert "aipdd_minimax_h3_ref2va_test: 提示词" in text


@pytest.mark.asyncio
async def test_omni_video_reject_names_the_channel_binding_that_would_work(
    monkeypatch, tmp_path
) -> None:
    """出路要从事实表现算：同渠道哪条在线绑定确实吃参考视频、它能不能按模型名调到。

    AIPDD 今天只有一条吃参考视频 / 音频的自托管绑定（对口型那类），但它没出现在渠道
    的模型清单里 —— 报错必须把这层说清，用户才知道该让渠道发布模型位，而不是
    盲目在渠道里找一个不存在的「支持参考视频」模型。
    """

    _patch_omni_facts(monkeypatch, _omni_facts())
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_provider_capability_bindings",
        lambda _provider: [
            {
                "code": "aipdd_minimax_h3_ref2va_test",
                "taskKind": "first_last_frame_to_video",
                "uiTypes": ["image_url"],
                "inputModalities": ["image"],
                "available": True,
            },
            {
                "code": "aipdd_latentsync1.5",
                "taskKind": "lip_sync",
                "uiTypes": ["audio_url", "video_url"],
                "inputModalities": ["audio", "video"],
                "available": True,
            },
        ],
    )
    # 渠道模型清单里没有那条绑定 → 按模型名提交不到。
    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.list_provider_upstream_models",
        lambda _provider: (["ap-minimax-h3", "ap-ltx-2.5"], ""),
    )

    with pytest.raises(HTTPException) as exc:
        await _send_omni(monkeypatch, tmp_path, _VIDEO_REF, "ap-minimax-h3")

    assert "aipdd_latentsync1.5" in exc.value.detail
    assert "lip_sync" in exc.value.detail
    assert "按模型名提交不到" in exc.value.detail


@pytest.mark.asyncio
async def test_omni_video_reject_offers_published_binding_as_switch_target(
    monkeypatch, tmp_path
) -> None:
    """同一条绑定如果已经发布成可调用模型，报错要直接说出「可改用」。"""

    _patch_omni_facts(monkeypatch, _omni_facts())
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_provider_capability_bindings",
        lambda _provider: [
            {
                "code": "aipdd_latentsync1.5",
                "taskKind": "lip_sync",
                "uiTypes": ["audio_url", "video_url"],
                "inputModalities": ["audio", "video"],
                "available": True,
            }
        ],
    )
    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.list_provider_upstream_models",
        lambda _provider: (["aipdd_latentsync1.5"], ""),
    )

    with pytest.raises(HTTPException) as exc:
        await _send_omni(monkeypatch, tmp_path, _VIDEO_REF, "ap-minimax-h3")

    assert "可直接改用的在线绑定" in exc.value.detail
    assert "已发布为可调用模型" in exc.value.detail


def test_omni_video_reject_stands_by_itself_when_model_list_is_unreachable(
    monkeypatch
) -> None:
    """模型清单拉不到时不能把远程故障说成渠道缺陷：那句「调不到」就不写。"""

    from novelvideo.api.routes.freezone import catalog as catalog_module
    import novelvideo.newapi_provisioner as provisioner

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_provider_capability_bindings",
        lambda _provider: [
            {
                "code": "aipdd_latentsync1.5",
                "taskKind": "lip_sync",
                "uiTypes": ["video_url"],
                "inputModalities": ["video"],
                "available": True,
            }
        ],
    )

    def boom(_provider: str):
        raise RuntimeError("upstream unreachable")

    monkeypatch.setattr(provisioner, "list_provider_upstream_models", boom)
    text = catalog_module._omni_reference_input_block_text(
        model="ap-minimax-h3",
        kind="video",
        label="参考视频",
        feature="替换视频产品 / 替换视频人物",
        consequence="产物将是一条与原视频毫不相干的片子",
        facts_list=_omni_facts(),
        mode="all_reference",
    )
    assert "aipdd_latentsync1.5" in text
    assert "全能参考" in text
    assert "按模型名提交不到" not in text
    assert "未出现在渠道的模型清单" not in text


@pytest.mark.asyncio
async def test_omni_video_rejects_reference_audio_when_binding_takes_video_only(
    monkeypatch, tmp_path
) -> None:
    """吃视频不等于吃音频：对口型链路要按音频通道单独判。"""

    captured = await _install_route_fakes(monkeypatch, tmp_path, _wan_catalog())
    _patch_omni_facts(monkeypatch, _omni_facts(acceptsReferenceVideo=True))

    with pytest.raises(HTTPException, match="参考音频") as exc:
        await freezone_routes.freezone_video_omni_gen(
            "project",
            FreezoneVideoOmniGenRequest(
                prompt="让他说这段话", references=_AUDIO_REF, model="ap-minimax-h3"
            ),
            {"username": "admin"},
        )

    assert "对口型" in exc.value.detail
    assert "start" not in captured


# ---------------------------------------------------------------- req43 目录出口：能力面按渠道申报展示
#
# 【纠错】先前这一层拿 /v1/capabilities 的绑定入参去把 /v1/models 申报的参考上限归零，
# 理由是「界面与提交必须同一套事实」。口径反了：渠道接线不足不等于模型没这个能力，
# 归零会把用户本来能用的全能参考入口直接藏掉，而且渠道补好绑定后也不会自动回来。
# 现在界面按渠道申报放开，递不递得进去到提交那一步再按绑定拦、并把冲突摊进报错。


def _h3_declared_entry() -> dict:
    """渠道 /v1/models 给 H3 的真实申报形态：报了参考图 / 参考音频上限，参考视频漏写。"""

    return {
        "apiModel": "ap-minimax-h3",
        "referenceImageMax": 9,
        "referenceAudioMax": 3,
    }


def test_video_catalog_no_longer_zeroes_declared_caps_from_binding_facts() -> None:
    """绑定事实不能改写目录出口：归零那一刀必须彻底不存在。

    这是纠错的回归闸：哪天有人再把「渠道今天没接线」当成「用户没能力」，这里会红。
    参考视频上限没申报也不补 0：缺失落回默认额度，而不是「渠道说它只能挂 0 条」。
    """

    from novelvideo.api.routes.freezone import catalog as catalog_module

    assert not hasattr(catalog_module, "_reconcile_video_reference_inputs")
    entry = _h3_declared_entry()
    # 渠道申报了参考音频上限就具备全能参考消费能力，与今天提交走得通与否无关。
    assert freezone_routes._catalog_omni_reference_capable(entry) is True
    assert freezone_routes._catalog_reference_limits(
        entry, image_default=9, video_default=3, audio_default=3
    ) == {"image": 9, "video": 3, "audio": 3, "file": 0, "link": 0}


# 渠道给同一个模型名的三条在线绑定（ /v1/capabilities 实测缩样）：入参集各不相同。
_AIPDD_H3_BINDINGS = [
    {
        "code": "aipdd_minimax_h3_t2v_speed_test",
        "name": "MiniMax H3 文生视频",
        "endpointType": "openai-video",
        "taskKind": "text_to_video",
        "inputModalities": ["text"],
        "params": [
            {"uiType": "textarea", "paramKey": "prompt", "paramName": "提示词", "isRequired": True},
            {"uiType": "number", "paramKey": "durationSeconds", "paramName": "时长（秒）"},
        ],
        "available": True,
    },
    {
        "code": "aipdd_minimax_h3_first_last_frame_speed_test",
        "name": "MiniMax H3 首尾帧",
        "endpointType": "openai-video",
        "taskKind": "first_last_frame_to_video",
        "inputModalities": ["text", "image"],
        "params": [
            {"uiType": "textarea", "paramKey": "prompt", "paramName": "提示词", "isRequired": True},
            {"uiType": "image_url", "paramKey": "image", "paramName": "首帧图片", "isRequired": True},
            {
                "uiType": "image_url",
                "paramKey": "referenceImage",
                "paramName": "尾帧图片",
                "isRequired": True,
            },
        ],
        "available": True,
    },
    {
        # 线上实况：名字写着 ref2va，接线却只到「首尾帧两张图」，连 taskKind 都写错。
        "code": "aipdd_minimax_h3_ref2va_test",
        "name": "MiniMax H3 参考图生视频",
        "endpointType": "openai-video",
        "taskKind": "first_last_frame_to_video",
        "inputModalities": ["text", "image"],
        "params": [
            {"uiType": "textarea", "paramKey": "prompt", "paramName": "提示词", "isRequired": True},
            {"uiType": "image_url", "paramKey": "image", "paramName": "首帧图片", "isRequired": True},
            {
                "uiType": "image_url",
                "paramKey": "referenceImage",
                "paramName": "尾帧图片",
                "isRequired": True,
            },
            {"uiType": "number", "paramKey": "durationSeconds", "paramName": "时长（秒）"},
        ],
        "available": True,
    },
]


@pytest.mark.asyncio
async def test_omni_video_gate_uses_the_real_mode_scoped_matcher(
    monkeypatch, tmp_path
) -> None:
    """端到端跑真的按模式筛逻辑：全能参考只问 ref2va 那一条，不拿并集下结论。"""

    from novelvideo import model_gateway_settings
    from novelvideo.catalog.upstream_capabilities import summarize_video_bindings

    monkeypatch.setattr(
        model_gateway_settings, "model_owner_providers", lambda _model: ["aipdd"]
    )
    monkeypatch.setattr(
        model_gateway_settings,
        "get_provider_capability_bindings",
        lambda _provider: summarize_video_bindings(_AIPDD_H3_BINDINGS),
    )

    with pytest.raises(HTTPException) as exc:
        await _send_omni(
            monkeypatch, tmp_path, _VIDEO_REF, "ap-minimax-h3", _h3_declared_entry()
        )

    detail = exc.value.detail
    assert "aipdd_minimax_h3_ref2va_test" in detail
    assert "尾帧图片" in detail
    # 文生那条连参考图节点都没有，首尾帧那条只递两张图：证据只能来自该模式关联的绑定，
    # 不能把三条并起来说话。
    assert "aipdd_minimax_h3_t2v_speed_test" not in detail


@pytest.mark.asyncio
async def test_omni_video_reference_image_only_is_never_blocked_by_the_video_gate(
    monkeypatch, tmp_path
) -> None:
    """只递参考图时不走视频/音频那一刀：全能参考模式下递图是 ref2va 绑定真吃了的。"""

    from novelvideo import model_gateway_settings
    from novelvideo.catalog.upstream_capabilities import summarize_video_bindings

    monkeypatch.setattr(
        model_gateway_settings, "model_owner_providers", lambda _model: ["aipdd"]
    )
    monkeypatch.setattr(
        model_gateway_settings,
        "get_provider_capability_bindings",
        lambda _provider: summarize_video_bindings(_AIPDD_H3_BINDINGS),
    )

    captured = await _send_omni(
        monkeypatch,
        tmp_path,
        [{"type": "image", "url": "https://example.com/keyframe.png"}],
        "ap-minimax-h3",
        _h3_declared_entry(),
    )

    assert captured["start"]["reference_items"][0]["type"] == "image"

@pytest.mark.asyncio
async def test_omni_video_forwards_asset_library_reference(
    monkeypatch, tmp_path: Path
) -> None:
    """[aipdd fork delta req47] 全能参考递素材库引用：原样落到 ``path``，且绕开本地路径解析。

    真实的解析器只认同源引用（带 scheme 的会被当外链拒掉，那是 req 系列钉过的 SSRF 边界），
    所以这里把探针换成「一旦被调用就失败」：正确做法是在调用点之前就认出这是库内引用，
    而不是偷偷放宽解析器。
    """

    captured = await _install_route_fakes(monkeypatch, tmp_path, _catalog("all_reference"))
    resolver_seen: list[str] = []

    def recording_resolver(_project_dir, urls):
        resolver_seen.extend(str(url) for url in urls if url)
        return [str(url) for url in urls if url]

    monkeypatch.setattr(freezone_routes, "_resolve_url_list", recording_resolver)

    await freezone_routes.freezone_video_omni_gen(
        "project",
        FreezoneVideoOmniGenRequest(
            prompt="让这位真人走出画面",
            references=[
                {
                    "type": "image",
                    "url": "asset://asset-20260318071009-abcde",
                    "role": "真人形象",
                },
                {"type": "image", "url": "https://example.com/extra.png"},
            ],
            model="catalog-video",
        ),
        {"username": "admin"},
    )

    assert captured["start"]["reference_items"] == [
        {
            "type": "image",
            "path": "asset://asset-20260318071009-abcde",
            "role": "真人形象",
        },
        {"type": "image", "path": "https://example.com/extra.png", "role": ""},
    ]
    # 引用没进本地解析器（真解析器只认同源引用，带 scheme 一律当外链拒掉）。
    assert "asset://asset-20260318071009-abcde" not in resolver_seen


@pytest.mark.asyncio
async def test_omni_video_rejects_asset_reference_without_an_id(
    monkeypatch, tmp_path: Path
) -> None:
    """空 Id 的 ``asset://`` 在提交前就拒：递上去只会被上游拒掉，还可能照扣。"""

    await _install_route_fakes(monkeypatch, tmp_path, _catalog("all_reference"))

    with pytest.raises(HTTPException, match="素材 Id 不能为空"):
        await freezone_routes.freezone_video_omni_gen(
            "project",
            FreezoneVideoOmniGenRequest(
                prompt="生成一段视频",
                references=[{"type": "image", "url": "asset://"}],
                model="catalog-video",
            ),
            {"username": "admin"},
        )


@pytest.mark.asyncio
async def test_omni_video_skips_file_extension_allowlist_for_asset_reference(
    monkeypatch, tmp_path: Path
) -> None:
    """素材库引用没有扩展名可校：类型与审核状态在入库时就定了，不该被 file 白名单挡掉。"""

    captured = await _install_route_fakes(monkeypatch, tmp_path, _wan_catalog())

    await freezone_routes.freezone_video_omni_gen(
        "project",
        FreezoneVideoOmniGenRequest(
            prompt="按这位真人的形象生成",
            references=[
                {
                    "type": "file",
                    "url": "asset://asset-20260318071009-abcde",
                    "role": "真人素材",
                }
            ],
            model="catalog-video",
        ),
        {"username": "admin"},
    )

    assert captured["start"]["reference_items"] == [
        {
            "type": "file",
            "path": "asset://asset-20260318071009-abcde",
            "role": "真人素材",
        }
    ]
