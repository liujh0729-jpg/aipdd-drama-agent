from types import SimpleNamespace

import pytest

from novelvideo import video_billing


@pytest.mark.asyncio
async def test_probe_total_video_duration_sums_raw_durations_before_billing_rounding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations = {"a.mp4": 7.05, "b.mp4": 4.9}

    async def fake_probe(path: str) -> float:
        return durations[path]

    monkeypatch.setattr(video_billing, "probe_video_duration_seconds", fake_probe)

    assert await video_billing.probe_total_video_duration_seconds(
        ["a.mp4", "b.mp4"]
    ) == pytest.approx(11.95)


@pytest.mark.asyncio
async def test_probe_video_duration_rejects_non_finite_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        video_billing.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="Infinity\n",
            stderr="",
        ),
    )

    with pytest.raises(ValueError, match="positive"):
        await video_billing.probe_video_duration_seconds("broken.mp4")


def _fake_ffprobe_streams(width: int, height: int, rotation: float | None = None):
    """把 ffprobe 的 JSON 输出拼成消费方要的形状（rotation 走 display matrix）。"""

    stream = {"width": width, "height": height}
    if rotation is not None:
        stream["side_data_list"] = [
            {"side_data_type": "Display Matrix", "rotation": rotation}
        ]

    def _run(_cmd, **_kwargs):
        import json

        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"streams": [stream]}), stderr=""
        )

    return _run


@pytest.mark.asyncio
async def test_probe_video_aspect_ratio_prefers_portrait_without_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(video_billing.subprocess, "run", _fake_ffprobe_streams(1080, 1920))

    assert await video_billing.probe_video_aspect_ratio("clip.mp4") == "9:16"


@pytest.mark.asyncio
async def test_probe_video_aspect_ratio_swaps_for_display_matrix_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """手机竖屏视频常是 width>height + rotate 90，裸读宽高会把竖屏判成横屏。"""

    monkeypatch.setattr(
        video_billing.subprocess, "run", _fake_ffprobe_streams(1920, 1080, rotation=90)
    )

    assert await video_billing.probe_video_aspect_ratio("clip.mp4") == "9:16"


@pytest.mark.asyncio
async def test_probe_video_aspect_ratio_only_returns_declared_tiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """档位以渠道目录为准：不能自创上游不认的 9:16。"""

    monkeypatch.setattr(video_billing.subprocess, "run", _fake_ffprobe_streams(1080, 1920))

    assert await video_billing.probe_video_aspect_ratio(
        "clip.mp4", allowed=["4:3", "1:1"]
    ) == "1:1"


@pytest.mark.asyncio
async def test_probe_video_aspect_ratio_rejects_unreadable_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(_cmd, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="moov atom not found")

    monkeypatch.setattr(video_billing.subprocess, "run", _run)

    with pytest.raises(ValueError, match="moov atom"):
        await video_billing.probe_video_aspect_ratio("clip.mp4")
