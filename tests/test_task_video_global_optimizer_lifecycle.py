from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.project_context import ProjectContext


def _project_ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_123",
        project_name="demo",
        owner_type="user",
        owner_id="user_owner",
        owner_username="admin",
        requester_user_id="user_editor",
        requester_username="admin",
        requester_principals=(("user", "user_editor"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


@pytest.mark.asyncio
async def test_global_optimize_video_closes_cognee_store_on_success(monkeypatch, tmp_path):
    from novelvideo import cognee
    from novelvideo.agents import global_video_optimizer
    from novelvideo.task_backend.runners import video
    from novelvideo.utils.path_resolver import PathResolver

    sketch_path = PathResolver(str(tmp_path), 1).sketch(1)
    sketch_path.parent.mkdir(parents=True, exist_ok=True)
    sketch_path.write_bytes(b"fake-png")

    calls: list[str] = []

    class FakeTaskManager:
        def update_progress_for_project(self, *args, **kwargs):
            return None

    class FakeCogneeStore:
        def __init__(self, *args, **kwargs):
            calls.append("init")

        async def initialize(self):
            calls.append("initialize")

        async def load_graph_state(self):
            calls.append("load_graph_state")

        async def update_beat_asset(self, **kwargs):
            calls.append("update_beat_asset")
            return True

        async def close(self):
            calls.append("close")

    class FakeOptimizer:
        async def optimize_single_beat(self, **kwargs):
            return {"prompt": "optimized prompt"}

    monkeypatch.setattr(video, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(cognee, "CogneeStore", FakeCogneeStore)
    monkeypatch.setattr(
        global_video_optimizer,
        "prepare_global_optimizer_input",
        lambda **kwargs: ([str(sketch_path)], {}, 1),
    )
    monkeypatch.setattr(
        global_video_optimizer,
        "get_global_video_optimizer",
        lambda: FakeOptimizer(),
    )

    result = await video._run_global_optimize_video_async(
        {
            "episode": 1,
            "payload": {
                "episode": 1,
                "beats": [{"beat_number": 1, "visual_description": "frame"}],
                "characters": [],
                "output_dir": str(tmp_path),
            },
        },
        _project_ctx(tmp_path),
    )

    assert result["optimized"] == 1
    assert calls == [
        "init",
        "initialize",
        "load_graph_state",
        "update_beat_asset",
        "close",
    ]


@pytest.mark.asyncio
async def test_global_optimize_video_closes_cognee_store_on_failure(monkeypatch, tmp_path):
    from novelvideo import cognee
    from novelvideo.agents import global_video_optimizer
    from novelvideo.task_backend.runners import video
    from novelvideo.utils.path_resolver import PathResolver

    sketch_path = PathResolver(str(tmp_path), 1).sketch(1)
    sketch_path.parent.mkdir(parents=True, exist_ok=True)
    sketch_path.write_bytes(b"fake-png")

    calls: list[str] = []

    class FakeTaskManager:
        def update_progress_for_project(self, *args, **kwargs):
            return None

    class FakeCogneeStore:
        def __init__(self, *args, **kwargs):
            calls.append("init")

        async def initialize(self):
            calls.append("initialize")

        async def load_graph_state(self):
            calls.append("load_graph_state")

        async def close(self):
            calls.append("close")

    class FakeOptimizer:
        async def optimize_single_beat(self, **kwargs):
            calls.append("optimize_single_beat")
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(video, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(cognee, "CogneeStore", FakeCogneeStore)
    monkeypatch.setattr(
        global_video_optimizer,
        "prepare_global_optimizer_input",
        lambda **kwargs: ([str(sketch_path)], {}, 1),
    )
    monkeypatch.setattr(
        global_video_optimizer,
        "get_global_video_optimizer",
        lambda: FakeOptimizer(),
    )

    with pytest.raises(RuntimeError, match="model unavailable"):
        await video._run_global_optimize_video_async(
            {
                "episode": 1,
                "payload": {
                    "episode": 1,
                    "beats": [{"beat_number": 1, "visual_description": "frame"}],
                    "characters": [],
                    "output_dir": str(tmp_path),
                },
            },
            _project_ctx(tmp_path),
        )

    assert calls == [
        "init",
        "initialize",
        "load_graph_state",
        "optimize_single_beat",
        "close",
    ]
