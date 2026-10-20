# SPDX-License-Identifier: Elastic-2.0
# [aipdd fork delta] 拍点预检（渲染契约第 4 条「先出静帧再出成片」）的引擎侧单测。
"""两件事：把页面自报的拍点换成「该截哪几秒」，以及按这份表逐拍截图落盘。

换算的每条口径都是显式决定，用例逐条守着：开场一定截（循环片的构图要回到第一帧，
不截就检不出来）、超出 DUR 的拍点不算、近到同一帧的拍点只截一次、拍点比上限还密时
等间隔抽样并保住最后一张、页面没写 ``window.EV`` 时退成等间隔网格且如实标记。

出图那一段只守调用形状：必须复用成片同一个 :meth:`MotionPage.capture_at`（预检看到
的就得是成片会有的画面），Chrome 换成假对象，绝不起真浏览器。
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest

from novelvideo.freezone.motion_render import pipeline
from novelvideo.freezone.motion_render.capturer import MotionRenderError, MotionTiming

_PAGE = "<!doctype html><html><head></head><body><canvas id=c></canvas></body></html>"
_PNG = bytes.fromhex("89504e470d0a1a0a") + b"stills"


# --------------------------------------------------------------- 张数上限的夹取


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, pipeline.DEFAULT_MAX_STILLS),
        ("", pipeline.DEFAULT_MAX_STILLS),
        ("bad", pipeline.DEFAULT_MAX_STILLS),
        (0, 1),
        (-5, 1),
        (8, 8),
        ("12", 12),
        (999, 120),
    ],
)
def test_clamp_max_stills(value: Any, expected: int) -> None:
    assert pipeline.clamp_max_stills(value) == expected


# ------------------------------------------------------------ 拍点 → 该截哪几秒


def test_the_open_frame_is_always_captured() -> None:
    """页面只报了一个中途拍点：开场那张仍要截，否则首帧构图没人检查。"""
    beats = pipeline.still_times(
        [{"t": 2.0, "type": "hit"}], duration_seconds=3.0, max_stills=24
    )
    assert beats == [{"t": 0.0, "type": "open"}, {"t": 2.0, "type": "hit"}]


def test_an_event_at_zero_does_not_duplicate_the_open_frame() -> None:
    beats = pipeline.still_times(
        [{"t": 0.0, "type": "hit"}, {"t": 1.0, "type": "cut"}],
        duration_seconds=2.0,
        max_stills=24,
    )
    assert [entry["t"] for entry in beats] == [0.0, 1.0]


def test_beats_beyond_the_page_duration_are_dropped() -> None:
    """渲染契约第 2 条：超出 DUR 的一帧都不生成，预检也不许去截不存在的时刻。"""
    beats = pipeline.still_times(
        [{"t": 1.0}, {"t": 4.0, "type": "late"}, {"t": -1.0}],
        duration_seconds=2.0,
        max_stills=24,
    )
    assert beats == [{"t": 0.0, "type": "open"}, {"t": 1.0, "type": ""}]


def test_near_duplicate_beats_capture_one_frame() -> None:
    beats = pipeline.still_times(
        [{"t": 1.0}, {"t": 1.005, "type": "hit"}],
        duration_seconds=3.0,
        max_stills=24,
    )
    assert [entry["t"] for entry in beats] == [0.0, 1.0]


def test_a_dense_grid_keeps_the_last_beat_when_sampled() -> None:
    """抽样是等间隔的，但收尾那张必须留住：循环片的最后一帧要回到开场构图。"""
    events = [{"t": float(index), "type": f"b{index}"} for index in range(1, 41)]
    beats = pipeline.still_times(events, duration_seconds=40.0, max_stills=10)

    assert len(beats) == 10
    assert beats[0]["t"] == 0.0
    assert beats[-1]["t"] == 40.0
    assert [entry["t"] for entry in beats] == sorted(entry["t"] for entry in beats)


def test_a_one_frame_allowance_returns_the_open_frame() -> None:
    """``max_stills`` 被夹到 1 时抽样分母是 0；口径是保开场那张，不是崩。"""
    events = [{"t": float(index), "type": "hit"} for index in range(1, 6)]
    assert pipeline.still_times(events, duration_seconds=6.0, max_stills=1) == [
        {"t": 0.0, "type": "open"}
    ]
    assert pipeline.still_times(events, duration_seconds=6.0, max_stills=0) == [
        {"t": 0.0, "type": "open"}
    ]


def test_ragged_events_are_skipped_not_crashed() -> None:
    beats = pipeline.still_times(
        [{"t": 1.0}, "不是字典", {"type": "hit"}],  # 缺 t 的按 0 处理，与开场重复
        duration_seconds=2.0,
        max_stills=24,
    )
    assert beats == [{"t": 0.0, "type": "open"}, {"t": 1.0, "type": ""}]


def test_a_page_without_beats_falls_back_to_an_even_grid() -> None:
    grid = pipeline._grid_times(3.0, 24)
    assert [entry["t"] for entry in grid] == [0.0, 1.0, 2.0]
    assert all(entry["type"] == "grid" for entry in grid)

    # 时长比上限大时按上限等间隔分：1.25s 一格，最后一格落在 DUR 之前。
    assert [entry["t"] for entry in pipeline._grid_times(5.0, 4)] == [0.0, 1.25, 2.5, 3.75]


# ------------------------------------------------------------------ 预检依赖


def test_stills_preflight_needs_only_a_browser(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """静帧不编码：缺 ffmpeg 也放行，这是它与整片预检唯一的差别。"""
    monkeypatch.setattr(pipeline, "chrome_available", lambda: True)
    monkeypatch.setattr(pipeline, "ffmpeg_executable", lambda: None)
    pipeline.stills_preflight()


def test_stills_preflight_refuses_without_a_browser(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "chrome_available", lambda: False)
    monkeypatch.setattr(pipeline, "ffmpeg_executable", lambda: "ffmpeg")
    with pytest.raises(MotionRenderError, match="Chrome"):
        pipeline.stills_preflight()


# ---------------------------------------------------------------- 逐拍出图


class _FakePage:
    def __init__(self, timing: MotionTiming) -> None:
        self.timing = timing
        self.shots: list[float] = []
        self.closes = 0

    async def capture_at(self, at: float) -> bytes:
        self.shots.append(float(at))
        return _PNG

    async def close(self) -> None:
        self.closes += 1


def _patch_chrome(
    monkeypatch: pytest.MonkeyPatch, timing: MotionTiming
) -> dict[str, Any]:
    seen: dict[str, Any] = {"opened": [], "endpoint_kwargs": []}
    page = _FakePage(timing)

    @contextlib.asynccontextmanager
    async def fake_endpoint(**kwargs: Any):
        seen["endpoint_kwargs"].append(kwargs)
        yield "http://127.0.0.1:9333"

    class _FakeMotionPage:
        @staticmethod
        async def open(cdp_url: str, uri: str, **kwargs: Any):
            seen["opened"].append({"cdp_url": cdp_url, "uri": uri, **kwargs})
            return page

    monkeypatch.setattr(pipeline, "chrome_endpoint", fake_endpoint)
    monkeypatch.setattr(pipeline, "MotionPage", _FakeMotionPage)
    seen["page"] = page
    return seen


@pytest.mark.asyncio
async def test_stills_are_named_by_beat_and_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _patch_chrome(
        monkeypatch,
        MotionTiming(duration_seconds=2.0, events=[{"t": 1.0, "type": "swap"}]),
    )
    progress: list[tuple[int, int]] = []

    result = await pipeline.render_motion_stills(
        _PAGE,
        tmp_path,
        width=1080,
        height=1920,
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert seen["page"].shots == [0.0, 1.0]
    assert [Path(item.path).name for item in result.stills] == [
        "beat-01-0.000s.png",
        "beat-02-1.000s.png",
    ]
    assert Path(result.stills[0].path).read_bytes() == _PNG
    assert result.beat_grid is False
    assert result.page_duration_seconds == 2.0
    assert result.events == ({"t": 1.0, "type": "swap"},)
    assert progress == [(1, 2), (2, 2)]
    # 窗口尺寸就是任务画幅：预检的画布必须与成片一致，否则检的不是同一张构图。
    assert seen["endpoint_kwargs"] == [{"window_size": (1080, 1920)}]
    assert seen["opened"][0]["width"] == 1080
    assert seen["page"].closes == 1
    # 页面源码带引导脚本落在同一目录：任何一张图都能开这一页回到那一秒复现。
    page_html = (tmp_path / "page.html").read_text(encoding="utf-8")
    assert "__mg_step" in page_html
    # 同名还落一份原始源码：注入的引导脚本不属于页面，「改完再渲」只能改这份。
    source_html = pipeline.page_source_path(tmp_path / "page.html").read_text(
        encoding="utf-8"
    )
    assert source_html == _PAGE
    assert "__mg_step" not in source_html


@pytest.mark.asyncio
async def test_a_page_without_beats_reports_the_grid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """页面没写 window.EV 不是失败，但「对的是网格不是拍点」这个事实要带出来。"""
    seen = _patch_chrome(monkeypatch, MotionTiming(duration_seconds=2.0))

    result = await pipeline.render_motion_stills(
        _PAGE, tmp_path, width=640, height=360, max_stills=2
    )

    assert result.beat_grid is True
    assert [entry.at_seconds for entry in result.stills] == [0.0, 1.0]
    assert seen["page"].shots == [0.0, 1.0]


@pytest.mark.asyncio
async def test_the_browser_is_closed_even_when_a_shot_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _patch_chrome(
        monkeypatch, MotionTiming(duration_seconds=2.0, events=[{"t": 1.0}])
    )
    page = seen["page"]

    async def _boom(at: float) -> bytes:
        raise MotionRenderError("1.000s 处截图为空")

    page.capture_at = _boom  # type: ignore[method-assign]

    with pytest.raises(MotionRenderError, match="截图为空"):
        await pipeline.render_motion_stills(_PAGE, tmp_path)

    assert page.closes == 1


@pytest.mark.asyncio
async def test_an_empty_page_never_starts_a_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _patch_chrome(monkeypatch, MotionTiming(duration_seconds=1.0))

    with pytest.raises(MotionRenderError, match="MG 页面内容为空"):
        await pipeline.render_motion_stills("   ", tmp_path)

    assert seen["endpoint_kwargs"] == []
