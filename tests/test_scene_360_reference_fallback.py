"""场景 360 的参考图降级重试。

渠道对「一次递几张参考图」有硬上限，而图模型目录不申报这个上限（只有视频模型有
``maxImageInputs``/``maxInputImages``），所以拿不到事前结论：只能被拒之后按优先级逐张
减一张再试。拒单不扣费，代价就是一趟被拒的请求。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.director_world import scene_360_builder as builder

# 线上原话：渠道把「这次递了几张」写进拒绝理由，但没写它到底收几张。
CHANNEL_REJECTION = "当前模型暂无法处理 4 张参考图，请减少参考图后重试；本次请求未扣费"


def test_detects_reference_count_rejection() -> None:
    assert builder.is_reference_count_rejection(CHANNEL_REJECTION) is True
    assert (
        builder.is_reference_count_rejection("This model only supports 2 input images") is True
    )
    assert builder.is_reference_count_rejection("too many reference images") is True


@pytest.mark.parametrize(
    "message",
    [
        "",
        "HTTP 503 · model_not_found",
        "No available channel for model X under group default (distributor)",
        "insufficient credits, please recharge",
        "image size exceeds the maximum allowed pixels",
    ],
)
def test_ignores_unrelated_provider_errors(message: str) -> None:
    assert builder.is_reference_count_rejection(message) is False


def test_drop_order_keeps_master_and_geometry() -> None:
    kept = ["master", "reverse", "direction_guide", "topology_strip", "spatial_layout"]
    dropped: list[str] = []
    while len(kept) > 1:
        victim = builder.next_reference_to_drop(kept)
        assert victim is not None
        kept.remove(victim)
        dropped.append(victim)
    assert dropped == ["topology_strip", "direction_guide", "reverse", "spatial_layout"]
    assert kept == ["master"]
    assert builder.next_reference_to_drop(["master"]) is None


def _png(path: Path, color: tuple[int, int, int] = (200, 120, 60)) -> Path:
    Image.new("RGB", (240, 160), color=color).save(path, format="PNG")
    return path


def _stub_builder(monkeypatch, fake_call) -> None:
    """把起图以外的四周（.env、风格库、联系人图）收掉，只留下参考图降级这条链。"""
    monkeypatch.setattr(builder, "load_env", lambda: None)
    monkeypatch.setattr(builder, "make_contact_sheet", lambda *args: None)
    monkeypatch.setattr(builder, "get_style_preset", lambda style, **kwargs: {})
    monkeypatch.setattr(builder, "_call_openai_image_api", fake_call)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def _args(tmp_path: Path, master: Path, reverse: Path) -> SimpleNamespace:
    return SimpleNamespace(
        quality="medium",
        image_size="1K",
        provider="openai",
        output_dir=str(tmp_path / "out"),
        text_only=False,
        scene_name="Hall",
        master=str(master),
        reverse_master=str(reverse),
        spatial_layout="",
        spatial_contract="auto",
        overlap_analysis="auto",
        model="test-image-model",
        scene_description="a compact interior",
        style="spider_verse_mixed_media",
        layer_mode="full",
    )


@pytest.mark.asyncio
async def test_scene_360_drops_references_until_the_channel_accepts(
    monkeypatch, tmp_path: Path
) -> None:
    master = _png(tmp_path / "master.png")
    reverse = _png(tmp_path / "reverse_master.png", color=(60, 120, 200))

    sent: list[int] = []

    async def fake_call(**kwargs):
        refs = kwargs["reference_images"] or []
        sent.append(len(refs))
        if len(refs) > 2:
            return None, "", CHANNEL_REJECTION
        return b"png-bytes", "", ""

    _stub_builder(monkeypatch, fake_call)

    assert await builder.run(_args(tmp_path, master, reverse)) == 0

    # 四张 → 三张 → 两张：先扔拓扑条，再扔方向指引，master / reverse 保住。
    assert sent == [4, 3, 2]

    manifest = json.loads(
        (tmp_path / "out" / "scene_360_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["reference_images_built"] == [
        "master",
        "reverse",
        "direction_guide",
        "topology_strip",
    ]
    assert manifest["reference_images_sent"] == ["master", "reverse"]
    assert manifest["reference_images_dropped"] == ["direction_guide", "topology_strip"]
    assert [attempt["reference_image_count"] for attempt in manifest["reference_attempts"]] == [
        4,
        3,
        2,
    ]
    assert manifest["reference_attempts"][0]["error"] == CHANNEL_REJECTION
    assert manifest["reference_attempts"][-1]["error"] == ""

    # 提示词里的图序必须跟着这次真发出去的图重算：留着没发的图序，模型会当真去找不存在的图。
    prompt = (tmp_path / "out" / "scene_360.prompt.txt").read_text(encoding="utf-8")
    roles = prompt.split("INPUT IMAGE ROLES:")[-1].split("\n\n")[0]
    assert "(topology_strip_2to1.jpg)" not in roles
    assert "(direction_alignment_guide.jpg)" not in roles
    assert "Reference image 3" not in roles
    assert "Reference image 2 (reverse_master.png) = BACK-HALF VISUAL BIBLE." in roles


@pytest.mark.asyncio
async def test_scene_360_gives_up_with_the_channel_message_when_one_ref_is_the_floor(
    monkeypatch, tmp_path: Path
) -> None:
    master = _png(tmp_path / "master.png")
    reverse = _png(tmp_path / "reverse_master.png")

    sent: list[int] = []

    async def always_reject(**kwargs):
        sent.append(len(kwargs["reference_images"] or []))
        return None, "", CHANNEL_REJECTION

    _stub_builder(monkeypatch, always_reject)

    with pytest.raises(RuntimeError) as failed:
        await builder.run(_args(tmp_path, master, reverse))

    # 降到只剩 master 就到底了，最后一趟的渠道原话原样抛给用户，不改写不藏。
    assert CHANNEL_REJECTION in str(failed.value)
    assert sent == [4, 3, 2, 1]
