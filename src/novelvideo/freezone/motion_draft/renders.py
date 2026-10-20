"""[aipdd fork delta] 动效大片交付：把项目里已渲染的 MG 成片读回成可选片段。

「导出剪映草稿」的输入只能是磁盘上的成片：任务记录会过期、资产库条目名会被改名，只有
渲染目录 + 同名 sidecar 是稳定事实。本模块纯读文件、不出网、不碰剪映，于是可以纯单测。

排序按渲染先后（mtime），集号/镜号只在分镜工作台发起时才有值；调用方（路由）拿到
的是本地路径，静态 URL 由路由那侧统一拼——路径口径不能泄漏进纯函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novelvideo.freezone.motion_draft.project import (
    load_sidecar,
    pick_float,
    pick_int,
)

MOTION_RENDER_TASK_TYPE = "freezone_motion_render"


@dataclass(frozen=True, slots=True)
class MotionRenderClip:
    """一条已渲染的 MG 成片 + 它的渲染元数据（sidecar 缺字段时为兜底值）。"""

    job_id: str
    video_path: Path
    name: str
    episode: int
    beat_number: int
    case_id: str
    style_id: str
    duration_seconds: float | None
    width: int
    height: int
    fps: int
    aspect_ratio: str
    beats: tuple[Any, ...]
    has_sidecar: bool
    modified_at: float

    def as_dict(self) -> dict[str, Any]:
        """给路由序列化的字段（不含本地路径：前端只认同源 URL）。"""
        return {
            "job_id": self.job_id,
            "name": self.name,
            "episode": self.episode,
            "beat_number": self.beat_number,
            "case_id": self.case_id,
            "style_id": self.style_id,
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "aspect_ratio": self.aspect_ratio,
            "beats": [dict(item) if isinstance(item, dict) else item for item in self.beats],
            "has_sidecar": self.has_sidecar,
            "modified_at": round(self.modified_at, 3),
        }


def _beats_of(sidecar: dict[str, Any]) -> tuple[Any, ...]:
    raw = sidecar.get("beats")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(item for item in raw if isinstance(item, (dict, int, float)))


def scan_motion_renders(render_dir: Path) -> tuple[MotionRenderClip, ...]:
    """列出 ``render_dir`` 里的 MG 成片，按渲染先后排序；目录不存在就返回空。"""
    if not render_dir.is_dir():
        return ()
    clips: list[MotionRenderClip] = []
    for video_path in render_dir.glob("*.mp4"):
        try:
            modified_at = video_path.stat().st_mtime
        except OSError:
            continue
        sidecar = load_sidecar(video_path)
        duration = pick_float(sidecar.get("duration_seconds"))
        clips.append(
            MotionRenderClip(
                job_id=video_path.stem,
                video_path=video_path,
                name=str(sidecar.get("title") or "").strip() or video_path.stem,
                episode=pick_int(sidecar.get("episode")),
                beat_number=pick_int(sidecar.get("beat_number")),
                case_id=str(sidecar.get("case_id") or "").strip(),
                # 按风格出片时 case_id 是空的，出处只剩这一个字段，草稿侧要能认。
                style_id=str(sidecar.get("style_id") or "").strip(),
                duration_seconds=duration,
                width=pick_int(sidecar.get("width")),
                height=pick_int(sidecar.get("height")),
                fps=pick_int(sidecar.get("fps")),
                aspect_ratio=str(sidecar.get("aspect_ratio") or "").strip(),
                beats=_beats_of(sidecar),
                has_sidecar=bool(sidecar),
                modified_at=modified_at,
            )
        )
    return tuple(sorted(clips, key=lambda item: (item.modified_at, item.job_id)))
