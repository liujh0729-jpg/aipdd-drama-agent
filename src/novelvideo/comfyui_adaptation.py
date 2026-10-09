# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 本地 ComfyUI「AI 分析并自动配置」→ 适配文件。

为什么需要这一层：渠道里的 ComfyUI 工作流是用户自己的资产，后端此前只能按节点
``class_type`` 猜槽位（``comfyui_executor.bind_comfyui_workflow``），模型能力
（支持的模式 / 分辨率 / 宽高比 / 时长）也只能从名字 token 猜
（``api/routes/model_gateway/comfyui.py`` 的 ``_default_comfyui_media_model_config``）。
工作流一多、自定义节点一换名，猜错就是把提示词写进别的节点、把尺寸写进 upscale 节点。

本模块把「猜」换成「一次真实分析 + 一份可审计产物」：

1. 把 API 格式工作流压成节点摘要（含每个槽位的当前值与是否连线）；
2. 交给**已配置的文本模型**（显式选的分析模型，或文本功能位三层解析）产出严格 JSON：
   节点绑定 + 能力声明；
3. **严格校验**：绑定的节点 id 与输入名必须在工作流里真实存在且可写（不是连线占位），
   任何越界引用都按模型逐条报错，不静默丢弃、不改写用户的；
4. 落一份适配文件到 ``<STATE_DIR>/comfyui/adaptations/<渠道指纹>.json``，渠道设置里
   只存指针（``settings.comfyui.adaptation_by_model``）与能力声明
   （``settings.comfyui.capabilities_by_model``）。

消费方：执行器按显式节点绑定注入参数（``bind_comfyui_workflow(adaptation=...)``，
未覆盖的槽位仍回落 class_type 启发式）；渠道保存时把能力声明投影进媒体模型配置，
替代名字 token 猜测。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from novelvideo.shared import openai_compat

ADAPTATION_SCHEMA_VERSION = 1
ANALYZER_MAX_NODES = 240
ANALYZER_VALUE_MAX_CHARS = 160
ANALYZER_MAX_TOKENS = 3000
ANALYZER_TIMEOUT_SECONDS = 180.0
ADAPTATION_SUBDIR = ("comfyui", "adaptations")
MEDIA_TYPES = ("image", "video")
OUTPUT_KINDS = ("image", "video")
IMAGE_ROLES = ("first_frame", "last_frame", "reference")
# 媒体模型配置里允许被 AI 声明覆盖的键（其余键由用户在模型广场自行维护）。
CAPABILITY_KEYS = (
    "mediaType",
    "supportedModes",
    "resolutionOptions",
    "ratioOptions",
    "minDuration",
    "maxDuration",
    "referenceImageMax",
    "fps",
    "sizeStep",
)


class ComfyUIAdaptationError(RuntimeError):
    """面向用户的适配失败（成因原文可携带上游返回，不吞）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def channel_key(base_url: str) -> str:
    """渠道指纹：同一 ComfyUI 地址的适配集中在一个文件里（不含密钥，可安全入日志）。"""
    cleaned = str(base_url or "").strip().rstrip("/")
    return hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:16]


def _state_root() -> Path:
    configured = str(os.environ.get("NOVELVIDEO_STATE_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    from novelvideo.config import STATE_DIR

    return Path(STATE_DIR)


def adaptations_root() -> Path:
    """适配文件目录（``<STATE_DIR>/comfyui/adaptations``）。"""
    return _state_root().joinpath(*ADAPTATION_SUBDIR)


def adaptation_path(base_url: str) -> Path:
    return adaptations_root() / f"{channel_key(base_url)}.json"


def workflow_digest(workflow: dict[str, Any]) -> str:
    """工作流内容指纹：节点增删 / 槽位变化都会改变它，用于判断适配是否过期。"""
    canonical = json.dumps(
        workflow if isinstance(workflow, dict) else {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_link(value: Any) -> bool:
    """API 格式里 ``[节点id, 槽位]`` 表示这个输入是连线来的，不能写标量。"""
    return isinstance(value, list) and len(value) == 2


def build_workflow_summary(workflow: dict[str, Any]) -> dict[str, Any]:
    """把 API 格式工作流压成给模型看的节点摘要（纯函数，不触网）。"""

    nodes: list[dict[str, Any]] = []
    for node_id, spec in (workflow or {}).items():
        if not isinstance(spec, dict):
            continue
        inputs: dict[str, Any] = {}
        raw_inputs = spec.get("inputs") if isinstance(spec.get("inputs"), dict) else {}
        for name, value in raw_inputs.items():
            if _is_link(value):
                inputs[str(name)] = {"link": f"{value[0]}:{value[1]}"}
            else:
                text = value if isinstance(value, str) else json.dumps(
                    value, ensure_ascii=False, default=str
                )
                if len(text) > ANALYZER_VALUE_MAX_CHARS:
                    text = text[:ANALYZER_VALUE_MAX_CHARS] + "…"
                inputs[str(name)] = {"value": text}
        nodes.append(
            {
                "id": str(node_id),
                "classType": str(spec.get("class_type") or ""),
                "title": str(((spec.get("_meta") or {}) if isinstance(spec.get("_meta"), dict) else {}).get("title") or ""),
                "inputs": inputs,
            }
        )
        if len(nodes) >= ANALYZER_MAX_NODES:
            break
    return {
        "nodeCount": len(workflow or {}),
        "truncated": len(nodes) < len(workflow or {}),
        "nodes": nodes,
    }


def _system_prompt() -> str:
    return (
        "你是 ComfyUI 工作流适配分析师。用户给出一个 ComfyUI「API 格式」工作流的节点"
        "摘要，你要判断这份生成流程里：哪些节点接运行参数、产物是什么媒体类型、以及该"
        "工作流真实支持的生成能力。只输出一个 JSON 对象，不要 Markdown 代码块，不要任何"
        "解释文字。"
    )


def _user_prompt(model: str, workflow_name: str, summary: dict[str, Any]) -> str:
    schema = {
        "mediaType": "image 或 video（按输出节点判定）",
        "binding": {
            "prompt": {"node": "节点id", "input": "该节点里接正向提示词的输入名"},
            "negative": {"node": "节点id", "input": "输入名（没有负向节点就整项省略）"},
            "images": [
                {
                    "node": "节点id",
                    "input": "输入名",
                    "role": "first_frame|last_frame|reference",
                }
            ],
            "size": {"node": "节点id", "width": "宽输入名", "height": "高输入名"},
            "frames": {
                "node": "节点id",
                "input": "帧数输入名",
                "fps": {"node": "节点id", "input": "fps输入名"},
            },
            "seed": [{"node": "节点id", "input": "种子输入名"}],
            "outputs": [{"node": "节点id", "kind": "image|video"}],
        },
        "capabilities": {
            "supportedModes": "该工作流真实支持的模式：text_to_image/image_to_image/"
            "text_to_video/image_to_video/all_reference 里的若干",
            "resolutionOptions": "工作流里能真实产出的分辨率档位，如 480p/720p/1080p/1K/2K",
            "ratioOptions": "可支持的宽高比，如 16:9/9:16/1:1",
            "minDuration": "视频最短秒数（整数），图片省略",
            "maxDuration": "视频最长秒数（整数），图片省略",
            "referenceImageMax": "可注入的参考图数量上限（等于 images 绑定条数）",
            "fps": "帧率（整数，取自工作流里的 fps 值）",
            "sizeStep": "尺寸步进（8/16/32/64，取自尺寸控件）",
        },
        "notes": "一句话说明关键判断依据（中文，不超过 80 字）",
    }
    return (
        f"渠道模型名：{model}\n工作流名称：{workflow_name}\n\n"
        f"节点摘要（value 是当前存的值，link 表示该输入由连线提供、不能当标量写）：\n"
        f"{json.dumps(summary, ensure_ascii=False, default=str)}\n\n"
        f"严格按这个结构回答：\n{json.dumps(schema, ensure_ascii=False, default=str)}\n\n"
        "硬规则：\n"
        "1. node 必须是节点摘要里出现过的 id，input/width/height 必须是该节点 inputs 里"
        "真实存在且不是 link 的名字；不确定就不要写这一项（省略比猜错好）。\n"
        "2. 提示词只给一个正向节点；没有负向节点就省略 negative。\n"
        "3. size 只允许指向「生成空潜空间」的节点，不要指向放大/裁剪节点。\n"
        "4. outputs 至少一项，指向真正保存产物的节点（SaveImage/SaveVideo/VHS_VideoCombine 等）。\n"
        "5. capabilities 只声明工作流里真实存在的档位，不要用你的先验知识补充没出现的分辨率或时长。\n"
        "6. 参考图数量与 role 按 LoadImage 类节点的数量与标题语义给。"
    )


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            raise ComfyUIAdaptationError(
                f"分析模型没有返回可解析的 JSON，原文片段：{cleaned[:300]}"
            ) from None
        try:
            parsed = json.loads(match.group(0))
        except ValueError as exc:
            raise ComfyUIAdaptationError(
                f"分析模型返回的 JSON 无法解析：{exc}；原文片段：{cleaned[:300]}"
            ) from exc
    if not isinstance(parsed, dict):
        raise ComfyUIAdaptationError("分析模型返回的顶层结构不是 JSON 对象")
    return parsed


async def ask_analyzer(
    *,
    summary: dict[str, Any],
    model: str,
    workflow_name: str,
    text_model: str,
) -> dict[str, Any]:
    """调已配置的文本模型做一次非流式分析，返回其 JSON（错误保留上游原文）。"""

    api_key, gateway_base = openai_compat.resolve_credentials()
    base_url = openai_compat.normalize_base_url(gateway_base)
    if not base_url:
        raise ComfyUIAdaptationError("公共网关地址未配置，无法调用文本模型做分析")
    payload = {
        "model": text_model,
        "temperature": 0,
        "max_tokens": ANALYZER_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(model, workflow_name, summary)},
        ],
    }
    async with httpx.AsyncClient(
        timeout=openai_compat.api_timeout(ANALYZER_TIMEOUT_SECONDS)
    ) as client:
        response = await client.post(
            openai_compat.chat_completions_url(base_url),
            headers=openai_compat.auth_headers(api_key),
            json=payload,
        )
    if response.status_code >= 400:
        raise ComfyUIAdaptationError(
            openai_compat.error_message(
                int(response.status_code),
                response.text,
                prefix="分析模型网关",
            )
        )
    data = response.json() if response.content else {}
    text = openai_compat.first_message_text(data if isinstance(data, dict) else {})
    if not text:
        raise ComfyUIAdaptationError(
            f"分析模型 {text_model} 没有返回内容（响应片段："
            f"{json.dumps(data, ensure_ascii=False, default=str)[:300]}）"
        )
    return _extract_json(text)


def _writable_inputs(workflow: dict[str, Any], node_id: str) -> set[str] | None:
    spec = workflow.get(node_id)
    if not isinstance(spec, dict):
        return None
    inputs = spec.get("inputs") if isinstance(spec.get("inputs"), dict) else {}
    return {str(key) for key, value in inputs.items() if not _is_link(value)}


def validate_binding(binding: Any, workflow: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """严格校验节点绑定：越界引用逐条报错（只保留通过校验的项，不改写用户工作流）。"""

    errors: list[str] = []
    clean: dict[str, Any] = {}
    if not isinstance(binding, dict):
        return clean, ["binding 必须是对象"]

    def _slot_ref(raw: Any, label: str) -> tuple[str, str] | None:
        if not isinstance(raw, dict):
            errors.append(f"{label} 必须是 {{node, input}} 对象")
            return None
        node_id = str(raw.get("node") or "").strip()
        input_name = str(raw.get("input") or "").strip()
        if not node_id or not input_name:
            errors.append(f"{label} 缺少 node 或 input")
            return None
        writable = _writable_inputs(workflow, node_id)
        if writable is None:
            errors.append(f"{label} 指向不存在的节点 {node_id}")
            return None
        if input_name not in writable:
            errors.append(f"{label} 的输入 {node_id}.{input_name} 不存在或由连线提供")
            return None
        return node_id, input_name

    for key in ("prompt", "negative"):
        if binding.get(key) in (None, {}, ""):
            continue
        ref = _slot_ref(binding.get(key), f"binding.{key}")
        if ref:
            clean[key] = {"node": ref[0], "input": ref[1]}

    if binding.get("images"):
        raw_images = binding["images"]
        if not isinstance(raw_images, list):
            errors.append("binding.images 必须是数组")
        else:
            images: list[dict[str, str]] = []
            for index, item in enumerate(raw_images):
                ref = _slot_ref(item, f"binding.images[{index}]")
                if not ref:
                    continue
                role = str((item or {}).get("role") or "reference").strip().lower()
                if role not in IMAGE_ROLES:
                    role = "reference"
                images.append({"node": ref[0], "input": ref[1], "role": role})
            if images:
                clean["images"] = images

    if binding.get("size") not in (None, {}):
        raw_size = binding["size"]
        node_id = str((raw_size or {}).get("node") or "").strip()
        width = str((raw_size or {}).get("width") or "").strip()
        height = str((raw_size or {}).get("height") or "").strip()
        writable = _writable_inputs(workflow, node_id) if node_id else None
        if writable is None:
            errors.append(f"binding.size 指向不存在的节点 {node_id or '(空)'}")
        elif not width or not height or width not in writable or height not in writable:
            errors.append(
                f"binding.size 的输入 {node_id}.{width or '?'}/{node_id}.{height or '?'} "
                "不存在或由连线提供"
            )
        else:
            clean["size"] = {"node": node_id, "width": width, "height": height}

    if binding.get("frames") not in (None, {}):
        raw_frames = binding["frames"]
        ref = _slot_ref(raw_frames, "binding.frames")
        if ref:
            entry: dict[str, Any] = {"node": ref[0], "input": ref[1]}
            fps = raw_frames.get("fps") if isinstance(raw_frames, dict) else None
            if fps not in (None, {}):
                fps_ref = _slot_ref(fps, "binding.frames.fps")
                if fps_ref:
                    entry["fps"] = {"node": fps_ref[0], "input": fps_ref[1]}
            clean["frames"] = entry

    if binding.get("seed"):
        raw_seed = binding["seed"]
        if not isinstance(raw_seed, list):
            errors.append("binding.seed 必须是数组")
        else:
            seeds: list[dict[str, str]] = []
            for index, item in enumerate(raw_seed):
                ref = _slot_ref(item, f"binding.seed[{index}]")
                if ref:
                    seeds.append({"node": ref[0], "input": ref[1]})
            if seeds:
                clean["seed"] = seeds

    if binding.get("outputs"):
        raw_outputs = binding["outputs"]
        if not isinstance(raw_outputs, list):
            errors.append("binding.outputs 必须是数组")
        else:
            outputs: list[dict[str, str]] = []
            for index, item in enumerate(raw_outputs):
                node_id = str((item or {}).get("node") or "").strip()
                kind = str((item or {}).get("kind") or "").strip().lower()
                if node_id not in (workflow or {}):
                    errors.append(f"binding.outputs[{index}] 指向不存在的节点 {node_id}")
                    continue
                if kind not in OUTPUT_KINDS:
                    errors.append(
                        f"binding.outputs[{index}].kind 只能是 image/video，收到 {kind or '(空)'}"
                    )
                    continue
                outputs.append({"node": node_id, "kind": kind})
            if outputs:
                clean["outputs"] = outputs

    return clean, errors


def validate_capabilities(raw: Any, media_type: str) -> dict[str, Any]:
    """能力声明收敛：只留白名单键与合法取值（空声明就不产出，避免覆盖成无意义档位）。"""

    source = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {"mediaType": media_type}

    def _str_list(key: str) -> list[str]:
        values = source.get(key)
        if not isinstance(values, list):
            return []
        return [str(item).strip() for item in values if str(item).strip()][:24]

    for key in ("supportedModes", "resolutionOptions", "ratioOptions"):
        items = _str_list(key)
        if items:
            out[key] = items
    for key in ("minDuration", "maxDuration", "referenceImageMax", "fps", "sizeStep"):
        value = source.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = int(value)
        if number > 0 or key in ("minDuration",):
            out[key] = number
    if media_type == "image":
        out.pop("minDuration", None)
        out.pop("maxDuration", None)
    if len(out) <= 1:
        return {}
    return out


def resolve_analyzer_model(explicit: str = "") -> str:
    """分析用的文本模型：显式选的优先，否则走文本功能位三层解析（不吞未绑定异常）。"""

    name = str(explicit or "").strip()
    if name:
        return name
    from novelvideo.config import get_newapi_text_model_name
    from novelvideo.official_defaults import DEFAULT_FREEZONE_TEXT_WRITER_MODEL

    return get_newapi_text_model_name(
        "FREEZONE_TEXT_WRITER_MODEL",
        DEFAULT_FREEZONE_TEXT_WRITER_MODEL,
    )


async def analyze_one(
    *,
    model: str,
    workflow_name: str,
    workflow: dict[str, Any],
    text_model: str,
) -> dict[str, Any]:
    """分析单份工作流：出适配条目 + 校验错误（错误由调用方按模型上报，不静默）。"""

    summary = build_workflow_summary(workflow)
    answer = await ask_analyzer(
        summary=summary,
        model=model,
        workflow_name=workflow_name,
        text_model=text_model,
    )
    media_type = str(answer.get("mediaType") or "").strip().lower()
    if media_type not in MEDIA_TYPES:
        from novelvideo.comfyui_executor import detect_media_type_from_workflow

        detected = detect_media_type_from_workflow(workflow)
        media_type = detected or "video"
    binding, errors = validate_binding(answer.get("binding"), workflow)
    outputs = binding.get("outputs") or []
    if not outputs:
        errors.append("未能确定输出节点（binding.outputs 缺失或全部越界），适配不可用")
    capabilities = validate_capabilities(answer.get("capabilities"), media_type)
    entry: dict[str, Any] = {
        "workflowName": workflow_name,
        "workflowDigest": workflow_digest(workflow),
        "mediaType": media_type,
        "binding": binding,
        "capabilities": capabilities,
        "notes": str(answer.get("notes") or "")[:300],
        "generatedAt": _now_iso(),
        "analyzer": text_model,
        "schemaVersion": ADAPTATION_SCHEMA_VERSION,
    }
    return {"entry": entry, "errors": errors}


def _read_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ComfyUIAdaptationError(
            f"适配文件已损坏，请重新执行「AI分析并自动配置」（{path}：{exc}）"
        ) from exc
    return parsed if isinstance(parsed, dict) else {}


def _safe_adaptation_file(pointer: dict[str, Any]) -> Path | None:
    """指针里的相对路径必须落在适配目录内（防止设置里写入路径穿越）。"""

    rel = str(pointer.get("file") or "").strip()
    if not rel:
        return None
    root = adaptations_root().resolve()
    candidate = Path(rel)
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (_state_root() / candidate).resolve()
    )
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def write_adaptations(base_url: str, entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """把本次分析结果并进渠道的适配文件，返回写入用的指针表（不覆盖其它模型）。"""

    if not entries:
        return {"file": "", "pointers": {}}
    path = adaptation_path(base_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = _read_file(path)
    adaptations = document.get("adaptations") if isinstance(document.get("adaptations"), dict) else {}
    for model, entry in entries.items():
        adaptations[str(model)] = entry
    document.update(
        {
            "schemaVersion": ADAPTATION_SCHEMA_VERSION,
            "baseUrl": str(base_url or "").strip().rstrip("/"),
            "updatedAt": _now_iso(),
            "adaptations": adaptations,
        }
    )
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    try:
        rel = str(path.relative_to(_state_root()))
    except ValueError:  # pragma: no cover - state root moved
        rel = str(path)
    pointers = {
        str(model): {
            "file": rel,
            "entry": str(model),
            "workflowName": entry.get("workflowName", ""),
            "digest": entry.get("workflowDigest", ""),
            "mediaType": entry.get("mediaType", ""),
            "generatedAt": entry.get("generatedAt", ""),
            "analyzer": entry.get("analyzer", ""),
            "schemaVersion": ADAPTATION_SCHEMA_VERSION,
        }
        for model, entry in entries.items()
    }
    return {"file": rel, "pointers": pointers, "path": str(path)}


def load_adaptation(settings: dict[str, Any], model: str) -> dict[str, Any] | None:
    """按渠道指针读取该模型的适配条目（无适配 / 文件损坏时返回 None 走启发式）。"""

    comfyui = settings.get("comfyui") if isinstance(settings.get("comfyui"), dict) else {}
    pointers = (
        comfyui.get("adaptation_by_model")
        if isinstance(comfyui.get("adaptation_by_model"), dict)
        else {}
    )
    pointer = pointers.get(str(model))
    if not isinstance(pointer, dict):
        return None
    path = _safe_adaptation_file(pointer)
    if path is None:
        return None
    document = _read_file(path)
    adaptations = document.get("adaptations") if isinstance(document.get("adaptations"), dict) else {}
    entry = adaptations.get(str(pointer.get("entry") or model))
    return entry if isinstance(entry, dict) else None
