"""[aipdd fork delta] req39：freezone 提交前置拦截「实测已判死」的 chat 模型选型。

现场事实（线上 2.0.95 实测）：「视频反推剧本」选 ``volcengine::doubao-seed-1-6-vision-250815``
时，任务把 ffmpeg 抽帧全跑完才在 55% 的视觉调用上死于 404
``InvalidEndpointOrModel.NotFound`` —— 而这个名字在实测缓存里**早就**是 ``not-granted``，
现网 17 项视觉候选里也已经没有它。代价是白等几分钟的本地重活。

纪律（与 req33/req35/req38 一条线）：

- 只拦缓存里已有明确结论的名字（文本 ``not-granted``；视觉 ``not-granted`` / ``not-vision``）；
  ``ok``、``unknown``（限流 / 鉴权 / 超时 / 端点临时关闭 / 这轮没测过）、归属判不出、
  缓存读失败一律放行 —— 误拦的代价是「本来能跑的任务跑不了」，比漏拦严重。
- 不改选、不猜替代模型：结论是「这个账号没有这个名字」时，替用户决定用哪个属于隐式裁决。
- 表只登记**确认过当 chat 用**的 payload 字段：``payload["model"]`` 在反推剧本里是带图
  chat 的视觉模型，在 gen / audio / copywrite 里是图像 / 语音 / ASR 后端模型，后者不在
  chat 实测的判定范围内（音频经 chat 永远拿不到 2xx），误登记会挡掉能跑的任务。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from novelvideo import config
from novelvideo import model_gateway_settings
from novelvideo import text_model_probe
from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.api.routes import model_gateway as model_gateway_routes
from novelvideo.api.routes.freezone.media_jobs import _enqueue_freezone_background_job
from novelvideo.api.schemas import FreezoneVideoScriptReverseRequest
from novelvideo.freezone.model_selection import (
    FREEZONE_CHAT_MODEL_FIELDS,
    find_unusable_chat_model,
    unusable_chat_model_detail,
)

DEAD_VISION = "doubao-seed-1-6-vision-250815"
DEAD_VISION_CALL = f"volcengine::{DEAD_VISION}"
DEAD_TEXT = "doubao-seed-1-6-lite-251015"
DEAD_TEXT_CALL = f"volcengine::{DEAD_TEXT}"

TEXT_TASKS = [
    "freezone_video_clone",
    "freezone_video_mix",
    "freezone_whiteboard_animation",
    "freezone_explainer_video",
    "freezone_video_idea",
]


@pytest.fixture(autouse=True)
def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    for key in (
        "ST_CONTROL_PLANE_DSN",
        "MODEL_GATEWAY_MODE",
        "LOCALAPI_API_KEY",
        "LOCALAPI_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    model_gateway_routes._text_models_cache = None
    model_gateway_routes._vision_models_cache = None


def _seed(kind: str, provider: str, states: dict[str, str]) -> None:
    save = (
        model_gateway_settings.save_text_model_probe
        if kind == "text"
        else model_gateway_settings.save_vision_model_probe
    )
    save(provider, states)


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """把漏斗下游的 task backend 与响应拼装替换成记录器，聚焦「入队 / 未入队」。"""

    calls: dict[str, Any] = {"enqueue": []}

    class _QueuedTaskState:
        task_id = "task-1"

    class _Queued:
        backend = "sqlite"
        queue = "default"
        task_state = _QueuedTaskState()

    class _FakeBackend:
        async def enqueue_project_task(self, ctx, **kwargs):  # noqa: ARG001
            calls["enqueue"].append(kwargs)
            return _Queued()

    monkeypatch.setattr(freezone_routes, "get_task_backend", lambda: _FakeBackend())
    monkeypatch.setattr(
        freezone_routes,
        "_project_job_response",
        lambda **kwargs: {
            "ok": True,
            "data": {"task_type": kwargs["task_type"], "job_id": kwargs["job_id"]},
        },
    )
    return calls


async def _enqueue(task_type: str, payload: dict[str, Any]) -> dict:
    return await _enqueue_freezone_background_job(
        ctx=SimpleNamespace(output_dir="/tmp/project", project_id="p", project_name="58"),
        project_dir=Path("/tmp/project"),
        task_type=task_type,
        job_id="job-1",
        payload=payload,
    )


# ---------------------------------------------------------------------------
# 谓词本身
# ---------------------------------------------------------------------------


def test_verdict_only_reports_explicit_removals() -> None:
    _seed("vision", "volcengine", {DEAD_VISION: "not-granted", "glm-v": "not-vision"})
    _seed("text", "volcengine", {DEAD_TEXT: "not-granted", "ok-one": "ok"})

    assert find_unusable_chat_model(
        task_type="freezone_video_script_reverse",
        payload={"model": DEAD_VISION_CALL},
    ) == ("volcengine", DEAD_VISION_CALL, "not-granted")
    assert find_unusable_chat_model(
        task_type="freezone_video_script_reverse",
        payload={"model": "volcengine::glm-v"},
    ) == ("volcengine", "volcengine::glm-v", "not-vision")
    # 没测过 = unknown，不拦。
    assert (
        find_unusable_chat_model(
            task_type="freezone_video_script_reverse",
            payload={"model": "volcengine::never-probed"},
        )
        is None
    )
    # 两份缓存互不越界：视觉选型不借文本缓存的判死，文本选型也不借视觉的 not-vision。
    assert (
        find_unusable_chat_model(
            task_type="freezone_video_script_reverse", payload={"model": DEAD_TEXT_CALL}
        )
        is None
    )
    assert (
        find_unusable_chat_model(
            task_type="freezone_video_clone", payload={"model_text": "volcengine::glm-v"}
        )
        is None
    )
    assert find_unusable_chat_model(
        task_type="freezone_video_clone", payload={"model_text": DEAD_TEXT_CALL}
    ) == ("volcengine", DEAD_TEXT_CALL, "not-granted")
    assert (
        find_unusable_chat_model(
            task_type="freezone_video_clone", payload={"model_text": "volcengine::ok-one"}
        )
        is None
    )


@pytest.mark.parametrize("call_name", ["", "   ", None])
def test_blank_selection_follows_the_system_default_chain(call_name: Any) -> None:
    assert (
        find_unusable_chat_model(
            task_type="freezone_video_script_reverse", payload={"model": call_name}
        )
        is None
    )


@pytest.mark.parametrize(
    "task_type", ["freezone_gen", "freezone_douyin_copywrite", "freezone_audio_speech"]
)
def test_unregistered_task_types_are_untouched(task_type: str) -> None:
    _seed("vision", "volcengine", {DEAD_VISION: "not-granted"})
    # 这些任务的 model 字段是图像后端 / ASR / 语音模型，不在 chat 实测判定范围内。
    assert (
        find_unusable_chat_model(task_type=task_type, payload={"model": DEAD_VISION_CALL})
        is None
    )


def test_bare_name_blocks_only_with_single_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed("vision", "volcengine", {"shared-vision": "not-granted"})

    monkeypatch.setattr(
        model_gateway_settings, "model_owner_providers", lambda _m: ["volcengine"]
    )
    assert find_unusable_chat_model(
        task_type="freezone_video_script_reverse", payload={"model": "shared-vision"}
    ) == ("volcengine", "shared-vision", "not-granted")

    # 多归属 / 无归属：系统不做隐式裁决，也不猜渠道 → 放行。
    monkeypatch.setattr(
        model_gateway_settings,
        "model_owner_providers",
        lambda _m: ["volcengine", "aipdd"],
    )
    assert (
        find_unusable_chat_model(
            task_type="freezone_video_script_reverse", payload={"model": "shared-vision"}
        )
        is None
    )
    monkeypatch.setattr(model_gateway_settings, "model_owner_providers", lambda _m: [])
    assert (
        find_unusable_chat_model(
            task_type="freezone_video_script_reverse", payload={"model": "shared-vision"}
        )
        is None
    )


def test_probe_cache_read_failure_never_blocks_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("settings db is gone")

    monkeypatch.setattr(text_model_probe, "model_probe_state", _boom)
    assert (
        find_unusable_chat_model(
            task_type="freezone_video_script_reverse", payload={"model": DEAD_VISION_CALL}
        )
        is None
    )


@pytest.mark.parametrize(
    ("verdict", "needle", "absent"),
    [
        ("not-granted", "控制台", "读不了图片"),
        ("not-vision", "读不了图片", "控制台"),
    ],
)
def test_rejection_wording_is_actionable(verdict: str, needle: str, absent: str) -> None:
    detail = unusable_chat_model_detail(DEAD_VISION_CALL, verdict, provider="volcengine")

    assert needle in detail
    assert absent not in detail
    assert DEAD_VISION_CALL in detail
    assert "任务未提交" in detail
    assert "跟随系统默认" in detail
    assert "改选" in detail
    if verdict == "not-granted":
        # 渠道要说人话，用户才知道该去哪个控制台。
        assert "VolcEngine 火山方舟" in detail


def test_table_registers_only_verified_chat_fields() -> None:
    """口径钉死：媒体 / 语音 / ASR 类任务一律不得进表。"""

    assert set(FREEZONE_CHAT_MODEL_FIELDS) == {
        *TEXT_TASKS,
        "freezone_video_script_reverse",
    }
    assert FREEZONE_CHAT_MODEL_FIELDS["freezone_video_script_reverse"] == (
        ("model", "vision"),
    )
    for task_type in TEXT_TASKS:
        assert FREEZONE_CHAT_MODEL_FIELDS[task_type] == (("model_text", "text"),)
    for banned in (
        "freezone_gen",
        "freezone_edit",
        "freezone_audio_speech",
        "freezone_audio_eleven_music",
        "freezone_douyin_copywrite",
        "freezone_media_copywrite",
        "freezone_video_erase",
        "freezone_video_upscale",
        "freezone_story_script",
        "freezone_text_generate",
    ):
        assert banned not in FREEZONE_CHAT_MODEL_FIELDS


# ---------------------------------------------------------------------------
# 漏斗与路由：命中就不入队，未命中照常入队
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_vision_pick_is_refused_without_queuing(backend) -> None:
    _seed("vision", "volcengine", {DEAD_VISION: "not-granted"})

    with pytest.raises(HTTPException) as excinfo:
        await _enqueue(
            "freezone_video_script_reverse",
            {"video_path": "/tmp/a.mp4", "model": DEAD_VISION_CALL},
        )

    assert excinfo.value.status_code == 400
    assert DEAD_VISION_CALL in str(excinfo.value.detail)
    assert backend["enqueue"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["ok", "unknown"])
async def test_names_without_a_removal_verdict_still_enqueue(
    backend, state: str
) -> None:
    _seed("vision", "volcengine", {"doubao-seed-2-1-pro-260628": state})

    await _enqueue(
        "freezone_video_script_reverse",
        {"video_path": "/tmp/a.mp4", "model": "volcengine::doubao-seed-2-1-pro-260628"},
    )

    assert len(backend["enqueue"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", TEXT_TASKS)
async def test_dead_text_pick_is_refused_for_every_text_task(
    backend, task_type: str
) -> None:
    _seed("text", "volcengine", {DEAD_TEXT: "not-granted"})

    with pytest.raises(HTTPException) as excinfo:
        await _enqueue(task_type, {"model_text": DEAD_TEXT_CALL})

    assert excinfo.value.status_code == 400
    assert backend["enqueue"] == []


@pytest.mark.asyncio
async def test_asr_model_field_is_not_swept_by_the_intercept(backend) -> None:
    """copywrite 的 model 是 ASR 选型，即使与判死的视觉同名也必须照常提交。"""

    _seed("vision", "volcengine", {DEAD_VISION: "not-granted"})

    await _enqueue(
        "freezone_douyin_copywrite", {"video": "/tmp/a.mp4", "model": DEAD_VISION}
    )

    assert len(backend["enqueue"]) == 1


@pytest.mark.asyncio
async def test_route_level_submit_of_dead_name_returns_400(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend,
) -> None:
    """端到端钉死：路由里没有 except 把这条 400 吞成 500。"""

    project_dir = tmp_path / "project"
    video = project_dir / "freezone" / "_uploads" / "clip.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"mp4")

    async def fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return (
            SimpleNamespace(
                output_dir=str(project_dir), project_id="p", project_name=project
            ),
            "admin",
            project,
            project_dir,
            str(project_dir),
        )

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", fake_resolve)
    _seed("vision", "volcengine", {DEAD_VISION: "not-granted"})

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_script_reverse(
            project="58",
            body=FreezoneVideoScriptReverseRequest(
                video_url="/static/admin/58/freezone/_uploads/clip.mp4",
                model=DEAD_VISION_CALL,
            ),
            user={"username": "admin"},
        )

    assert excinfo.value.status_code == 400
    assert backend["enqueue"] == []
