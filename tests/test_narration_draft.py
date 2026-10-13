"""[aipdd fork delta] 口播智能剪辑草稿组装器单测：mock VectCut HTTP，断言请求序列与降级。

不出真网络：用一个假 ``httpx.AsyncClient`` 记录每次 POST 的 (path, body) 并按需返回
成功/失败。覆盖 VectCut 未配置内联提示、完整落轨序列（create→逐段 add_video→
add_subtitle→add_image→add_audio→save_draft）、草稿目录透传、字幕/B-roll/BGM 失败
不致命、add_video 失败即中断、以及 create_draft 无 draft_id 的兜底。
"""

from __future__ import annotations

import httpx
import pytest

from novelvideo.freezone import narration_draft


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def add(self, path: str, body: dict) -> None:
        self.calls.append((path, body))

    def paths(self) -> list[str]:
        return [path for path, _ in self.calls]


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """按 path 返回预置响应；未预置的一律 success=True。记录每次 (path, body)。"""

    def __init__(self, responses: dict[str, list[dict]], recorder: _Recorder, **_kwargs) -> None:
        self._responses = responses
        self._recorder = recorder
        self._cursor: dict[str, int] = {}

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def post(self, url: str, *, json: dict, headers: dict) -> _FakeResponse:  # noqa: ARG002
        path = url.split("9001", 1)[-1]
        self._recorder.add(path, json)
        queue = self._responses.get(path)
        if not queue:
            return _FakeResponse({"success": True, "output": {"draft_id": "dfd_x"}})
        index = self._cursor.get(path, 0)
        self._cursor[path] = index + 1
        payload = queue[index] if index < len(queue) else queue[-1]
        return _FakeResponse(payload)


def _patch(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder, responses: dict[str, list[dict]]) -> None:
    monkeypatch.setenv("VECTCUT_BASE_URL", "http://vectcut:9001")

    def factory(**kwargs):
        return _FakeClient(responses, recorder, **kwargs)

    monkeypatch.setattr(narration_draft.httpx, "AsyncClient", factory)


async def test_not_configured_returns_inline_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VECTCUT_BASE_URL", raising=False)
    result = await narration_draft.build_vectcut_draft(
        width=1080, height=1920, video_clips=[{"url": "/a.mp4", "source_start": 0, "source_end": 1}]
    )
    assert result.ok is False
    assert result.not_configured is True
    assert result.hint == narration_draft.VECTCUT_NOT_CONFIGURED_HINT


async def test_full_draft_sequence_and_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch(
        monkeypatch,
        recorder,
        {
            "/create_draft": [{"success": True, "output": {"draft_id": "dfd_1"}}],
            "/aipdd/save_draft": [{"success": True, "output": {"path": "/drafts/dfd_1"}}],
        },
    )
    clips = [
        {"url": "/data/src.mp4", "source_start": 0.0, "source_end": 2.0, "timeline_start": 0.0},
        {"url": "/data/src.mp4", "source_start": 4.0, "source_end": 6.0, "timeline_start": 2.0},
    ]
    broll = [{"url": "/data/broll0.png", "timeline_start": 1.0, "timeline_end": 3.0}]
    result = await narration_draft.build_vectcut_draft(
        width=1080,
        height=1920,
        video_clips=clips,
        srt_path="/data/sub.srt",
        broll_images=broll,
        bgm_path="/data/bgm.mp3",
        bgm_gain=0.3,
        final_duration=4.0,
        project_name="口播成片",
    )
    assert result.ok is True
    assert result.saved is True
    assert result.draft_id == "dfd_1"
    assert recorder.paths() == [
        "/create_draft",
        "/add_video",
        "/add_video",
        "/add_subtitle",
        "/add_image",
        "/add_audio",
        "/aipdd/save_draft",
    ]
    # 第二段：源裁切 4→6、贴到时间轴 2.0、时长自动补 2.0。
    second_clip = recorder.calls[2][1]
    assert second_clip["start"] == 4.0
    assert second_clip["end"] == 6.0
    assert second_clip["target_start"] == 2.0
    assert second_clip["duration"] == 2.0
    assert second_clip["video_url"] == "/data/src.mp4"
    # BGM 音量夹到 0..1、时长跟随整片。
    bgm = recorder.calls[5][1]
    assert bgm["volume"] == pytest.approx(0.3)
    assert bgm["duration"] == 4.0
    assert bgm["audio_url"] == "/data/bgm.mp3"
    # save_draft：未显式给 draft_folder → 不注入（走部署默认）。
    save_body = recorder.calls[6][1]
    assert save_body["draft_id"] == "dfd_1"
    assert save_body["project_name"] == "口播成片"


async def test_draft_folder_transparently_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch(monkeypatch, recorder, {"/aipdd/save_draft": [{"success": True, "output": {}}]})
    result = await narration_draft.build_vectcut_draft(
        width=720,
        height=1280,
        video_clips=[{"url": "/a.mp4", "source_start": 0, "source_end": 3}],
        draft_folder="/Users/me/JianyingPro/Drafts",
    )
    assert result.ok is True
    save_body = recorder.calls[-1][1]
    assert save_body["draft_folder"] == "/Users/me/JianyingPro/Drafts"


async def test_add_video_failure_aborts_before_save(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch(
        monkeypatch,
        recorder,
        {"/add_video": [{"success": False, "error": "素材读不到"}]},
    )
    result = await narration_draft.build_vectcut_draft(
        width=1080,
        height=1920,
        video_clips=[{"url": "/missing.mp4", "source_start": 0, "source_end": 2}],
        srt_path="/s.srt",
    )
    assert result.ok is False
    assert "素材读不到" in result.error
    # 第一段 add_video 就失败，后续字幕/保存都不该发生。
    assert recorder.paths() == ["/create_draft", "/add_video"]


async def test_subtitle_broll_bgm_failures_are_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch(
        monkeypatch,
        recorder,
        {
            "/add_subtitle": [{"success": False, "error": "字幕坏"}],
            "/add_image": [{"success": False, "error": "图坏"}],
            "/add_audio": [{"success": False, "error": "BGM坏"}],
            "/aipdd/save_draft": [{"success": True, "output": {}}],
        },
    )
    result = await narration_draft.build_vectcut_draft(
        width=1080,
        height=1920,
        video_clips=[{"url": "/a.mp4", "source_start": 0, "source_end": 2}],
        srt_path="/s.srt",
        broll_images=[{"url": "/b.png", "timeline_start": 0, "timeline_end": 1}],
        bgm_path="/bgm.mp3",
        final_duration=2.0,
    )
    # 三条可选轨失败都不阻断，草稿照常保存成功。
    assert result.ok is True
    assert result.saved is True
    assert any(step.name == "add_subtitle" and not step.ok for step in result.steps)


async def test_save_draft_failure_marks_not_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch(monkeypatch, recorder, {"/aipdd/save_draft": [{"success": False, "error": "目录只读"}]})
    result = await narration_draft.build_vectcut_draft(
        width=1080,
        height=1920,
        video_clips=[{"url": "/a.mp4", "source_start": 0, "source_end": 2}],
    )
    assert result.ok is False
    assert result.saved is False
    assert "目录只读" in result.error


async def test_create_draft_without_id_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch(monkeypatch, recorder, {"/create_draft": [{"success": True, "output": {}}]})
    result = await narration_draft.build_vectcut_draft(
        width=1080,
        height=1920,
        video_clips=[{"url": "/a.mp4", "source_start": 0, "source_end": 2}],
    )
    assert result.ok is False
    assert "draft_id" in result.error


async def test_no_clips_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTCUT_BASE_URL", "http://vectcut:9001")
    result = await narration_draft.build_vectcut_draft(width=1080, height=1920, video_clips=[])
    assert result.ok is False
    assert result.draft_id == ""


async def test_network_error_returns_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTCUT_BASE_URL", "http://vectcut:9001")

    class _Boom:
        def __init__(self, **_kwargs) -> None:
            return

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, *args, **kwargs):  # noqa: ARG002
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(narration_draft.httpx, "AsyncClient", _Boom)
    result = await narration_draft.build_vectcut_draft(
        width=1080,
        height=1920,
        video_clips=[{"url": "/a.mp4", "source_start": 0, "source_end": 2}],
    )
    assert result.ok is False
    assert "VectCut" in result.hint
