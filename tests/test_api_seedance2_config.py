import pytest


pytestmark = pytest.mark.m09


class _FakeStore:
    def __init__(self):
        self.updated = None

    async def get_script_as_dict(self, episode: int):
        assert episode == 3
        return {
            "beats": [
                {
                    "beat_number": 2,
                    "video_prompt": "old",
                    "seedance2_config_json": "{}",
                }
            ]
        }

    async def update_beat_asset(self, **kwargs):
        self.updated = kwargs
        return True


@pytest.mark.asyncio
async def test_update_beat_accepts_seedance2_config_json(monkeypatch, tmp_path):
    from novelvideo.api import deps
    from novelvideo.api.routes import scripts
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.schemas import BeatUpdate

    store = _FakeStore()

    async def fake_make_sqlite_store(username, project):
        assert username == "alice"
        assert project == "demo"
        return store

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(scripts, "resolve_project_scope", fake_resolve_project_scope)
    monkeypatch.setattr(deps, "make_sqlite_store", fake_make_sqlite_store)

    response = await scripts.update_beat(
        project="demo",
        episode_num=3,
        beat_num=2,
        body=BeatUpdate(
            seedance2_config_json='{"final_prompt":"参考图片1生成视频。"}',
        ),
        user={"username": "alice"},
    )

    assert response["ok"] is True
    assert response["data"]["seedance2_config_json"] == (
        '{"final_prompt":"参考图片1生成视频。"}'
    )
    assert store.updated["seedance2_config_json"] == (
        '{"final_prompt":"参考图片1生成视频。"}'
    )
