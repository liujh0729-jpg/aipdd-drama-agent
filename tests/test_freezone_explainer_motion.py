"""[aipdd fork delta] 科普讲解视频渲染核心单测。

钉住 ``novelvideo.freezone.explainer_motion`` 的确定性：同一帧两次绘制必须逐字节
相同（否则同一次任务重跑会出两条不一样的片），八种图元在稳态必须真的画出内容
（而不是只有幕底），字幕带/进度条/章节卡按帧区间出现，以及**流式**编码的调用形状
（帧进 ffmpeg 管道，绝不落盘）。默认用假 Popen，不触发真实 ffmpeg；最后一个用例在
本机有 ffmpeg 时做一次极小片长的真实编码冒烟。
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from novelvideo.freezone import explainer_motion as module
from novelvideo.freezone.explainer import (
    ExplainerChapter,
    ExplainerShot,
    ExplainerTitle,
    plan_explainer_timeline,
)

_CASES: dict[str, dict[str, Any]] = {
    "term": dict(items=("检索增强",), numbers=(), headline="先查再答", motion="typewriter"),
    "flow": dict(items=("切块", "向量化", "检索", "生成"), numbers=(), headline="RAG 四步", motion="travel"),
    "compare": dict(items=("易编造|有据", "成本高|可控"), numbers=(), headline="有无检索", motion="lightup"),
    "bars": dict(items=("朴素", "混合", "重排"), numbers=("12元", "34元", "58元"), headline="每千次成本", motion="countup"),
    "bignumber": dict(items=("月省",), numbers=("0.05元",), headline="单次调用", motion="countup"),
    "list": dict(items=("分块重叠", "元数据过滤", "混合检索", "重排截断"), numbers=(), headline="四个开关", motion="lightup"),
    "cycle": dict(items=("提问", "检索", "拼装", "生成"), numbers=(), headline="闭环", motion="travel"),
    "timeline": dict(items=("向量库", "LangChain", "GraphRAG"), numbers=(), headline="四年演进", motion="softin"),
}


def _timeline(primitive: str, *, duration_ms: int = 8_000) -> Any:
    sentence = "这一步把输入变成输出，先看点再看到结论。"
    chapter = ExplainerChapter("怎么做", (sentence,))
    shot = ExplainerShot(
        chapter_index=0,
        sentence_start=1,
        sentence_end=1,
        sentences=(sentence,),
        primitive=primitive,
        headline=_CASES[primitive]["headline"],
        items=_CASES[primitive]["items"],
        numbers=_CASES[primitive]["numbers"],
        emphasis=0 if _CASES[primitive]["items"] else -1,
        motion=_CASES[primitive]["motion"],
    )
    return plan_explainer_timeline(
        chapters=[chapter],
        shots=[shot],
        title=ExplainerTitle(big="RAG", rest="检索增强生成", en="Retrieval-Augmented Generation", tagline="为什么要先查资料"),
        duration_ms=duration_ms,
        fps=30,
    )


def _blank_timeline(*, duration_ms: int = 8_000) -> Any:
    """没有镜头的时间轴：只剩幕底 + 进度条，用作「画面没画内容」的基线。"""
    chapter = ExplainerChapter("怎么做", ("占位句。",))
    return plan_explainer_timeline(chapters=[chapter], shots=[], duration_ms=duration_ms, fps=30)


def _bright_count(frame: np.ndarray) -> int:
    return int(np.count_nonzero(frame.max(axis=2) > 150))


# ============================================================
# 幕底
# ============================================================


def _backdrop_frame(frame: int, background: str) -> np.ndarray:
    from PIL import Image, ImageDraw

    layer = Image.new("RGBA", (640, 360), (0, 0, 0, 0))
    module.draw_backdrop(ImageDraw.Draw(layer), module._Geom(640, 360), frame, 30, background)
    return np.asarray(layer.convert("RGB"))


def test_backdrop_is_deterministic_and_animates() -> None:
    assert np.array_equal(_backdrop_frame(30, "dots"), _backdrop_frame(30, "dots"))
    # 点阵波必须随时间起伏，否则画面是死的。
    assert not np.array_equal(_backdrop_frame(0, "dots"), _backdrop_frame(30, "dots"))
    assert _bright_count(_backdrop_frame(30, "dots")) > 0


def test_star_backdrop_picks_a_stable_field() -> None:
    # 星点位置由固定种子决定：同一个任务重跑不能出两次不同的星空。
    assert module._star_field(module._STAR_SEED) == module._star_field(module._STAR_SEED)
    assert len(module._star_field(module._STAR_SEED)) == module._STAR_COUNT
    assert np.array_equal(_backdrop_frame(12, "stars"), _backdrop_frame(12, "stars"))
    # 星点缓慢漂移：换一帧就该看见位置变化。
    assert not np.array_equal(_backdrop_frame(0, "stars"), _backdrop_frame(60, "stars"))


# ============================================================
# 逐帧合成
# ============================================================


@pytest.mark.parametrize("primitive", sorted(_CASES))
def test_every_primitive_draws_content_at_steady_state(primitive: str) -> None:
    timeline = _timeline(primitive)
    scene = timeline.scenes[0]
    frame = int(scene.start_frame + scene.duration_frames * 0.72)
    image = module.compose_explainer_frame(timeline, frame, width=640, height=360)
    baseline = module.compose_explainer_frame(_blank_timeline(), frame, width=640, height=360)
    assert image.shape == (360, 640, 3)
    assert image.dtype == np.uint8
    # 图元真的画出了亮部内容，而不只剩幕底（空流程/空清单是本类工具最常见的哑火）。
    assert _bright_count(image) > _bright_count(baseline) * 1.2


def test_compose_frame_is_deterministic() -> None:
    timeline = _timeline("flow")
    first = module.compose_explainer_frame(timeline, 120, width=480, height=270)
    second = module.compose_explainer_frame(timeline, 120, width=480, height=270)
    assert np.array_equal(first, second)


def test_subtitle_band_follows_cues() -> None:
    timeline = _timeline("list", duration_ms=12_000)
    scene = timeline.scenes[0]
    cue = scene.cues[0]
    inside = module.compose_explainer_frame(timeline, (cue.start_frame + cue.end_frame) // 2, width=640, height=360)
    assert module._cue_at(scene, (cue.start_frame + cue.end_frame) // 2) is cue
    assert module._cue_at(scene, scene.end_frame + 1) is None
    # 字幕带落在画面下缘，不压内容区。
    band = inside[300:345, :, :]
    assert band.max() > 150


def test_lead_cards_only_when_the_scene_has_lead_room() -> None:
    timeline = _timeline("term", duration_ms=20_000)
    # 首镜留得出片头卡的余量才预留，否则片头会盖掉正文。
    assert module._lead_frames(timeline, 0) == module.TITLE_FRAMES
    short = _timeline("term", duration_ms=3_000)
    assert module._lead_frames(short, 0) == 0

    chaptered = plan_explainer_timeline(
        chapters=[
            ExplainerChapter("第一章", ("本章第一句把话说明白，留出章节卡的时间。",)),
            ExplainerChapter("第二章", ("本章第二句同样足够长可以放章节卡。",)),
        ],
        shots=[
            ExplainerShot(0, 1, 1, ("本章第一句把话说明白，留出章节卡的时间。",), primitive="term", headline="一"),
            ExplainerShot(1, 2, 2, ("本章第二句同样足够长可以放章节卡。",), primitive="term", headline="二"),
        ],
        duration_ms=20_000,
        fps=30,
    )
    assert chaptered.chapter_starts[1] > 0
    assert module._lead_frames(chaptered, 1) == module.CHAPTER_CARD_FRAMES
    assert module._chapter_index_at(chaptered, chaptered.chapter_starts[1]) == 1


def test_countup_keeps_surrounding_text() -> None:
    assert module._count_text("12元", 0.0).startswith("0")
    assert module._count_text("12元", 1.0) == "12元"
    rolled = module._count_text("约 0.05元/次", 0.5)
    assert rolled.endswith("元/次") and rolled.startswith("约 ")
    assert "0.05" not in rolled
    # 没有数字的文本原样返回（不该被 countup 改坏）。
    assert module._count_text("没有数字", 0.4) == "没有数字"


# ============================================================
# 编码调用形状
# ============================================================


def test_encoder_command_without_audio_is_video_only() -> None:
    command, graph = module._build_encoder_command(
        width=1280,
        height=720,
        fps=30,
        out_path=Path("/tmp/out.mp4"),
        duration_seconds=4.0,
        narration_path=None,
        bgm_path=None,
        bgm_volume=0.2,
    )
    assert graph is None
    assert "-an" in command
    assert "pipe:0" in command
    assert "-s" in command and "1280x720" in command
    assert "-stream_loop" not in command
    assert Path(command[-1]) == Path("/tmp/out.mp4")
    assert "-t" in command


def test_encoder_command_mixes_voice_and_looped_bgm() -> None:
    command, graph = module._build_encoder_command(
        width=1280,
        height=720,
        fps=30,
        out_path=Path("/tmp/out.mp4"),
        duration_seconds=4.0,
        narration_path="/tmp/narration.wav",
        bgm_path="/tmp/bgm.mp3",
        bgm_volume=0.35,
    )
    assert command.count("-i") == 3
    assert "-stream_loop" in command
    assert graph is not None and "amix=inputs=2" in graph and "normalize=0" in graph
    assert "volume=0.350" in graph
    assert command[command.index("-map") + 1] == "0:v:0"
    assert "[aout]" in command
    assert "-an" not in command
    # BGM 音量夹在可用区间，模型/用户给 0 或 2 都不会把配音顶掉或削波。
    _, quiet = module._build_encoder_command(
        width=640,
        height=360,
        fps=30,
        out_path=Path("/tmp/out.mp4"),
        duration_seconds=4.0,
        narration_path="/tmp/narration.wav",
        bgm_path="/tmp/bgm.mp3",
        bgm_volume=0.0,
    )
    assert quiet is not None and "volume=0.020" in quiet


class _Sink:
    """只记字节数的假管道：close() 后仍要能读到内容（真管道就是这样的）。"""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, payload: bytes) -> int:
        self.buffer.extend(payload)
        return len(payload)

    def close(self) -> None:
        return None

    def getvalue(self) -> bytes:
        return bytes(self.buffer)


class _FakeProcess:
    """记录写进 stdin 的字节数，代替真正的 ffmpeg。"""

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.stdin = _Sink()
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()

    def wait(self) -> int:
        return 0


def test_render_streams_frames_to_ffmpeg_without_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: list[_FakeProcess] = []

    def fake_popen(command: list[str], **_kwargs: Any) -> _FakeProcess:
        process = _FakeProcess(command)
        launched.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    timeline = _timeline("bars", duration_ms=2_000)
    progress: list[float] = []
    out = module.render_explainer_video(
        timeline=timeline,
        out_path=tmp_path / "film.mp4",
        width=320,
        height=180,
        on_progress=lambda ratio, _message: progress.append(ratio),
    )

    assert out == tmp_path / "film.mp4"
    assert launched and launched[0].command[0] == "ffmpeg"
    payload = launched[0].stdin.getvalue()
    # 每帧原样进管道：帧数 × 单帧字节数，且不落任何中间图片文件。
    assert len(payload) == timeline.total_frames * 320 * 180 * 3
    assert list(tmp_path.iterdir()) == []
    assert progress and pytest.approx(progress[-1]) == 1.0


def test_render_rejects_empty_timeline(tmp_path: Path) -> None:
    from novelvideo.freezone.explainer import ExplainerTimeline

    with pytest.raises(ValueError, match="empty"):
        module.render_explainer_video(
            timeline=ExplainerTimeline(fps=30, total_frames=0, scenes=(), chapter_starts=(0,)),
            out_path=tmp_path / "film.mp4",
        )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要本机 ffmpeg")
def test_real_encode_produces_a_playable_file(tmp_path: Path) -> None:
    timeline = _timeline("flow", duration_ms=1_000)
    out = module.render_explainer_video(
        timeline=timeline,
        out_path=tmp_path / "film.mp4",
        width=480,
        height=270,
    )
    assert out.exists() and out.stat().st_size > 1024
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=nb_streams", "-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0:
        assert probe.stdout.strip().splitlines()[0] == "1"
