from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.m09


class _FakeTaskManager:
    def __init__(self):
        self.updates = []

    def update_progress_for_project(self, *args, **kwargs):
        self.updates.append((args, kwargs))


def _ctx(tmp_path):
    return SimpleNamespace(output_dir=tmp_path)


def _write_beat_video(project_dir, episode: int, beat_num: int) -> None:
    video_dir = project_dir / "videos" / "beats" / f"ep{episode:03d}"
    video_dir.mkdir(parents=True)
    (video_dir / f"beat_{beat_num:02d}.mp4").write_bytes(b"video")


def test_compose_episode_preserves_embedded_audio_when_no_external_mp3(
    monkeypatch,
    tmp_path,
):
    from novelvideo.task_backend.runners import video

    _write_beat_video(tmp_path, episode=1, beat_num=3)
    manager = _FakeTaskManager()
    commands = []

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video, "get_task_manager", lambda: manager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    result = video.run_compose_episode(
        {
            "episode": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "beats": [{"beat_number": 3}],
            },
        },
        _ctx(tmp_path),
    )

    clip_cmd = next(cmd for cmd in commands if cmd[0] == "ffmpeg" and "beat_0003.mp4" in cmd[-1])
    assert result["video_path"].endswith("videos/episodes/ep001_final.mp4")
    assert "anullsrc=r=44100:cl=stereo" not in clip_cmd
    assert clip_cmd[clip_cmd.index("-map") + 1] == "0:v:0"
    assert "0:a:0" in clip_cmd
    assert any(
        "使用视频内置音轨" in line
        for _args, kwargs in manager.updates
        for line in kwargs.get("logs", [])
    )
