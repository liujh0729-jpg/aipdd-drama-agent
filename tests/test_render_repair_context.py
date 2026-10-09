from pathlib import Path

import pytest
from PIL import Image

from novelvideo.task_identity import selection_scope
from novelvideo.verification.render_repair_context import (
    build_render_repair_context,
    collect_active_render_entries,
    compute_missing_render_beats,
    prepare_render_regen_task_plan,
)


pytestmark = pytest.mark.m09


def test_collect_active_render_entries_reads_frames_and_selected_sketches(tmp_path: Path):
    project_dir = tmp_path / "demo"
    frames_dir = project_dir / "frames" / "ep001"
    sketches_dir = project_dir / "sketches" / "ep001"
    frames_dir.mkdir(parents=True)
    sketches_dir.mkdir(parents=True)
    Image.new("RGB", (32, 32), color="white").save(frames_dir / "beat_19.png")
    Image.new("RGB", (32, 32), color="black").save(sketches_dir / "beat_19.png")

    beats = [{"beat_number": 19, "visual_description": "close-up lower face"}]

    rows = collect_active_render_entries(project_dir, 1, beats)

    assert len(rows) == 1
    assert rows[0].beat_number == 19
    assert rows[0].frame_path.name == "beat_19.png"
    assert rows[0].sketch_path.name == "beat_19.png"


def test_compute_missing_render_beats_reports_beats_without_active_frames(tmp_path: Path):
    project_dir = tmp_path / "demo"
    frames_dir = project_dir / "frames" / "ep001"
    frames_dir.mkdir(parents=True)
    Image.new("RGB", (32, 32), color="white").save(frames_dir / "beat_02.png")
    Image.new("RGB", (32, 32), color="white").save(frames_dir / "beat_04.png")

    beats = [
        {"beat_number": 1},
        {"beat_number": 2},
        {"beat_number": 3},
        {"beat_number": 4},
    ]
    entries = collect_active_render_entries(project_dir, 1, beats)

    assert compute_missing_render_beats(beats, entries) == [1, 3]


def test_build_render_repair_context_writes_run_artifacts(tmp_path: Path, monkeypatch):
    project_dir = tmp_path / "demo"
    frames_dir = project_dir / "frames" / "ep001"
    sketches_dir = project_dir / "sketches" / "ep001"
    frames_dir.mkdir(parents=True)
    sketches_dir.mkdir(parents=True)
    Image.new("RGB", (64, 64), color="white").save(frames_dir / "beat_19.png")
    Image.new("RGB", (64, 64), color="black").save(sketches_dir / "beat_19.png")
    monkeypatch.setattr(
        "novelvideo.verification.render_repair_context.load_script_payload",
        lambda _project_dir, _episode_num: {
            "beats": [{"beat_number": 19, "visual_description": "close-up lower face"}]
        },
    )

    summary = build_render_repair_context(project_dir=project_dir, episode_num=1)

    run_dir = project_dir / "verify_reports" / "ep001" / "render_repair_run"
    assert summary["beat_count"] == 1
    assert (run_dir / "render_repair_context.json").exists()
    assert (run_dir / "overview_grid.jpg").exists()
    assert (run_dir / "compressed" / "frame_19.jpg").exists()
    assert (run_dir / "compressed" / "sketch_19.jpg").exists()


def test_prepare_render_regen_task_plan_blocks_beats_without_render_identities(
    tmp_path: Path, monkeypatch
):
    project_dir = tmp_path / "demo"
    frames_dir = project_dir / "frames" / "ep001"
    sketches_dir = project_dir / "sketches" / "ep001"
    frames_dir.mkdir(parents=True)
    sketches_dir.mkdir(parents=True)
    Image.new("RGB", (64, 64), color="white").save(frames_dir / "beat_19.png")
    Image.new("RGB", (64, 64), color="black").save(sketches_dir / "beat_19.png")
    Image.new("RGB", (64, 64), color="white").save(frames_dir / "beat_20.png")
    Image.new("RGB", (64, 64), color="black").save(sketches_dir / "beat_20.png")
    monkeypatch.setattr(
        "novelvideo.verification.render_repair_context.load_script_payload",
        lambda _project_dir, _episode_num: {
            "beats": [
                {
                    "beat_number": 19,
                    "visual_description": "close-up",
                    "detected_identities": ["A_1"],
                },
                {
                    "beat_number": 20,
                    "visual_description": "wide shot",
                    "detected_identities": [],
                },
            ]
        },
    )

    build_render_repair_context(project_dir=project_dir, episode_num=1)

    plan = prepare_render_regen_task_plan(
        project_dir=project_dir,
        episode_num=1,
        bad_beat_numbers=[19, 20],
    )

    assert plan["blocked_beats"] == [
        {
            "beat_number": 20,
            "render_reference_ready": False,
            "detected_identities": [],
            "reasons": [
                {
                    "code": "render_reference_not_ready",
                    "message": "No detected identities available for render preflight",
                }
            ],
        }
    ]
    assert plan["executable_batches"] == [
        {
            "batch_index": 1,
            "mode_key": "1x1_2-3",
            "beat_indices": [19],
            "scope": selection_scope("1x1_2-3", [19]),
        }
    ]
