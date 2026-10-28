"""Freezone 视频节点辅助逻辑。

包含：
- 文生视频运镜模板库
- 角色素材库本地持久化
- 视频提示词组装
- 全能参考输入校验
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from novelvideo.freezone.paths import freezone_root
from novelvideo.usage.video_duration import (
    normalize_video_duration_for_backend as normalize_video_duration_for_backend,
    video_duration_bounds_for_backend,
)

logger = logging.getLogger(__name__)

# 资产库是「整读整写」的 JSON 文件，本进程内多个写入方可能同时落笔（工具页批量
# 任务在线程池里并发完成、完成后自动登记产物）。不加锁时后写会覆盖先写、静默
# 丢条目，所以所有库文件写函数共用这一把锁：锁内只有同步 IO，不会与协程互锁。
_LIBRARY_WRITE_LOCK = threading.Lock()

VIDEO_CAMERA_TEMPLATES: list[dict[str, str]] = [
    {
        "id": "locked_off",
        "name": "固定镜头",
        "prompt": "镜头固定，机位稳定，不推不摇不移，由角色和环境自然完成表演。",
    },
    {
        "id": "follow_tracking",
        "name": "跟随拍摄",
        "prompt": "镜头持续跟随主体移动，保持主角始终处于视觉中心，运动自然顺滑。",
    },
    {
        "id": "orbit_up",
        "name": "盘旋抬升",
        "prompt": "镜头围绕主体盘旋，同时缓慢抬升，营造空间展开和情绪提升。",
    },
    {
        "id": "orbit_down",
        "name": "盘旋下降",
        "prompt": "镜头围绕主体盘旋，同时缓慢下降，营造压迫感和沉浸式包围。",
    },
    {
        "id": "tilt_up",
        "name": "镜头上摇",
        "prompt": "镜头从下往上平滑上摇，逐步揭示主体上方信息与空间高度。",
    },
    {
        "id": "tilt_down",
        "name": "镜头下摇",
        "prompt": "镜头从上往下平滑下摇，逐步聚焦主体动作与地面细节。",
    },
    {
        "id": "pan_left",
        "name": "镜头左摇",
        "prompt": "镜头向左平滑横摇，带出画面左侧环境与叙事信息。",
    },
    {
        "id": "pan_right",
        "name": "镜头右摇",
        "prompt": "镜头向右平滑横摇，带出画面右侧环境与叙事信息。",
    },
    {
        "id": "pedestal_up",
        "name": "镜头上升",
        "prompt": "镜头整体垂直上升，视角逐步抬高，增强空间层次和临场感。",
    },
    {
        "id": "pedestal_down",
        "name": "镜头下降",
        "prompt": "镜头整体垂直下降，视角逐步压低，强化人物压迫和沉浸感。",
    },
    {
        "id": "truck_left",
        "name": "镜头左移",
        "prompt": "镜头整体向左平移，保持运镜稳定，突出场景横向调度。",
    },
    {
        "id": "truck_right",
        "name": "镜头右移",
        "prompt": "镜头整体向右平移，保持运镜稳定，突出场景横向调度。",
    },
]

FREEZONE_DEFAULT_VIDEO_BACKEND = "newapi_seedance-2.0-fast"


def get_video_camera_templates() -> list[dict[str, str]]:
    return [dict(item) for item in VIDEO_CAMERA_TEMPLATES]


def get_video_camera_template(template_id: str | None) -> dict[str, str] | None:
    if not template_id:
        return None
    for item in VIDEO_CAMERA_TEMPLATES:
        if item["id"] == template_id:
            return dict(item)
    return None


def normalize_video_aspect_ratio(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if text in {"auto", "adaptive"}:
        return "auto"
    if not text:
        return "16:9"
    return text


def normalize_video_resolution(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "720p"
    return text


FREEZONE_SEEDANCE2_RESOLUTION_OPTIONS_BY_MODEL: dict[str, tuple[str, ...]] = {
    "seedance-2.0-fast": ("480p", "720p"),
    "seedance-2.0": ("480p", "720p", "1080p"),
    "seedance-2.0-value": ("720p", "1080p"),
    "seedance-2.0-fast-value": ("720p", "1080p"),
}
FREEZONE_DEFAULT_VIDEO_RESOLUTION_OPTIONS = ("480p", "720p", "1080p")
FREEZONE_DEFAULT_SEEDANCE2_RESOLUTION_OPTIONS = ("480p", "720p")


def _freezone_video_model_from_backend(backend: str | None) -> str:
    text = str(backend or "").strip().lower()
    if text.startswith("newapi_"):
        return text[len("newapi_") :].strip()
    return text


def freezone_video_resolution_options(backend: str | None) -> tuple[str, ...]:
    model = _freezone_video_model_from_backend(backend)
    if "seedance-2.0" in model:  # [aipdd fork delta #6] 子串匹配,兼容 "AP Seedance-2.0 标准版"
        return FREEZONE_SEEDANCE2_RESOLUTION_OPTIONS_BY_MODEL.get(
            model,
            FREEZONE_DEFAULT_SEEDANCE2_RESOLUTION_OPTIONS,
        )
    return FREEZONE_DEFAULT_VIDEO_RESOLUTION_OPTIONS


def is_freezone_seedance2_value_backend(backend: str | None) -> bool:
    model = _freezone_video_model_from_backend(backend)
    return model in {"seedance-2.0-value", "seedance-2.0-fast-value"}


def default_freezone_seedance2_scene_optimize(backend: str | None) -> str:
    model = _freezone_video_model_from_backend(backend)
    return "realistic" if model == "seedance-2.0-fast-value" else "anime"


def normalize_freezone_seedance2_scene_optimize(
    backend: str | None,
    value: str | None,
) -> str:
    if not is_freezone_seedance2_value_backend(backend):
        return ""
    text = str(value or "").strip().lower()
    if text in {"anime", "realistic"}:
        return text
    return default_freezone_seedance2_scene_optimize(backend)


def normalize_video_resolution_for_backend(
    backend: str | None,
    value: str | None,
    configured_options: list[str] | tuple[str, ...] | None = None,
) -> str:
    resolution = normalize_video_resolution(value)
    configured = tuple(
        str(option).strip()
        for option in (configured_options or ())
        if str(option).strip()
    )
    if configured:
        matched = next(
            (option for option in configured if option.lower() == resolution.lower()),
            None,
        )
        if matched is not None:
            return normalize_video_resolution(matched)
        preferred = next(
            (option for option in configured if option.lower() == "720p"),
            None,
        )
        return normalize_video_resolution(preferred or configured[0])
    options = freezone_video_resolution_options(backend)
    if resolution in options:
        return resolution
    if "720p" in options:
        return "720p"
    return options[0]


def freezone_video_duration_bounds(
    backend: str | None,
) -> tuple[int | None, int | None]:
    return video_duration_bounds_for_backend(backend)


def _freezone_newapi_video_options() -> dict[str, str]:
    """返回 freezone 视频后端选项：与配置的媒体模型定义同源，不做写死白名单。"""
    from novelvideo.generators.video_generator import newapi_video_backend_options

    options = dict(newapi_video_backend_options())
    if FREEZONE_DEFAULT_VIDEO_BACKEND not in options:
        return options
    ordered = {FREEZONE_DEFAULT_VIDEO_BACKEND: options[FREEZONE_DEFAULT_VIDEO_BACKEND]}
    ordered.update(
        (key, value)
        for key, value in options.items()
        if key != FREEZONE_DEFAULT_VIDEO_BACKEND
    )
    return ordered


def get_freezone_video_model_options() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = []
    for backend, label in _freezone_newapi_video_options().items():
        duration_bounds = freezone_video_duration_bounds(backend)
        item = {
            "id": backend,
            "providerId": "newapi",
            "provider": "newapi",
            "apiModel": backend,
            "api_model": backend,
            "label": label,
            "backend": backend,
            "resolutionOptions": list(freezone_video_resolution_options(backend)),
            "resolution_options": list(freezone_video_resolution_options(backend)),
            "minDuration": duration_bounds[0],
            "min_duration": duration_bounds[0],
            "maxDuration": duration_bounds[1],
            "max_duration": duration_bounds[1],
        }
        if is_freezone_seedance2_value_backend(backend):
            item.update(
                {
                    "sceneOptimizeOptions": ["anime", "realistic"],
                    "scene_optimize_options": ["anime", "realistic"],
                    "defaultSceneOptimize": default_freezone_seedance2_scene_optimize(
                        backend
                    ),
                    "default_scene_optimize": default_freezone_seedance2_scene_optimize(
                        backend
                    ),
                }
            )
        data.append(item)
    return data


def get_freezone_video_model_names() -> list[str]:
    return list(_freezone_newapi_video_options().keys())


def resolve_freezone_video_backend(model: str | None) -> str:
    text = str(model or "").strip()
    options = _freezone_newapi_video_options()
    if not text:
        if FREEZONE_DEFAULT_VIDEO_BACKEND in options:
            return FREEZONE_DEFAULT_VIDEO_BACKEND
        if options:
            return next(iter(options))
        raise ValueError("no video model configured")
    if text in options:
        return text

    folded = text.casefold()
    for backend, label in options.items():
        if label.casefold() == folded:
            return backend

    from novelvideo.generators.video_generator import (
        LOCALAPI_DISABLED_VIDEO_MODELS,
        parse_newapi_video_backend,
    )

    # [aipdd fork delta] 负向过滤器：被禁用的网关模型（非 5 渠道）即使显式传入也拒绝。
    if _freezone_video_model_from_backend(text) in LOCALAPI_DISABLED_VIDEO_MODELS:
        raise ValueError(f"unknown video model: {text}")
    if parse_newapi_video_backend(text):
        return text
    raise ValueError(f"unknown video model: {text}")


def is_freezone_seedance2_backend(backend: str | None) -> bool:
    text = str(backend or "").strip()
    if text == "seedance_2":
        return True

    from novelvideo.generators.video_generator import parse_newapi_video_backend

    model = parse_newapi_video_backend(text)
    return bool(model and "seedance-2.0" in model.lower())  # [aipdd fork delta #6]


def is_freezone_seedance_backend(backend: str | None) -> bool:
    """任何代际的 Seedance（1.0/1.5/2.x）——参考图尺寸规则只对它们成立。"""
    text = str(backend or "").strip()
    if text in {"seedance_2", "seedance_fast"}:
        return True

    from novelvideo.generators.video_generator import parse_newapi_video_backend

    model = parse_newapi_video_backend(text)
    return bool(model and "seedance" in model.lower())  # [aipdd fork delta #6]


def _coarse_mark_region(mark: dict[str, Any]) -> str:
    px = mark.get("point_x")
    py = mark.get("point_y")
    if not isinstance(px, (int, float)) or not isinstance(py, (int, float)):
        box_x = mark.get("box_x")
        box_y = mark.get("box_y")
        box_width = mark.get("box_width")
        box_height = mark.get("box_height")
        if all(
            isinstance(value, (int, float))
            for value in [box_x, box_y, box_width, box_height]
        ):
            px = float(box_x) + float(box_width) / 2.0
            py = float(box_y) + float(box_height) / 2.0
    if isinstance(px, (int, float)) and isinstance(py, (int, float)):
        horizontal = "左侧" if px < 0.33 else "右侧" if px > 0.66 else "中部"
        vertical = "上方" if py < 0.33 else "下方" if py > 0.66 else "中间"
        return f"{horizontal}{vertical}"
    return ""


def format_video_marks(marks: list[dict[str, Any]] | None) -> str:
    clean_marks = [
        mark for mark in (marks or []) if str(mark.get("label") or "").strip()
    ]
    if not clean_marks:
        return ""

    lines: list[str] = []
    for mark in clean_marks:
        label = str(mark.get("label") or "").strip()
        region = _coarse_mark_region(mark)
        note = str(mark.get("note") or "").strip()
        suffix_parts = [part for part in [region, note] if part]
        suffix = f"（{'，'.join(suffix_parts)}）" if suffix_parts else ""
        lines.append(f"- {label}{suffix}")
    return "重点元素标记：\n" + "\n".join(lines)


def build_freezone_video_prompt(
    *,
    user_prompt: str,
    camera_template_id: str | None = None,
    character_names: list[str] | None = None,
    marks: list[dict[str, Any]] | None = None,
) -> str:
    parts = [str(user_prompt or "").strip()]

    template = get_video_camera_template(camera_template_id)
    if template:
        parts.append(f"运镜模板：{template['name']}。{template['prompt']}")

    if character_names:
        joined = "、".join(name for name in character_names if name)
        if joined:
            parts.append(
                f"角色一致性要求：保持 {joined} 的外观、服装和身份特征稳定一致。"
            )

    marks_block = format_video_marks(marks)
    if marks_block:
        parts.append(marks_block)

    parts.append(
        "输出要求：生成单条连贯视频镜头，动作自然，运动平滑，避免闪烁、变形、跳帧和主体身份漂移。"
    )
    return "\n".join(part for part in parts if part)


def build_freezone_image_to_video_prompt(
    *,
    user_prompt: str = "",
    camera_template_id: str | None = None,
    marks: list[dict[str, Any]] | None = None,
    reference_image_count: int = 1,
) -> str:
    parts: list[str] = []

    if user_prompt and user_prompt.strip():
        parts.append(user_prompt.strip())

    template = get_video_camera_template(camera_template_id)
    if template:
        parts.append(f"运镜模板：{template['name']}。{template['prompt']}")

    marks_block = format_video_marks(marks)
    if marks_block:
        parts.append(marks_block)

    if int(reference_image_count or 1) > 1:
        parts.append(
            "图片参考约束：综合参考多张输入图片，优先保持主体身份、外观、服装、场景线索与整体风格一致，"
            "不要把多张图拼贴成多画面。"
        )
    else:
        parts.append(
            "图片参考约束：把输入图片作为主体、外观、色调、质感和整体风格参考，由提示词主导视频内容；"
            "不要强制把输入图片锁定为视频第一帧。"
        )
    parts.append(
        "输出要求：生成单条连贯视频镜头，动作自然，运动平滑，避免闪烁、变形、跳帧和主体身份漂移。"
    )
    return "\n".join(part for part in parts if part)


def build_freezone_video_extend_prompt(
    *,
    user_prompt: str,
    camera_template_id: str | None = None,
) -> str:
    """Build an explicit continuation prompt without reference/edit wording."""

    parts = [str(user_prompt or "").strip()]
    template = get_video_camera_template(camera_template_id)
    if template:
        parts.append(f"运镜模板：{template['name']}。{template['prompt']}")
    parts.append(
        "视频延长要求：从输入视频的结尾继续生成，保持主体身份、场景、光线、镜头运动、"
        "声音与叙事连续，不要重做或编辑输入视频已有内容。"
    )
    return "\n".join(part for part in parts if part)


def build_freezone_keyframe_video_prompt(
    *,
    user_prompt: str = "",
    camera_template_id: str | None = None,
    marks: list[dict[str, Any]] | None = None,
    has_first_frame: bool = True,
    has_last_frame: bool = True,
) -> str:
    parts: list[str] = []

    if user_prompt and user_prompt.strip():
        parts.append(user_prompt.strip())

    template = get_video_camera_template(camera_template_id)
    if template:
        parts.append(f"运镜模板：{template['name']}。{template['prompt']}")

    marks_block = format_video_marks(marks)
    if marks_block:
        parts.append(marks_block)

    if has_first_frame and has_last_frame:
        parts.append(
            "首尾帧约束：严格从首帧自然过渡到尾帧，保持主体身份、构图逻辑、光线与场景连续。"
        )
    elif has_first_frame:
        parts.append(
            "首帧约束：严格继承输入图片中的主体、构图、服装、光线和场景信息，把输入图作为视频首帧参考。"
        )
    elif has_last_frame:
        parts.append(
            "尾帧约束：以输入图片作为目标收束画面，确保镜头最终自然落到该主体状态和构图。"
        )

    parts.append(
        "输出要求：生成单条连贯视频镜头，动作自然，运动平滑，避免闪烁、变形、跳帧、主体身份漂移和首尾帧跳变。"
    )
    return "\n".join(part for part in parts if part)


# [aipdd fork delta req42] 全能参考里「替换视频人物 / 替换视频产品 / 对口型」的画幅、
# 时长、主体都写在**源视频**里，用户往往只留一句空提示词。旧口径只拼一段通用模板，
# 模型把源视频当灵感而不是基准，产物容易与原视频毫不相干。这里按「工具 + 参考素材
# 组成」补一条明确的任务指令：以源视频为画面基准，只改要改的那一样东西。
_OMNI_SUBJECT_LABELS: dict[str, str] = {
    "replace-product": "产品",
    "replace-person": "人物",
}


def build_omni_source_video_directive(
    reference_items: list[dict[str, Any]] | None,
    source_tool: str = "",
) -> str:
    """源视频 + 参考素材组合下的任务指令；不属于这些组合时返回空串。"""

    counts = summarize_omni_reference_counts(reference_items or [])
    has_video = counts["video_count"] >= 1
    tool = str(source_tool or "").strip().lower()
    if has_video and counts["image_count"] >= 1:
        label = _OMNI_SUBJECT_LABELS.get(tool) or "与参考图对应的主体"
        action = (
            f"把源视频画面中的{label}替换为参考图所示的{label}"
            if tool in _OMNI_SUBJECT_LABELS
            else f"以参考图所示对象替换源视频画面中的{label}"
        )
        return (
            f"任务：{action}。必须完整保留源视频的场景、构图、镜头运动、剪辑节奏、"
            "时长与其余主体动作，只改变被替换对象的外观；参考图仅提供该对象的样子，"
            "不要把它当成新画面重拍、不要把镜头搬到参考图上。"
        )
    if has_video and counts["audio_count"] >= 1:
        return (
            "任务：按驱动音频为人物配音并对齐口型。必须保留源视频的画面、构图、"
            "镜头运动、剪辑节奏与时长，只改变人物口型与发声，不要重新编排画面。"
        )
    return ""


def build_freezone_omni_video_prompt(
    *,
    user_prompt: str,
    theme: str = "",
    camera_template_id: str | None = None,
    marks: list[dict[str, Any]] | None = None,
    reference_items: list[dict[str, Any]] | None = None,
    source_tool: str = "",
) -> str:
    parts = [str(user_prompt or "").strip()]
    directive = build_omni_source_video_directive(reference_items, source_tool)
    if directive:
        parts.append(directive)

    if theme and theme.strip():
        parts.append(f"主题要求：{theme.strip()}")

    template = get_video_camera_template(camera_template_id)
    if template:
        parts.append(f"运镜模板：{template['name']}。{template['prompt']}")

    marks_block = format_video_marks(marks)
    if marks_block:
        parts.append(marks_block)

    parts.append(
        "全能参考模式要求：综合文本、图像、视频、音频、文件和网页链接参考进行统一建模，"
        "优先保持主体身份、场景连续性、风格一致性和动作自然性。"
    )
    counts = summarize_omni_reference_counts(reference_items or [])
    single_video_reference_instruction = "这是视频参考生成新的视频，不是视频编辑。"
    if (
        counts["video_count"] == 1
        and counts["image_count"] == 0
        and counts["audio_count"] == 0
        and counts["file_count"] == 0
        and counts["link_count"] == 0
        and single_video_reference_instruction not in "\n".join(parts)
    ):
        parts.append(single_video_reference_instruction)
    parts.append(
        "输出要求：生成单条连贯视频镜头，动作自然，运动平滑，避免闪烁、变形、跳帧和主体身份漂移。"
    )
    return "\n".join(part for part in parts if part)


def summarize_omni_reference_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    image_count = sum(1 for item in items if str(item.get("type")) == "image")
    video_count = sum(1 for item in items if str(item.get("type")) == "video")
    audio_count = sum(1 for item in items if str(item.get("type")) == "audio")
    file_count = sum(1 for item in items if str(item.get("type")) == "file")
    link_count = sum(1 for item in items if str(item.get("type")) == "link")
    return {
        "image_count": image_count,
        "video_count": video_count,
        "audio_count": audio_count,
        "file_count": file_count,
        "link_count": link_count,
        "total_count": image_count + video_count + audio_count + file_count + link_count,
    }


def validate_omni_reference_limits(
    items: list[dict[str, Any]],
    *,
    image_max: int = 9,
    video_max: int = 3,
    audio_max: int = 3,
    file_max: int = 0,
    link_max: int = 0,
    total_max: int = 12,
) -> None:
    counts = summarize_omni_reference_counts(items)
    if counts["total_count"] > total_max:
        raise ValueError(f"references total count must be <= {total_max}")
    if counts["image_count"] > image_max:
        raise ValueError(f"image references count must be <= {image_max}")
    if counts["video_count"] > video_max:
        raise ValueError(f"video references count must be <= {video_max}")
    if counts["audio_count"] > audio_max:
        raise ValueError(f"audio references count must be <= {audio_max}")
    if counts["file_count"] > file_max:
        raise ValueError(f"file references count must be <= {file_max}")
    if counts["link_count"] > link_max:
        raise ValueError(f"link references count must be <= {link_max}")
    if counts["file_count"] and counts["link_count"]:
        raise ValueError("reference_file and reference_link are mutually exclusive")


# 火山 Seedance 对参考图的硬规则（从 400 报文里实测）：
#   `[InvalidParameter.HeightTooSmall] Height must be between 300px and 6000px.`
# 宽高比 0.4~2.5 与 seedance2_i2v/assets.py 里分镜路径用的同一组数字。
MIN_OMNI_REFERENCE_IMAGE_PX = 300
MAX_OMNI_REFERENCE_IMAGE_PX = 6000
MIN_OMNI_REFERENCE_IMAGE_ASPECT = 0.4
MAX_OMNI_REFERENCE_IMAGE_ASPECT = 2.5


def _read_reference_image_size(path: Path) -> tuple[int, int] | None:
    """只读文件头拿尺寸；读不出（文件不存在 / 不是图片）返回 None 交给厂商判。"""
    try:
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
    except Exception:  # noqa: BLE001 - 任何读取失败都不该替厂商拦请求
        return None
    if width <= 0 or height <= 0:
        return None
    return int(width), int(height)


def validate_omni_reference_image_dimensions(
    paths: list[str | Path | None],
    *,
    min_px: int = MIN_OMNI_REFERENCE_IMAGE_PX,
    max_px: int = MAX_OMNI_REFERENCE_IMAGE_PX,
    min_aspect: float = MIN_OMNI_REFERENCE_IMAGE_ASPECT,
    max_aspect: float = MAX_OMNI_REFERENCE_IMAGE_ASPECT,
) -> None:
    """参考图尺寸兜底校验，入队前拦住厂商必拒的图，省掉一次预扣 + 上传 + 退款。

    与 `validate_omni_reference_audio_durations` 同一口径：读不出尺寸的条目**不参与判定**。
    先报边长越界，再报宽高比，两类同时出现时只报前者。
    """
    out_of_range: list[str] = []
    bad_aspect: list[str] = []
    for raw in paths:
        text = str(raw or "").strip()
        if not text:
            continue
        path = Path(text)
        size = _read_reference_image_size(path)
        if size is None:
            continue
        width, height = size
        label = path.name or text
        if not (min_px <= width <= max_px and min_px <= height <= max_px):
            out_of_range.append(f"{label} ({width}x{height})")
            continue
        aspect = width / height
        if not (min_aspect <= aspect <= max_aspect):
            bad_aspect.append(f"{label} ({width}x{height}, {aspect:.2f})")
    if out_of_range:
        raise ValueError(
            f"参考图尺寸不符合要求，宽度和高度需在 {min_px} 至 {max_px} 像素之间: "
            + ", ".join(out_of_range)
        )
    if bad_aspect:
        raise ValueError(
            f"参考图宽高比需在 {min_aspect:g} 至 {max_aspect:g} 之间: "
            + ", ".join(bad_aspect)
        )


async def run_trusted_freezone_video_gen(
    *,
    project_dir: Path,
    job_id: str,
    prompt: str,
    egress_context: object,
    task_type: str,
    episode: int,
    beat_num: int,
    scope: str,
    reference_items: list[dict[str, str]] | None = None,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    duration_seconds: int = 5,
    generate_audio: bool = False,
    human_review: bool = False,
    scene_optimize: str | None = None,
    backend: str = "newapi_seedance-2.0-fast",
    last_frame_path: str | None = None,
    audio_setting: str | None = None,
    gen_mode: str | None = None,
    model_params: dict[str, Any] | None = None,
    request_schema: dict[str, Any] | None = None,
) -> Path:
    """Run the Freezone video leaf without losing its verified egress context."""

    from novelvideo.freezone.jobs import outputs_dir
    from novelvideo.generators.video_generator import (
        ShotReference,
        VideoGenStatus,
        create_video_generator,
    )

    output_path = outputs_dir(project_dir, "freezone_video_gen") / f"{job_id}.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    references = [
        ShotReference(
            str(item.get("type") or "image"),
            str(item.get("path") or ""),
            str(item.get("role") or ""),
        )
        for item in (reference_items or [])
        if str(item.get("path") or "").strip()
    ]
    generator = create_video_generator(
        backend=backend,
        resolution=resolution,
        generate_audio=generate_audio,
        model_params=model_params,
        request_schema=request_schema,
        egress_context=egress_context,
    )
    first_image = next((ref for ref in references if ref.type == "image"), None)
    generate_kwargs: dict[str, Any] = {
        "image_path": first_image.path if first_image is not None else None,
        "prompt": prompt,
        "output_path": str(output_path),
        "aspect_ratio": aspect_ratio,
        "duration": float(duration_seconds),
        "last_frame_path": last_frame_path,
        "references": references,
        "human_review": bool(human_review),
        "seedance2_config": (
            {"scene_optimize": scene_optimize} if scene_optimize else None
        ),
        "gen_mode": gen_mode,
        "egress_context": egress_context,
        "task_type": task_type,
        "episode": episode,
        "beat_num": beat_num,
        "scope": scope,
    }
    if audio_setting:
        generate_kwargs["audio_setting"] = audio_setting
    result = await generator.generate(**generate_kwargs)
    if not result or result.status is not VideoGenStatus.DONE:
        error = result.error if result else "video generation failed"
        raise RuntimeError(f"freezone video generation failed: {error}")
    if not output_path.exists():
        raise RuntimeError("video generation returned success without output")
    return output_path

# 全能参考音频时长：厂商（doubao-seedance-2-0 / r2v）有**两条互相独立**的规则，
# 两条都以 400 打回，只卡其中一条就等于没卡：
#   1. 逐条：`[InvalidParameter.DurationTooShort] Duration must be between 1.8s and 15.2s`
#   2. 总和：`the parameter audio total duration (seconds) specified in the request must
#      be less than or equal to 15.2 for model doubao-seedance-2-0 in r2v`
#
# 第 2 条是 2026-08-06 从 3060 环境两次失败任务里实测抓到的
# （freezone_video_gen/01KZ5R8ZZZY9M8T9F01H159RP7，gen_mode=allReference）。在那之前
# 前后端都只按第 1 条判定，3 条各 6s 每条都合法、总计 18s 必被厂商拒——用户白等一轮。
# 别再把总时长这条当成「我们自己臆想的规则」删掉。
MIN_OMNI_REFERENCE_AUDIO_SECONDS = 1.8
MAX_OMNI_REFERENCE_AUDIO_SECONDS = 15.2
MAX_OMNI_REFERENCE_AUDIO_TOTAL_SECONDS = 15.2


def _format_seconds(value: float) -> str:
    """按毫秒精度展示，去掉无意义尾随 0：15.2 → `15.2`、6.0 → `6`、1.799 → `1.799`。

    不能 `round(x, 1)`：15.201 显示成「15.2」时，用户看到的正好是合法边界值却被告知
    越界，只会怀疑我们算错了。前端 `formatClipSeconds` 是同一口径。
    """
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _exceeds(value: float, limit: float) -> bool:
    """`value > limit`，但先归整到毫秒。

    浮点和会自己漂出去：6 + 6 + 3.2 == 15.200000000000001，裸比较会把一组正好顶格
    15.2s 的合法音频判成超限。
    """
    return round(value - limit, 3) > 0


def validate_omni_reference_audio_durations(
    durations: list[tuple[str, float | None]],
    *,
    min_seconds: float | None = MIN_OMNI_REFERENCE_AUDIO_SECONDS,
    max_seconds: float | None = MAX_OMNI_REFERENCE_AUDIO_SECONDS,
    total_min_seconds: float | None = None,
    total_max_seconds: float | None = MAX_OMNI_REFERENCE_AUDIO_TOTAL_SECONDS,
    media_label: str = "audio",
) -> None:
    """全能参考音频时长兜底校验，入参是 `(标签, 秒数)`，秒数 None = 探测不出。

    探测不出的条目**不参与判定**：ffprobe 缺失 / 文件读不了时，宁可放过去让厂商判，
    也不要凭空拦死一次正常提交。这让总和成为**下界**，但判定方向仍然安全——漏算只会
    让和更小，所以「算出来超了」必定真超，不存在因此产生的误拦。

    三个上限各自可以传 `None` = **这项不判定**。逐条边界（1.8~15.2s）是从 Seedance 2.0
    的报文里实测出来的，只对它成立；管理员在目录里配了总时长、但模型不是 2.0 时，调用方
    应当把 min/max 传 None ——拿 2.0 的数字去卡别家模型就是凭空 400。

    太短 → 单条太长 → 总和太长，逐类上报；同时越界时报一类比混在一起列更好读。
    """
    measured = [
        (label, float(seconds))
        for label, seconds in durations
        if isinstance(seconds, (int, float))
        and not isinstance(seconds, bool)
        and seconds > 0
    ]
    if not measured:
        return

    def _clips(items: list[tuple[str, float]]) -> str:
        return ", ".join(f"{label} ({_format_seconds(value)}s)" for label, value in items)

    too_short = (
        [item for item in measured if _exceeds(min_seconds, item[1])]
        if min_seconds is not None
        else []
    )
    if too_short:
        raise ValueError(
            f"{media_label} reference duration must be >= {_format_seconds(min_seconds)}s: "
            + _clips(too_short)
        )
    too_long = (
        [item for item in measured if _exceeds(item[1], max_seconds)]
        if max_seconds is not None
        else []
    )
    if too_long:
        raise ValueError(
            f"{media_label} reference duration must be <= {_format_seconds(max_seconds)}s: "
            + _clips(too_long)
        )
    total = sum(value for _, value in measured)
    # 总时长下限只有在每一条素材都成功探测时才可判定；漏测会让和偏小，不能据此误拦。
    if (
        total_min_seconds is not None
        and len(measured) == len(durations)
        and _exceeds(total_min_seconds, total)
    ):
        raise ValueError(
            f"{media_label} references total duration must be >= "
            f"{_format_seconds(total_min_seconds)}s, got {_format_seconds(total)}s: "
            + _clips(measured)
        )
    if total_max_seconds is not None and _exceeds(total, total_max_seconds):
        raise ValueError(
            f"{media_label} references total duration must be <= "
            f"{_format_seconds(total_max_seconds)}s, got {_format_seconds(total)}s: "
            + _clips(measured)
        )


def video_character_library_path(project_dir: Path) -> Path:
    return freezone_root(project_dir) / "video_character_library.json"


def load_video_character_library(project_dir: Path) -> list[dict[str, Any]]:
    path = video_character_library_path(project_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def save_video_character_library(
    project_dir: Path, items: list[dict[str, Any]]
) -> None:
    path = video_character_library_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


# [aipdd fork delta] 设置→风格管理里上传的封面要在资产库「风格」栏目里可见：封面
# 本体存 STATE 卷（跨项目共享）、不属于任何项目，所以读取时投影成虚拟只读条目，
# 不落盘。id 带 `style_gallery:` 前缀，改名/删除在路由层被拒。
STYLE_COVER_ITEM_PREFIX = "style_gallery:"


def compose_library_with_style_covers(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把已配封面的风格投影成资产库「风格」栏目的虚拟条目。

    与设置→风格管理同源（实时扫描清单 + 封面文件）：上传/换图/删除封面都即时
    反映，不产生双份存储；真实条目已引用同一封面 URL 时跳过，避免重复展示。
    """
    from urllib.parse import urlsplit

    from novelvideo.freezone import style_gallery

    rows = style_gallery.list_gallery_styles()
    if not rows:
        return items

    used_paths: set[str] = set()
    for item in items:
        candidates = [item.get("cover_url"), item.get("video_url"), item.get("audio_url")]
        urls = item.get("image_urls")
        if isinstance(urls, list):
            candidates.extend(urls)
        for value in candidates:
            if isinstance(value, str) and value:
                used_paths.add(urlsplit(value).path)

    projected: list[dict[str, Any]] = []
    for row in rows:
        style_id = str(row.get("id") or "").strip()
        url = str(row.get("cover_url") or "")
        if not style_id or not row.get("has_cover") or not url:
            continue
        if urlsplit(url).path in used_paths:
            continue
        updated_at: str | None = None
        cover = style_gallery.cover_path(style_id)
        if cover is not None:
            try:
                updated_at = datetime.fromtimestamp(cover.stat().st_mtime).isoformat()
            except OSError:
                updated_at = None
        projected.append(
            {
                "id": f"{STYLE_COVER_ITEM_PREFIX}{style_id}",
                "name": str(row.get("label") or style_id),
                "media": "image",
                "source": "upload",
                "category": "style",
                "folder": "style",
                "image_urls": [url],
                "video_url": None,
                "audio_url": None,
                "cover_url": url,
                "created_at": updated_at,
                "updated_at": updated_at,
            }
        )
    if not projected:
        return items
    return [*items, *projected]


# [aipdd fork delta] 新增「草图」一级类目：分镜草图作为可复用资产与人物/场景/道具并列。
# [aipdd fork delta] 新增「剪映草稿」一级类目：VectCut 跑出的剪映草稿也是资产，但它
# 不是可直接预览的媒体文件（media="draft"），条目只承载草稿 id（draft_id），下载走
# api 代理的草稿包（zip）端点，前端对这种条目单独渲染。
# [aipdd fork delta] 新增「俯视图（机位走位图）」类目：条目仍是 media="image"（渲染预览
# 图），但随条目内联一份可编辑场景 scene（TopDownScene JSON），从资产库送回画布时据此
# 生成新的俯视图节点、可继续编辑。与前端 assetLibraryItems.ts 的 ASSET_CATEGORIES 一致。
# [aipdd fork delta] 新增「故事版」类目：分镜脚本页 AI 生成的分镜板参考图（media="image"），
# 「镜头生成视频」「草图生成渲染图」选参考图时可取用。与前端 ASSET_CATEGORIES 一致。
# [aipdd fork delta req98] 新增「3D」「文本」类目：只收生成产物（media="model3d"/"text"），
# 与剪映草稿（draft）一样不走上传链路；URL 落在 model3d_url / text_url 字段。
LIBRARY_CATEGORIES = ("other", "character", "scene", "prop", "sketch", "style", "audio", "draft", "topdown", "storyVersion", "model3d", "text")

# 资产库目录树。文件夹（保存位置）和类目（标签）是两个独立维度：类目只管
# 「这素材是干嘛的」，文件夹管「它放在哪」。系统文件夹两个——主线同步来的一律
# 收进 mainline；本地上传缺省落在 other（前端显示为「待分类资产」）。类目 key
# 同时充当同名系统文件夹的 key，这样老条目（没有 folder 字段）按类目归位即可，
# 不需要数据迁移。用户新建的文件夹用随机 id，不会和这些保留 key 撞上。
MAINLINE_FOLDER_KEY = "mainline"
# [aipdd fork delta] 剪映草稿的类目 / 文件夹保留 key：只收 VectCut 产物，
# 上传路由据此拒绝普通素材落进「剪映草稿」。
DRAFT_FOLDER_KEY = "draft"
RESERVED_FOLDER_KEYS = (MAINLINE_FOLDER_KEY, *LIBRARY_CATEGORIES)
# 与前端 assetLibraryItems.ts 的系统文件夹名保持一致，防止用户建出同名文件夹。
RESERVED_FOLDER_NAMES = (
    "主线",
    "待分类资产",
    "其它",
    "人物",
    "场景",
    "物品",
    "草图",
    "风格",
    "音效",
    "剪映草稿",
    "俯视图",
    "故事版",
    "3D",
    "文本",
)
FOLDER_NAME_MAX_LEN = 20


def video_character_folders_path(project_dir: Path) -> Path:
    return freezone_root(project_dir) / "video_character_folders.json"


def load_video_character_folders(project_dir: Path) -> list[dict[str, Any]]:
    """读用户自建的资产库文件夹（系统文件夹不落盘，由前端按保留 key 生成）。"""
    path = video_character_folders_path(project_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("id")]


def save_video_character_folders(project_dir: Path, folders: list[dict[str, Any]]) -> None:
    path = video_character_folders_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(folders, ensure_ascii=False, indent=2), encoding="utf-8")


def add_video_character_folder(project_dir: Path, *, name: str) -> dict[str, Any]:
    """新建一个资产库文件夹。重名（含系统文件夹名）直接拒绝，避免目录里两个同名项。"""
    clean = name.strip()
    folders = load_video_character_folders(project_dir)
    _validate_folder_name(clean, folders)
    folder = {
        "id": uuid.uuid4().hex[:12],
        "name": clean,
        "created_at": datetime.now().isoformat(),
    }
    folders.append(folder)
    save_video_character_folders(project_dir, folders)
    return folder


def _validate_folder_name(clean: str, folders: list[dict[str, Any]], *, skip_id: str = "") -> None:
    """新建/重命名共用的一套校验：非空、不超长、不撞系统名、不与其它文件夹重名。"""
    if not clean:
        raise ValueError("folder name is required")
    if len(clean) > FOLDER_NAME_MAX_LEN:
        raise ValueError(f"folder name must be <= {FOLDER_NAME_MAX_LEN} characters")
    if clean in RESERVED_FOLDER_NAMES:
        raise ValueError(f"folder name is reserved: {clean}")
    for folder in folders:
        if skip_id and str(folder.get("id")) == skip_id:
            continue
        if str(folder.get("name") or "").strip() == clean:
            raise ValueError(f"folder already exists: {clean}")


def update_video_character_folder(
    project_dir: Path,
    folder_id: str,
    *,
    name: str | None = None,
    cover: str | None = None,
) -> dict[str, Any] | None:
    """改名 / 换封面。只动传进来的字段，两者都不传视作空操作。

    封面存的是素材本身的 URL（前端从文件夹里挑一张），所以不需要额外的文件管理；
    素材被删掉后封面会指向失效 URL，前端按缺省封面渲染即可。
    """
    folders = load_video_character_folders(project_dir)
    target = next((f for f in folders if str(f.get("id")) == folder_id), None)
    if target is None:
        return None
    if name is not None:
        clean = name.strip()
        _validate_folder_name(clean, folders, skip_id=folder_id)
        target["name"] = clean
    if cover is not None:
        target["cover"] = cover.strip() or None
    save_video_character_folders(project_dir, folders)
    return target


def delete_video_character_folder(project_dir: Path, folder_id: str) -> int | None:
    """整柜清空：删掉文件夹本身，连同落在里面的素材条目。

    返回被删掉的素材条数；文件夹不存在时返回 ``None``。系统文件夹（主线/类目同名
    目录）不落盘，也就永远走不到这里——路由层按 id 找不到直接 404。
    """
    folders = load_video_character_folders(project_dir)
    kept_folders = [f for f in folders if str(f.get("id")) != folder_id]
    if len(kept_folders) == len(folders):
        return None
    with _LIBRARY_WRITE_LOCK:
        items = load_video_character_library(project_dir)
        kept_items = [item for item in items if str(item.get("folder") or "") != folder_id]
        removed = len(items) - len(kept_items)
        if removed:
            save_video_character_library(project_dir, kept_items)
        save_video_character_folders(project_dir, kept_folders)
    return removed


def library_folder_keys(project_dir: Path) -> set[str]:
    """当前可用作「保存位置」的文件夹 key：系统保留 key + 用户自建文件夹 id。"""
    keys = set(RESERVED_FOLDER_KEYS)
    for folder in load_video_character_folders(project_dir):
        keys.add(str(folder.get("id")))
    return keys


def _resolve_library_folder(
    folder: str | None,
    *,
    existing: dict[str, Any] | None,
    source: str,
    category: str,
) -> str:
    """定出条目落在哪个文件夹，与前端 assetLibraryItems.ts 的归位逻辑保持一致。

    显式指定优先；其次沿用条目已有的位置（重复同步/重复登记不能把用户挪好的
    位置冲掉）；最后兜底——主线同步来的进 mainline，本地上传按类目进同名系统
    文件夹（没归类的就是 other，即「待分类资产」）。
    """
    if folder:
        return str(folder)
    if existing is not None:
        kept = existing.get("folder")
        if kept:
            return str(kept)
    if source != "upload":
        return MAINLINE_FOLDER_KEY
    return category


def _resolve_library_category(
    category: str | None,
    *,
    existing: dict[str, Any] | None,
    source: str,
    media: str,
) -> str:
    """定出条目的用途类目，与前端 assetLibraryItems.ts 的 deriveCategory 保持一致。

    优先用显式传入的类目；其次沿用条目已有的类目（主线重复同步不能把用户归好的
    类冲掉）；最后按来源/媒介兜底——人物/场景/道具对号入座，音频归音效，其余归其它。
    """
    if category in LIBRARY_CATEGORIES:
        return str(category)
    if existing is not None:
        kept = existing.get("category")
        if kept in LIBRARY_CATEGORIES:
            return str(kept)
    if source in ("character", "scene", "prop", "sketch"):
        return source
    if media == "audio":
        return "audio"
    # [aipdd fork delta req98] 3D / 文本载体对号入座同名标签（同音频→音效机制）。
    if media in ("model3d", "text"):
        return str(media)
    return "other"


def _upsert_library_item(
    items: list[dict[str, Any]],
    *,
    name: str,
    image_urls: list[str] | None,
    media: str,
    source: str,
    video_url: str | None,
    audio_url: str | None,
    item_id: str | None,
    category: str | None = None,
    folder: str | None = None,
    draft_id: str | None = None,
    scene: dict[str, Any] | None = None,
    model3d_url: str | None = None,
    text_url: str | None = None,
) -> dict[str, Any]:
    """纯内存 upsert：按 id 就地更新或追加 ``items``，返回写入的条目。

    不做任何磁盘 IO，供单条登记与批量同步复用（后者一次读、一次写即可）。
    """
    now = datetime.now().isoformat()
    urls = list(image_urls or [])
    if media == "video":
        cover = video_url
    elif media in ("audio", "model3d", "text", "draft"):
        cover = None
    else:
        cover = urls[0] if urls else None
    resolved_id = item_id or uuid.uuid4().hex[:12]
    existing_idx = next(
        (i for i, it in enumerate(items) if it.get("id") == resolved_id), None
    )
    existing = items[existing_idx] if existing_idx is not None else None
    # [aipdd fork delta] 保存位置落在系统类目同名文件夹、又没给标签时，标签跟随位置：
    # 往「风格」文件夹传图就是在传风格素材，不该按上传兜底进「其它」。旧行为（拖拽
    # 上传会带上当时的筛选类目）写出过 folder=style / category=character 的错位条目，
    # 资产库顶部「风格」标签下永远看不到它。已有有效类目的条目不被改写——用户手动
    # 归过的标签要留住。
    if (
        category is None
        and source == "upload"
        and folder in LIBRARY_CATEGORIES
        and (existing is None or existing.get("category") not in LIBRARY_CATEGORIES)
    ):
        category = str(folder)
    resolved_category = _resolve_library_category(
        category, existing=existing, source=source, media=media
    )
    item = {
        "id": resolved_id,
        "name": name.strip(),
        "media": media,
        "source": source,
        "category": resolved_category,
        "folder": _resolve_library_folder(
            folder, existing=existing, source=source, category=resolved_category
        ),
        "image_urls": urls,
        "video_url": video_url,
        "audio_url": audio_url,
        # [aipdd fork delta req98] 3D / 文本载体的文件地址字段（只收生成产物）。
        "model3d_url": model3d_url,
        "text_url": text_url,
        "cover_url": cover,
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
    }
    # [aipdd fork delta] 剪映草稿条目的附属字段：draft_id 是草稿在 VectCut 服务里的
    # 唯一标识，草稿包下载与重复保存去重都靠它。
    if draft_id:
        item["draft_id"] = str(draft_id)
    # [aipdd fork delta] 俯视图草稿的可编辑场景：仿 draft_id 原样透传落库，避免被
    # 固定 schema 丢弃。非俯视图条目不带此字段。
    if scene is not None:
        item["scene"] = scene
    if existing_idx is not None:
        items[existing_idx] = item
    else:
        items.append(item)
    return item


def add_video_character_library_item(
    project_dir: Path,
    *,
    name: str,
    image_urls: list[str] | None = None,
    media: str = "image",
    source: str = "upload",
    video_url: str | None = None,
    audio_url: str | None = None,
    item_id: str | None = None,
    category: str | None = None,
    folder: str | None = None,
    draft_id: str | None = None,
    scene: dict[str, Any] | None = None,
    model3d_url: str | None = None,
    text_url: str | None = None,
) -> dict[str, Any]:
    """把一条素材登记到资产库。

    图片走 ``image_urls``，视频/音频走 ``video_url`` / ``audio_url``，3D 模型 /
    文本文件走 ``model3d_url`` / ``text_url``。``item_id``
    非空时按 id upsert（主线同步用稳定合成 id，重复同步是更新而非新增）。
    ``category`` 是用途类目（标签），``folder`` 是保存位置，两者互不影响，缺省时
    分别按来源/媒介、按类目兜底推导；``draft_id`` 只给剪映草稿条目（media="draft"）
    附上草稿在 VectCut 服务里的标识。
    """
    with _LIBRARY_WRITE_LOCK:
        items = load_video_character_library(project_dir)
        item = _upsert_library_item(
            items,
            name=name,
            image_urls=image_urls,
            media=media,
            source=source,
            video_url=video_url,
            audio_url=audio_url,
            item_id=item_id,
            category=category,
            folder=folder,
            draft_id=draft_id,
            scene=scene,
            model3d_url=model3d_url,
            text_url=text_url,
        )
        save_video_character_library(project_dir, items)
    return item


# [aipdd fork delta] 剪映草稿条目的稳定 id 前缀：<前缀><draft_id>，同一草稿重复保存走
# upsert 更新而不是新增。
VECTCUT_DRAFT_ITEM_ID_PREFIX = "vectcut-draft:"


def register_vectcut_draft_item(
    project_dir: Path,
    *,
    draft_id: str,
    name: str,
) -> dict[str, Any]:
    """把 VectCut 保存出的剪映草稿登记进资产库「剪映草稿」（单独类目 + 独立文件夹）。

    草稿不是图片/视频/音频：media 用 "draft"、条目带 ``draft_id``，不承载可直接
    预览的素材 URL，草稿包下载走 api 的草稿包代理端点（见 routes/freezone/library.py）。
    source 沿用 "upload"（非主线同步产物），类目与保存位置都显式落在 draft。
    """
    return add_video_character_library_item(
        project_dir,
        item_id=f"{VECTCUT_DRAFT_ITEM_ID_PREFIX}{draft_id}",
        name=name,
        media="draft",
        source="upload",
        category="draft",
        folder="draft",
        draft_id=draft_id,
    )


# [aipdd fork delta] 工具产物自动归档的条目 id 前缀：按任务 id 稳定生成，
# 重复登记走 upsert 更新而不是新增。
TOOL_LIBRARY_ITEM_ID_PREFIX = "tool-"


def archive_generated_library_item(
    project_dir: Path,
    *,
    item_id: str,
    name: str,
    media: str,
    url: str,
) -> dict[str, Any] | None:
    """把生成产物登记进资产库的「待分类资产」，失败返回 ``None``。

    自动归档是任务完成后的附加动作：产物已经落盘，任务记录里的「打开结果」不
    依赖这一步，登记失败不能反过来把已完成的任务翻成失败，所以这里吞掉异常只
    记日志。``name`` 由调用方按来源素材拼好，``media`` 决定 URL 填进哪个字段。
    """
    try:
        return add_video_character_library_item(
            project_dir,
            item_id=item_id,
            name=name,
            media=media,
            source="upload",
            # 显式落在「待分类资产」：source 非 upload 会被兜底进主线，位置口径
            # 以这里的显式 folder 为准。音频额外带上「音效」标签、3D/文本带
            # 同名标签（req98）；图片/视频保持无标签（folder=other 会把标签一并
            # 兜底成 other，即「待分类」）。
            folder="other",
            category="audio" if media == "audio" else str(media) if media in ("model3d", "text") else None,
            image_urls=[url] if media == "image" else None,
            video_url=url if media == "video" else None,
            audio_url=url if media == "audio" else None,
            model3d_url=url if media == "model3d" else None,
            text_url=url if media == "text" else None,
        )
    except Exception:
        logger.warning(
            "generated library item archive failed",
            extra={"item_id": item_id, "media": media},
            exc_info=True,
        )
        return None


# 资产名只用于展示，给个宽松上限防止有人贴一整段文案进来把列表撑爆。
LIBRARY_ITEM_NAME_MAX_LEN = 60


def rename_video_character_library_item(
    project_dir: Path, item_id: str, *, name: str
) -> dict[str, Any] | None:
    """给资产库条目改名。条目不存在返回 ``None``，名字非法抛 ``ValueError``。

    只动 ``name`` 和 ``updated_at``：URL / 类目 / 保存位置一概不碰，画布上已经引用
    该素材的节点不受影响。和文件夹改名不同，素材名不要求唯一——同名素材在库里是
    常态（同一角色的多张参考图）。
    """
    clean = name.strip()
    if not clean:
        raise ValueError("item name is required")
    if len(clean) > LIBRARY_ITEM_NAME_MAX_LEN:
        raise ValueError(f"item name must be <= {LIBRARY_ITEM_NAME_MAX_LEN} characters")
    with _LIBRARY_WRITE_LOCK:
        items = load_video_character_library(project_dir)
        target = next((item for item in items if str(item.get("id")) == item_id), None)
        if target is None:
            return None
        target["name"] = clean
        target["updated_at"] = datetime.now().isoformat()
        save_video_character_library(project_dir, items)
    return target


def delete_video_character_library_item(project_dir: Path, item_id: str) -> bool:
    with _LIBRARY_WRITE_LOCK:
        items = load_video_character_library(project_dir)
        kept = [item for item in items if item.get("id") != item_id]
        if len(kept) == len(items):
            return False
        save_video_character_library(project_dir, kept)
    return True


def sync_mainline_assets_into_library(
    project_dir: Path,
    *,
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把主线资产（已解析好 name/url/media/source/id）幂等写进资产库。

    ``assets`` 每项形如 ``{"id","name","media","source","url"}``。用稳定合成 id
    upsert，因此重复同步只更新 URL、不产生重复条目。返回同步后的完整库。

    整个批次只读一次、写一次库文件（内存里逐条 upsert），避免 N 条资产触发
    N 次全量 load+save 的 O(N²) IO。
    """
    with _LIBRARY_WRITE_LOCK:
        items = load_video_character_library(project_dir)
        changed = False
        for asset in assets:
            media = str(asset.get("media") or "image")
            url = asset.get("url") or ""
            if not url:
                continue
            _upsert_library_item(
                items,
                name=str(asset.get("name") or ""),
                media=media,
                source=str(asset.get("source") or "upload"),
                item_id=str(asset.get("id") or "") or None,
                category=str(asset.get("category") or "") or None,
                image_urls=[url] if media == "image" else None,
                video_url=url if media == "video" else None,
                audio_url=url if media == "audio" else None,
            )
            changed = True
        if changed:
            save_video_character_library(project_dir, items)
    return items
