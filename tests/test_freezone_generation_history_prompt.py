"""Generation-history records persist the prompt that produced each version.

Video gen results only carry an output url, which forced the frontend history
to fall back to the node's *current* prompt — so every old version showed the
latest prompt after an edit+regenerate. These tests pin that each recorded
version keeps its own prompt.
"""

from __future__ import annotations

from pathlib import Path

from novelvideo.freezone.history import (
    MAX_HISTORY_PROMPT_CHARS,
    build_node_history_record,
    read_generation_history,
)
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.runners.freezone import _append_node_history
from novelvideo.task_backend.runners.video import _append_freezone_video_node_history


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_freezone",
        project_name="demo",
        owner_type="user",
        owner_id="owner_1",
        owner_username="admin",
        requester_user_id="owner_1",
        requester_username="admin",
        requester_principals=(("user", "owner_1"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path / "output" / "admin" / "demo",
        state_dir=tmp_path / "state" / "admin" / "demo",
        runtime_dir=tmp_path / "runtime" / "admin" / "demo",
        is_home_node=True,
    )


def test_freezone_video_history_persists_prompt(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    project_dir = tmp_path / "proj"
    payload = {
        "node_id": "video_1",
        "canvas_id": "default",
        "prompt": "电影级武侠风，深夜古街，斗笠侠客。",
    }
    rec = _append_freezone_video_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        job_id="job_v1",
        result={"output_url": "/static/x.mp4"},
    )
    assert rec is not None
    assert rec["prompt"] == payload["prompt"]

    stored = read_generation_history(
        project_dir=project_dir, canvas_id="default", node_id="video_1"
    )
    assert stored[-1]["prompt"] == payload["prompt"]


def test_freezone_video_history_keeps_per_version_prompt_after_edit(
    tmp_path: Path,
) -> None:
    """Editing the prompt + regenerating must not rewrite older versions."""
    ctx = _ctx(tmp_path)
    project_dir = tmp_path / "proj"
    base = {"node_id": "video_1", "canvas_id": "default"}

    _append_freezone_video_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload={**base, "prompt": "旧提示词"},
        job_id="job_old",
        result={"output_url": "/static/old.mp4"},
    )
    _append_freezone_video_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload={**base, "prompt": "新提示词"},
        job_id="job_new",
        result={"output_url": "/static/new.mp4"},
    )

    stored = read_generation_history(
        project_dir=project_dir, canvas_id="default", node_id="video_1"
    )
    by_job = {r["job_id"]: r for r in stored}
    assert by_job["job_old"]["prompt"] == "旧提示词"
    assert by_job["job_new"]["prompt"] == "新提示词"


def test_freezone_video_history_omits_prompt_when_absent(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    project_dir = tmp_path / "proj"
    rec = _append_freezone_video_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload={"node_id": "video_1", "canvas_id": "default"},
        job_id="job_np",
        result={"output_url": "/static/x.mp4"},
    )
    assert rec is not None
    assert "prompt" not in rec


def test_freezone_image_history_persists_prompt(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    project_dir = tmp_path / "proj"
    rec = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload={"node_id": "img_1", "canvas_id": "default", "prompt": "图片提示词"},
        task_type="freezone_gen",
        job_id="job_i1",
        media_type="image",
        result={"output_url": "/static/i.png"},
    )
    assert rec is not None
    assert rec["prompt"] == "图片提示词"


def test_image_history_falls_back_to_input_key(tmp_path: Path) -> None:
    """Text/audio nodes carry the user text under 'input', not 'prompt'."""
    ctx = _ctx(tmp_path)
    rec = _append_node_history(
        ctx=ctx,
        project_dir=tmp_path / "proj",
        payload={"node_id": "n1", "canvas_id": "default", "input": "配音文本"},
        task_type="freezone_audio_gen",
        job_id="job_a1",
        media_type="audio",
        result={"output_url": "/static/a.mp3"},
    )
    assert rec is not None
    assert rec["prompt"] == "配音文本"


# ---- shared builder ----

def test_builder_strips_and_caps_prompt() -> None:
    rec = build_node_history_record(
        task_type="t", job_id="j", task_key="k", status="completed",
        media_type="image", prompt="  " + "x" * (MAX_HISTORY_PROMPT_CHARS + 500) + "  ",
    )
    assert len(rec["prompt"]) == MAX_HISTORY_PROMPT_CHARS


def test_builder_omits_blank_prompt_and_deepcopies_result() -> None:
    src = {"nested": {"v": 1}}
    rec = build_node_history_record(
        task_type="t", job_id="j", task_key="k", status="failed",
        media_type="image", result=src, error="boom", prompt="   ",
    )
    assert "prompt" not in rec
    assert rec["status"] == "failed"
    # deepcopy: mutating the source must not touch the stored record
    src["nested"]["v"] = 999
    assert rec["result"]["nested"]["v"] == 1


def test_api_route_helper_persists_prompt(tmp_path: Path) -> None:
    """The API-route helper (_record_freezone_node_history) also stores prompt."""
    from novelvideo.api.routes.freezone import _record_freezone_node_history

    project_dir = tmp_path / "proj"
    rec = _record_freezone_node_history(
        ctx=None,
        project_dir=project_dir,
        canvas_id="default",
        node_id="story_1",
        task_type="freezone_story_script",
        username="admin",
        project="demo",
        job_id="job_s1",
        status="completed",
        media_type="text",
        prompt="故事脚本提示词",
        result={"output_format": "json"},
    )
    assert rec is not None
    assert rec["prompt"] == "故事脚本提示词"
    stored = read_generation_history(
        project_dir=project_dir, canvas_id="default", node_id="story_1"
    )
    assert stored[-1]["prompt"] == "故事脚本提示词"
    # the dead prompt_preview field is no longer emitted
    assert "prompt_preview" not in stored[-1]
