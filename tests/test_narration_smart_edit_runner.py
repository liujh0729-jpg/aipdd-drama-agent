"""[aipdd fork delta] 口播智能剪辑 runner 契约测试。

只守「阶段编排 + 分发点 + 产物形状」：本地 ffmpeg 算子（probe/detect/cut/mix）与
VectCut 草稿落盘全部换成显式假对象，四个出网叶（ASR / 文本×2 / 生图 / 生乐）经
统一假分发器记录调用名与 kwargs。覆盖：全开关的完整流水线、output_mode 分支
（video 不落草稿不生 B-roll）、阶段开关短路（关瑕疵/B-roll/BGM/字幕则不出 ASR/文本）、
无瑕疵剔除不致命。分发计数与 egress 传递的表侧契约由
``test_p0g4i_freezone_leaf_classification.py`` 守，本文件守 runner 侧调用点。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from novelvideo.freezone import narration_draft, narration_edit
from novelvideo.project_context import ProjectContext


def _project_context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="project-1",
        project_name="project",
        owner_type="user",
        owner_id="user-1",
        owner_username="user",
        requester_user_id="user-1",
        requester_username="user",
        requester_principals=(("user", "user-1"),),
        effective_role="editor",
        home_node_id="local",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


def _narration_payload(project_dir: Path, source_path: str, **overrides: object) -> dict:
    payload: dict[str, object] = {
        "job_id": "narr_job",
        "project_dir": str(project_dir),
        "source_path": source_path,
        "output_mode": "both",
        "aspect": "9:16",
        "cut_silence": True,
        "cut_fillers": True,
        "plan_broll": True,
        "gen_bgm": True,
        "captions": True,
        "noise_db": -35.0,
        "min_silence_s": 0.4,
        "broll_max": 8,
        "asr_model": "funasr-a",
        "text_model": "DC-llm-custom",
        "image_model": "gpt-image-2",
        "image_provider": "newapi",
        "image_model_params": {},
        "image_request_schema": {},
        "music_model": "music-3.0",
        "bgm_prompt": "轻快垫乐",
        "bgm_gain": 0.2,
        "title": "口播测试",
        "archive_to_library": True,
        "source_tool": "narration-smart-edit",
    }
    payload.update(overrides)
    return payload


def _patch_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    transcript: str = "第一句话。第二句话。第三句话。",
    text_responses: list[str] | None = None,
    broll_slot_json: str | None = None,
) -> dict[str, Any]:
    """把四个出网叶 + 本地算子 + 草稿落盘全换成假对象，返回调用记录。"""
    from novelvideo.task_backend.runners import freezone as freezone_runner

    calls: dict[str, Any] = {
        "leaf": [],
        "archived": [],
        "progress": [],
        "gen": [],
    }
    if text_responses is None:
        # 第 1 次文本 leaf = 瑕疵剪：剔除编号 1；第 2 次 = 分镜 B-roll 计划。
        text_responses = [
            '{"drop": [1]}',
            broll_slot_json
            or '{"broll_slots": [{"t_start": 0.5, "t_end": 2.0, "prompt": "画面一"}]}',
        ]

    async def fake_call_leaf(envelope, leaf, leaf_name, /, **kwargs):  # noqa: ANN001, ARG001
        calls["leaf"].append({"name": leaf_name, **kwargs})
        if leaf_name == "transcribe_freezone_clone_dialogue":
            return transcript
        if leaf_name == "generate_freezone_text":
            index = sum(1 for c in calls["leaf"] if c["name"] == "generate_freezone_text") - 1
            return ("DC-llm-custom", text_responses[min(index, len(text_responses) - 1)])
        if leaf_name == "run_freezone_gen":
            image_path = tmp_path / f"broll_{len(calls['gen'])}.jpg"
            image_path.write_bytes(b"jpg")
            calls["gen"].append(kwargs)
            return image_path
        if leaf_name == "run_freezone_music_generate":
            bgm_path = tmp_path / "bgm.mp3"
            bgm_path.write_bytes(b"mp3")
            return SimpleNamespace(audio_path=str(bgm_path), duration_ms=5000)
        raise AssertionError(f"unexpected leaf {leaf_name}")

    async def fake_await(coro, **_kwargs):  # noqa: ANN001
        return await coro

    def fake_cut_and_concat(video_path, kept, out_path, *, work_dir):  # noqa: ANN001, ARG001
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"mp4")
        return True

    def fake_mix_bgm(video_src, bgm_path, out_path, *, bgm_gain):  # noqa: ANN001, ARG001
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"mp4")
        return True

    async def fake_build_draft(**kwargs):  # noqa: ANN001, ARG001
        calls.setdefault("draft_kwargs", kwargs)
        return narration_draft.DraftBuildResult(ok=True, draft_id="draft_1", saved=True)

    monkeypatch.setattr(freezone_runner, "_call_freezone_leaf", fake_call_leaf)
    monkeypatch.setattr(freezone_runner, "_await_with_cancel_watch", fake_await)
    monkeypatch.setattr(
        freezone_runner,
        "_update",
        lambda *_a, **_k: calls["progress"].append(_a),
    )
    monkeypatch.setattr(freezone_runner, "_extract_video_cover", lambda *_a, **_k: True)
    monkeypatch.setattr(
        freezone_runner,
        "_archive_tool_output_to_library",
        lambda **kwargs: calls["archived"].append(kwargs),
    )
    # 本地算子里需要真 ffmpeg 的两个函数换掉，纯计算函数保留真身。
    monkeypatch.setattr(narration_edit, "probe_duration", lambda _p: 10.0)
    monkeypatch.setattr(
        narration_edit,
        "detect_silences",
        lambda _p, **_k: [narration_edit.Interval(2.0, 3.0)],
    )
    monkeypatch.setattr(narration_edit, "cut_and_concat", fake_cut_and_concat)
    monkeypatch.setattr(narration_edit, "mix_bgm", fake_mix_bgm)
    # 草稿落盘 + 资产登记 + 静态 URL + 目录初始化。
    monkeypatch.setattr(narration_draft, "build_vectcut_draft", fake_build_draft)
    monkeypatch.setattr(
        "novelvideo.freezone.video_node.register_vectcut_draft_item",
        lambda *_a, **_k: {"id": "item_1"},
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    return calls


@pytest.mark.asyncio
async def test_narration_runner_full_pipeline_draft_and_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"mp4")
    calls = _patch_runner(monkeypatch, tmp_path)

    result = await freezone_runner._run_freezone_narration_smart_edit_async(
        {"task_type": "freezone_narration_smart_edit", "payload": _narration_payload(project_dir, str(source))},
        _project_context(tmp_path),
    )

    assert result["media_type"] == "video"
    assert result["output_mode"] == "both"
    assert result["source_duration"] == pytest.approx(10.0)
    assert result["transcript"] == "第一句话。第二句话。第三句话。"
    # 静音剪保留两段（10s 去掉 [2,3]），瑕疵剪再剔 1 段 → 至少剩 1 段。
    assert len(result["kept_ranges"]) >= 1

    leaf_names = [c["name"] for c in calls["leaf"]]
    # ASR ×1、文本 ×2（瑕疵 + 分镜）、生图 ×(槽位数)、音乐 ×1。
    assert leaf_names.count("transcribe_freezone_clone_dialogue") == 1
    assert leaf_names.count("generate_freezone_text") == 2
    assert leaf_names.count("run_freezone_music_generate") == 1
    assert leaf_names.count("run_freezone_gen") == len(result["broll_slots"]) >= 1

    # ASR 叶带 asr_model；文本叶带 text_model；生图叶带解析后的 image_model/provider。
    asr_call = next(c for c in calls["leaf"] if c["name"] == "transcribe_freezone_clone_dialogue")
    assert asr_call["asr_model"] == "funasr-a"
    assert all(c["model"] == "DC-llm-custom" for c in calls["leaf"] if c["name"] == "generate_freezone_text")
    gen_call = next(c for c in calls["leaf"] if c["name"] == "run_freezone_gen")
    assert gen_call["model"] == "gpt-image-2"
    assert gen_call["provider"] == "newapi"

    # 草稿：ok → 登记 draft_id；直出：产出 output_url + 封面并归档一次。
    assert result["draft"]["ok"] is True
    assert result["draft_id"] == "draft_1"
    assert result["output_url"].endswith("narr_job.mp4")
    assert result["cover_url"].endswith("narr_job_cover.jpg")
    assert len(calls["archived"]) == 1
    assert calls["archived"][0]["media"] == "video"


@pytest.mark.asyncio
async def test_narration_runner_video_only_skips_draft_and_broll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"mp4")
    calls = _patch_runner(monkeypatch, tmp_path)

    result = await freezone_runner._run_freezone_narration_smart_edit_async(
        {
            "task_type": "freezone_narration_smart_edit",
            "payload": _narration_payload(project_dir, str(source), output_mode="video"),
        },
        _project_context(tmp_path),
    )

    # 只要直出：不组草稿、不生 B-roll 图（B-roll 仅落草稿）。
    assert "draft" not in result
    assert "draft_id" not in result
    leaf_names = [c["name"] for c in calls["leaf"]]
    assert "run_freezone_gen" not in leaf_names
    assert result["broll_count"] == 0
    assert any("B-roll" in line and "草稿" in line for line in result["report"])
    # 直出产物仍然齐全。
    assert result["output_url"].endswith("narr_job.mp4")


@pytest.mark.asyncio
async def test_narration_runner_stage_switches_short_circuit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"mp4")
    # 关掉瑕疵剪 / B-roll / BGM / 字幕：连 ASR 都不该触发（无文本消费需求）。
    calls = _patch_runner(monkeypatch, tmp_path)

    result = await freezone_runner._run_freezone_narration_smart_edit_async(
        {
            "task_type": "freezone_narration_smart_edit",
            "payload": _narration_payload(
                project_dir,
                str(source),
                output_mode="video",
                cut_silence=True,
                cut_fillers=False,
                plan_broll=False,
                gen_bgm=False,
                captions=False,
            ),
        },
        _project_context(tmp_path),
    )

    leaf_names = [c["name"] for c in calls["leaf"]]
    assert leaf_names == []  # 只静音剪：无 ASR / 无文本 / 无生图 / 无音乐
    assert result["transcript"] == ""
    assert result["broll_slots"] == []
    assert "run_freezone_music_generate" not in leaf_names
    # 纯静音剪（本地假静音 [2,3]）→ 保留两段，仍能出片。
    assert len(result["kept_ranges"]) == 2
    assert result["output_url"].endswith("narr_job.mp4")


@pytest.mark.asyncio
async def test_narration_runner_no_defect_drops_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"mp4")
    # 文本叶返回空 drop、空 B-roll 计划：都应正常走完不抛。
    calls = _patch_runner(
        monkeypatch,
        tmp_path,
        text_responses=['{"drop": []}', '{"broll_slots": []}'],
    )

    result = await freezone_runner._run_freezone_narration_smart_edit_async(
        {
            "task_type": "freezone_narration_smart_edit",
            "payload": _narration_payload(project_dir, str(source)),
        },
        _project_context(tmp_path),
    )

    assert any("未检出需剔除" in line for line in result["report"])
    assert result["broll_count"] == 0
    # 空 B-roll 计划→不生图（生图叶一次都不该触发）。
    assert "run_freezone_gen" not in [c["name"] for c in calls["leaf"]]
    # 静音剪保留的两段没有被瑕疵剪减少。
    assert len(result["kept_ranges"]) == 2
    assert result["draft"]["ok"] is True


@pytest.mark.asyncio
async def test_narration_runner_missing_source_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    _patch_runner(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="未找到"):
        await freezone_runner._run_freezone_narration_smart_edit_async(
            {
                "task_type": "freezone_narration_smart_edit",
                "payload": _narration_payload(project_dir, str(tmp_path / "nope.mp4")),
            },
            _project_context(tmp_path),
        )
