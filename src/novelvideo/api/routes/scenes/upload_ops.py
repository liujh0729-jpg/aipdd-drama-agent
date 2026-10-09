"""scenes.upload_ops 子模块（自 api/routes/scenes.py 拆分）。"""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

from fastapi import UploadFile

from novelvideo.api.routes import scenes as _scenes
from novelvideo.director_world import stage_manifest
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.utils.asset_names import move_asset_dir
from novelvideo.utils.upload_safety import MAX_PROJECT_UPLOAD_BYTES
from novelvideo.utils.upload_safety import create_staged_upload_file
from novelvideo.utils.upload_safety import stream_to_file_with_limit



def _copy_upload_to_temp_file(
    file: UploadFile,
    *,
    suffix: str,
    max_bytes: int = MAX_PROJECT_UPLOAD_BYTES,
) -> tuple[Path, int]:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            try:
                file.file.seek(0)
            except (AttributeError, OSError):
                pass
        size = stream_to_file_with_limit(file.file, tmp_path, max_bytes=max_bytes)
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path, size


def _persist_custom_scene_upload(
    file: UploadFile,
    *,
    suffix: str,
    project_dir: Path,
    scene_name: str,
) -> None:
    from novelvideo import stage_asset_tasks

    tmp_path: Path | None = None
    try:
        tmp_path, size = _scenes._copy_upload_to_temp_file(
            file,
            suffix=suffix,
            max_bytes=MAX_PROJECT_UPLOAD_BYTES,
        )
        if size == 0:
            raise _scenes._EmptyScenePackageError("Custom scene package is empty")
        stage_asset_tasks.upload_scene_package(project_dir, scene_name, tmp_path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _load_scene_upload_image(file: UploadFile):
    from PIL import Image

    try:
        try:
            file.file.seek(0)
        except (AttributeError, OSError):
            pass
        with Image.open(file.file) as source:
            return source.convert("RGB")
    except Exception as exc:
        raise _scenes._InvalidSceneImageError(str(exc)) from exc


def _publish_scene_image(image, target: Path, archive_stem: str) -> None:
    tmp_path: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = create_staged_upload_file(
            target.parent,
            prefix=f".{target.stem}_",
            suffix=target.suffix,
            destination=target,
        )
        image.save(tmp_path, format="PNG")
        if target.exists():
            target.replace(target.parent / f"{archive_stem}_{time.time_ns()}.png")
        tmp_path.replace(target)
        tmp_path = None
    finally:
        image.close()
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _persist_scene_master_upload(file: UploadFile, target: Path) -> None:
    image = _load_scene_upload_image(file)
    _publish_scene_image(image, target, "master")


def _persist_scene_pano_upload(
    file: UploadFile,
    *,
    project_dir: Path,
    scene_name: str,
) -> None:
    image = _load_scene_upload_image(file)
    width, height = image.size
    if height <= 0 or abs((width / height) - 2.0) > 0.08:
        image.close()
        raise _scenes._InvalidScenePanoRatioError(
            f"360 panorama must be close to 2:1 equirectangular; got {width}x{height}"
        )
    out_dir = stage_manifest.stage_dir(project_dir, scene_name)
    pano_path = out_dir / "pano_360.png"
    _publish_scene_image(image, pano_path, "pano_360")
    stage_manifest.update_manifest(
        project_dir,
        scene_name,
        clear_fields=[
            "ply_path",
            "collision_glb_path",
            "voxel_json_path",
            "pano_sharp_args",
            "splat_transform_args",
        ],
        pano_path=pano_path.name,
        source="uploaded_360",
    )


def _scene_upload_lock_key(project_dir: Path, scene_name: str) -> tuple[str, str]:
    stage_dir = stage_manifest.stage_dir(project_dir, scene_name)
    return "scene-stage", str(stage_dir.resolve())


def _delete_scene_pano_files(project_dir: Path, scene_name: str) -> bool:
    pano_path = stage_manifest.resolve_pano_path(project_dir, scene_name)
    deleted = False
    if pano_path is not None:
        pano_path.unlink(missing_ok=True)
        deleted = True
    stage_manifest.update_manifest(
        project_dir,
        scene_name,
        clear_fields=[
            "source",
            "pano_path",
            "ply_path",
            "collision_glb_path",
            "voxel_json_path",
            "pano_sharp_args",
            "splat_transform_args",
        ],
    )
    return deleted


def _delete_scene_custom_files(project_dir: Path, scene_name: str) -> bool:
    custom_scene_path = stage_manifest.resolve_ply_path(
        project_dir, scene_name, ply_kind="custom"
    )
    active_ply_path = stage_manifest.resolve_ply_path(project_dir, scene_name)
    deleted = False
    if custom_scene_path is not None:
        custom_scene_path.unlink(missing_ok=True)
        deleted = True

    clear_fields = ["custom_scene_path"]
    manifest = stage_manifest.load_manifest(project_dir, scene_name) or {}
    if manifest.get("source") == "custom_scene" or (
        custom_scene_path is not None
        and active_ply_path is not None
        and custom_scene_path.resolve() == active_ply_path.resolve()
    ):
        clear_fields.extend(
            [
                "source",
                "ply_path",
                "collision_glb_path",
                "voxel_json_path",
                "splat_transform_args",
            ]
        )
    stage_manifest.update_manifest(project_dir, scene_name, clear_fields=clear_fields)
    return deleted


def _move_dir_if_exists(old_dir: Path, new_dir: Path) -> None:
    if not old_dir.exists():
        return
    if new_dir.exists():
        raise ValueError(f"Target asset directory already exists: {new_dir}")
    new_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old_dir), str(new_dir))


def _rename_scene_asset_dirs(project_dir: Path, old_name: str, new_name: str) -> None:
    # 走 move_asset_dir 而不是直接拼路径：存量自愈传进来的 old_name 是没消毒过的库里
    # 旧值，``../../config.json`` 拼出来的源路径在资产根之外。
    move_asset_dir(project_dir / "assets" / "scenes", old_name, new_name)

    # stage_dir 自己过 safe_name（会把点全剥掉），拼不出爬到 director_worlds 之外的路径。
    old_stage_root = stage_manifest.stage_dir(project_dir, old_name).parent
    new_stage_root = stage_manifest.stage_dir(project_dir, new_name).parent
    _move_dir_if_exists(old_stage_root, new_stage_root)

    manifest = stage_manifest.load_manifest(project_dir, new_name)
    if manifest is not None:
        manifest["scene_id"] = new_name
        stage_manifest.save_manifest(project_dir, new_name, manifest)


async def _heal_path_unsafe_scene_names(store: SQLiteStore, project_dir: Path) -> dict[str, str]:
    """修好库里名字带斜杠的存量场景。

    模型层的 ``NovelScene.sanitize_name`` 只管新数据；那道闸加上之前落库的
    ``家中客厅/哥哥卧室`` 还躺在表里，它的 ``{name}`` 接口全是 404，删不掉也生不出源图。

    调用方要先过 ``may_run_asset_repair``：这是一次写操作（搬目录 + 改主键），不该由
    只读协作者的一次页面加载触发。
    """

    def move_assets(old_name: str, new_name: str) -> None:
        _rename_scene_asset_dirs(project_dir, old_name, new_name)

    return await store.repair_path_unsafe_asset_names("scene", move_assets)
