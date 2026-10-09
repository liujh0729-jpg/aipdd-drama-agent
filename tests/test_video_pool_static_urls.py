from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.models import VideoPoolEntry, VideoPoolIndex
from novelvideo.project_context import ProjectContext


pytestmark = pytest.mark.m09


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_video_123",
        project_name="demo",
        owner_type="user",
        owner_id="user_owner",
        owner_username="alice",
        requester_user_id="user_editor",
        requester_username="bob",
        requester_principals=(("user", "user_editor"),),
        effective_role="editor",
        home_node_id="local",
        output_dir=tmp_path / "output" / "alice" / "demo",
        state_dir=tmp_path / "state" / "alice" / "demo",
        runtime_dir=tmp_path / "runtime" / "alice" / "demo",
        is_home_node=True,
    )


def _configure_state_roots(monkeypatch: pytest.MonkeyPatch, ctx: ProjectContext) -> None:
    from novelvideo.utils import state_index_files

    monkeypatch.setattr(state_index_files, "OUTPUT_DIR", str(Path(ctx.output_dir).parents[1]))
    monkeypatch.setattr(state_index_files, "STATE_DIR", str(Path(ctx.state_dir).parents[1]))


def _write_video_pool(ctx: ProjectContext, episode: int = 1) -> tuple[Path, VideoPoolEntry]:
    from novelvideo.generators.video_pool_indexer import save_video_pool_index

    videos_ep_dir = Path(ctx.output_dir) / "videos" / "beats" / f"ep{episode:03d}"
    pool_dir = videos_ep_dir / "pool"
    pool_dir.mkdir(parents=True, exist_ok=True)
    entry = VideoPoolEntry(
        id="beat_06_20260529_120000",
        beat_num=6,
        video_path="beat_06_20260529_120000.mp4",
        generated_at=datetime(2026, 5, 29, 12, 0, 0),
        duration=5.0,
        video_mode="first_frame",
        backend="seedance2",
        prompt="test",
    )
    (pool_dir / entry.video_path).write_bytes(b"mp4")
    save_video_pool_index(
        VideoPoolIndex(episode=episode, videos=[entry], beat_assignments={"6": entry.id}),
        videos_ep_dir,
    )
    return videos_ep_dir, entry


async def _fake_resolve(
    ctx: ProjectContext,
    project: str,
    user: dict,
    required_role: str = "editor",
):
    return SimpleNamespace(
        ctx=ctx,
        username=ctx.owner_username,
        project_name=ctx.project_name,
        project_dir=Path(ctx.output_dir),
    )


@pytest.mark.asyncio
async def test_video_pool_list_returns_project_id_static_urls(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation

    ctx = _ctx(tmp_path)
    _configure_state_roots(monkeypatch, ctx)
    _write_video_pool(ctx)

    async def fake_resolve(project: str, user: dict, required_role: str = "editor"):
        return await _fake_resolve(ctx, project, user, required_role)

    monkeypatch.setattr(generation, "_resolve_generation_project", fake_resolve)

    response = await generation.list_video_pool("proj_video_123", 1, user={"id": "user_editor"})

    video_url = response["data"]["videos"][0]["video_url"]
    assert video_url.startswith(
        "/static/projects/proj_video_123/videos/beats/ep001/pool/"
        "beat_06_20260529_120000.mp4?v="
    )
    assert "/static/alice/demo/" not in video_url


@pytest.mark.asyncio
async def test_video_pool_select_returns_project_id_static_url(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation
    from novelvideo.api.schemas import VideoPoolSelectRequest

    ctx = _ctx(tmp_path)
    _configure_state_roots(monkeypatch, ctx)
    videos_ep_dir, entry = _write_video_pool(ctx)

    async def fake_resolve(project: str, user: dict, required_role: str = "editor"):
        return await _fake_resolve(ctx, project, user, required_role)

    monkeypatch.setattr(generation, "_resolve_generation_project", fake_resolve)

    response = await generation.select_video_pool(
        "proj_video_123",
        1,
        6,
        VideoPoolSelectRequest(pool_id=entry.id),
        user={"id": "user_editor"},
    )

    assert (videos_ep_dir / "beat_06.mp4").exists()
    video_url = response["data"]["video_url"]
    assert video_url.startswith("/static/projects/proj_video_123/videos/beats/ep001/beat_06.mp4?v=")
    assert "/static/alice/demo/" not in video_url
