"""[aipdd fork delta] 动效大片交付：逐镜 MG 成片 → 剪映草稿（纯本地自研，不依赖任何草稿库或服务）。

``renders`` 把项目里已渲染的成片连 sidecar 一起读回来（「导出剪映草稿」的候选清单），
``project`` 把这些渲染产物（成片 + ``*.motion.json`` sidecar + 逐镜/整片旁白 + BGM + 字幕）
翻译成规格，``schema`` 把规格组装成草稿字典（纯函数、微秒整数），``writer`` 把这盘工程
按剪映认得的目录结构落盘（含 ``Timelines/`` 与各项镜像）。
"""

from novelvideo.freezone.motion_draft.project import (
    SCORE_GAIN_UNDER_BGM,
    SCORE_GAIN_UNDER_NARRATION,
    ClipInput,
    MotionDraftInputError,
    beats_to_microseconds,
    build_spec,
    load_sidecar,
    pick_float,
    pick_int,
    read_clips,
    resolve_media_path,
    sidecar_has_score,
    sidecar_path_for,
)
from novelvideo.freezone.motion_draft.renders import (
    MOTION_RENDER_TASK_TYPE,
    MotionRenderClip,
    scan_motion_renders,
)
from novelvideo.freezone.motion_draft.schema import (
    MICROSECOND,
    AudioClip,
    MotionDraftSpec,
    TextClip,
    VideoClip,
    build_draft_content,
    draft_path,
    to_microseconds,
    total_duration_us,
)
from novelvideo.freezone.motion_draft.writer import (
    DraftWriteResult,
    jianying_projects_dir,
    template_dir,
    write_motion_draft,
)

__all__ = [
    "MICROSECOND",
    "MOTION_RENDER_TASK_TYPE",
    "SCORE_GAIN_UNDER_BGM",
    "SCORE_GAIN_UNDER_NARRATION",
    "AudioClip",
    "ClipInput",
    "DraftWriteResult",
    "MotionDraftInputError",
    "MotionDraftSpec",
    "MotionRenderClip",
    "TextClip",
    "VideoClip",
    "beats_to_microseconds",
    "build_draft_content",
    "build_spec",
    "draft_path",
    "jianying_projects_dir",
    "load_sidecar",
    "pick_float",
    "pick_int",
    "read_clips",
    "resolve_media_path",
    "scan_motion_renders",
    "sidecar_has_score",
    "sidecar_path_for",
    "template_dir",
    "to_microseconds",
    "total_duration_us",
    "write_motion_draft",
]
