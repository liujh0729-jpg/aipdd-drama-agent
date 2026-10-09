"""本地 ComfyUI 工作流：按名称从 ComfyUI 服务拉取并转换为 API 格式。

[aipdd fork delta] 参考项目要求用户手动粘贴 ComfyUI「Export (API)」导出的
JSON。这里改为「用户只填模型名 + 工作流名称」，后端连上用户的 ComfyUI，
按名称取回浏览器保存格式的工作流，并在服务端转换为 API 格式（class_type /
inputs 结构），再交给既有的渠道保存链路。

ComfyUI 服务端只提供浏览器保存格式（nodes/links），没有「按名称返回 API 格
式」的官方接口，因此这里内置一个尽力而为的 graph -> api 转换器：
- 依据 ``/api/object_info`` 的输入定义顺序映射 ``widgets_values``；
- 识别被转成连线输入的槽位（node.inputs）并跳过对应 widget；
- 跳过 hidden 输入与 muted/bypass 节点；
- 处理 seed 类控件紧随其后的 control_after_generate 值（fixed/increment/...）。
转换结果会用与后端一致的「API 格式」校验；失败的模型会在返回里明确报出，
UI 需据此提示用户。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

# ComfyUI「生成控制」下拉的取值，出现在 seed/noise 等整数控件之后。
_CONTROL_AFTER_GENERATE = {"fixed", "increment", "decrement", "randomize"}


def _spec_options(spec: Any) -> dict[str, Any]:
    """从 object_info 的输入定义里取出选项 dict（形如 [TYPE, {...}]）。"""
    if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1]
    return {}


def is_api_format_workflow(obj: Any) -> bool:
    """判断给定对象是否已经是 API 格式（节点值含 class_type/inputs）。"""
    if not isinstance(obj, dict) or not obj:
        return False
    return any(
        isinstance(node, dict) and ("class_type" in node or "inputs" in node)
        for node in obj.values()
    )


def coerce_graph(payload: Any) -> dict[str, Any] | None:
    """[aipdd fork delta] 公共名：本地 ComfyUI MCP（``chat.comfyui_mcp``）同样要复用
    这套「多种包装结构 → 浏览器图」的识别，不再跨模块引私有名。

    在 ComfyUI 返回的多种包装结构中找到含 nodes/links 的浏览器格式图。
    """
    candidates: list[Any] = []
    if isinstance(payload, dict):
        candidates.append(payload)
        for key in ("json", "workflow", "graph", "definitions", "data", "value", "content"):
            candidates.append(payload.get(key))
    for cand in candidates:
        if isinstance(cand, str):
            try:
                cand = json.loads(cand)
            except (ValueError, TypeError):
                continue
        if isinstance(cand, dict) and isinstance(cand.get("nodes"), list) and "links" in cand:
            return cand
    return None


def convert_graph_to_api(
    graph: dict[str, Any],
    object_info: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """把 ComfyUI 浏览器格式图转换为 API 格式。

    返回 (api_workflow, missing_class_types)。missing_class_types 记录在
    object_info 里查不到的节点类型（多为自定义节点未加载），供上层提示。
    """
    nodes = graph.get("nodes") or []
    links = graph.get("links") or []
    links_by_id: dict[Any, list[Any]] = {}
    for link in links:
        # link 形如 [id, origin_node, origin_slot, target_node, target_slot, type]
        if isinstance(link, list) and len(link) >= 5:
            links_by_id[link[0]] = link

    api: dict[str, Any] = {}
    missing: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        # 2 = muted, 4 = bypass：ComfyUI 导出 API 时会跳过这两类节点。
        if node.get("mode", 0) in (2, 4):
            continue
        node_id = node.get("id")
        class_type = node.get("type")
        if node_id is None or not class_type:
            continue
        info = object_info.get(class_type)
        if not isinstance(info, dict):
            missing.add(str(class_type))
        input_spec = (info or {}).get("input") or {}
        required = input_spec.get("required") or {}
        optional = input_spec.get("optional") or {}
        defs: dict[str, Any] = {**required, **optional}
        order = input_spec.get("order")
        if not isinstance(order, list) or not order:
            order = list(required.keys()) + [
                key for key in optional.keys() if key not in required
            ]

        node_inputs = node.get("inputs") or []
        socket_names: set[str] = set()
        linked: dict[str, list[Any]] = {}
        for sock in node_inputs:
            if not isinstance(sock, dict):
                continue
            name = sock.get("name")
            if name is None:
                continue
            socket_names.add(str(name))
            link_id = sock.get("link")
            if link_id is None:
                raw_links = sock.get("links")
                if isinstance(raw_links, list) and raw_links:
                    link_id = raw_links[0]
            if link_id is not None:
                resolved = links_by_id.get(link_id)
                if resolved:
                    linked[str(name)] = [str(resolved[1]), resolved[2]]

        inputs: dict[str, Any] = dict(linked)
        widget_values = list(node.get("widgets_values") or [])
        cursor = 0
        for name in order:
            key = str(name)
            # 连线/被转成输入的槽位、或 hidden 输入都不消耗 widget 值。
            if key in socket_names:
                continue
            spec = defs.get(key)
            if spec is None:
                continue
            options = _spec_options(spec)
            if options.get("hidden"):
                continue
            if cursor >= len(widget_values):
                break
            inputs[key] = widget_values[cursor]
            cursor += 1
            # seed 类整数控件后紧跟一个 control_after_generate 下拉值，需跳过。
            if (
                cursor < len(widget_values)
                and isinstance(widget_values[cursor], str)
                and widget_values[cursor] in _CONTROL_AFTER_GENERATE
            ):
                cursor += 1

        entry: dict[str, Any] = {"class_type": str(class_type), "inputs": inputs}
        title = node.get("title")
        if isinstance(title, str) and title.strip():
            entry["_meta"] = {"title": title.strip()}
        api[str(node_id)] = entry

    return api, sorted(missing)


def _endpoint(base_url: str) -> str:
    return base_url.rstrip("/")


def _pick_workflow(items: list[Any], name: str) -> dict[str, Any] | None:
    """在 /api/workflows 列表里按名称（大小写不敏感）匹配工作流。"""
    target = name.strip().lower()
    for item in items:
        if isinstance(item, dict) and str(item.get("name") or "").strip().lower() == target:
            return item
    return None


async def _fetch_object_info(
    client: httpx.AsyncClient, base_url: str
) -> dict[str, Any]:
    resp = await client.get(f"{_endpoint(base_url)}/api/object_info")
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {}


async def _fetch_workflows(
    client: httpx.AsyncClient, base_url: str
) -> list[Any]:
    resp = await client.get(f"{_endpoint(base_url)}/api/workflows")
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


async def _fetch_workflow_detail(
    client: httpx.AsyncClient, base_url: str, workflow_id: Any
) -> Any:
    resp = await client.get(f"{_endpoint(base_url)}/api/workflows/{workflow_id}")
    resp.raise_for_status()
    return resp.json()


async def resolve_comfyui_workflows(
    base_url: str,
    items: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """按名称解析每个模型的工作流为 API 格式。

    ``items`` 形如 ``[{"model": "...", "workflowName": "..."}]``。返回
    ``(workflow_by_model, errors)``，errors 形如
    ``[{"model": "...", "workflowName": "...", "error": "..."}]``。
    """
    workflow_by_model: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    cleaned = [
        {
            "model": str(it.get("model") or "").strip(),
            "workflowName": str(it.get("workflowName") or "").strip(),
        }
        for it in items
    ]
    cleaned = [it for it in cleaned if it["model"] and it["workflowName"]]
    if not cleaned:
        return workflow_by_model, errors

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            listing = await _fetch_workflows(client, base_url)
        except httpx.HTTPStatusError as exc:
            raise ValueError(
                f"ComfyUI /api/workflows 返回 {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ValueError(f"连接 ComfyUI 失败：{exc}") from exc

        object_info: dict[str, Any] | None = None
        for item in cleaned:
            model = item["model"]
            name = item["workflowName"]
            matched = _pick_workflow(listing, name)
            if matched is None:
                errors.append(
                    {
                        "model": model,
                        "workflowName": name,
                        "error": "在 ComfyUI 工作流列表里找不到该名称",
                    }
                )
                continue
            payload: Any = matched
            graph = coerce_graph(payload)
            if graph is None and matched.get("id") is not None:
                try:
                    payload = await _fetch_workflow_detail(client, base_url, matched["id"])
                except httpx.HTTPError as exc:
                    errors.append(
                        {
                            "model": model,
                            "workflowName": name,
                            "error": f"拉取工作流详情失败：{exc}",
                        }
                    )
                    continue
                graph = coerce_graph(payload)

            if is_api_format_workflow(payload):
                api_wf = payload
            elif graph is not None:
                if object_info is None:
                    try:
                        object_info = await _fetch_object_info(client, base_url)
                    except httpx.HTTPError:
                        object_info = {}
                api_wf, missing = convert_graph_to_api(graph, object_info)
                if missing:
                    errors.append(
                        {
                            "model": model,
                            "workflowName": name,
                            "error": "部分节点类型未在 ComfyUI object_info 中找到："
                            + ", ".join(missing),
                        }
                    )
            else:
                errors.append(
                    {
                        "model": model,
                        "workflowName": name,
                        "error": "无法识别工作流格式（既非 API 也非浏览器格式）",
                    }
                )
                continue

            if not is_api_format_workflow(api_wf):
                errors.append(
                    {
                        "model": model,
                        "workflowName": name,
                        "error": "转换结果不是有效的 API 格式工作流",
                    }
                )
                continue
            workflow_by_model[model] = api_wf

    return workflow_by_model, errors
