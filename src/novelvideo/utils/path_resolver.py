"""统一的路径计算工具。

所有资源路径都应通过此模块计算，不再存储在数据中。
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from novelvideo.task_identity import selection_scope
from novelvideo.utils.state_index_files import resolve_state_index_path


def _safe_component(value: str, fallback: str = "_") -> str:
    """清洗调用方提供的资源目录名片段（char/scene/prop/mode），防目录穿越。

    只重写能真正逃单目录的字符：``/`` 、``\\`` 、NUL 换成 ``_``，裸的 ``.``/``..``
    段回退到 ``fallback``。所有合法磁盘名都逐字节不变（单段文件名不可能含 ``/``），
    计算出的插槽路径不会与创建时落盘的目录错位。上游创建时名字校验 +
    ``resolve_static_url_to_path`` 的 ``is_relative_to`` 仍是主防线，此处为 builder 纵深防御。
    """
    text = re.sub(r"[/\\\x00]", "_", str(value or ""))
    if text in ("", ".", ".."):
        return fallback
    return text


def _scene_dir(project_dir: Path, scene_name: str) -> Path:
    return project_dir / "assets" / "scenes" / _safe_component(scene_name)


def canonical_portrait_path(project_dir: Path, char_name: str) -> Path:
    """Canonical portrait slot path. Does not require the file to exist."""
    return project_dir / "assets" / "characters" / _safe_component(char_name) / "portrait.png"


def compute_portrait_path(project_dir: Path, char_name: str) -> str:
    """动态计算头像路径。"""
    path = canonical_portrait_path(project_dir, char_name)
    return str(path) if path.exists() else ""


def compute_scene_reference_path(project_dir: Path, scene_name: str) -> str:
    """动态计算场景参考图路径。

    2.0 主线只维护 master 源图；360/3GS/voxel 都从 master 或 pano_360 派生。
    """
    return compute_scene_master_path(project_dir, scene_name)


def compute_scene_master_path(project_dir: Path, scene_name: str) -> str:
    """动态计算场景 master 图路径，用于创建 DirectorWorld。"""
    path = canonical_scene_master_path(project_dir, scene_name)
    return str(path) if path.exists() else ""


def canonical_scene_master_path(project_dir: Path, scene_name: str) -> Path:
    """Canonical scene master slot path. Does not require the file to exist."""
    return _scene_dir(project_dir, scene_name) / "master.png"


def canonical_scene_360_path(project_dir: Path, scene_name: str) -> Path:
    """Canonical scene 360 slot path for Freezone Commit."""
    return _scene_dir(project_dir, scene_name) / "scene_panorama_sketch_360.png"


def canonical_scene_render_anchors_dir(project_dir: Path, scene_name: str) -> Path:
    """Scene-level fixed background reference slots for render/colorization."""
    return _scene_dir(project_dir, scene_name) / "render_anchors"


def compute_scene_render_anchor_path(
    project_dir: Path, scene_name: str, anchor_id: str = "latest"
) -> str:
    """Resolve one selected perspective background reference for render.

    Scene-level fixed slots are intentionally small: latest is the 360 viewer
    screenshot and external is the user-uploaded override.
    """
    safe_id = re.sub(r'[/\\:*?"<>|]', "_", str(anchor_id or "latest").strip()) or "latest"
    path = canonical_scene_render_anchors_dir(project_dir, scene_name) / f"{safe_id}.png"
    return str(path) if path.exists() else ""


def canonical_beat_control_frame_dir(project_dir: Path, episode: int, beat_num: int) -> Path:
    """Canonical beat-level director/control-frame directory."""
    return project_dir / "director_control_frames" / f"ep{episode:03d}" / f"beat_{beat_num:02d}"


def canonical_beat_selected_background_path(project_dir: Path, episode: int, beat_num: int) -> Path:
    """Canonical beat-owned background selected from 360 viewer or upload."""
    return (
        canonical_beat_control_frame_dir(project_dir, episode, beat_num) / "selected_background.png"
    )


def compute_beat_selected_background_path(project_dir: Path, episode: int, beat_num: int) -> str:
    """Resolve the beat-owned selected background if it exists."""
    path = canonical_beat_selected_background_path(project_dir, episode, beat_num)
    return str(path) if path.exists() else ""


def canonical_beat_director_env_only_path(project_dir: Path, episode: int, beat_num: int) -> Path:
    """Canonical beat-owned Director/3GS environment-only plate."""
    return canonical_beat_control_frame_dir(project_dir, episode, beat_num) / "env_only.png"


def compute_beat_director_env_only_path(project_dir: Path, episode: int, beat_num: int) -> str:
    """Resolve the beat-owned Director/3GS environment-only plate if it exists."""
    path = canonical_beat_director_env_only_path(project_dir, episode, beat_num)
    return str(path) if path.exists() else ""


def canonical_scene_spatial_layout_path(project_dir: Path, scene_name: str) -> Path:
    """Canonical scene-level spatial layout slot path."""
    return _scene_dir(project_dir, scene_name) / "spatial_layout.png"


def compute_scene_spatial_layout_path(project_dir: Path, scene_name: str) -> str:
    """动态计算场景级空间布局图路径，用于草图生成和 director 修正。"""
    path = canonical_scene_spatial_layout_path(project_dir, scene_name)
    return str(path) if path.exists() else ""


def _first_existing_scene_file(project_dir: Path, scene_name: str, candidates: list[str]) -> str:
    scene_dir = _scene_dir(project_dir, scene_name)
    for rel_path in candidates:
        path = scene_dir / rel_path
        if path.exists():
            return str(path)
    return ""


def canonical_scene_reverse_master_path(project_dir: Path, scene_name: str) -> Path:
    """Canonical scene-level reverse master image slot path."""
    return _scene_dir(project_dir, scene_name) / "reverse_master.png"


def compute_scene_reverse_master_path(project_dir: Path, scene_name: str) -> str:
    """动态计算场景 reverse master 路径（BACK-FACING establishing view）。"""
    path = canonical_scene_reverse_master_path(project_dir, scene_name)
    return str(path) if path.exists() else ""


def compute_scene_topdown_path(project_dir: Path, scene_name: str) -> str:
    """动态计算场景俯视/平面图路径。

    当前仍处在实验迁移期，因此兼容最近几轮测试文件名；以后稳定后应收敛到
    `top_down.png` 或 `floor_plan.png`。
    """
    return _first_existing_scene_file(
        project_dir,
        scene_name,
        [
            "top_down.png",
            "top_down_ref.png",
            "floor_plan.png",
            "top_down_openai_master_style_test_1k_square.png",
            "top_down_openai_master_style_test_1k.png",
            "blueprint_sheet_openai_master_test/floor_plan.png",
        ],
    )


def compute_scene_reverse_path(project_dir: Path, scene_name: str) -> str:
    """动态计算场景反向/背面参考图路径。"""
    return _first_existing_scene_file(
        project_dir,
        scene_name,
        [
            "reverse.png",
            "reverse_ref.png",
            "reverse_openai_master_topdown_preset_v2_1k.png",
            "reverse_openai_master_topdown_test_1k.png",
            "reverse_render_ref.png",
            "blueprint_sheet_openai_master_test/back_elevation.png",
        ],
    )


def compute_scene_world_model_pack_paths(project_dir: Path, scene_name: str) -> list[str]:
    """返回 render 阶段的场景参考包。

    正式主线不再附加旧 top_down/reverse 场景参考图；它们只作为 legacy
    调试文件保留。需要稳定几何时应使用 pano_360、voxel world 或 3GS control frame。
    """
    paths = [compute_scene_master_path(project_dir, scene_name)]
    return [path for path in paths if path]


def compute_scene_style_reference_path(project_dir: Path, scene_name: str) -> str:
    """Legacy alias: 返回 master 源图。"""
    return compute_scene_master_path(project_dir, scene_name)


def compute_director_ref_dir(project_dir: Path, episode: int, beat_num: int) -> Path:
    """Beat 级导演模式参考目录。"""
    return project_dir / "assets" / "director_refs" / f"ep{episode:03d}" / f"beat_{beat_num:02d}"


def compute_director_color_ref_path(project_dir: Path, episode: int, beat_num: int) -> str:
    """动态计算 beat 级导演彩图参考路径。"""
    path = compute_director_ref_dir(project_dir, episode, beat_num) / "director_color_ref.png"
    return str(path) if path.exists() else ""


def compute_director_sketch_ref_path(project_dir: Path, episode: int, beat_num: int) -> str:
    """动态计算 beat 级导演线稿参考路径。"""
    path = compute_director_ref_dir(project_dir, episode, beat_num) / "director_sketch_ref.png"
    return str(path) if path.exists() else ""


def compute_director_blocking_ref_path(project_dir: Path, episode: int, beat_num: int) -> str:
    """动态计算 DirectorWorld 控制图路径。"""
    path = compute_director_ref_dir(project_dir, episode, beat_num) / "director_blocking_ref.png"
    return str(path) if path.exists() else ""


def compute_director_world_control_ref_path(project_dir: Path, episode: int, beat_num: int) -> str:
    """动态计算原始 DirectorWorld 控制图路径。

    这是编辑器/截图导出的 raw control，用于锁定镜头、位置、遮挡和 actor/prop
    marker；不应被当作美术风格参考，也不应被二次生成流程覆盖。
    """
    path = (
        compute_director_ref_dir(project_dir, episode, beat_num) / "director_world_control_ref.png"
    )
    return str(path) if path.exists() else ""


def compute_director_view_meta_path(project_dir: Path, episode: int, beat_num: int) -> Path:
    """Beat 级导演机位元数据路径。"""
    return compute_director_ref_dir(project_dir, episode, beat_num) / "director_view.json"


def compute_prop_reference_path(project_dir: Path, prop_name: str) -> str:
    """动态计算道具参考三视图路径。"""
    path = canonical_prop_reference_path(project_dir, prop_name)
    return str(path) if path.exists() else ""


def canonical_prop_reference_path(project_dir: Path, prop_name: str) -> Path:
    """Canonical prop reference slot path. Does not require the file to exist."""
    return project_dir / "assets" / "props" / _safe_component(prop_name) / "reference_3view.png"


def compute_identity_path(project_dir: Path, char_name: str, identity_name: str) -> str:
    """动态计算身份参考图路径。"""
    path = canonical_identity_path(project_dir, char_name, identity_name)
    return str(path) if path.exists() else ""


def canonical_identity_path(project_dir: Path, char_name: str, identity_name: str) -> Path:
    """Canonical identity slot path. Does not require the file to exist."""
    prefix = f"{char_name}_"
    if identity_name.startswith(prefix):
        identity_name = identity_name[len(prefix) :]
    safe_name = re.sub(r'[/\\:*?"<>|]', "_", identity_name)
    return project_dir / "assets" / "characters" / _safe_component(char_name) / "identities" / f"{safe_name}.png"


def compute_identity_portrait_path(project_dir: Path, char_name: str, identity_name: str) -> str:
    """动态计算身份级 portrait 路径（年龄变体等面部差异大的身份）。"""
    preferred = canonical_identity_portrait_path(project_dir, char_name, identity_name)
    if preferred.exists():
        return str(preferred)
    prefix = f"{char_name}_"
    if identity_name.startswith(prefix):
        identity_name = identity_name[len(prefix) :]
    safe_name = re.sub(r'[/\\:*?"<>|]', "_", identity_name)
    legacy = (
        project_dir
        / "assets"
        / "characters"
        / _safe_component(char_name)
        / "identities"
        / f"{safe_name}_portrait.png"
    )
    return str(legacy) if legacy.exists() else ""


def canonical_identity_portrait_path(project_dir: Path, char_name: str, identity_name: str) -> Path:
    """Canonical identity portrait slot path. Does not require the file to exist."""
    prefix = f"{char_name}_"
    if identity_name.startswith(prefix):
        identity_name = identity_name[len(prefix) :]
    safe_name = re.sub(r'[/\\:*?"<>|]', "_", identity_name)
    return (
        project_dir
        / "assets"
        / "characters"
        / _safe_component(char_name)
        / "identities"
        / f"{char_name}_{safe_name}_portrait.png"
    )


def compute_identity_face_mat_path(project_dir: Path, char_name: str, identity_name: str) -> str:
    """[aipdd fork delta] 动态计算身份级五官垫图路径。"""
    path = canonical_identity_face_mat_path(project_dir, char_name, identity_name)
    return str(path) if path.exists() else ""


def canonical_identity_face_mat_path(
    project_dir: Path, char_name: str, identity_name: str
) -> Path:
    """[aipdd fork delta] Canonical identity face-mat slot path. Does not require the file to exist."""
    prefix = f"{char_name}_"
    if identity_name.startswith(prefix):
        identity_name = identity_name[len(prefix) :]
    safe_name = re.sub(r'[/\\:*?"<>|]', "_", identity_name)
    return (
        project_dir
        / "assets"
        / "characters"
        / _safe_component(char_name)
        / "identities"
        / f"{char_name}_{safe_name}_face_mat.png"
    )


def compute_identity_composite_path(project_dir: Path, char_name: str, identity_name: str) -> str:
    """[aipdd fork delta] 动态计算身份级「三视图 + 五官垫图」拼接图路径。"""
    path = canonical_identity_composite_path(project_dir, char_name, identity_name)
    return str(path) if path.exists() else ""


def canonical_identity_composite_path(
    project_dir: Path, char_name: str, identity_name: str
) -> Path:
    """[aipdd fork delta] Canonical identity composite slot path. Does not require the file to exist."""
    prefix = f"{char_name}_"
    if identity_name.startswith(prefix):
        identity_name = identity_name[len(prefix) :]
    safe_name = re.sub(r'[/\\:*?"<>|]', "_", identity_name)
    return (
        project_dir
        / "assets"
        / "characters"
        / _safe_component(char_name)
        / "identities"
        / f"{char_name}_{safe_name}_composite.png"
    )


def compute_identity_costume_path(project_dir: Path, char_name: str, identity_name: str) -> str:
    """动态计算身份级服装参考图路径。"""
    path = canonical_identity_costume_path(project_dir, char_name, identity_name)
    return str(path) if path.exists() else ""


def canonical_identity_costume_path(project_dir: Path, char_name: str, identity_name: str) -> Path:
    """Canonical identity costume slot path. Does not require the file to exist."""
    prefix = f"{char_name}_"
    if identity_name.startswith(prefix):
        identity_name = identity_name[len(prefix) :]
    safe_name = re.sub(r'[/\\:*?"<>|]', "_", identity_name)
    return (
        project_dir
        / "assets"
        / "characters"
        / _safe_component(char_name)
        / "identities"
        / f"{safe_name}_costume.png"
    )


def compute_scoped_grid_filename(
    mode_key: str,
    beat_numbers: list[int] | tuple[int, ...],
    *,
    prefix: str = "grid",
    ext: str = "png",
) -> str:
    """按 mode_key + beat_numbers 生成稳定唯一的整图文件名。"""
    normalized = [int(beat) for beat in beat_numbers if beat is not None]
    if not mode_key or not normalized:
        raise ValueError("scoped grid filename requires mode_key and beat_numbers")
    scope = selection_scope(mode_key, normalized)
    return f"{prefix}_{scope}.{ext.lstrip('.')}"


def resolve_render_grid_path(
    project_dir: Path,
    episode: int,
    mode_key: str,
    beat_numbers: list[int] | tuple[int, ...],
    *,
    grid_index: int | None = None,
) -> Path:
    """优先返回 scoped 唯一路径；没有则 fallback 到旧的 grid_XX.png。"""
    mode_dir = project_dir / "grids" / f"ep{episode:03d}" / _safe_component(mode_key)
    scoped_path = mode_dir / compute_scoped_grid_filename(
        mode_key,
        beat_numbers,
        prefix="grid",
        ext="png",
    )
    if scoped_path.exists():
        return scoped_path
    if grid_index is not None:
        legacy_path = mode_dir / f"grid_{grid_index + 1:02d}.png"
        if legacy_path.exists():
            return legacy_path
        return scoped_path
    return scoped_path


def resolve_sketch_grid_path(
    project_dir: Path,
    episode: int,
    mode_key: str | None,
    beat_numbers: list[int] | tuple[int, ...] | None,
    *,
    grid_index: int | None = None,
    rows: int | None = None,
    cols: int | None = None,
    beat_start: int | None = None,
    beat_end: int | None = None,
) -> Path:
    """优先返回 scoped 草图文件；没有则 fallback 到旧 sketch_g / sketch_b。"""
    sketch_base = project_dir / "grids" / f"ep{episode:03d}" / "sketch"
    normalized = [int(beat) for beat in (beat_numbers or []) if beat is not None]
    if mode_key and normalized:
        scoped_name = compute_scoped_grid_filename(
            mode_key,
            normalized,
            prefix="sketch",
            ext="jpg",
        )
        scoped_path = sketch_base / scoped_name
        if scoped_path.exists():
            return scoped_path
    if grid_index is not None and rows is not None and cols is not None:
        legacy_name = f"sketch_g{grid_index}_{rows}x{cols}.jpg"
        legacy_path = sketch_base / legacy_name
        if legacy_path.exists():
            return legacy_path
    if beat_start is not None and beat_end is not None and rows is not None and cols is not None:
        old_name = f"sketch_b{beat_start}-{beat_end}_{rows}x{cols}.jpg"
        old_path = sketch_base / old_name
        if old_path.exists():
            return old_path
    if sketch_base.exists():
        for ratio_dir in sketch_base.iterdir():
            if not ratio_dir.is_dir():
                continue
            if mode_key and normalized:
                candidate = ratio_dir / compute_scoped_grid_filename(
                    mode_key,
                    normalized,
                    prefix="sketch",
                    ext="jpg",
                )
                if candidate.exists():
                    return candidate
            if grid_index is not None and rows is not None and cols is not None:
                candidate = ratio_dir / f"sketch_g{grid_index}_{rows}x{cols}.jpg"
                if candidate.exists():
                    return candidate
            if (
                beat_start is not None
                and beat_end is not None
                and rows is not None
                and cols is not None
            ):
                candidate = ratio_dir / f"sketch_b{beat_start}-{beat_end}_{rows}x{cols}.jpg"
                if candidate.exists():
                    return candidate
    if mode_key and normalized:
        return sketch_base / compute_scoped_grid_filename(
            mode_key,
            normalized,
            prefix="sketch",
            ext="jpg",
        )
    if beat_start is not None and beat_end is not None and rows is not None and cols is not None:
        return sketch_base / f"sketch_b{beat_start}-{beat_end}_{rows}x{cols}.jpg"
    return sketch_base / "sketch_unknown.jpg"


class PathResolver:
    """项目资源路径计算器。"""

    def __init__(self, output_dir: str, episode: int):
        self.output_dir = Path(output_dir)
        self.episode = episode
        self._ep_str = f"ep{episode:03d}"

    # === Beat 级别资源 ===

    def frame(self, beat_num: int) -> Path:
        """首帧图片路径（显示用，可能经过裁剪处理）。"""
        return self.output_dir / "frames" / self._ep_str / f"beat_{beat_num:02d}.png"

    def director_render(self, beat_num: int) -> Path:
        """Director Render combined.png — Sprint C/D 的 3GS 主链路产物。

        路径：`director_control_frames/ep<NNN>/beat_<MM>/combined.png`，
        由 PlayCanvas 直接截取实时 combined 画面，保留视口中的环境/actor 遮挡关系。
        """
        return (
            self.output_dir
            / "director_control_frames"
            / self._ep_str
            / f"beat_{beat_num:02d}"
            / "combined.png"
        )

    def video_input_frame(self, beat_num: int, *, slot: str = "first_frame") -> Path:
        """视频输入派生帧路径。

        这里存放从已选 render 裁出来的视频模型输入，不覆盖 `frames/...`
        源资产，确保资产比例和视频输入比例分离。
        """
        safe_slot = "last_frame" if slot == "last_frame" else "first_frame"
        return (
            self.output_dir
            / "video_inputs"
            / self._ep_str
            / f"beat_{beat_num:02d}"
            / f"{safe_slot}.png"
        )

    def video_input_frame_meta(self, beat_num: int, *, slot: str = "first_frame") -> Path:
        frame_path = self.video_input_frame(beat_num, slot=slot)
        return frame_path.with_suffix(".json")

    @staticmethod
    def _source_signature(source_path: Path) -> dict[str, object] | None:
        try:
            stat = source_path.stat()
        except OSError:
            return None
        return {
            "source_path": str(source_path),
            "source_mtime_ns": int(stat.st_mtime_ns),
            "source_size": int(stat.st_size),
        }

    def write_video_input_frame_meta(
        self,
        beat_num: int,
        *,
        slot: str,
        source_path: Path,
    ) -> None:
        signature = self._source_signature(source_path)
        if signature is None:
            return
        meta_path = self.video_input_frame_meta(beat_num, slot=slot)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps({"version": 1, **signature}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def valid_video_input_frame(
        self,
        beat_num: int,
        *,
        slot: str = "first_frame",
        source_path: Path,
    ) -> Path | None:
        frame_path = self.video_input_frame(beat_num, slot=slot)
        if not frame_path.exists():
            return None
        expected = self._source_signature(source_path)
        if expected is None:
            return None
        meta_path = self.video_input_frame_meta(beat_num, slot=slot)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (
            str(meta.get("source_path") or "") == str(expected["source_path"])
            and int(meta.get("source_mtime_ns") or -1) == expected["source_mtime_ns"]
            and int(meta.get("source_size") or -1) == expected["source_size"]
        ):
            return frame_path
        return None

    def first_frame_for_video(self, beat_num: int, *, use_director_render: bool = False) -> Path:
        """视频生成用首帧路径。

        `use_director_render=True` 时才使用
        `director_control_frames/.../combined.png`。默认走旧
        `frames/.../beat_NN.png`，避免 stale Director Render 静默覆盖已确认首帧。
        """
        director_render = self.director_render(beat_num)
        source = (
            director_render
            if use_director_render and director_render.exists()
            else self.frame(beat_num)
        )
        override = self.valid_video_input_frame(
            beat_num,
            slot="first_frame",
            source_path=source,
        )
        return override or source

    def sketch(self, beat_num: int) -> Path:
        """草图图片路径（显示用，类似 frame）。"""
        return self.output_dir / "sketches" / self._ep_str / f"beat_{beat_num:02d}.png"

    def sketches_dir(self) -> Path:
        return self.output_dir / "sketches" / self._ep_str

    def audio(self, beat_num: int) -> Path:
        """音频路径。"""
        return self.output_dir / "audio" / self._ep_str / f"beat_{beat_num:02d}.mp3"

    def video(self, beat_num: int) -> Path:
        """视频路径。"""
        return self.output_dir / "videos" / "beats" / self._ep_str / f"beat_{beat_num:02d}.mp4"

    # === 视频提示词 ===

    def video_prompt(self, beat_num: int) -> Path:
        """视频提示词文件路径。"""
        return self.output_dir / "prompts" / "videos" / self._ep_str / f"beat_{beat_num:02d}.txt"

    # === Grid 级别资源 ===

    def grid(self, mode: str, grid_idx: int) -> Path:
        """网格图片路径。grid_idx 从 1 开始。"""
        return self.output_dir / "grids" / self._ep_str / _safe_component(mode) / f"grid_{grid_idx:02d}.png"

    def grid_prompt(self, mode: str, grid_idx: int) -> Path:
        """网格提示词路径。"""
        return (
            self.output_dir
            / "grids"
            / self._ep_str
            / _safe_component(mode)
            / "prompts"
            / f"grid_{grid_idx:02d}.prompt.txt"
        )

    # === Shot 级别资源 ===

    def shot_video(self, shot_id: int) -> Path:
        """Shot 视频路径。"""
        return self.output_dir / "videos" / "shots" / self._ep_str / f"shot_{shot_id:02d}.mp4"

    def shot_audio(self, shot_id: int) -> Path:
        """Shot 合并 TTS 音轨路径。"""
        return self.output_dir / "audio" / "shots" / self._ep_str / f"shot_{shot_id:02d}.mp3"

    def shots_video_dir(self) -> Path:
        return self.output_dir / "videos" / "shots" / self._ep_str

    # === Episode 级别资源 ===

    def final_video(self, filename: Optional[str] = None) -> Path:
        """最终合成视频路径。"""
        if filename:
            return self.output_dir / "videos" / "episodes" / _safe_component(filename)
        return self.output_dir / "videos" / "episodes" / f"{self._ep_str}_final.mp4"

    def episodes_dir(self) -> Path:
        return self.output_dir / "videos" / "episodes"

    def script(self) -> Path:
        """剧本 JSON 路径。"""
        return self.output_dir / "scripts" / f"{self._ep_str}_script.json"

    def script_mtime(self) -> Optional[datetime]:
        """剧本文件的修改时间，文件不存在时返回 None。"""
        script_path = self.script()
        if not script_path.exists():
            return None
        try:
            return datetime.fromtimestamp(script_path.stat().st_mtime)
        except OSError:
            return None

    def clean_sketches(self) -> int:
        """清理该集的 sketch 展示文件（sketches/epXXX/）。

        grids 目录保留不动。

        Returns:
            删除的文件数
        """
        import shutil

        sketch_dir = self.output_dir / "sketches" / self._ep_str
        if not sketch_dir.exists():
            return 0
        count = sum(1 for f in sketch_dir.rglob("*") if f.is_file())
        shutil.rmtree(sketch_dir)
        sketch_dir.mkdir(parents=True, exist_ok=True)
        return count

    # === 目录 ===

    def frames_dir(self) -> Path:
        return self.output_dir / "frames" / self._ep_str

    def audio_dir(self) -> Path:
        return self.output_dir / "audio" / self._ep_str

    def videos_dir(self) -> Path:
        return self.output_dir / "videos" / "beats" / self._ep_str

    def video_pool_dir(self) -> Path:
        """视频池目录。"""
        return self.output_dir / "videos" / "beats" / self._ep_str / "pool"

    def video_pool_index(self) -> Path:
        """视频池索引文件路径。"""
        return resolve_state_index_path(
            self.output_dir / "videos" / "beats" / self._ep_str,
            "video_pool_index.json",
        )

    def grids_dir(self, mode: str) -> Path:
        return self.output_dir / "grids" / self._ep_str / _safe_component(mode)

    # === Action Beat 资源（2.0 短剧模式） ===

    def action_grid(self, beat_num: int) -> Path:
        """Action beat 5×5 网格图路径。"""
        return self.output_dir / "grids" / self._ep_str / "action" / f"beat_{beat_num:02d}_grid.png"

    def action_panels_dir(self, beat_num: int) -> Path:
        """Action beat 切割面板目录。"""
        return self.output_dir / "grids" / self._ep_str / "action" / f"beat_{beat_num:02d}_panels"

    def action_panel(self, beat_num: int, panel_index: int) -> Path:
        """Action beat 单个面板路径（panel_index 1-25）。"""
        return self.action_panels_dir(beat_num) / f"panel_{panel_index:02d}.png"

    def action_selected_frame(self, beat_num: int, order: int) -> Path:
        """Action beat 选中帧路径（order 从 1 开始，按用户选取顺序编号）。

        选中时从 action_panel() 池复制到此处，与 1.0 pool→frame 约定一致。
        """
        return (
            self.output_dir / "frames" / self._ep_str / f"beat_{beat_num:02d}_sel_{order:02d}.png"
        )

    def action_video(self, beat_num: int, order: int) -> Path:
        """Action beat 选中帧对应的视频路径（order 与 selected_frame 对应）。"""
        return (
            self.output_dir
            / "videos"
            / "beats"
            / self._ep_str
            / f"beat_{beat_num:02d}_sel_{order:02d}.mp4"
        )

    # === 非 Episode 级别资源 ===

    def character_assets_dir(self, char_name: str) -> Path:
        """角色资源目录（不依赖 episode）。"""
        return self.output_dir / "assets" / "characters" / _safe_component(char_name)

    def sketch_dir(self) -> Path:
        """草图目录（flat，不按 ratio 分子目录）。

        Returns:
            草图目录路径，如 grids/ep001/sketch/
        """
        return self.output_dir / "grids" / self._ep_str / "sketch"

    def render_dir(self) -> Path:
        """渲染图输出目录（beat 中心命名格式）。"""
        return self.output_dir / "grids" / self._ep_str / "render"

    def has_sketch(self) -> bool:
        """检查是否存在草图文件。"""
        d = self.sketch_dir()
        if not d.exists():
            return False
        # 新格式（flat）: beat_*_t*.*
        if list(d.glob("beat_*_t*.*")):
            return True
        # 整图格式: sketch_g*_*x*.*
        if list(d.glob("sketch_g*_*x*.*")):
            return True
        # 旧格式: sketch_b*_*x*.*
        if list(d.glob("sketch_b*_*x*.*")):
            return True
        # 兼容旧 ratio 子目录（如 sketch/1-1/beat_*）
        for sub in d.iterdir():
            if sub.is_dir() and list(sub.glob("beat_*_t*.*")):
                return True
        return False

    # === Phase 2: Preset 目录 + Grid 文件名 ===

    def episode_grids_dir(self) -> Path:
        """集数 grids 根目录。"""
        return self.output_dir / "grids" / self._ep_str

    def preset_dir(self, preset: str) -> Path:
        """preset 分组目录。

        Args:
            preset: 分组名，scene / char / loc / custom

        Returns:
            如 grids/ep001/scene/
        """
        return self.output_dir / "grids" / self._ep_str / _safe_component(preset)

    def grid_filename(self, grid_type: str, mode_key: str, beat_nums: list[int], ts: str) -> str:
        """生成整图文件名。

        Args:
            grid_type: render | sketch
            mode_key: 如 3x3, 1x1_9-16
            beat_nums: beat 编号列表
            ts: 时间戳字符串（YYYYMMDDHHmmss）

        Returns:
            如 render_3x3_1-2-3-4-5-6-7-8-9_grid_20260227143052.png
        """
        beats_str = "-".join(str(b) for b in beat_nums)
        return f"{grid_type}_{mode_key}_{beats_str}_grid_{ts}.png"

    def grid_prompt_filename(
        self, grid_type: str, mode_key: str, beat_nums: list[int], ts: str
    ) -> str:
        """生成整图提示词文件名。"""
        beats_str = "-".join(str(b) for b in beat_nums)
        return f"{grid_type}_{mode_key}_{beats_str}_prompt.txt"

    def cell_filename(self, beat_num: int, ts: str) -> str:
        """生成 cell 文件名（beat 中心命名）。

        Returns:
            如 beat_01_t20260227143052.png
        """
        return f"beat_{beat_num:02d}_t{ts}.png"

    def pool_index_path(self) -> Path:
        """池索引文件路径。"""
        return resolve_state_index_path(
            self.output_dir / "grids" / self._ep_str,
            "pool_index.json",
        )

    def find_grid_image(
        self,
        grid_type: str,
        mode_key: str,
        beat_nums: list[int],
    ) -> Optional[Path]:
        """查找整图文件（兼容新旧路径）。

        搜索顺序：
        1. 新 preset 目录: {preset}/{type}_{mode}_{beats}_grid_{ts}.png
        2. 旧 mode_key 目录: {mode_key}/grid_*.png
        3. 旧 render/sketch 目录: render/regen_*.png, sketch/regen_*.png

        Returns:
            找到的文件路径，或 None
        """
        ep_grids = self.episode_grids_dir()
        beats_str = "-".join(str(b) for b in beat_nums)

        # 1. 搜索 preset 目录
        for preset in ("scene", "char", "loc", "custom"):
            preset_dir = ep_grids / preset
            if preset_dir.exists():
                pattern = f"{grid_type}_{mode_key}_{beats_str}_grid_*.png"
                matches = sorted(preset_dir.glob(pattern))
                if matches:
                    return matches[-1]  # 最新的

        # 2. 旧 mode_key 目录
        mode_dir = ep_grids / mode_key
        if mode_dir.exists():
            scoped_path = mode_dir / compute_scoped_grid_filename(
                mode_key,
                beat_nums,
                prefix="grid",
                ext="png",
            )
            if scoped_path.exists():
                return scoped_path
            for f in sorted(mode_dir.glob("grid_*.png")):
                return f

        # 3. 旧 render/sketch 目录
        type_dir = ep_grids / ("sketch" if grid_type == "sketch" else "render")
        if type_dir.exists():
            for f in sorted(type_dir.glob(f"regen_{mode_key}_b*.png")):
                return f
            for f in sorted(type_dir.glob(f"regen_{mode_key}_g*.png")):
                return f

        return None
