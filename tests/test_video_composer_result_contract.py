from pathlib import Path

import pytest

from novelvideo.generators.video_composer import SceneAsset, VideoComposer


@pytest.mark.asyncio
async def test_compose_episode_reports_failure_when_title_card_fails(tmp_path: Path) -> None:
    class ComposerWithBrokenTitle(VideoComposer):
        async def _create_scene_video(self, scene, output_path, effect=None):
            Path(output_path).write_bytes(b"scene")
            return True

        async def _create_title_card(self, title, output_path):
            return False

        async def _create_end_card(self, output_path):
            Path(output_path).write_bytes(b"end")
            return True

        async def _concat_videos(self, video_paths, output_path):
            Path(output_path).write_bytes(b"joined")
            return True

    result = await ComposerWithBrokenTitle().compose_episode(
        scenes=[
            SceneAsset(
                scene_number=1,
                image_path="image.png",
                audio_path="audio.mp3",
                duration_seconds=1.0,
            )
        ],
        output_path=str(tmp_path / "ep001.mp4"),
        title="line one\nline two",
    )

    assert result.success is False
    assert "title card" in (result.error or "")


@pytest.mark.asyncio
async def test_compose_episode_reports_failure_when_concat_does_not_write_output(
    tmp_path: Path,
) -> None:
    class ComposerWithBrokenConcat(VideoComposer):
        async def _create_scene_video(self, scene, output_path, effect=None):
            Path(output_path).write_bytes(b"scene")
            return True

        async def _create_title_card(self, title, output_path):
            Path(output_path).write_bytes(b"title")
            return True

        async def _create_end_card(self, output_path):
            Path(output_path).write_bytes(b"end")
            return True

        async def _concat_videos(self, video_paths, output_path):
            return True

    result = await ComposerWithBrokenConcat().compose_episode(
        scenes=[
            SceneAsset(
                scene_number=1,
                image_path="image.png",
                audio_path="audio.mp3",
                duration_seconds=1.0,
            )
        ],
        output_path=str(tmp_path / "ep001.mp4"),
        title="safe title",
    )

    assert result.success is False
    assert "output file" in (result.error or "")
