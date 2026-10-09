"""场景资产与导演采集工作流构建（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations

from typing import Any

from .refs import _safe_id
from .nodes import (
    _pano_360_viewer_node,
    _three_d_world_node,
    _director_world_node,
    _asset_image_node,
    _edge,
)
from .prompts import _prompt_text_node
from .roles import (
    SCENE_IMAGE_ROLES,
    SCENE_PLY_INPUT_ROLES,
    SCENE_PLY_WORKFLOW_ROLES,
    DIRECTOR_CAPTURE_IMAGE_ROLES,
)
from .scene_helpers import (
    _scene_prompt_content,
    _scene_reference_prompt_content,
    _scene_ply_input_roles,
)


def _append_scene_asset_workflow(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    ref_node_ids: list[tuple[dict[str, Any], str]],
    layout_style: str = "default",
) -> None:
    scene_refs: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for ref, node_id in ref_node_ids:
        role = str(ref.get("role") or "")
        if role not in SCENE_IMAGE_ROLES and not role.startswith("scene_3gs_"):
            continue
        meta = ref.get("meta") if isinstance(ref.get("meta"), dict) else {}
        scene_id = str(meta.get("scene_id") or meta.get("scene") or "").strip()
        if not scene_id:
            continue
        scene_refs.setdefault(scene_id, []).append((ref, node_id))

    for scene_index, (scene_id, items) in enumerate(sorted(scene_refs.items())):
        prompt_source_ref = next(
            (ref for ref, _node_id in items if str(ref.get("role") or "") in SCENE_IMAGE_ROLES),
            items[0][0],
        )
        prompt_meta = (
            prompt_source_ref.get("meta") if isinstance(prompt_source_ref.get("meta"), dict) else {}
        )
        safe_scene = _safe_id(scene_id, fallback="scene")
        role_to_node: dict[str, str] = {}
        role_to_ref: dict[str, dict[str, Any]] = {}
        for ref, node_id in items:
            role = str(ref.get("role") or "")
            role_to_node.setdefault(role, node_id)
            role_to_ref.setdefault(role, ref)

        def _ensure_ply_workflow_node(
            role: str,
            *,
            x: int,
            y: int,
        ) -> str | None:
            existing_id = role_to_node.get(role)
            if existing_id:
                return existing_id
            config = SCENE_PLY_WORKFLOW_ROLES.get(role)
            if not config:
                return None
            ply_kind, preferred_source_role, label_suffix = config
            input_roles = SCENE_PLY_INPUT_ROLES.get(role, ())
            has_input = any(input_role in role_to_node for input_role in input_roles)
            if not has_input:
                return None
            source_ref = role_to_ref.get(preferred_source_role)
            if source_ref is None:
                source_ref = next(
                    (
                        role_to_ref[input_role]
                        for input_role in input_roles
                        if input_role in role_to_ref
                    ),
                    None,
                )
            source_meta = (
                source_ref.get("meta")
                if source_ref is not None and isinstance(source_ref.get("meta"), dict)
                else prompt_meta
            )
            ref = {
                "kind": "scene",
                "role": role,
                "label": f"{scene_id} {label_suffix}",
                "rel_path": None,
                "url": None,
                "exists": False,
                "media_type": "file",
                "aspect_ratio": "1:1",
                "meta": {**source_meta, "scene_id": scene_id, "ply_kind": ply_kind},
            }
            node_id = f"workflow_scene_{safe_scene}_{ply_kind}_ply"
            if any(node.get("id") == node_id for node in nodes):
                role_to_node[role] = node_id
                role_to_ref[role] = ref
                return node_id
            nodes.append(_three_d_world_node(node_id, x, y, ref))
            items.append((ref, node_id))
            role_to_node[role] = node_id
            role_to_ref[role] = ref
            return node_id

        def _set_scene_node_position(node_id: str | None, x: int, y: int) -> None:
            if not node_id:
                return
            for node in nodes:
                if node.get("id") == node_id:
                    node["position"] = {"x": x, "y": y}
                    return

        master_node = role_to_node.get("scene_master")
        reverse_node = role_to_node.get("scene_reverse_master")
        if layout_style == "sketch":
            master_prompt_pos = (-630, 590)
            reverse_prompt_pos = (1040, 730)
            generic_prompt_pos = (-680, 1160)
        elif layout_style == "beat":
            scene_row_y = 640 + scene_index * 900
            master_prompt_pos = (-540, scene_row_y)
            reverse_prompt_pos = (300, scene_row_y + 380)
            generic_prompt_pos = (-540, scene_row_y + 460)
        else:
            scene_y = scene_index * 1180
            master_prompt_pos = (100, 110 + scene_y)
            reverse_prompt_pos = (655, -376 + scene_y)
            generic_prompt_pos = (55, 520 + scene_y)
            _set_scene_node_position(master_node, 620, 100 + scene_y)
            _set_scene_node_position(reverse_node, 1460, -250 + scene_y)

            pano_roles = [
                role for role in ("scene_director_pano_360", "scene_360") if role in role_to_node
            ]
            for idx, role in enumerate(pano_roles):
                # Pano viewer 从 x=620 右移到 x=1240,腾出左边空间给
                # scene_director_pano_360 workflow trigger 表达数据流方向。
                _set_scene_node_position(role_to_node.get(role), 1240, 500 + scene_y + idx * 320)

            ply_roles = [
                role
                for role in (
                    "scene_3gs_active_ply",
                    "scene_3gs_master_ply",
                    "scene_3gs_reverse_ply",
                    "scene_3gs_pano_ply",
                    "scene_3gs_custom_scene",
                )
                if role in role_to_node
            ]
            for idx, role in enumerate(ply_roles):
                _set_scene_node_position(role_to_node.get(role), 1460, 460 + scene_y + idx * 320)

            for idx, role in enumerate(SCENE_PLY_WORKFLOW_ROLES):
                _ensure_ply_workflow_node(role, x=1460, y=460 + scene_y + idx * 320)

        if master_node:
            master_prompt_id = f"prompt_scene_{safe_scene}_master"
            if not any(node.get("id") == master_prompt_id for node in nodes):
                master_meta = (
                    role_to_ref.get("scene_master", {}).get("meta")
                    if isinstance(role_to_ref.get("scene_master", {}).get("meta"), dict)
                    else prompt_meta
                )
                nodes.append(
                    _prompt_text_node(
                        master_prompt_id,
                        master_prompt_pos[0],
                        master_prompt_pos[1],
                        f"{scene_id} master prompt",
                        _scene_reference_prompt_content(
                            scene_id,
                            master_meta,
                            kind="master",
                        ),
                        source_meta={
                            "kind": "scene_prompt",
                            "role": "scene_master_generation_prompt",
                            "label": f"{scene_id} master prompt",
                            "meta": {"scene_id": scene_id, "scene_reference_kind": "master"},
                        },
                    )
                )
            edges.append(
                _edge(
                    f"edge_{master_prompt_id}_to_{_safe_id(master_node)}",
                    master_prompt_id,
                    master_node,
                )
            )
            base_scene_id = str(prompt_meta.get("base_scene_id") or "").strip()
            base_master_url = str(prompt_meta.get("base_master_url") or "").strip()
            if base_scene_id and base_master_url:
                base_master_node_id = f"ref_scene_base_master_{safe_scene}"
                if not any(node.get("id") == base_master_node_id for node in nodes):
                    base_master_rel_path = str(
                        prompt_meta.get("base_master_rel_path") or ""
                    ).strip()
                    base_ref = {
                        "kind": "scene",
                        "role": "scene_base_master",
                        "label": f"{base_scene_id} base master",
                        "rel_path": base_master_rel_path or None,
                        "url": base_master_url,
                        "exists": True,
                        "media_type": "image",
                        "aspect_ratio": "16:9",
                        "meta": {
                            **prompt_meta,
                            "scene_id": base_scene_id,
                            "derived_scene_id": scene_id,
                            "base_for_scene_id": scene_id,
                        },
                    }
                    nodes.append(
                        _asset_image_node(
                            base_master_node_id,
                            master_prompt_pos[0],
                            master_prompt_pos[1] + 300,
                            base_ref,
                        )
                    )
                edges.append(
                    _edge(
                        f"edge_{base_master_node_id}_to_{_safe_id(master_node)}",
                        base_master_node_id,
                        master_node,
                        {"edgeKind": "scene_base_master_dependency"},
                    )
                )

        if reverse_node:
            reverse_prompt_id = f"prompt_scene_{safe_scene}_reverse_master"
            if not any(node.get("id") == reverse_prompt_id for node in nodes):
                reverse_meta = (
                    role_to_ref.get("scene_reverse_master", {}).get("meta")
                    if isinstance(role_to_ref.get("scene_reverse_master", {}).get("meta"), dict)
                    else prompt_meta
                )
                nodes.append(
                    _prompt_text_node(
                        reverse_prompt_id,
                        reverse_prompt_pos[0],
                        reverse_prompt_pos[1],
                        f"{scene_id} reverse master prompt",
                        _scene_reference_prompt_content(
                            scene_id,
                            reverse_meta,
                            kind="reverse_master",
                            has_master_reference=bool(master_node),
                        ),
                        source_meta={
                            "kind": "scene_prompt",
                            "role": "scene_reverse_master_generation_prompt",
                            "label": f"{scene_id} reverse master prompt",
                            "meta": {
                                "scene_id": scene_id,
                                "scene_reference_kind": "reverse_master",
                            },
                        },
                    )
                )
            if master_node:
                edges.append(
                    _edge(
                        f"edge_{_safe_id(master_node)}_to_{_safe_id(reverse_node)}",
                        master_node,
                        reverse_node,
                    )
                )
            edges.append(
                _edge(
                    f"edge_{reverse_prompt_id}_to_{_safe_id(reverse_node)}",
                    reverse_prompt_id,
                    reverse_node,
                )
            )

        generic_roles = {
            role
            for role in SCENE_IMAGE_ROLES
            if role not in {"scene_master", "scene_reverse_master"}
        }
        if any(role in role_to_node for role in generic_roles):
            prompt_id = f"prompt_scene_{safe_scene}"
        else:
            prompt_id = ""
        if prompt_id and not any(node.get("id") == prompt_id for node in nodes):
            nodes.append(
                _prompt_text_node(
                    prompt_id,
                    generic_prompt_pos[0],
                    generic_prompt_pos[1],
                    f"{scene_id} scene prompt",
                    _scene_prompt_content(scene_id, prompt_meta),
                    source_meta={
                        "kind": "scene_prompt",
                        "role": "scene_generation_prompt",
                        "label": f"{scene_id} scene prompt",
                        "meta": {"scene_id": scene_id},
                    },
                )
            )

        for ref, node_id in items:
            role = str(ref.get("role") or "")
            if prompt_id and role in generic_roles:
                # 资产画布 (layout_style=="default") 下,scene_director_pano_360 /
                # scene_360 viewer 旁会有独立的 workflow_scene_director_pano_360
                # trigger;prompt 应该连到 trigger 而非 viewer。这条 edge 在 asset
                # preset Phase 1d 阶段单独 emit。
                # Beat 画布 (layout_style=="beat" / "sketch") 下,pano viewer 是
                # 信息性参考节点 (没独立 trigger),prompt → pano viewer 仍有意义。
                if layout_style == "default" and role in {"scene_director_pano_360", "scene_360"}:
                    continue
                edges.append(
                    _edge(
                        f"edge_{prompt_id}_to_{_safe_id(node_id)}",
                        prompt_id,
                        node_id,
                    )
                )

        for pano_role in ("scene_director_pano_360", "scene_360"):
            source_id = role_to_node.get(pano_role)
            source_ref = role_to_ref.get(pano_role)
            if not source_id or not source_ref:
                continue
            source_node = next((node for node in nodes if node.get("id") == source_id), None)
            if source_node and source_node.get("type") == "pano360ViewerNode":
                continue
            viewer_id = f"viewer_{_safe_id(source_id)}"
            if any(node.get("id") == viewer_id for node in nodes):
                continue
            source_position = (source_node or {}).get("position") or {}
            source_x = int(source_position.get("x") or 0)
            source_y = int(source_position.get("y") or 0)
            viewer_x = source_x
            viewer_y = source_y + 360
            if layout_style == "sketch":
                viewer_x = 880
                viewer_y = 1135 + scene_index * 520
            if layout_style == "default":
                viewer_x = 1420
                viewer_y = 945 + scene_index * 1180
            nodes.append(
                _pano_360_viewer_node(
                    viewer_id,
                    viewer_x,
                    viewer_y,
                    source_ref,
                )
            )
            edges.append(
                _edge(
                    f"edge_{_safe_id(source_id)}_to_{_safe_id(viewer_id)}",
                    source_id,
                    viewer_id,
                )
            )

        for ref, node_id in items:
            role = str(ref.get("role") or "")
            input_roles = _scene_ply_input_roles(role, ref)
            if not input_roles:
                continue
            source_id = next(
                (
                    role_to_node[input_role]
                    for input_role in input_roles
                    if input_role in role_to_node
                ),
                None,
            )
            if source_id:
                edges.append(
                    _edge(
                        f"edge_{_safe_id(source_id)}_to_{_safe_id(node_id)}",
                        source_id,
                        node_id,
                    )
                )

        if layout_style == "default":
            director_world_id = f"director_world_scene_{safe_scene}"
            source_roles = {
                "scene_master",
                "scene_reverse_master",
                "scene_director_pano_360",
                "scene_360",
                "scene_3gs_active_ply",
                "scene_3gs_master_ply",
                "scene_3gs_reverse_ply",
                "scene_3gs_pano_ply",
                "scene_3gs_custom_scene",
            }
            source_refs = [
                ref for ref, _node_id in items if str(ref.get("role") or "") in source_roles
            ]

            for pano_role in ("scene_director_pano_360", "scene_360"):
                pano_node_id = role_to_node.get(pano_role)
                pano_ref = role_to_ref.get(pano_role)
                if not pano_node_id or not pano_ref:
                    continue
                existing_node = next(
                    (node for node in nodes if node.get("id") == pano_node_id),
                    None,
                )
                if not existing_node:
                    continue
                pos = existing_node.get("position") or {}
                pano_node = _asset_image_node(
                    pano_node_id,
                    int(pos.get("x") or 1240),
                    int(pos.get("y") or 500 + scene_index * 1180),
                    pano_ref,
                )
                pano_node["data"].update(
                    {
                        "media_kind": "pano360",
                        "output_role": "scene_director_pano_360",
                        "aspectRatio": "2:1",
                        "actualAspectRatio": "2:1",
                    }
                )
                nodes[:] = [pano_node if node.get("id") == pano_node_id else node for node in nodes]

            removed_world_node_ids = {
                node_id
                for role, node_id in role_to_node.items()
                if role.startswith("scene_3gs_") and node_id != director_world_id
            }
            if removed_world_node_ids:
                nodes[:] = [node for node in nodes if node.get("id") not in removed_world_node_ids]
                edges[:] = [
                    edge
                    for edge in edges
                    if edge.get("source") not in removed_world_node_ids
                    and edge.get("target") not in removed_world_node_ids
                ]

            existing_world = next(
                (node for node in nodes if node.get("id") == director_world_id),
                None,
            )
            director_world = _director_world_node(
                director_world_id,
                1880,
                500 + scene_index * 1180,
                scene_id=scene_id,
                refs=source_refs,
            )
            if existing_world:
                nodes[:] = [
                    director_world if node.get("id") == director_world_id else node
                    for node in nodes
                ]
            else:
                nodes.append(director_world)

            for role in (
                "scene_master",
                "scene_reverse_master",
                "scene_director_pano_360",
                "scene_360",
            ):
                source_id = role_to_node.get(role)
                if not source_id:
                    continue
                if any(
                    edge.get("source") == source_id and edge.get("target") == director_world_id
                    for edge in edges
                ):
                    continue
                edges.append(
                    _edge(
                        f"edge_{_safe_id(source_id)}_to_{_safe_id(director_world_id)}",
                        source_id,
                        director_world_id,
                    )
                )


def _append_director_capture_workflow(
    *,
    edges: list[dict[str, Any]],
    ref_node_ids: list[tuple[dict[str, Any], str]],
) -> None:
    role_to_nodes: dict[str, list[str]] = {}
    for ref, node_id in ref_node_ids:
        role = str(ref.get("role") or "")
        role_to_nodes.setdefault(role, []).append(node_id)

    source_id = next(
        (
            node_id
            for role in (
                "scene_3gs_active_ply",
                "scene_3gs_master_ply",
                "scene_3gs_pano_ply",
                "scene_3gs_reverse_ply",
                "scene_3gs_uploaded_ply",
            )
            for node_id in role_to_nodes.get(role, [])
        ),
        None,
    )
    if not source_id:
        return

    def has_edge(source: str, target: str) -> bool:
        return any(edge.get("source") == source and edge.get("target") == target for edge in edges)

    for role in DIRECTOR_CAPTURE_IMAGE_ROLES:
        for target_id in role_to_nodes.get(role, []):
            if has_edge(source_id, target_id):
                continue
            edges.append(
                _edge(
                    f"edge_{_safe_id(source_id)}_to_{_safe_id(target_id)}",
                    source_id,
                    target_id,
                )
            )
