from __future__ import annotations

import json

from novelvideo.models import PoolIndex, VideoPoolIndex


def _configure_roots(monkeypatch, tmp_path):
    from novelvideo.utils import state_index_files

    output_root = tmp_path / "output"
    state_root = tmp_path / "state"
    monkeypatch.setattr(state_index_files, "OUTPUT_DIR", str(output_root))
    monkeypatch.setattr(state_index_files, "STATE_DIR", str(state_root))
    return output_root, state_root


def test_save_pool_index_writes_state_sidecar_not_output(monkeypatch, tmp_path):
    output_root, state_root = _configure_roots(monkeypatch, tmp_path)
    from novelvideo.generators.pool_indexer import save_pool_index

    grids_dir = output_root / "admin" / "demo" / "grids" / "ep001"
    grids_dir.mkdir(parents=True)
    pool = PoolIndex(episode=1, beat_assignments={"1": "render/beat_01.png"})

    saved_path = save_pool_index(pool, grids_dir)

    expected_path = state_root / "admin" / "demo" / "grids" / "ep001" / "pool_index.json"
    assert saved_path == expected_path
    assert expected_path.exists()
    assert not (grids_dir / "pool_index.json").exists()
    payload = json.loads(expected_path.read_text(encoding="utf-8"))
    assert payload["beat_assignments"] == {"1": "render/beat_01.png"}


def test_load_pool_index_lazily_moves_legacy_output_sidecar(monkeypatch, tmp_path):
    output_root, state_root = _configure_roots(monkeypatch, tmp_path)
    from novelvideo.generators.pool_indexer import load_pool_index

    grids_dir = output_root / "admin" / "demo" / "grids" / "ep001"
    grids_dir.mkdir(parents=True)
    legacy_path = grids_dir / "pool_index.json"
    legacy_path.write_text(
        json.dumps(
            {
                "episode": 1,
                "generated_at": "2026-01-01T00:00:00",
                "version": 2,
                "modes": {},
                "grids": [],
                "images": [],
                "beat_assignments": {"1": "render/beat_01.png"},
            }
        ),
        encoding="utf-8",
    )

    pool = load_pool_index(grids_dir)

    state_path = state_root / "admin" / "demo" / "grids" / "ep001" / "pool_index.json"
    assert pool is not None
    assert pool.beat_assignments == {"1": "render/beat_01.png"}
    assert state_path.exists()
    assert not legacy_path.exists()


def test_save_video_pool_index_writes_state_sidecar_not_output(monkeypatch, tmp_path):
    output_root, state_root = _configure_roots(monkeypatch, tmp_path)
    from novelvideo.generators.video_pool_indexer import save_video_pool_index

    videos_ep_dir = output_root / "admin" / "demo" / "videos" / "beats" / "ep001"
    videos_ep_dir.mkdir(parents=True)
    pool = VideoPoolIndex(episode=1, beat_assignments={"1": "beat_01_20260101_000000"})

    save_video_pool_index(pool, videos_ep_dir)

    expected_path = (
        state_root / "admin" / "demo" / "videos" / "beats" / "ep001" / "video_pool_index.json"
    )
    assert expected_path.exists()
    assert not (videos_ep_dir / "video_pool_index.json").exists()
    payload = json.loads(expected_path.read_text(encoding="utf-8"))
    assert payload["beat_assignments"] == {"1": "beat_01_20260101_000000"}


def test_load_video_pool_index_lazily_moves_legacy_output_sidecar(monkeypatch, tmp_path):
    output_root, state_root = _configure_roots(monkeypatch, tmp_path)
    from novelvideo.generators.video_pool_indexer import load_video_pool_index

    videos_ep_dir = output_root / "admin" / "demo" / "videos" / "beats" / "ep001"
    videos_ep_dir.mkdir(parents=True)
    legacy_path = videos_ep_dir / "video_pool_index.json"
    legacy_path.write_text(
        json.dumps(
            {
                "episode": 1,
                "generated_at": "2026-01-01T00:00:00",
                "videos": [],
                "beat_assignments": {"1": "beat_01_20260101_000000"},
            }
        ),
        encoding="utf-8",
    )

    pool = load_video_pool_index(videos_ep_dir)

    state_path = (
        state_root / "admin" / "demo" / "videos" / "beats" / "ep001" / "video_pool_index.json"
    )
    assert pool is not None
    assert pool.beat_assignments == {"1": "beat_01_20260101_000000"}
    assert state_path.exists()
    assert not legacy_path.exists()


def test_add_video_to_pool_keeps_media_in_output_and_index_in_state(monkeypatch, tmp_path):
    output_root, state_root = _configure_roots(monkeypatch, tmp_path)
    from novelvideo.generators.video_pool_indexer import add_video_to_pool

    videos_ep_dir = output_root / "admin" / "demo" / "videos" / "beats" / "ep001"
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video")

    entry = add_video_to_pool(
        videos_ep_dir=videos_ep_dir,
        episode=1,
        beat_num=2,
        source_video_path=source_video,
    )

    assert (videos_ep_dir / "pool" / entry.video_path).exists()
    output_index = videos_ep_dir / "video_pool_index.json"
    state_index = (
        state_root / "admin" / "demo" / "videos" / "beats" / "ep001" / "video_pool_index.json"
    )
    assert not output_index.exists()
    payload = json.loads(state_index.read_text(encoding="utf-8"))
    assert payload["beat_assignments"] == {"2": entry.id}
