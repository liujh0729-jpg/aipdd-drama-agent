import pytest


pytestmark = pytest.mark.m09


async def test_update_beat_asset_persists_seedance2_config_json(tmp_path):
    from novelvideo.models import NovelEpisode, NovelVisualBeat
    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore("user/project", output_dir=str(tmp_path / "out"), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="Ep 1"))
    await store.add_visual_beats(
        [
            NovelVisualBeat(
                episode_number=1,
                beat_number=1,
                narration="n",
                visual_description="v",
            )
        ],
    )

    ok = await store.update_beat_asset(
        episode_number=1,
        beat_number=1,
        seedance2_config_json='{"final_prompt":"参考图片1生成视频。"}',
    )
    beats = await store.get_beats_as_dicts(1)

    assert ok is True
    assert beats[0]["seedance2_config_json"] == '{"final_prompt":"参考图片1生成视频。"}'
