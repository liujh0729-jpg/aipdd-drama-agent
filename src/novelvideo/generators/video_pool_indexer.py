"""视频池索引管理模块。

管理所有生成的视频版本，提供统一的索引和检索功能。
参考 pool_indexer.py 的模式，但无 grid/cell 逻辑，大幅简化。
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from novelvideo.utils.state_index_files import (
    ensure_state_index_from_legacy,
    index_file_lock,
    resolve_state_index_path,
    write_json_atomic,
)
from novelvideo.models import VideoPoolEntry, VideoPoolIndex

_VIDEO_POOL_INDEX_FILENAME = "video_pool_index.json"


def _state_video_pool_index_path(videos_ep_dir: Path) -> Path:
    return resolve_state_index_path(videos_ep_dir, _VIDEO_POOL_INDEX_FILENAME)


def _load_video_pool_index_unlocked(index_path: Path) -> Optional[VideoPoolIndex]:
    if not index_path.exists():
        return None
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        return VideoPoolIndex.model_validate(data)
    except Exception:
        return None


def _save_video_pool_index_unlocked(pool: VideoPoolIndex, index_path: Path) -> None:
    write_json_atomic(index_path, pool.model_dump(mode="json"))


def load_video_pool_index(videos_ep_dir: Path) -> Optional[VideoPoolIndex]:
    """读取视频池索引。

    Args:
        videos_ep_dir: 视频集目录，如 output/user/project/videos/beats/ep001

    Returns:
        VideoPoolIndex 对象，不存在返回 None
    """
    index_path = _state_video_pool_index_path(videos_ep_dir)
    with index_file_lock(index_path):
        index_path = ensure_state_index_from_legacy(
            videos_ep_dir,
            _VIDEO_POOL_INDEX_FILENAME,
        )
        return _load_video_pool_index_unlocked(index_path)


def save_video_pool_index(pool: VideoPoolIndex, videos_ep_dir: Path) -> None:
    """序列化写入视频池索引。

    Args:
        pool: VideoPoolIndex 对象
        videos_ep_dir: 视频集目录
    """
    index_path = _state_video_pool_index_path(videos_ep_dir)
    with index_file_lock(index_path):
        index_path = ensure_state_index_from_legacy(
            videos_ep_dir,
            _VIDEO_POOL_INDEX_FILENAME,
        )
        _save_video_pool_index_unlocked(pool, index_path)


def add_video_to_pool(
    videos_ep_dir: Path,
    episode: int,
    beat_num: int,
    source_video_path: Path,
    duration: float = 5.0,
    video_mode: str = "first_frame",
    backend: str = "comfyui",
    prompt: str = "",
) -> VideoPoolEntry:
    """将视频添加到视频池。

    1. 创建 pool/ 目录
    2. 生成 timestamp ID
    3. 复制源视频到 pool/
    4. 更新 index

    Args:
        videos_ep_dir: 视频集目录
        episode: 集数
        beat_num: Beat 编号
        source_video_path: 源视频路径（canonical 位置）
        duration: 视频时长
        video_mode: 视频模式
        backend: 后端名称
        prompt: 提示词

    Returns:
        新创建的 VideoPoolEntry
    """
    source_video_path = Path(source_video_path)
    pool_dir = videos_ep_dir / "pool"
    pool_dir.mkdir(parents=True, exist_ok=True)

    # 生成 timestamp 和 ID
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    entry_id = f"beat_{beat_num:02d}_{timestamp}"
    pool_filename = f"{entry_id}.mp4"
    pool_path = pool_dir / pool_filename

    # 复制源视频到 pool（源文件保留在 canonical 位置）
    shutil.copy2(str(source_video_path), str(pool_path))

    # 创建条目
    entry = VideoPoolEntry(
        id=entry_id,
        beat_num=beat_num,
        video_path=pool_filename,
        generated_at=datetime.now(),
        duration=duration,
        video_mode=video_mode,
        backend=backend,
        prompt=prompt,
    )

    index_path = _state_video_pool_index_path(videos_ep_dir)
    with index_file_lock(index_path):
        index_path = ensure_state_index_from_legacy(
            videos_ep_dir,
            _VIDEO_POOL_INDEX_FILENAME,
        )
        pool_index = _load_video_pool_index_unlocked(index_path)
        if pool_index is None:
            pool_index = VideoPoolIndex(episode=episode)

        # 添加条目并更新 beat_assignments
        pool_index.videos.append(entry)
        pool_index.beat_assignments[str(beat_num)] = entry_id

        # 保存
        _save_video_pool_index_unlocked(pool_index, index_path)

    return entry


def assign_video_to_beat(
    videos_ep_dir: Path,
    beat_num: int,
    pool_entry_id: str,
) -> bool:
    """用户从池中选择旧版本时调用。

    1. 加载 index，找到 entry
    2. 复制 pool/{entry.video_path} -> beat_{NN}.mp4（覆盖 canonical）
    3. 更新 beat_assignments

    Args:
        videos_ep_dir: 视频集目录
        beat_num: Beat 编号
        pool_entry_id: 池条目 ID

    Returns:
        是否成功
    """
    index_path = _state_video_pool_index_path(videos_ep_dir)
    with index_file_lock(index_path):
        index_path = ensure_state_index_from_legacy(
            videos_ep_dir,
            _VIDEO_POOL_INDEX_FILENAME,
        )
        pool_index = _load_video_pool_index_unlocked(index_path)
        if pool_index is None:
            return False

        entry = pool_index.get_entry(pool_entry_id)
        if entry is None:
            return False

        pool_path = videos_ep_dir / "pool" / entry.video_path
        if not pool_path.exists():
            return False

        # 覆盖 canonical 位置
        canonical_path = videos_ep_dir / f"beat_{beat_num:02d}.mp4"
        shutil.copy2(str(pool_path), str(canonical_path))

        # 更新 assignment
        pool_index.beat_assignments[str(beat_num)] = pool_entry_id
        _save_video_pool_index_unlocked(pool_index, index_path)

    return True


def delete_video_from_pool(
    videos_ep_dir: Path,
    pool_entry_id: str,
) -> bool:
    """删除池中的视频版本。

    Args:
        videos_ep_dir: 视频集目录
        pool_entry_id: 池条目 ID

    Returns:
        是否成功
    """
    index_path = _state_video_pool_index_path(videos_ep_dir)
    with index_file_lock(index_path):
        index_path = ensure_state_index_from_legacy(
            videos_ep_dir,
            _VIDEO_POOL_INDEX_FILENAME,
        )
        pool_index = _load_video_pool_index_unlocked(index_path)
        if pool_index is None:
            return False

        entry = pool_index.get_entry(pool_entry_id)
        if entry is None:
            return False

        # 删除 pool 文件
        pool_path = videos_ep_dir / "pool" / entry.video_path
        if pool_path.exists():
            pool_path.unlink()

        # 从 index 移除
        pool_index.videos = [v for v in pool_index.videos if v.id != pool_entry_id]

        # 如果该条目是当前 assignment，清除
        for beat_key, assigned_id in list(pool_index.beat_assignments.items()):
            if assigned_id == pool_entry_id:
                del pool_index.beat_assignments[beat_key]

        _save_video_pool_index_unlocked(pool_index, index_path)
    return True
