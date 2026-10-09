"""[aipdd fork delta] 渠道驱动的通用 ComfyUI 执行器（图片 / 视频共用）。

设计原则：任何生成入口都按「选中模型 → 该模型渠道 provider → 该渠道的规则」
分发，杜绝写死的模板 / 节点 ID / 服务器地址。当选中模型属于 comfyui 渠道时，
后端直连该渠道自身的 ``baseUrl``，用渠道里存储的「API 格式」工作流
（``settings.comfyui.workflow_by_model`` 或 ``workflow_routes``）通用执行。

运行参数按节点 ``class_type`` 自动识别注入：识别到的槽位写入运行值，识别不到
的一律保留工作流自带值（尽力而为 + 兜底，不做任何硬编码映射）。图片与视频走
同一个执行器，仅按输出节点类型决定从 history 取 ``images`` 还是 ``gifs/videos``。
"""

from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import random
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp

try:  # pragma: no cover - optional at import time in some envs
    import websockets
except Exception:  # noqa: BLE001
    websockets = None


# --- 已知 ComfyUI 节点类型（尽力而为匹配常见命名，未命中即保留工作流自带值） ---
_TEXT_NODE = "CLIPTextEncode"
_TEXT_FALLBACK_PREFIXES = ("TextEncode",)
_NEG_MARKERS = ("neg", "负")
_IMAGE_LOAD_NODES = {"LoadImage"}
_IMAGE_SKIP_NODES = {"LoadImageMask"}
_SEED_KEYS = ("seed", "noise_seed")
# 尺寸绑定只作用于「空潜空间」类节点，避免误伤 upscale/裁剪等含 width/height 的节点。
_LATENT_SIZE_PREFIXES = ("EmptyLatentImage", "EmptySD3LatentImage", "EmptyFlux", "EmptyHunyuan")
# 帧数/时长绑定只作用于视频潜空间或 Wan 类节点上的 length/frames 语义槽位。
_VIDEO_FRAME_MARKERS = ("Wan", "Hunyuan", "LTX", "Video", "Frame")
_FRAME_KEYS = ("length", "frames", "frame_count")
_IMAGE_OUTPUT_CLASSES = {"SaveImage"}
_VIDEO_OUTPUT_CLASSES = {
    "SaveVideo",
    "VHS_VideoCombine",
    "VideoCombine",
    "CreateVideo",
    "SaveWEBM",
    "SaveAnimatedWEBP",
    "SaveAnimatedPNG",
}


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _class_type(spec: object) -> str:
    return str((spec or {}).get("class_type") or "") if isinstance(spec, dict) else ""


def _node_title(spec: object) -> str:
    if not isinstance(spec, dict):
        return ""
    meta = spec.get("_meta")
    if isinstance(meta, dict):
        return str(meta.get("title") or "")
    return ""


@dataclass
class BindReport:
    """自动绑定结果报告，供日志与断言使用。"""

    prompt_nodes: list[str] = field(default_factory=list)
    negative_nodes: list[str] = field(default_factory=list)
    image_nodes: list[str] = field(default_factory=list)
    seed_nodes: list[str] = field(default_factory=list)
    size_nodes: list[str] = field(default_factory=list)
    frame_nodes: list[str] = field(default_factory=list)
    has_image_output: bool = False
    has_video_output: bool = False
    warnings: list[str] = field(default_factory=list)
    seed: Optional[int] = None
    # [aipdd fork delta] 参数注入来源：命中「AI分析并自动配置」产出的显式节点
    # 绑定时为 ``adaptation``，完全按 class_type 启发式时为 ``heuristic``。
    source: str = "heuristic"

    def detected_output(self) -> str:
        if self.has_video_output:
            return "video"
        if self.has_image_output:
            return "image"
        return "unknown"


def _is_link(value: object) -> bool:
    """API 格式里 ``[节点id, 槽位]`` 表示该输入由连线提供，不能写标量。"""
    return isinstance(value, list) and len(value) == 2


def _adaptation_inputs(
    wf: dict[str, Any],
    ref: object,
    report: BindReport,
    label: str,
) -> dict[str, Any] | None:
    """按适配绑定里的 ``{node, input}`` 定位可写 inputs dict。

    节点不存在 / 输入不存在 / 输入是连线，都记警告并回 None：该槽位交给启发式
    兜底，绝不静默写错地方（适配文件过期时要能从日志里看出来）。
    """
    node_id = str((ref or {}).get("node") or "") if isinstance(ref, dict) else ""
    input_name = str((ref or {}).get("input") or "") if isinstance(ref, dict) else ""
    if not node_id or not input_name:
        report.warnings.append(f"adaptation.{label} 缺 node 或 input，已忽略")
        return None
    spec = wf.get(node_id)
    if not isinstance(spec, dict):
        report.warnings.append(f"adaptation.{label} 指向不存在的节点 {node_id}，回落启发式")
        return None
    inputs = spec.get("inputs")
    if not isinstance(inputs, dict) or input_name not in inputs:
        report.warnings.append(
            f"adaptation.{label} 的输入 {node_id}.{input_name} 不存在，回落启发式"
        )
        return None
    if _is_link(inputs[input_name]):
        report.warnings.append(
            f"adaptation.{label} 的 {node_id}.{input_name} 由连线提供，无法写标量，回落启发式"
        )
        return None
    return inputs


def _apply_adaptation(
    wf: dict[str, Any],
    binding: dict[str, Any],
    report: BindReport,
    *,
    prompt: str | None,
    negative_prompt: str | None,
    images: list[str],
    seed: int,
    width: int | None,
    height: int | None,
    duration: float | None,
) -> set[str]:
    """按显式节点绑定注入参数，返回已成功覆盖的槽位名（未覆盖的走启发式）。"""

    covered: set[str] = set()

    prompt_ref = binding.get("prompt")
    if prompt is not None and prompt_ref:
        inputs = _adaptation_inputs(wf, prompt_ref, report, "prompt")
        if inputs is not None:
            inputs[str(prompt_ref["input"])] = prompt  # type: ignore[index]
            report.prompt_nodes.append(str(prompt_ref["node"]))
            covered.add("prompt")

    negative_ref = binding.get("negative")
    if negative_prompt and negative_ref:
        inputs = _adaptation_inputs(wf, negative_ref, report, "negative")
        if inputs is not None:
            inputs[str(negative_ref["input"])] = negative_prompt
            report.negative_nodes.append(str(negative_ref["node"]))
            covered.add("negative")

    image_refs = binding.get("images")
    if images and isinstance(image_refs, list) and image_refs:
        bound = 0
        for index, name in enumerate(images):
            ref = image_refs[index] if index < len(image_refs) else None
            if not isinstance(ref, dict):
                report.warnings.append(
                    f"adaptation.images 只有 {len(image_refs)} 项，第 {index + 1} 张参考图未注入"
                )
                break
            inputs = _adaptation_inputs(wf, ref, report, f"images[{index}]")
            if inputs is None:
                continue
            inputs[str(ref["input"])] = name
            report.image_nodes.append(str(ref["node"]))
            bound += 1
        if bound:
            covered.add("images")

    seed_refs = binding.get("seed")
    if isinstance(seed_refs, list) and seed_refs:
        written = 0
        for index, ref in enumerate(seed_refs):
            inputs = _adaptation_inputs(wf, ref, report, f"seed[{index}]")
            if inputs is None:
                continue
            inputs[str(ref["input"])] = seed
            report.seed_nodes.append(str(ref["node"]))
            written += 1
        if written:
            covered.add("seed")

    size_ref = binding.get("size")
    if width and height and isinstance(size_ref, dict):
        node_id = str(size_ref.get("node") or "")
        spec = wf.get(node_id)
        inputs = spec.get("inputs") if isinstance(spec, dict) else None
        width_key = str(size_ref.get("width") or "")
        height_key = str(size_ref.get("height") or "")
        if isinstance(inputs, dict) and width_key in inputs and height_key in inputs:
            inputs[width_key] = int(width)
            inputs[height_key] = int(height)
            report.size_nodes.append(node_id)
            covered.add("size")
        else:
            report.warnings.append(
                f"adaptation.size 的输入 {node_id}.{width_key or '?'}/{height_key or '?'} 不存在，回落启发式"
            )

    frames_ref = binding.get("frames")
    if duration is not None and isinstance(frames_ref, dict):
        fps: float | None = None
        fps_ref = frames_ref.get("fps")
        if isinstance(fps_ref, dict):
            fps_inputs = _adaptation_inputs(wf, fps_ref, report, "frames.fps")
            if fps_inputs is not None:
                raw = fps_inputs.get(str(fps_ref.get("input") or ""))
                if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
                    fps = float(raw)
        if fps is None:
            for spec in wf.values():
                inputs = spec.get("inputs") if isinstance(spec, dict) else None
                if isinstance(inputs, dict) and isinstance(
                    inputs.get("fps"), (int, float)
                ) and not isinstance(inputs.get("fps"), bool):
                    fps = float(inputs["fps"])
                    break
        if fps and fps > 0:
            frames = int(round(float(duration) * fps))
            target_class = _class_type(wf.get(str(frames_ref.get("node") or ""), {}))
            if frames >= 4 and frames % 4 == 0 and any(
                marker in target_class for marker in _VIDEO_FRAME_MARKERS
            ):
                # 只有目标节点确实属于 Wan/Hunyuan/LTX/Video 族时才做 4n+1 修正，
                # 自定义帧数节点按模型给的整数原样写。
                frames += 1
            inputs = _adaptation_inputs(wf, frames_ref, report, "frames")
            if inputs is not None:
                inputs[str(frames_ref["input"])] = frames
                report.frame_nodes.append(str(frames_ref["node"]))
                covered.add("frames")
        else:
            report.warnings.append("adaptation.frames 未找到可用 fps，时长用工作流默认值")

    outputs = binding.get("outputs")
    if isinstance(outputs, list) and outputs:
        kinds: list[str] = []
        for item in outputs:
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("node") or "")
            if node_id not in wf:
                report.warnings.append(
                    f"adaptation.outputs 指向不存在的节点 {node_id or '(空)'}，已忽略"
                )
                continue
            kinds.append(str(item.get("kind") or "").strip().lower())
        if kinds:
            report.has_image_output = "image" in kinds
            report.has_video_output = "video" in kinds
            covered.add("outputs")

    return covered


def bind_comfyui_workflow(
    workflow: dict[str, Any],
    *,
    prompt: str | None = "",
    negative_prompt: str | None = None,
    uploaded_image_names: list[str] | None = None,
    seed: int | None = None,
    width: int | None = None,
    height: int | None = None,
    duration: float | None = None,
    adaptation: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], BindReport]:
    """把运行参数注入 API 格式工作流。

    纯函数：不触网。``uploaded_image_names`` 是已上传到 ComfyUI 的文件名（按
    图生图参考顺序映射）；返回新的 workflow 与绑定报告，识别不到的槽位保留工作流
    自带值。

    ``adaptation`` 是「AI分析并自动配置」产出的适配条目（含 ``binding``，也可直接传
    一个 binding 字典）。给了就先按**显式节点 id + 输入名**注入，只有未被适配覆盖
    （或引用已失效）的槽位才回落 class_type 启发式——自定义节点改名、多个含
    width/height 的节点这类误伤，从「猜」变成「按声明写」。
    """

    wf = copy.deepcopy(workflow) if isinstance(workflow, dict) else {}
    report = BindReport()
    images = [str(name) for name in (uploaded_image_names or []) if str(name).strip()]
    resolved_seed = int(seed) if seed is not None else random.randint(0, 0xFFFFFFFFFFFF)
    report.seed = resolved_seed

    binding: dict[str, Any] = {}
    if isinstance(adaptation, dict):
        inner = adaptation.get("binding")
        binding = inner if isinstance(inner, dict) else adaptation
    covered: set[str] = set()
    if binding:
        covered = _apply_adaptation(
            wf,
            binding,
            report,
            prompt=prompt,
            negative_prompt=negative_prompt,
            images=images,
            seed=resolved_seed,
            width=width,
            height=height,
            duration=duration,
        )
        report.source = "adaptation"

    # --- 提示词：CLIPTextEncode，按标题区分正/负向 ---
    text_ids = [nid for nid, spec in wf.items() if _class_type(spec) == _TEXT_NODE]

    def _is_negative(nid: str) -> bool:
        blob = f"{_node_title(wf[nid])} {nid}".lower()
        title_raw = _node_title(wf[nid])
        return any(marker in blob or marker in title_raw for marker in _NEG_MARKERS)

    positives = [nid for nid in text_ids if not _is_negative(nid)]
    negatives = [nid for nid in text_ids if _is_negative(nid)]
    if not negatives and len(positives) >= 2:
        # 无显式负向标记时，次个未标记文本节点按惯例视作负向。
        negatives = [positives.pop(1)]
    if prompt is not None and positives and "prompt" not in covered:
        wf[positives[0]].setdefault("inputs", {})["text"] = prompt
        report.prompt_nodes.append(positives[0])
        for nid in positives[1:]:
            report.warnings.append(f"多个正向文本节点，仅注入首个：{nid}")
    if negative_prompt and "negative" not in covered:
        for nid in negatives:
            wf[nid].setdefault("inputs", {})["text"] = negative_prompt
            report.negative_nodes.append(nid)
    if not text_ids and prompt and "prompt" not in covered:
        # 回退：常见自定义文本节点（TextEncodeInput 等）含 text 槽位。
        for nid, spec in wf.items():
            ct = _class_type(spec)
            if ct.startswith(_TEXT_FALLBACK_PREFIXES):
                inputs = spec.get("inputs") if isinstance(spec, dict) else None
                if isinstance(inputs, dict) and "text" in inputs:
                    inputs["text"] = prompt
                    report.prompt_nodes.append(nid)
                    break
    if "prompt" not in covered and not text_ids and prompt:
        report.warnings.append("工作流未找到可写的提示词节点，提示词沿用工作流自带值")

    # --- 参考图：LoadImage 按顺序注入已上传文件名 ---
    if "images" not in covered:
        image_ids = [
            nid
            for nid, spec in wf.items()
            if _class_type(spec) in _IMAGE_LOAD_NODES
        ]
        for index, nid in enumerate(image_ids):
            if index < len(images):
                wf[nid].setdefault("inputs", {})["image"] = images[index]
                report.image_nodes.append(nid)
        if images and not image_ids:
            report.warnings.append("工作流未找到 LoadImage 节点，参考图未注入")

    # --- 种子：任意整数 seed/noise_seed 槽位 ---
    if "seed" not in covered:
        seed_nodes: set[str] = set(report.seed_nodes)
        for nid, spec in wf.items():
            inputs = spec.get("inputs") if isinstance(spec, dict) else None
            if not isinstance(inputs, dict):
                continue
            for key in _SEED_KEYS:
                if key in inputs and _is_int(inputs[key]):
                    inputs[key] = resolved_seed
                    seed_nodes.add(nid)
        report.seed_nodes = sorted(seed_nodes)

    # --- 尺寸：仅空潜空间类节点同时含整型 width/height 时写入 ---
    if width and height and "size" not in covered:
        for nid, spec in wf.items():
            ct = _class_type(spec)
            inputs = spec.get("inputs") if isinstance(spec, dict) else None
            if not isinstance(inputs, dict):
                continue
            if not any(ct.startswith(prefix) for prefix in _LATENT_SIZE_PREFIXES):
                continue
            if _is_int(inputs.get("width")) and _is_int(inputs.get("height")):
                inputs["width"] = int(width)
                inputs["height"] = int(height)
                report.size_nodes.append(nid)

    # --- 帧数/时长：识别 fps 后换算帧数写入视频类节点 length/frames ---
    if duration is not None and "frames" not in covered:
        fps: float | None = None
        for spec in wf.values():
            inputs = spec.get("inputs") if isinstance(spec, dict) else None
            if isinstance(inputs, dict) and (
                _is_int(inputs.get("fps")) or isinstance(inputs.get("fps"), float)
            ):
                fps = float(inputs["fps"])
                break
        frames: int | None = None
        if fps and fps > 0:
            frames = int(round(float(duration) * fps))
            # Wan / LTX 等希望 4n+1 帧数。
            if frames >= 4 and frames % 4 == 0:
                frames += 1
            for nid, spec in wf.items():
                ct = _class_type(spec)
                inputs = spec.get("inputs") if isinstance(spec, dict) else None
                if not isinstance(inputs, dict) or frames is None:
                    continue
                if not any(marker in ct for marker in _VIDEO_FRAME_MARKERS):
                    continue
                for key in _FRAME_KEYS:
                    if key in inputs and _is_int(inputs[key]):
                        inputs[key] = frames
                        report.frame_nodes.append(nid)
        else:
            report.warnings.append("未检测到 fps 控件，无法换算帧数，时长用工作流默认值")

    # --- 输出类型判定 ---
    if "outputs" not in covered:
        report.has_image_output = any(
            _class_type(spec) in _IMAGE_OUTPUT_CLASSES for spec in wf.values()
        )
        report.has_video_output = any(
            _class_type(spec) in _VIDEO_OUTPUT_CLASSES for spec in wf.values()
        )
    return wf, report


def detect_media_type_from_workflow(workflow: dict[str, Any]) -> str | None:
    """[aipdd fork delta] 从 API 格式工作流的输出节点推断媒体类型。

    命中 ``SaveImage`` 返回 ``"image"``；命中视频输出节点返回 ``"video"``；
    两者都没有（或非标准命名）返回 ``None``，交由渠道显式声明。
    """

    if not isinstance(workflow, dict):
        return None
    classes = {_class_type(spec) for spec in workflow.values()}
    if classes & _IMAGE_OUTPUT_CLASSES:
        return "image"
    if classes & _VIDEO_OUTPUT_CLASSES:
        return "video"
    return None


@dataclass
class ComfyUIExecutionResult:
    """执行结果：成功时 data 为产物字节，output_path 为落盘路径（若给）。"""

    success: bool
    data: bytes = b""
    output_path: str | None = None
    prompt_id: str | None = None
    error: str | None = None
    report: BindReport | None = None


def _is_loopback_host(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in {"localhost", ""}:
        return host == "localhost"
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class ComfyUIWorkflowExecutor:
    """直连渠道 ComfyUI 服务器执行一份 API 格式工作流。

    服务器地址来自渠道 ``baseUrl``（不写死），产物类型由工作流输出节点决定。
    """

    def __init__(self, base_url: str, *, timeout: float = 600.0) -> None:
        cleaned = str(base_url or "").strip().rstrip("/")
        if not cleaned:
            raise ValueError("ComfyUI baseUrl 不能为空")
        if "://" not in cleaned:
            cleaned = f"http://{cleaned}"
        parsed = urllib.parse.urlsplit(cleaned)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        self.http_url = cleaned
        self.ws_url = f"{ws_scheme}://{parsed.netloc}"
        self.timeout = timeout
        self.is_loopback = _is_loopback_host(cleaned)

    def _headers(self) -> dict[str, str]:
        return {}

    async def _upload_image(self, image_bytes: bytes, filename: str) -> dict:
        data = aiohttp.FormData()
        data.add_field("image", image_bytes, filename=filename, content_type="image/png")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
            async with session.post(
                f"{self.http_url}/upload/image", data=data, headers=self._headers()
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"上传图片到 ComfyUI 失败: HTTP {resp.status} - {text[:300]}")
                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"ComfyUI 上传返回非 JSON: {text[:200]}") from exc

    async def _queue_prompt(self, workflow: dict, client_id: str) -> str:
        payload = {"prompt": workflow, "client_id": client_id}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
            async with session.post(
                f"{self.http_url}/prompt", json=payload, headers=self._headers()
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"提交 ComfyUI 工作流失败: HTTP {resp.status} - {text[:500]}")
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"ComfyUI /prompt 返回非 JSON: {text[:200]}") from exc
                prompt_id = data.get("prompt_id")
                if not prompt_id:
                    raise RuntimeError(f"ComfyUI 未返回 prompt_id: {data}")
                return str(prompt_id)

    async def _get_history(self, prompt_id: str) -> dict:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
            async with session.get(
                f"{self.http_url}/history/{prompt_id}", headers=self._headers()
            ) as resp:
                if resp.status != 200:
                    return {}
                return await resp.json()

    async def _download_view(self, filename: str, subfolder: str, type_: str) -> bytes:
        params = {"filename": filename, "subfolder": subfolder, "type": type_}
        url = f"{self.http_url}/view?{urllib.parse.urlencode(params)}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
            async with session.get(url, headers=self._headers()) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"下载 ComfyUI 产物失败: HTTP {resp.status}")
                return await resp.read()

    async def _wait_for_completion(
        self,
        prompt_id: str,
        client_id: str,
        *,
        log: Callable[[str], None],
        progress: Callable[[float], None] | None,
    ) -> None:
        """优先用 WebSocket 监听；无 websockets 或异常时回落到轮询 history。"""

        if websockets is None:
            await self._poll_history(prompt_id, log=log)
            return
        connect_url = f"{self.ws_url}/ws?clientId={client_id}"
        try:
            ws = await websockets.connect(
                connect_url,
                max_size=500 * 1024 * 1024,
                ping_interval=None,
                ping_timeout=None,
                proxy=None,
            )
        except Exception as exc:  # noqa: BLE001 - 回落轮询
            log(f"WebSocket 连接失败，改用轮询：{exc}")
            await self._poll_history(prompt_id, log=log)
            return
        try:
            while True:
                out = await ws.recv()
                if not isinstance(out, str):
                    continue
                message = json.loads(out)
                msg_type = message.get("type")
                data = message.get("data") or {}
                if msg_type == "executing":
                    if data.get("prompt_id") == prompt_id and data.get("node") is None:
                        log("ComfyUI 推理完成")
                        return
                elif msg_type == "progress":
                    value = data.get("value", 0)
                    max_val = data.get("max", 100) or 100
                    if progress:
                        progress(min(1.0, max(0.0, value / max_val)))
                elif msg_type == "execution_error":
                    raise RuntimeError(f"ComfyUI 执行错误: {data}")
        except Exception as exc:  # noqa: BLE001
            if "ComfyUI 执行错误" in str(exc):
                raise
            log(f"WebSocket 中断，改用轮询确认结果：{exc}")
            await self._poll_history(prompt_id, log=log)
        finally:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def _poll_history(self, prompt_id: str, *, log: Callable[[str], None]) -> None:
        deadline = self.timeout
        waited = 0.0
        interval = 3.0
        while waited < deadline:
            history = await self._get_history(prompt_id)
            if history and prompt_id in history:
                return
            await asyncio.sleep(interval)
            waited += interval
        raise RuntimeError("等待 ComfyUI 结果超时")

    async def execute(
        self,
        *,
        workflow: dict,
        media_type: str,
        prompt: str = "",
        negative_prompt: str | None = None,
        reference_image_paths: list[str] | None = None,
        seed: int | None = None,
        width: int | None = None,
        height: int | None = None,
        duration: float | None = None,
        output_path: str | None = None,
        adaptation: dict[str, Any] | None = None,
        log: Callable[[str], None] | None = None,
        progress: Callable[[float], None] | None = None,
    ) -> ComfyUIExecutionResult:
        """执行一份工作流。

        ``adaptation`` 是渠道指针读出的适配条目；给了就按显式节点绑定注入，缺失的
        槽位回落启发式（详见 :func:`bind_comfyui_workflow`）。
        """

        def _log(msg: str) -> None:
            if log:
                log(msg)

        ref_paths = [p for p in (reference_image_paths or []) if p and Path(p).exists()]
        uploaded_names: list[str] = []
        try:
            client_id = str(uuid.uuid4())

            # 1. 上传参考图（LoadImage 需要已落 ComfyUI input 的文件名）。
            for path in ref_paths:
                raw = Path(path).read_bytes()
                name = f"dcref_{uuid.uuid4().hex[:12]}.png"
                uploaded = await self._upload_image(raw, name)
                uploaded_names.append(str(uploaded.get("name") or name))
                _log(f"参考图已上传: {uploaded_names[-1]}")

            # 2. 自动绑定参数。
            bound, report = bind_comfyui_workflow(
                workflow,
                prompt=prompt,
                negative_prompt=negative_prompt,
                uploaded_image_names=uploaded_names,
                seed=seed,
                width=width,
                height=height,
                duration=duration,
                adaptation=adaptation,
            )
            for warning in report.warnings:
                _log(f"[comfyui-bind] {warning}")
            _log(
                "[comfyui-bind] "
                f"prompt={report.prompt_nodes} neg={report.negative_nodes} "
                f"image={report.image_nodes} seed={report.seed_nodes} "
                f"size={report.size_nodes} frame={report.frame_nodes} "
                f"output={report.detected_output()} source={report.source}"
            )

            # 3. 提交 + 等待。
            prompt_id = await self._queue_prompt(bound, client_id)
            _log(f"prompt_id: {prompt_id}")
            await self._wait_for_completion(prompt_id, client_id, log=_log, progress=progress)

            # 4. 取产物：视频优先 gifs/videos，图片优先 images。
            want_video = (media_type == "video") or report.has_video_output
            data = await self._fetch_output(prompt_id, want_video=want_video)
            if not data:
                return ComfyUIExecutionResult(
                    success=False, error="ComfyUI 未返回可下载产物", prompt_id=prompt_id, report=report
                )

            resolved_path: str | None = None
            if output_path:
                out = Path(output_path)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
                resolved_path = str(out)
            return ComfyUIExecutionResult(
                success=True, data=data, output_path=resolved_path, prompt_id=prompt_id, report=report
            )
        except Exception as exc:  # noqa: BLE001
            _log(f"错误: {exc}")
            return ComfyUIExecutionResult(success=False, error=str(exc))

    async def _fetch_output(self, prompt_id: str, *, want_video: bool) -> bytes:
        for _attempt in range(3):
            history = await self._get_history(prompt_id)
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            outputs = (entry or {}).get("outputs") or {}
            for node_out in outputs.values():
                if not isinstance(node_out, dict):
                    continue
                buckets: list[tuple[str, list]] = []
                if want_video:
                    for key in ("gifs", "videos", "images"):
                        if node_out.get(key):
                            buckets.append((key, node_out[key]))
                else:
                    if node_out.get("images"):
                        buckets.append(("images", node_out["images"]))
                for _key, items in buckets:
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        filename = item.get("filename")
                        if not filename:
                            continue
                        # 图片桶里也可能混着 .mp4，按扩展名过滤。
                        if want_video and not str(filename).lower().endswith(
                            (".mp4", ".webm", ".gif", ".mkv", ".mov")
                        ):
                            continue
                        if not want_video and str(filename).lower().endswith(
                            (".mp4", ".webm", ".mkv", ".mov")
                        ):
                            continue
                        return await self._download_view(
                            filename, item.get("subfolder", ""), item.get("type", "output")
                        )
            await asyncio.sleep(1)
        return b""
