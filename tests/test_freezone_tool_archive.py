# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""工具页产物自动归档「待分类资产」的回归测试。

覆盖三件事：
- 条目落位：folder=other（待分类资产）、音频带「音效」标签、URL 填对应媒体字段；
- 幂等：同一任务（item_id 相同）重复登记是更新而非新增；
- 开关语义：payload 不带 ``archive_to_library`` 时不动库、不写任务结果标记，
  带标记时同时写入 ``archived_to_library`` / ``library_item_id``。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from novelvideo.freezone.video_node import (
    TOOL_LIBRARY_ITEM_ID_PREFIX,
    archive_generated_library_item,
    load_video_character_library,
)
from novelvideo.task_backend.runners.freezone import _archive_tool_output_to_library


def _payload(project_dir: Path, **extra: object) -> dict:
    return {"job_id": "job-abc123", "project_dir": str(project_dir), **extra}


def test_archive_generated_library_item_lands_in_uncategorized(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"

    item = archive_generated_library_item(
        project_dir,
        item_id="tool-job-abc123",
        name="旅行照·视频去水印",
        media="video",
        url="/static/admin/58/freezone/video/erase/out.mp4",
    )

    assert item is not None
    assert item["folder"] == "other"
    assert item["video_url"] == "/static/admin/58/freezone/video/erase/out.mp4"
    stored = load_video_character_library(project_dir)
    assert [it["id"] for it in stored] == ["tool-job-abc123"]
    assert [it["name"] for it in stored] == ["旅行照·视频去水印"]


def test_archive_audio_item_carries_audio_tag(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"

    item = archive_generated_library_item(
        project_dir,
        item_id="tool-job-voice",
        name="第一句台词·生成语音",
        media="audio",
        url="/static/admin/58/freezone/audio/speech/a.mp3",
    )

    assert item is not None
    assert item["folder"] == "other"
    assert item["category"] == "audio"
    assert item["audio_url"] == "/static/admin/58/freezone/audio/speech/a.mp3"


def test_archive_same_job_updates_instead_of_duplicating(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"

    archive_generated_library_item(
        project_dir,
        item_id="tool-job-1",
        name="A",
        media="image",
        url="/static/a.png",
    )
    archive_generated_library_item(
        project_dir,
        item_id="tool-job-1",
        name="B",
        media="image",
        url="/static/b.png",
    )

    stored = load_video_character_library(project_dir)
    assert len(stored) == 1
    assert stored[0]["name"] == "B"
    assert stored[0]["image_urls"] == ["/static/b.png"]


def test_tool_archive_helper_honours_payload_flag(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    ctx = SimpleNamespace(output_dir=str(project_dir))
    result: dict = {}

    # 不带标记（画布等入口）：不写库、不写任务结果标记。
    skipped = _archive_tool_output_to_library(
        ctx=ctx,
        payload=_payload(project_dir),
        task_type="freezone_edit",
        job_id="job-abc123",
        media="image",
        url="/static/x.png",
        result=result,
    )
    assert skipped is None
    assert result == {}
    assert load_video_character_library(project_dir) == []

    # 带标记（工具页批量任务）：入库 + 任务结果写入归档证明。
    archived = _archive_tool_output_to_library(
        ctx=ctx,
        payload=_payload(project_dir, archive_to_library=True),
        task_type="freezone_edit",
        job_id="job-abc123",
        media="image",
        url="/static/x.png",
        result=result,
    )
    assert archived is not None
    assert result["archived_to_library"] is True
    assert result["library_item_id"] == f"{TOOL_LIBRARY_ITEM_ID_PREFIX}job-abc123"
    stored = load_video_character_library(project_dir)
    assert len(stored) == 1
    assert stored[0]["folder"] == "other"


def test_tool_archive_helper_names_item_from_source_stem(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    ctx = SimpleNamespace(output_dir=str(project_dir))

    _archive_tool_output_to_library(
        ctx=ctx,
        payload=_payload(project_dir, archive_to_library=True),
        task_type="freezone_video_erase",
        job_id="job-abc123",
        media="video",
        url="/static/out.mp4",
        result={},
        stem="旅行照",
    )

    stored = load_video_character_library(project_dir)
    assert [it["name"] for it in stored] == ["旅行照·视频去水印"]


def test_tool_archive_helper_falls_back_to_job_suffix_without_stem(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    ctx = SimpleNamespace(output_dir=str(project_dir))

    _archive_tool_output_to_library(
        ctx=ctx,
        payload=_payload(project_dir, archive_to_library=True),
        task_type="freezone_audio_speech",
        job_id="job-xyz789",
        media="audio",
        url="/static/a.mp3",
        result={},
        stem="",
    )

    stored = load_video_character_library(project_dir)
    assert [it["name"] for it in stored] == ["生成语音 xyz789"]


def test_tool_archive_failure_keeps_task_result_clean(tmp_path: Path) -> None:
    """登记失败不能翻车：库写不进去（比如路径被占位成文件）时标记也不能写。"""

    project_dir = tmp_path / "project"
    freezone_dir = project_dir / "freezone"
    freezone_dir.mkdir(parents=True)
    # 把库文件路径占成目录，save 时必然失败。
    (freezone_dir / "video_character_library.json").mkdir()

    ctx = SimpleNamespace(output_dir=str(project_dir))
    result: dict = {}

    archived = _archive_tool_output_to_library(
        ctx=ctx,
        payload=_payload(project_dir, archive_to_library=True),
        task_type="freezone_edit",
        job_id="job-abc123",
        media="image",
        url="/static/x.png",
        result=result,
    )

    assert archived is None
    assert result == {}