"""C5 (A4) tests: narration_style + narrator_reference_audio round-trip.

Schema follows source-branch contract (3 top-level keys, caller-supplied
``updated_at``). See ``test_project_tts_config.py`` in
``origin/docs/seedance2-api-comparison`` for a parallel pattern.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def narration_env(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    output_dir = tmp_path / "output"
    state_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setenv("NOVELVIDEO_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(state_dir))
    monkeypatch.setenv("NOVELVIDEO_OUTPUT_DIR", str(output_dir))

    import importlib
    import novelvideo.config as cfg
    import novelvideo.utils.project_paths as pp
    import novelvideo.project_config as pc

    importlib.reload(cfg)
    importlib.reload(pp)
    importlib.reload(pc)
    return pc


def test_default_narration_style_is_third_person(narration_env):
    pc = narration_env
    assert pc.load_narration_style("alice", "novel-x") == "third_person"


def test_invalid_narration_style_falls_back_to_default(narration_env):
    pc = narration_env
    pc.update_project_config_file(
        "alice",
        "novel-x",
        lambda config: config.update({pc.NARRATION_STYLE_KEY: "narrator_god_view"}),
    )
    assert pc.load_narration_style("alice", "novel-x") == "third_person"


def test_third_person_narration_style_round_trip(narration_env):
    pc = narration_env
    pc.update_project_config_file(
        "alice",
        "novel-x",
        lambda config: config.update({pc.NARRATION_STYLE_KEY: "third_person"}),
    )
    assert pc.load_narration_style("alice", "novel-x") == "third_person"


def test_default_narrator_reference_audio_is_empty(narration_env):
    pc = narration_env
    assert pc.load_narrator_reference_audio("alice", "novel-x") == {
        "path": "",
        "sha256": "",
        "updated_at": "",
    }


def test_set_and_load_narrator_reference_audio_with_explicit_updated_at(narration_env):
    pc = narration_env
    pc.set_narrator_reference_audio(
        "alice",
        "novel-x",
        relative_path="assets/narrator/voice_default.mp3",
        sha256="abc123",
        updated_at="2026-05-12T00:00:00+00:00",
    )
    rec = pc.load_narrator_reference_audio("alice", "novel-x")
    assert rec == {
        "path": "assets/narrator/voice_default.mp3",
        "sha256": "abc123",
        "updated_at": "2026-05-12T00:00:00+00:00",
    }


def test_set_narrator_reference_audio_auto_stamps_updated_at_when_omitted(narration_env):
    pc = narration_env
    pc.set_narrator_reference_audio(
        "alice", "novel-x", relative_path="a.mp3", sha256="aaa",
    )
    rec = pc.load_narrator_reference_audio("alice", "novel-x")
    assert rec["updated_at"]  # ISO timestamp not empty


def test_set_narrator_reference_audio_overwrites_previous(narration_env):
    pc = narration_env
    pc.set_narrator_reference_audio(
        "alice", "novel-x", relative_path="a.mp3", sha256="aaa", updated_at="t1",
    )
    pc.set_narrator_reference_audio(
        "alice", "novel-x", relative_path="b.mp3", sha256="bbb", updated_at="t2",
    )
    rec = pc.load_narrator_reference_audio("alice", "novel-x")
    assert rec == {"path": "b.mp3", "sha256": "bbb", "updated_at": "t2"}


def test_indextts2_beat_audio_task_module_imports():
    """C5 unblocks top-level import of indextts2_beat_audio_task."""
    import importlib

    mod = importlib.import_module("novelvideo.audio.indextts2_beat_audio_task")
    assert hasattr(mod, "run_indextts2_beat_audio_generation")


def test_indextts2_config_constants_exposed():
    from novelvideo import config as cfg

    assert hasattr(cfg, "INDEXTTS2_TIMEOUT_SECONDS")
    assert isinstance(cfg.INDEXTTS2_TIMEOUT_SECONDS, float)


def test_output_dir_alias_present_for_monkeypatching():
    """Source-branch tests monkeypatch ``project_config.OUTPUT_DIR`` to redirect roots."""
    import novelvideo.project_config as pc

    assert hasattr(pc, "OUTPUT_DIR")
