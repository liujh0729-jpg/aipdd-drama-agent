from __future__ import annotations

import pytest

from novelvideo.cognee import CogneeStore
from novelvideo.models import NovelEpisode


@pytest.mark.asyncio
async def test_update_episode_preserves_asset_menus_written_by_parallel_task(tmp_path):
    output_dir = tmp_path / "output" / "admin" / "parallel"
    state_dir = tmp_path / "state" / "admin" / "parallel"
    output_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    identity_store = CogneeStore(
        "admin/parallel",
        output_dir=str(output_dir),
        state_dir=str(state_dir),
    )
    asset_store = CogneeStore(
        "admin/parallel",
        output_dir=str(output_dir),
        state_dir=str(state_dir),
    )
    fresh_store = CogneeStore(
        "admin/parallel",
        output_dir=str(output_dir),
        state_dir=str(state_dir),
    )
    try:
        await identity_store.sqlite_store.initialize()
        await asset_store.sqlite_store.initialize()
        await fresh_store.sqlite_store.initialize()

        await identity_store.add_episodes([NovelEpisode(number=1, title="第一集")])
        await identity_store.load_graph_state()
        await asset_store.load_graph_state()
        await asset_store.update_episode(
            1,
            scene_menu=[{"scene_id": "京城街道", "variants": []}],
            prop_menu=[{"prop_id": "烧鸡", "prop_type": "accessory"}],
        )

        await identity_store.update_episode(1, identity_ids=["南珍_将军夫人时期"])

        await fresh_store.load_graph_state()
        episode = fresh_store.get_episode(1)
        assert episode is not None
        assert [item.scene_id for item in episode.scene_menu] == ["京城街道"]
        assert not hasattr(episode.scene_menu[0], "variants")
        assert [item.prop_id for item in episode.prop_menu] == ["烧鸡"]
        assert episode.identity_ids == ["南珍_将军夫人时期"]
    finally:
        await identity_store.close()
        await asset_store.close()
        await fresh_store.close()
