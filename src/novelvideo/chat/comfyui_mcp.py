# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 本地 ComfyUI MCP 服务器（stdio）。

官方 comfy-mcp（https://docs.comfy.org/zh/agent-tools/mcp）是「在装有 ComfyUI
的那台机器上，用 comfy-cli 驱动本地安装」的第一方服务器。本模块**参考它的工具
语义**自建一个只打「本地 ComfyUI」的 MCP：

- 不依赖 comfy-cli / comfy-mcp 二进制，也不需要本机另装 Python 环境 —— 服务器
  进程就在本平台运行时里起，Docker 部署同样可用；
- 目标地址只有一个来源：``comfyui`` 渠道的 ``baseUrl``（设置 › 模型 › 本地
  ComfyUI）。父进程启动子进程时会把它注入 ``COMFYUI_BASE_URL``，子进程自己也能
  从 settings.db 解析，杜绝第二份地址；
- 生命周期不归本 MCP 管（不 launch / 不装 ComfyUI）：地址没配或 ComfyUI 没起来
  时，每个工具都返回**可读且有出路**的错误，而不是让子进程启动失败只吐一句
  「连接失败」。

工具集（与官方语义对齐，命名保持小写下划线）：
``server_info`` / ``list_workflows`` / ``get_workflow`` / ``search_nodes`` /
``search_models`` / ``validate_workflow`` / ``run_workflow``。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

SERVER_NAME = "comfyui-local"
HTTP_TIMEOUT_SECONDS = 60.0
RUN_POLL_INTERVAL_SECONDS = 2.0
RUN_TIMEOUT_SECONDS = 600.0
SEARCH_LIMIT_DEFAULT = 30
# ComfyUI 的「已安装模型」没有统一列表接口，官方做法是读加载器类节点控件的候选
# 值（object_info 里 ckpt_name / lora_name 这类枚举）。这里按槽位名匹配这些加载器。
_MODEL_SLOT_KEYS = ("ckpt_name", "lora_name", "model_name", "vae_name", "unet_name")

_UNCONFIGURED_HINT = (
    "本地 ComfyUI 地址未配置：请到「设置 › 模型 › 本地 ComfyUI」填写 ComfyUI 地址"
    "（保存后即为所有 ComfyUI 服务的调用入口）。"
)


class ComfyUIMcpError(RuntimeError):
    """面向用户的工具级错误（文本可读、含出路，不抛裸异常）。"""


def _base_url() -> str:
    """本地 ComfyUI 地址：环境变量优先，其次 comfyui 渠道 baseUrl（唯一真源）。"""
    explicit = str(os.environ.get("COMFYUI_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    try:
        from novelvideo.model_gateway_settings import get_newapi_provider_channel

        channel = get_newapi_provider_channel("comfyui") or {}
        return str(channel.get("baseUrl") or "").strip().rstrip("/")
    except Exception:  # noqa: BLE001 - 解析不到就按「未配置」处理，错误信息里给出路
        return ""


async def _get_json(path: str) -> Any:
    base = _base_url()
    if not base:
        raise ComfyUIMcpError(_UNCONFIGURED_HINT)
    url = f"{base}{path}"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                raise ComfyUIMcpError(
                    f"本地 ComfyUI 返回 {response.status_code}（{url}）："
                    f"{response.text[:300]}"
                )
            return response.json()
    except ComfyUIMcpError:
        raise
    except httpx.HTTPError as exc:
        raise ComfyUIMcpError(
            f"连接本地 ComfyUI 失败（{url}）：{exc}。请确认 ComfyUI 已启动、"
            "地址可在本容器内访问（宿主机部署可用 host.docker.internal）。"
        ) from exc


async def _post_json(path: str, payload: dict[str, Any]) -> Any:
    base = _base_url()
    if not base:
        raise ComfyUIMcpError(_UNCONFIGURED_HINT)
    url = f"{base}{path}"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload)
            if response.status_code >= 400:
                raise ComfyUIMcpError(
                    f"本地 ComfyUI 返回 {response.status_code}（{url}）："
                    f"{response.text[:500]}"
                )
            return response.json()
    except ComfyUIMcpError:
        raise
    except httpx.HTTPError as exc:
        raise ComfyUIMcpError(f"请求本地 ComfyUI 失败（{url}）：{exc}") from exc


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------


async def server_info(**_kwargs: Any) -> dict[str, Any]:
    """服务器概况：地址、版本、节点数量（一次性探测，供 Agent 判断可用性）。"""
    base = _base_url()
    if not base:
        return {"configured": False, "error": _UNCONFIGURED_HINT}
    stats: dict[str, Any] = {}
    error = ""
    try:
        stats = await _get_json("/system_stats")
    except ComfyUIMcpError as exc:
        error = str(exc)
    object_info: Any = {}
    if not error:
        try:
            object_info = await _get_json("/object_info")
        except ComfyUIMcpError as exc:
            error = str(exc)
    system = stats.get("system") if isinstance(stats, dict) else {}
    return {
        "configured": True,
        "baseUrl": base,
        "comfyuiVersion": str((system or {}).get("comfyui_version") or ""),
        "nodeCount": len(object_info) if isinstance(object_info, dict) else 0,
        "pythonVersion": str((system or {}).get("python_version") or ""),
        "error": error,
    }


async def list_workflows(**_kwargs: Any) -> list[dict[str, Any]]:
    """本地 ComfyUI 已保存的工作流（id + 名称），按名称排序。"""
    data = await _get_json("/workflows")
    items = data if isinstance(data, list) else []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "id": item.get("id"),
                "name": str(item.get("name") or ""),
                "updatedAt": item.get("last_updated") or item.get("updated_at") or "",
            }
        )
    out.sort(key=lambda entry: entry["name"].lower())
    return out


async def get_workflow(name: str = "", **kwargs: Any) -> dict[str, Any]:
    """按名称取回一份工作流，并给出 API 格式（后端与网关执行用的就是 API 格式）。"""

    from novelvideo.comfyui_workflow import (
        coerce_graph,
        convert_graph_to_api,
        is_api_format_workflow,
    )

    workflow_id = kwargs.get("workflowId")

    wanted = str(name or "").strip()
    listing = await _get_json("/workflows")
    items = listing if isinstance(listing, list) else []
    matched: Any = None
    for item in items:
        if isinstance(item, dict) and str(item.get("name") or "").strip().lower() == wanted.lower():
            matched = item
            break
    if matched is None and workflow_id is not None:
        matched = await _get_json(f"/workflows/{workflow_id}")
    if matched is None:
        raise ComfyUIMcpError(
            f"本地 ComfyUI 工作流列表里找不到「{wanted}」。"
            "先用 list_workflows 看可用名称。"
        )
    payload: Any = matched
    graph = coerce_graph(payload)
    if graph is None and isinstance(matched, dict) and matched.get("id") is not None:
        payload = await _get_json(f"/workflows/{matched['id']}")
        graph = coerce_graph(payload)
    if is_api_format_workflow(payload):
        return {"name": wanted, "workflow": payload, "missingClassTypes": []}
    if graph is None:
        raise ComfyUIMcpError(
            f"工作流「{wanted}」既不是浏览器图（nodes/links），也不是 API 格式，无法解析。"
        )
    object_info = await _get_json("/object_info")
    api_workflow, missing = convert_graph_to_api(
        graph, object_info if isinstance(object_info, dict) else {}
    )
    return {"name": wanted, "workflow": api_workflow, "missingClassTypes": missing}


async def search_nodes(query: str = "", limit: Any = None, **_kwargs: Any) -> dict[str, Any]:
    """在本地实际安装的节点里检索（自定义节点没装，这里就一定查不到）。"""
    object_info = await _get_json("/object_info")
    if not isinstance(object_info, dict):
        raise ComfyUIMcpError("本地 ComfyUI /object_info 返回非对象")
    needle = str(query or "").strip().lower()
    cap = _int_or(limit, SEARCH_LIMIT_DEFAULT)
    matches: list[dict[str, Any]] = []
    for class_type, info in object_info.items():
        name = str(class_type)
        if needle and needle not in name.lower():
            continue
        inputs = (info or {}).get("input") if isinstance(info, dict) else {}
        required = list((inputs or {}).get("required") or {}) if isinstance(inputs, dict) else []
        optional = list((inputs or {}).get("optional") or {}) if isinstance(inputs, dict) else []
        matches.append(
            {
                "classType": name,
                "required": required,
                "optional": optional,
                "outputNode": bool((info or {}).get("output_node")),
            }
        )
        if len(matches) >= cap:
            break
    return {"total": len(object_info), "matched": len(matches), "nodes": matches}


async def search_models(query: str = "", **_kwargs: Any) -> dict[str, Any]:
    """本地已安装的模型：取加载器类节点控件的候选值（ComfyUI 无统一列表接口）。"""
    object_info = await _get_json("/object_info")
    if not isinstance(object_info, dict):
        raise ComfyUIMcpError("本地 ComfyUI /object_info 返回非对象")
    needle = str(query or "").strip().lower()
    found: dict[str, list[str]] = {}
    for class_type, info in object_info.items():
        if not isinstance(info, dict):
            continue
        inputs = info.get("input") if isinstance(info.get("input"), dict) else {}
        for group in ("required", "optional"):
            for slot, spec in (inputs.get(group) or {}).items():
                if str(slot).lower() not in _MODEL_SLOT_KEYS:
                    continue
                if not (isinstance(spec, list) and spec and isinstance(spec[0], list)):
                    continue
                names = [str(value) for value in spec[0] if str(value)]
                if needle:
                    names = [value for value in names if needle in value.lower()]
                if names:
                    found[f"{class_type}.{slot}"] = names
    return {"groups": len(found), "models": found}


async def validate_workflow(
    workflow: Any = None, name: str = "", **_kwargs: Any
) -> dict[str, Any]:
    """结构校验：API 格式 / 节点类型是否装了 / 连线引用与必填槽位是否齐。"""

    from novelvideo.comfyui_workflow import is_api_format_workflow

    payload = workflow
    if payload is None and str(name or "").strip():
        fetched = await get_workflow(name=name)
        payload = fetched.get("workflow")
    if not isinstance(payload, dict) or not payload:
        raise ComfyUIMcpError("validate_workflow 需要 workflow（API 格式对象）或 name")
    if not is_api_format_workflow(payload):
        return {
            "valid": False,
            "errors": ["不是 API 格式（节点值需含 class_type/inputs）"],
            "warnings": [],
        }
    object_info = await _get_json("/object_info")
    object_info = object_info if isinstance(object_info, dict) else {}
    errors: list[str] = []
    warnings: list[str] = []
    for node_id, spec in payload.items():
        if not isinstance(spec, dict):
            errors.append(f"节点 {node_id} 不是对象")
            continue
        class_type = str(spec.get("class_type") or "")
        info = object_info.get(class_type)
        if not isinstance(info, dict):
            errors.append(f"节点 {node_id}（{class_type or '?'}）：本地未安装该节点类型")
            continue
        inputs = info.get("input") if isinstance(info.get("input"), dict) else {}
        required = inputs.get("required") if isinstance(inputs.get("required"), dict) else {}
        node_inputs = spec.get("inputs") if isinstance(spec.get("inputs"), dict) else {}
        for slot, spec_value in required.items():
            hidden = isinstance(spec_value, list) and len(spec_value) > 1
            hidden = bool(hidden and isinstance(spec_value[1], dict) and spec_value[1].get("hidden"))
            if hidden:
                continue
            if slot not in node_inputs:
                errors.append(f"节点 {node_id}（{class_type}）缺必填输入：{slot}")
        for slot, value in node_inputs.items():
            if isinstance(value, list) and len(value) == 2:
                source = str(value[0])
                if source not in payload:
                    errors.append(
                        f"节点 {node_id}.{slot} 连线指向不存在的节点 {source}"
                    )
    return {"valid": not errors, "errors": errors, "warnings": warnings}


async def run_workflow(
    workflow: Any = None,
    name: str = "",
    timeout: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """把一份 API 格式工作流提交到本地 ComfyUI 跑一次，返回产物清单。

    只做「原样提交 + 等待 + 取 history 产物」：参数注入（提示词/尺寸/种子）属于
    生成链路（渠道 + 适配文件）的职责，MCP 这里不猜节点，避免把用户给的工作流
    改得面目全非。
    """

    payload = workflow
    if payload is None and str(name or "").strip():
        fetched = await get_workflow(name=name)
        payload = fetched.get("workflow")
    if not isinstance(payload, dict) or not payload:
        raise ComfyUIMcpError("run_workflow 需要 workflow（API 格式对象）或 name")
    base = _base_url()
    client_id = "comfyui-mcp"
    data = await _post_json("/prompt", {"prompt": payload, "client_id": client_id})
    prompt_id = str((data or {}).get("prompt_id") or "") if isinstance(data, dict) else ""
    if not prompt_id:
        raise ComfyUIMcpError(f"ComfyUI 未返回 prompt_id：{data}")
    deadline = _int_or(timeout, int(RUN_TIMEOUT_SECONDS))
    outputs = await _wait_for_history(prompt_id, timeout_seconds=deadline)
    node_outputs = outputs.get("outputs") if isinstance(outputs, dict) else {}
    artifacts: list[dict[str, Any]] = []
    for node_id, node_out in (node_outputs or {}).items():
        if not isinstance(node_out, dict):
            continue
        for key in ("images", "gifs", "videos", "audio"):
            for item in node_out.get(key) or []:
                if not isinstance(item, dict) or not item.get("filename"):
                    continue
                artifacts.append(
                    {
                        "nodeId": str(node_id),
                        "bucket": key,
                        "filename": str(item.get("filename")),
                        "subfolder": str(item.get("subfolder") or ""),
                        "type": str(item.get("type") or "output"),
                        "url": (
                            f"{base}/view?"
                            f"filename={item.get('filename')}"
                            f"&subfolder={item.get('subfolder') or ''}"
                            f"&type={item.get('type') or 'output'}"
                        ),
                    }
                )
    status = outputs.get("status") if isinstance(outputs, dict) else None
    return {
        "promptId": prompt_id,
        "status": status,
        "artifacts": artifacts,
        "viewBaseUrl": base,
    }


async def _wait_for_history(prompt_id: str, *, timeout_seconds: int) -> dict[str, Any]:
    waited = 0.0
    while waited < max(1, int(timeout_seconds)):
        history = await _get_json(f"/history/{prompt_id}")
        if isinstance(history, dict) and prompt_id in history:
            entry = history.get(prompt_id)
            return entry if isinstance(entry, dict) else {}
        await asyncio.sleep(RUN_POLL_INTERVAL_SECONDS)
        waited += RUN_POLL_INTERVAL_SECONDS
    raise ComfyUIMcpError(
        f"等待 ComfyUI 任务 {prompt_id} 结果超时（>{timeout_seconds}s）。"
        "任务可能仍在队列里，可用 prompt_id 到 ComfyUI 界面查看。"
    )


def _int_or(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


TOOLS: dict[str, tuple[dict[str, Any], Any]] = {
    "server_info": (
        {
            "description": "本地 ComfyUI 概况：地址、版本、已加载节点数量。用于先判断服务是否可用。",
            "parameters": {"type": "object", "properties": {}},
        },
        server_info,
    ),
    "list_workflows": (
        {
            "description": "列出本地 ComfyUI 已保存的工作流（id + 名称）。",
            "parameters": {"type": "object", "properties": {}},
        },
        list_workflows,
    ),
    "get_workflow": (
        {
            "description": "按名称取一份工作流，同时返回后端执行用的 API 格式（自动做浏览器图→API 转换）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "ComfyUI 工作流名称。"},
                    "workflowId": {"type": "integer", "description": "工作流 id（name 找不到时可用）。"},
                },
            },
        },
        get_workflow,
    ),
    "search_nodes": (
        {
            "description": "在本地实际安装的节点类型里检索（含必填/可选输入名）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "节点类型名子串，可空。"},
                    "limit": {"type": "integer", "description": "最多返回条数（默认 30）。"},
                },
            },
        },
        search_nodes,
    ),
    "search_models": (
        {
            "description": "列出本地已安装的模型（来自加载器节点控件候选：ckpt/lora/vae 等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "模型文件名子串，可空。"},
                },
            },
        },
        search_models,
    ),
    "validate_workflow": (
        {
            "description": "校验一份 API 格式工作流：节点是否装了、必填输入是否齐、连线引用是否存在。",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow": {"type": "object", "description": "API 格式工作流。"},
                    "name": {"type": "string", "description": "或直接给本地工作流名称。"},
                },
            },
        },
        validate_workflow,
    ),
    "run_workflow": (
        {
            "description": "把一份 API 格式工作流提交到本地 ComfyUI 执行，返回产物清单与下载地址。",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow": {"type": "object", "description": "API 格式工作流。"},
                    "name": {"type": "string", "description": "或直接给本地工作流名称。"},
                    "timeout": {"type": "integer", "description": "等待秒数（默认 600）。"},
                },
            },
        },
        run_workflow,
    ),
}


SERVER = Server(SERVER_NAME, version="0.1.0")


@SERVER.list_tools()
async def list_tools() -> list[types.Tool]:
    result: list[types.Tool] = []
    for name, (schema, _handler) in sorted(TOOLS.items()):
        parameters = schema.get("parameters")
        result.append(
            types.Tool(
                name=name,
                description=str(schema.get("description") or ""),
                inputSchema=parameters if isinstance(parameters, dict) else {"type": "object"},
            )
        )
    return result


@SERVER.call_tool(validate_input=True)
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    item = TOOLS.get(name)
    if item is None:
        raise ValueError(f"unknown ComfyUI tool: {name}")
    _schema, handler = item
    try:
        outcome = await handler(**(arguments or {}))
    except ComfyUIMcpError as exc:
        # 工具级失败用 MCP isError 文本回传：Agent 能读到成因并告诉用户下一步。
        return [types.TextContent(type="text", text=json.dumps(
            {"ok": False, "error": str(exc)}, ensure_ascii=False
        ))]
    return [
        types.TextContent(
            type="text",
            text=json.dumps({"ok": True, "data": outcome}, ensure_ascii=False, default=str),
        )
    ]


async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await SERVER.run(
            read_stream,
            write_stream,
            SERVER.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
