"""Grok 直连生成器必须为每次上游 HTTP 调用设显式超时。

回归护栏：曾三处 `aiohttp.ClientSession()` 漏传 timeout（本仓其余生成器都显式
设 `ClientTimeout`/`timeout=`），停摆的上游连接会无限占用视频 lane。这里用假
session 捕获 timeout，断言 submit / poll / download 三条路径都拿到带 total 的
ClientTimeout。
"""

from __future__ import annotations

import aiohttp
import pytest

from novelvideo.generators.video_generator.backends import grok as grok_mod
from novelvideo.generators.video_generator.backends.grok import GrokVideoGenerator


class _FakeResp:
    def __init__(self, method: str, url: str) -> None:
        self.status = 200
        self._method = method
        self._url = url

    async def __aenter__(self) -> "_FakeResp":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def json(self) -> dict:
        if self._method == "post":
            return {"request_id": "req-1"}
        return {"status": "done", "video": {"url": "http://up/v.mp4", "duration": 5}}

    async def text(self) -> str:
        return ""

    async def read(self) -> bytes:
        return b"video-bytes"


class _RecordingSession:
    """记录每次构造收到的 timeout，并让 get/post 走 _FakeResp。"""

    def __init__(self, captured: list[object], timeout: object = None) -> None:
        self._captured = captured
        self._captured.append(timeout)

    async def __aenter__(self) -> "_RecordingSession":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    def get(self, url: str, *_a: object, **_k: object) -> _FakeResp:
        return _FakeResp("get", url)

    def post(self, url: str, *_a: object, **_k: object) -> _FakeResp:
        return _FakeResp("post", url)


def _install_fake_sessions(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    captured: list[object] = []

    def _factory(*, timeout: object = None, **_k: object) -> _RecordingSession:
        return _RecordingSession(captured, timeout=timeout)

    monkeypatch.setattr(grok_mod.aiohttp, "ClientSession", _factory)
    return captured


def _assert_all_timeouts_bounded(captured: list[object]) -> None:
    assert captured, "no ClientSession was constructed"
    for timeout in captured:
        assert isinstance(timeout, aiohttp.ClientTimeout), timeout
        assert timeout.total is not None and timeout.total > 0, timeout


@pytest.mark.asyncio
async def test_grok_download_session_has_timeout(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _install_fake_sessions(monkeypatch)
    gen = GrokVideoGenerator(api_key="platform-key")

    ok = await gen._download_video("http://up/v.mp4", str(tmp_path / "out.mp4"))
    assert ok is True
    _assert_all_timeouts_bounded(captured)


@pytest.mark.asyncio
async def test_grok_generate_all_http_calls_have_timeout(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 让 poll_interval / sleep 不真的等待。
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(grok_mod.asyncio, "sleep", _no_sleep)
    captured = _install_fake_sessions(monkeypatch)
    gen = GrokVideoGenerator(api_key="platform-key")

    result = await gen.generate(
        image_path="data:image/png;base64,AAAA",
        prompt="p",
        output_path=str(tmp_path / "out.mp4"),
    )
    assert result.status.name == "DONE"
    # submit + poll(done) + download 至少三次构造，每次都必须带 total 超时。
    assert len(captured) >= 3
    _assert_all_timeouts_bounded(captured)
