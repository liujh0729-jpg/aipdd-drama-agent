from __future__ import annotations

from novelvideo.models import PoolIndex


def _configure_roots(monkeypatch, tmp_path):
    from novelvideo.utils import state_index_files

    output_root = tmp_path / "output"
    state_root = tmp_path / "state"
    monkeypatch.setattr(state_index_files, "OUTPUT_DIR", str(output_root))
    monkeypatch.setattr(state_index_files, "STATE_DIR", str(state_root))
    return output_root, state_root


def test_build_pool_index_keeps_all_timestamped_candidates(tmp_path):
    from novelvideo.generators.pool_indexer import build_pool_index

    grids_dir = tmp_path / "grids" / "ep001"
    sketch_dir = grids_dir / "sketch"
    render_dir = grids_dir / "render"
    sketch_dir.mkdir(parents=True)
    render_dir.mkdir(parents=True)

    (sketch_dir / "beat_01_t20260101010101.png").write_bytes(b"sketch-old")
    (sketch_dir / "beat_01_t20260101020202.png").write_bytes(b"sketch-new")
    (render_dir / "beat_01_t20260101030303.png").write_bytes(b"render-old")
    (render_dir / "beat_01_t20260101040404.png").write_bytes(b"render-new")

    pool = build_pool_index(grids_dir, episode=1)

    assert [img.id for img in pool.images] == [
        "render_beat_01_t20260101030303",
        "render_beat_01_t20260101040404",
        "sketch_beat_01_t20260101010101",
        "sketch_beat_01_t20260101020202",
    ]
    assert pool.modes["render"]["total_cells"] == 2
    assert pool.modes["sketch"]["total_cells"] == 2


def test_rebuild_pool_index_preserves_existing_assignments(monkeypatch, tmp_path):
    output_root, _state_root = _configure_roots(monkeypatch, tmp_path)
    from novelvideo.generators.pool_indexer import rebuild_pool_index, save_pool_index

    grids_dir = output_root / "admin" / "demo" / "grids" / "ep001"
    sketch_dir = grids_dir / "sketch"
    sketch_dir.mkdir(parents=True)
    (sketch_dir / "beat_01_t20260101010101.png").write_bytes(b"sketch-old")
    (sketch_dir / "beat_01_t20260101020202.png").write_bytes(b"sketch-new")

    save_pool_index(
        PoolIndex(
            episode=1,
            beat_assignments={
                # Existing generated entries use this ID shape; rebuilt entries
                # use the legacy rebuild shape but point at the same file.
                "1": "beat_01_t20260101010101_sketch",
            },
        ),
        grids_dir,
    )

    pool = rebuild_pool_index(grids_dir, episode=1)

    assert [img.id for img in pool.images] == [
        "sketch_beat_01_t20260101010101",
        "sketch_beat_01_t20260101020202",
    ]
    assert pool.beat_assignments == {"1": "sketch_beat_01_t20260101010101"}
