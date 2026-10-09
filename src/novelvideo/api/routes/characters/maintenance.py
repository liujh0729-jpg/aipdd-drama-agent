"""characters.maintenance 子模块（自 api/routes/characters.py 拆分）。"""

from pathlib import Path

from novelvideo.sqlite_store import SQLiteStore
from novelvideo.utils.asset_names import move_asset_dir



async def _unset_other_main_characters(store: SQLiteStore, name: str) -> None:
    """Keep the project on the same single narrator-main semantics as NiceGUI."""
    for character in store.get_all_characters():
        if character.name != name and getattr(character, "is_main", False):
            await store.update_character(character.name, is_main=False)


async def _repair_duplicate_main_characters(
    store: SQLiteStore, characters: list
) -> list:
    """Repair legacy data that still has multiple narrator-main characters."""
    seen_main = False
    repaired = []
    for character in characters:
        if not getattr(character, "is_main", False):
            repaired.append(character)
            continue
        if not seen_main:
            seen_main = True
            repaired.append(character)
            continue
        set_main = getattr(store, "set_character_main", None)
        if set_main is not None:
            if not await set_main(character.name, False):
                raise RuntimeError(
                    f"Failed to repair duplicate main character: {character.name}"
                )
        else:
            # Store test doubles and older compatible facades retain the
            # object-merge operation. Real SQLiteStore uses the cache-free,
            # column-level branch above.
            await store.update_character(character.name, is_main=False)
        character.is_main = False
        repaired.append(character)
    return repaired


async def _heal_path_unsafe_character_names(
    store: SQLiteStore, project_dir: Path
) -> dict[str, str]:
    """修好库里名字带斜杠的存量角色，原名转成别名。

    ``NovelCharacter.sanitize_name`` 一直在挡新数据，但它是**读的时候**才生效：主键里
    留着斜杠的老行读出来名字已经是干净的，``DELETE ... WHERE name = ?`` 却一行都删不掉。
    和场景 / 道具同一个毛病，见 :mod:`novelvideo.utils.asset_names`。

    调用方要先过 ``may_run_asset_repair``：这是一次写操作，不该由只读协作者触发。
    """

    def move_assets(old_name: str, new_name: str) -> None:
        # 见 ``move_asset_dir``：old_name 是库里没消毒过的旧值，直接拼路径会爬出资产根。
        move_asset_dir(project_dir / "assets" / "characters", old_name, new_name)

    return await store.repair_path_unsafe_asset_names("character", move_assets)
