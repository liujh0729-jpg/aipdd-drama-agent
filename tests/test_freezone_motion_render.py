"""[aipdd fork delta] 动效大片渲染引擎单测。

覆盖 ``novelvideo.freezone.motion_render`` 四个模块里**不需要真浏览器也能钉住**的
部分：时间引导的插入位置、页面自报时间轴的解析口径、时长→帧数的换算、编码器清单
解析与 H.264 选择（随包 ffmpeg 是 LGPL 构建，没有 x264，这条链路必须会降级）、
喂给 ffmpeg 的命令形状，以及逐帧截图/流式编码的调用形状（假 CDP + 假 ffmpeg）。
最后一条用例在本机有 ffmpeg 时做真实编码冒烟，证明 ``image2pipe`` 这条路真出片。
"""

from __future__ import annotations

import base64
import io
import shutil
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from novelvideo.freezone.motion_render import capturer, chrome, encoder, pipeline
from novelvideo.freezone.motion_render.capturer import MotionRenderError, MotionTiming
from novelvideo import video_encoder


# --------------------------------------------------------------- 时间引导注入


def test_bootstrap_goes_before_the_page_scripts() -> None:
    page = "<!doctype html><html><head><title>x</title><script>window.render=1</script></head><body></body></html>"
    injected = capturer.with_bootstrap(page)
    assert injected.index("__mg_step") < injected.index("window.render=1")
    assert injected.count("<script>") == 2


def test_bootstrap_creates_a_head_when_page_has_none() -> None:
    injected = capturer.with_bootstrap("<html><body><p>hi</p></body></html>")
    assert "<head><script>" in injected


def test_bootstrap_prepends_a_bare_fragment() -> None:
    injected = capturer.with_bootstrap("<div id=c></div>")
    assert injected.startswith("<script>")


def test_bootstrap_keeps_the_doctype_first_when_tags_carry_attributes() -> None:
    """``<html lang="zh-CN">`` 是模型产出的常态写法，只认字面 ``<html>`` 会把 doctype 挤下去。

    doctype 不在首行 = quirks mode：量出来的版式与用户在浏览器里复现的那份不一样，
    每条片子等于在赌页面自己带不带属性。
    """
    page = (
        '<!doctype html><html lang="zh-CN"><body><canvas id="c"></canvas></body></html>'
    )
    injected = capturer.with_bootstrap(page)
    assert injected.startswith("<!doctype html>")
    assert injected.index("<head><script>") > injected.index('<html lang="zh-CN">')
    assert injected.index("__mg_step") < injected.index("<canvas")


def test_bootstrap_inserts_into_a_head_with_attributes() -> None:
    page = '<!doctype html><html><head prefix="og: http://ogp.me/ns#"><title>x</title></head></html>'
    injected = capturer.with_bootstrap(page)
    assert injected.index("__mg_step") > injected.index('prefix="og:')
    assert injected.index("__mg_step") < injected.index("<title>")


# ------------------------------------------------------------------ 时间轴解析


async def test_page_ready_waits_for_the_navigated_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Page.navigate`` 是发完就回的，那一刻文档还是 ``about:blank``。

    ``about:blank`` 的 readyState 本来就是 ``complete``，所以只问 readyState 的话第一次
    轮询就放行，紧接着读 ``window.DUR`` 读的是空文档 —— 一页本来合规的动画会以
    「window.DUR 缺失」失败，而且是概率性的（取决于导航落地快不快）。
    """
    page_url = "file:///tmp/mg.html"
    replies = [
        "complete\nabout:blank",
        "interactive\n" + page_url,
    ]

    async def _fake_evaluate(_client: object, _session: str, _script: str, **_kw: object) -> str:
        return replies.pop(0)

    monkeypatch.setattr(capturer, "_evaluate", _fake_evaluate)
    monkeypatch.setattr(capturer, "_READY_POLL_SECONDS", 0.01)
    await capturer._await_page_ready(object(), "s1", 5.0, page_url)


async def test_page_ready_gives_up_with_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _always_blank(_client: object, _session: str, _script: str, **_kw: object) -> str:
        return "complete\nabout:blank"

    monkeypatch.setattr(capturer, "_evaluate", _always_blank)
    monkeypatch.setattr(capturer, "_READY_POLL_SECONDS", 0.01)
    with pytest.raises(MotionRenderError, match="没有加载完成"):
        await capturer._await_page_ready(object(), "s1", 1.0, "file:///tmp/mg.html")


def test_parse_timing_reads_duration_and_beats() -> None:
    timing = capturer.parse_timing('{"duration":29.5,"events":[{"t":1.5,"type":"swap"},{}]}')
    assert timing == MotionTiming(duration_seconds=29.5, events=[{"t": 1.5, "type": "swap"}])


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("not json", "不是合法 JSON"),
        ('[1,2]', "不是对象"),
        ('{"error":"window.DUR 缺失或不合法"}', "window.DUR 缺失或不合法"),
        ('{"duration":0,"events":[]}', "window.DUR 必须是正数秒"),
        ('{"duration":"20","events":[]}', "window.DUR 必须是正数秒"),
        # Python 的 json 会把 Infinity/NaN 照单全收，而它们不是合法 JSON：脏时长会一路
        # 流进帧数计算，必须在读页面回报时就拒掉。
        ('{"duration":Infinity,"events":[]}', "window.DUR 必须是正数秒"),
        ('{"duration":NaN,"events":[]}', "window.DUR 必须是正数秒"),
        ('{"duration":-Infinity,"events":[]}', "window.DUR 必须是正数秒"),
    ],
)
def test_parse_timing_rejects_bad_pages(payload: str, expected: str) -> None:
    with pytest.raises(MotionRenderError) as caught:
        capturer.parse_timing(payload)
    assert expected in str(caught.value)


def test_parse_timing_drops_non_finite_beats() -> None:
    """脏拍点丢掉而不是写进 sidecar：JSON 里没有 Infinity，落盘就是一份坏文件。"""
    timing = capturer.parse_timing(
        '{"duration":3,"events":[{"t":Infinity,"type":"hit"},{"t":1,"type":"hit"},{"t":null,"type":"x"}]}'
    )
    assert timing.events == [{"t": 1.0, "type": "hit"}]


def test_parse_timing_tolerates_missing_or_ragged_events() -> None:
    timing = capturer.parse_timing('{"duration":3,"events":"nope"}')
    assert timing.events == []


# --------------------------------------------------------------- 时长 → 帧数


@pytest.mark.parametrize(
    ("value", "expected"),
    [(30, 30), ("24", 24), (0, 1), (-5, 1), (240, pipeline.MAX_FPS), (None, pipeline.DEFAULT_FPS), ("x", pipeline.DEFAULT_FPS)],
)
def test_clamp_fps(value: Any, expected: int) -> None:
    assert pipeline.clamp_fps(value) == expected


def test_requested_shorter_than_the_page_truncates() -> None:
    plan = pipeline.plan_frames(page_duration=29.0, fps=30, requested=5.0)
    assert plan.frames == 150
    assert plan.duration_seconds == 5.0
    assert plan.page_duration_seconds == 29.0


def test_page_shorter_than_requested_wins() -> None:
    plan = pipeline.plan_frames(page_duration=3.2, fps=25, requested=10.0)
    assert plan.frames == 80
    assert plan.duration_seconds == pytest.approx(3.2)


def test_frames_round_instead_of_floor() -> None:
    assert pipeline.plan_frames(page_duration=0.9999, fps=30, requested=None).frames == 30


def test_over_long_request_is_refused_with_a_way_out() -> None:
    with pytest.raises(MotionRenderError) as caught:
        pipeline.plan_frames(page_duration=600.0, fps=60, requested=None)
    assert "拆成分镜" in str(caught.value)


# ------------------------------------------------------- 编码器清单与 H.264 选择

_ENCODERS_SAMPLE = """
Encoders:
  V..... = Video
  A..... = Audio
  ---
  List of supported encoders
  V....D libopenh264          OpenH264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
  V....D h264_mf              H264 via MediaFoundation (codec h264)
  V.S..D mpeg4                MPEG-4 part 2
  A....D aac                  AAC (Advanced Audio Coding)
"""


def test_parse_encoder_list_skips_headers_and_audio() -> None:
    names = encoder.parse_encoder_list(_ENCODERS_SAMPLE)
    assert {"libopenh264", "h264_mf", "mpeg4", "aac"} <= names
    assert "supported" not in names


def test_x264_is_preferred_and_uses_crf() -> None:
    name, args = encoder.h264_encoder_for(
        ["libx264", "h264_mf", "libopenh264"], width=1280, height=720, fps=30
    )
    assert name == "libx264"
    assert args == ["-preset", "veryfast", "-crf", "18"]


def test_lgpl_build_falls_back_to_bitrate_mode() -> None:
    name, args = encoder.h264_encoder_for(
        encoder.parse_encoder_list(_ENCODERS_SAMPLE), width=1280, height=720, fps=30
    )
    assert name == "h264_mf"
    assert args[0] == "-b:v"
    # 1280*720*30*0.1 = 2.76 Mbps，落在上下限之间，原样采用。
    assert int(args[1]) == 2_764_800


@pytest.mark.parametrize(
    ("width", "height", "fps", "expected"),
    [
        (640, 360, 10, encoder._MIN_BITRATE),
        (3840, 2160, 60, encoder._MAX_BITRATE),
    ],
)
def test_bitrate_stays_inside_the_band(width: int, height: int, fps: int, expected: int) -> None:
    assert encoder._bitrate_for(width, height, fps) == expected


def test_no_h264_encoder_is_a_clear_stop() -> None:
    with pytest.raises(MotionRenderError) as caught:
        encoder.h264_encoder_for(["aac", "mpeg4"], width=1280, height=720, fps=30)
    assert "H.264" in str(caught.value)


def test_encoder_command_shape() -> None:
    out = Path("out") / "mg.mp4"
    command = encoder.build_encoder_command(
        executable="/opt/bin/ffmpeg",
        fps=25,
        out_path=out,
        width=1280,
        height=720,
        encoder="libx264",
        encoder_args=["-crf", "18"],
    )
    joined = " ".join(command)
    # argv[0] 必须是解析出来的那一个：清单按它探，跑也得跑它，否则 FFMPEG_PATH 指的
    # 那台 build 与真正执行的裸 ffmpeg 不是一个（h264_mf 会报 Unknown encoder）。
    assert command[0] == "/opt/bin/ffmpeg"
    assert "-f image2pipe -framerate 25 -i pipe:0" in joined
    assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in joined
    assert "-c:v libx264 -crf 18 -pix_fmt yuv420p" in joined
    assert command[-2:] == ["-an", str(out)]


def test_ffmpeg_path_env_is_honoured_before_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``FFMPEG_PATH`` 是仓库既有口径（config.py + ffmpeg 指南），MG 这条链也得吃它。"""
    custom = tmp_path / "ffmpeg"
    custom.write_bytes(b"#!/bin/sh")
    monkeypatch.setenv("FFMPEG_PATH", str(custom))
    assert encoder.ffmpeg_executable() == str(custom)
    # 变量里只写命令名时按 PATH 解析，与默认值 ``ffmpeg`` 同一条路。
    monkeypatch.setenv("FFMPEG_PATH", "ffmpeg")
    assert encoder.ffmpeg_executable() == shutil.which("ffmpeg")


def test_a_typo_in_ffmpeg_path_is_reported_as_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """指了但指不到不许悄悄回落：编码器清单是按这个二进制探的，换二进制就对不上。"""
    monkeypatch.setenv("FFMPEG_PATH", str(tmp_path / "nope" / "ffmpeg"))
    assert encoder.ffmpeg_executable() is None


# ----------------------------------------------------------- 逐帧截图的调用形状


class _FakeCdp:
    """只回答 ``Runtime.evaluate`` / ``Page.captureScreenshot`` 的最小 CDP 客户端。"""

    def __init__(self, png: bytes) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._png = base64.b64encode(png).decode()

    async def call(self, method: str, params: Any = None, *, session_id: str | None = None, timeout: float = 1.0) -> dict:
        self.calls.append((method, params))
        if method == "Runtime.evaluate":
            return {"result": {"type": "string", "value": "0"}}
        return {"data": self._png}

    @property
    def evaluated(self) -> list[str]:
        return [p["expression"] for m, p in self.calls if m == "Runtime.evaluate"]


def _page(client: _FakeCdp) -> capturer.MotionPage:
    page = capturer.MotionPage(None, client, "s1", MotionTiming(duration_seconds=1.0))  # type: ignore[arg-type]
    page._target_id = "t1"
    return page


async def test_iter_frames_pins_time_then_screenshots(tmp_path: Path) -> None:
    png = bytes.fromhex("89504e470d0a1a0a") + b"fake"
    client = _FakeCdp(png)
    seen: list[tuple[int, int]] = []
    progress = lambda done, total: seen.append((done, total))  # noqa: E731
    page = _page(client)
    frames = [chunk async for chunk in page.iter_frames(3, fps=10, on_progress=progress)]

    assert frames == [png, png, png]
    assert client.calls[0] == (
        "Runtime.evaluate",
        {
            "expression": "String(window.__mg_step(0.0))",
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    assert 'String(window.__mg_step(0.1))' in client.evaluated[1]
    assert 'String(window.__mg_step(0.2))' in client.evaluated[2]
    assert [m for m, _ in client.calls] == ["Runtime.evaluate", "Page.captureScreenshot"] * 3
    assert seen == [(1, 3), (2, 3), (3, 3)]


async def test_iter_frames_refuses_zero_frames() -> None:
    page = _page(_FakeCdp(b"x"))
    with pytest.raises(MotionRenderError):
        [chunk async for chunk in page.iter_frames(0, fps=10)]


async def test_a_page_error_is_forwarded_verbatim() -> None:
    class _Throwing:
        async def call(self, method: str, params: Any = None, **_kwargs: Any) -> dict:
            return {"exceptionDetails": {"exception": {"description": "TypeError: x is not a function"}}}

    page = _page(_Throwing())  # type: ignore[arg-type]
    with pytest.raises(MotionRenderError) as caught:
        [chunk async for chunk in page.iter_frames(3, fps=10)]
    assert "TypeError: x is not a function" in str(caught.value)


async def test_empty_screenshot_is_reported_with_its_second() -> None:
    client = _FakeCdp(b"x")
    inner = client.call

    async def _blank_shot(method: str, params: Any = None, **kwargs: Any) -> dict:
        if method == "Page.captureScreenshot":
            return {"data": ""}
        return await inner(method, params, **kwargs)

    client.call = _blank_shot  # type: ignore[method-assign]
    page = _page(client)
    with pytest.raises(MotionRenderError) as caught:
        [chunk async for chunk in page.iter_frames(2, fps=10)]
    # 报错报的是「哪一秒」而不是「第几帧」：成片帧与拍点静帧共用 capture_at，
    # 秒数两边都成立，而且对着成片拖进度条时定位的也是秒。
    assert "0.000s 处截图为空" in str(caught.value)


# ------------------------------------------------------------- 编码管道的调用形状


class _FakeStdin:
    def __init__(self, target: Path, *, burst_after: int | None = None) -> None:
        self.target = target
        self.burst_after = burst_after
        self.chunks: list[bytes] = []
        self.closed = False

    def write(self, payload: bytes) -> None:
        if self.burst_after is not None and len(self.chunks) >= self.burst_after:
            raise BrokenPipeError
        self.chunks.append(payload)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True
        self.target.write_bytes(b"".join(self.chunks) or b"\x00")

    async def wait_until_closed(self) -> None:
        return None


class _FakeFfmpeg:
    def __init__(self, out_path: Path, *, code: int = 0, stderr: bytes = b"boom", burst_after: int | None = None) -> None:
        self.stdin = _FakeStdin(out_path, burst_after=burst_after)
        self.stderr = _FakeStderr(stderr)
        self.returncode: int | None = None
        self._code = code
        self.terminated = False

    async def wait(self) -> int:
        self.returncode = self._code
        return self._code

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


class _FakeStderr:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def read(self, limit: int = -1) -> bytes:
        # 与 asyncio 的 StreamReader 同口径：不带长度就是读到 EOF（全量）。
        return self._payload if limit < 0 else self._payload[:limit]


def _install_fake_process(monkeypatch: pytest.MonkeyPatch, process: _FakeFfmpeg) -> list[list[str]]:
    commands: list[list[str]] = []

    async def _spawn(*command: str, **_kwargs: Any) -> _FakeFfmpeg:
        commands.append(list(command))
        return process

    monkeypatch.setattr(encoder.asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(encoder, "_probe_cache", {})
    monkeypatch.setattr(
        encoder,
        "_probe_h264_encoder",
        lambda **_kwargs: _const(("libx264", ["-preset", "veryfast", "-crf", "18"])),
    )
    return commands


async def _const(value: Any) -> Any:
    return value


async def test_encode_frames_streams_and_finishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "mg.mp4"
    process = _FakeFfmpeg(out)
    commands = _install_fake_process(monkeypatch, process)
    monkeypatch.setattr(video_encoder.shutil, "which", lambda _name: "ffmpeg")

    async def _frames() -> Any:
        for chunk in (b"AA", b"BB", b"CC"):
            yield chunk

    result = await encoder.encode_frames(_frames(), fps=30, out_path=out, width=1280, height=720)

    assert result == out
    assert process.stdin.chunks == [b"AA", b"BB", b"CC"]
    assert process.stdin.closed is True
    assert commands[0][:2] == ["ffmpeg", "-y"]
    assert "-c:v" in commands[0] and "libx264" in commands[0]


async def test_broken_pipe_surfaces_the_ffmpeg_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "mg.mp4"
    process = _FakeFfmpeg(out, stderr=b"Unknown encoder 'libx264'", burst_after=1)
    _install_fake_process(monkeypatch, process)
    monkeypatch.setattr(video_encoder.shutil, "which", lambda _name: "ffmpeg")

    async def _frames() -> Any:
        for chunk in (b"AA", b"BB"):
            yield chunk

    with pytest.raises(MotionRenderError) as caught:
        await encoder.encode_frames(_frames(), fps=30, out_path=out, width=1280, height=720)
    assert "Unknown encoder" in str(caught.value)
    assert process.terminated is True


async def test_nonzero_exit_carries_the_stderr_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "mg.mp4"
    process = _FakeFfmpeg(out, code=1, stderr=b"Error opening output file")
    _install_fake_process(monkeypatch, process)
    monkeypatch.setattr(video_encoder.shutil, "which", lambda _name: "ffmpeg")

    async def _frames() -> Any:
        yield b"AA"

    with pytest.raises(MotionRenderError) as caught:
        await encoder.encode_frames(_frames(), fps=30, out_path=out, width=1280, height=720)
    assert "退出码 1" in str(caught.value)
    assert "Error opening output file" in str(caught.value)
    # 半截成片必须删掉：留着它，资产扫描会把这条坏 mp4 列成可用成片并嵌进剪映草稿。
    assert not out.exists()


async def test_the_binary_that_was_probed_is_the_one_that_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """编码器清单按哪台 ffmpeg 探的，argv[0] 就得是那一台。

    ``FFMPEG_PATH`` 指到 PATH 之外的 build 时，老写法（argv[0] 写死裸 ``ffmpeg``）会让
    「探到的编码器」与「跑的编码器」裂开，装机版上就是 ``Unknown encoder 'h264_mf'``。
    """
    out = tmp_path / "mg.mp4"
    process = _FakeFfmpeg(out)
    commands = _install_fake_process(monkeypatch, process)
    monkeypatch.setattr(video_encoder.shutil, "which", lambda _name: "/opt/other-build/ffmpeg")

    async def _frames() -> Any:
        yield b"AA"

    await encoder.encode_frames(_frames(), fps=30, out_path=out, width=1280, height=720)
    assert commands[0][0] == "/opt/other-build/ffmpeg"


async def test_no_frames_is_not_silently_a_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "mg.mp4"
    process = _FakeFfmpeg(out)
    _install_fake_process(monkeypatch, process)
    monkeypatch.setattr(video_encoder.shutil, "which", lambda _name: "ffmpeg")

    async def _frames() -> Any:
        return
        yield b""

    with pytest.raises(MotionRenderError) as caught:
        await encoder.encode_frames(_frames(), fps=30, out_path=out, width=1280, height=720)
    assert "没有帧可编码" in str(caught.value)
    assert process.terminated is True


async def test_missing_ffmpeg_tells_the_operator_what_to_do(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(video_encoder.shutil, "which", lambda _name: None)
    with pytest.raises(MotionRenderError) as caught:
        await encoder.encode_frames(_none(), fps=30, out_path=tmp_path / "mg.mp4", width=640, height=360)
    assert "ffmpeg" in str(caught.value)


async def _none() -> Any:
    return
    yield b""


async def test_probe_asks_the_binary_once_and_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probes: list[list[str]] = []

    async def _spawn(*command: str, **_kwargs: Any) -> _FakeFfmpeg:
        probes.append(list(command))
        return _FakeFfmpeg(tmp_path / "unused")

    async def _communicate(self: _FakeFfmpeg) -> tuple[bytes, bytes]:
        return (_ENCODERS_SAMPLE + "\n  V....D libx264  x264\n").encode(), b""

    monkeypatch.setattr(encoder.asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(_FakeFfmpeg, "communicate", _communicate)
    monkeypatch.setattr(encoder, "_probe_cache", {})

    chosen = await encoder._probe_h264_encoder(executable="ffmpeg", width=1280, height=720, fps=30)
    again = await encoder._probe_h264_encoder(executable="ffmpeg", width=1280, height=720, fps=30)

    assert chosen == ("libx264", ["-preset", "veryfast", "-crf", "18"])
    assert again == chosen
    assert len(probes) == 1
    assert probes[0][1:] == ["-hide_banner", "-encoders"]


# ---------------------------------------------------------------- Chrome 定位


def test_explicit_chrome_path_wins_when_it_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "chrome.exe" if chrome._IS_WINDOWS else tmp_path / "chrome"
    binary.write_bytes(b"x")
    monkeypatch.setenv(chrome.CHROME_PATH_ENV, str(binary))
    assert chrome_executable_is(chrome.chrome_executable(), binary)


def test_a_typo_in_the_env_is_not_silently_a_missing_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(chrome.CHROME_PATH_ENV, str(tmp_path / "nope.exe"))
    monkeypatch.setattr(chrome.shutil, "which", lambda _name: "/usr/bin/chrome")
    assert chrome.chrome_executable() is None


def test_bundled_chrome_is_found_under_both_resource_layouts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(chrome.CHROME_PATH_ENV, raising=False)
    monkeypatch.setenv(chrome.RESOURCE_PATH_ENV, str(tmp_path))
    monkeypatch.setattr(chrome.shutil, "which", lambda _name: None)
    name = "chrome.exe" if chrome._IS_WINDOWS else "chrome"
    bundled = tmp_path / "resources" / "bin" / "chrome" / name
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"x")
    assert chrome.chrome_executable() == str(bundled)


def test_path_lookup_is_the_last_tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(chrome.CHROME_PATH_ENV, raising=False)
    monkeypatch.delenv(chrome.RESOURCE_PATH_ENV, raising=False)
    name = "chrome.exe" if chrome._IS_WINDOWS else "chrome"
    monkeypatch.setattr(chrome.shutil, "which", lambda binary: f"/opt/{binary}" if binary == name else None)
    assert chrome.chrome_executable() == f"/opt/{name}"


def test_launch_flags_own_the_clock_and_the_port() -> None:
    flags = chrome._launch_flags(Path("/tmp/profile"), (1280, 720))
    assert "--headless=new" in flags
    assert "--remote-debugging-port=0" in flags
    assert "--window-size=1280,720" in flags
    assert "--user-data-dir=/tmp/profile" in flags or any(
        flag.startswith("--user-data-dir=") for flag in flags
    )
    assert "--disable-background-timer-throttling" in flags


async def test_external_endpoint_is_reused_and_never_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(chrome.CDP_URL_ENV, "127.0.0.1:9222")
    session = await chrome.open_chrome()
    assert session.cdp_url == "http://127.0.0.1:9222"
    assert session.external is True
    assert session.owns_process is False
    await chrome.close_chrome(session)  # 不许碰用户的浏览器


async def test_no_chrome_at_all_gives_every_way_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(chrome.CDP_URL_ENV, raising=False)
    monkeypatch.delenv(chrome.CHROME_PATH_ENV, raising=False)
    monkeypatch.delenv(chrome.RESOURCE_PATH_ENV, raising=False)
    monkeypatch.setattr(chrome.shutil, "which", lambda _name: None)
    with pytest.raises(chrome.ChromeUnavailable) as caught:
        await chrome.open_chrome()
    assert "ST_CHROME_PATH" in str(caught.value)


def chrome_executable_is(actual: Any, expected: Path) -> bool:
    return actual is not None and Path(actual).samefile(expected)


# ------------------------------------------------------------- 真实编码冒烟


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="本机 PATH 上没有 ffmpeg")
async def test_real_ffmpeg_turns_a_png_stream_into_a_video(tmp_path: Path) -> None:
    def _png(color: str) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), color=color).save(buffer, format="PNG")
        return buffer.getvalue()

    async def _frames() -> Any:
        for color in ("#ff0000", "#00ff00", "#0000ff", "#ffffff"):
            yield _png(color)

    out = tmp_path / "mg.mp4"
    result = await encoder.encode_frames(_frames(), fps=10, out_path=out, width=64, height=64)
    assert result == out
    assert out.stat().st_size > 0
