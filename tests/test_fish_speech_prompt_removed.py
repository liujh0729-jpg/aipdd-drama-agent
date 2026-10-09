from pathlib import Path


def test_fish_speech_prompt_field_and_agent_are_removed() -> None:
    sources = {
        "api schemas": Path("src/novelvideo/api/schemas.py").read_text(encoding="utf-8"),
        "script routes": Path("src/novelvideo/api/routes/scripts.py").read_text(encoding="utf-8"),
        "sqlite store": Path("src/novelvideo/sqlite_store.py").read_text(encoding="utf-8"),
        "models": Path("src/novelvideo/models.py").read_text(encoding="utf-8"),
        "cognee store": Path("src/novelvideo/cognee/store.py").read_text(encoding="utf-8"),
        "manual shots": Path("src/novelvideo/manual_shots.py").read_text(encoding="utf-8"),
        "sketch edit tasks": Path("src/novelvideo/verification/sketch_edit_tasks.py").read_text(
            encoding="utf-8"
        ),
    }

    for name, source in sources.items():
        assert "fish_speech_prompt" not in source, name
        assert "build_fish_speech_prompt" not in source, name

    assert not Path("src/novelvideo/agents/fish_speech_prompt_builder.py").exists()


def test_fish_audio_tts_path_is_removed() -> None:
    tts_generator = Path("src/novelvideo/generators/tts_generator.py").read_text(encoding="utf-8")

    assert "class FishAudioTTSGenerator" not in tts_generator
