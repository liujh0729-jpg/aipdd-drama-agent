"""freezone.skill_routes 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi import Body
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneCharacterMultiViewRequest
from novelvideo.api.schemas import FreezoneJobAcceptedResponse
from novelvideo.freezone.route_helpers import FREEZONE_DEFAULT_IMAGE_MODEL
from novelvideo.freezone.route_helpers import build_multi_view_prompt as _build_multi_view_prompt
from novelvideo.freezone.route_helpers import build_scene_360_prompt as _build_scene_360_prompt
from novelvideo.freezone.skill_registry import SkillRunRequest
from novelvideo.freezone.skill_registry import SkillRunResponse
from novelvideo.freezone.skill_registry import find_skill
from novelvideo.freezone.skill_registry import list_skills

from ._common import (MAINLINE_SCENE_360_IMAGE_SIZE, TAG_FREEZONE_IMAGE, TAG_FREEZONE_SKILLS)

router = APIRouter()


@router.get("/freezone/skills", tags=[TAG_FREEZONE_SKILLS])
async def freezone_skills(user: dict = Depends(get_api_user)):
    return {"ok": True, "data": [skill.model_dump(mode="json") for skill in list_skills()]}


async def _run_ai_staging_prop(request: dict[str, object]) -> dict[str, object]:
    return await asyncio.to_thread(_freezone.generate_ai_staging_prop, request)


@router.post("/projects/{project}/freezone/ai-staging-prop", tags=[TAG_FREEZONE_SKILLS])
async def freezone_ai_staging_prop(
    project: str,
    request: dict[str, object] = Body(default_factory=dict),
    user: dict = Depends(get_api_user),
):
    ctx, _username, _project_name, _project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="editor"
    )
    # Product requests always use the edition's effective NewAPI gateway.
    # Keep low-level overrides available to offline helpers, but never accept
    # credentials or an endpoint from an HTTP payload.
    request = dict(request)
    request.pop("api_key", None)
    request.pop("base_url", None)
    # 出网侧的闸门（`task_backend/subprocesses.py:122-128`，经
    # `staging_prop_ai.py:233`）读的是 ambient context，而 `_MODEL_GATEWAY_CONTEXT`
    # 的生产 set 点全在 worker 侧、请求路径上一个都没有。于是这道闸门此前永远读到
    # `None`、永远放行，组织用户的调用落在平台 `MODEL_API_KEY` 上（台账 EG-17）。
    # 绑定能穿过 `_run_ai_staging_prop` 的 `asyncio.to_thread`，因为它复制
    # contextvars（`model_gateway_runtime.py:52-54`）；`run_in_executor` 不会。
    from novelvideo.api.egress_binding import request_egress_scope
    from novelvideo.ports.authz import AuthzError
    from novelvideo.task_backend.subprocesses import EgressBoundaryError

    async with request_egress_scope(
        requester_user_id=ctx.requester_user_id,
        project_id=ctx.project_id,
        task_type="freezone_ai_staging_prop",
    ):
        try:
            result = await _freezone._run_ai_staging_prop(request)
        except EgressBoundaryError as exc:
            # `EgressBoundaryError` 也是 `RuntimeError` 子类，没有这一支时组织拒绝
            # 会被下面压成裸 502 + 自由文本，前端拿不到机器码。同形先例见 :353-358。
            # 这一支必须在 `except RuntimeError` **之前**，且 `AuthzError`（自身也是
            # `RuntimeError` 子类）由 app 级 handler 渲染成契约化 4xx。
            raise AuthzError(exc.code) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(
            status_code=502, detail=str(result.get("error") or "AI staging prop failed")
        )
    return {"ok": True, "data": result}


@router.post(
    "/projects/{project}/freezone/skills/{skill_id}/run",
    response_model=SkillRunResponse,
    tags=[TAG_FREEZONE_SKILLS],
)
async def freezone_skill_run(
    project: str,
    skill_id: str,
    body: SkillRunRequest,
    user: dict = Depends(get_api_user),
):
    skill = find_skill(skill_id)
    if skill is None:
        _freezone._raise_skill_error(
            404,
            code="skill_not_found",
            category="not_found",
            message="skill not found",
            user_action_hint="Refresh the skill registry and try again.",
        )
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    idempotency_request_hash, idempotent_response = _freezone._idempotent_skill_run_response(
        project_dir,
        skill_id,
        body,
    )
    if idempotent_response is not None:
        return idempotent_response
    grouped = _freezone._group_and_validate_skill_inputs(
        skill,
        body.resolved_inputs,
        project=project,
        ctx=ctx,
        username=username,
        project_name=project_name,
    )
    auto_commit = _freezone._skill_node_is_preset_managed(
        project_dir=project_dir,
        ctx=ctx,
        canvas_id=body.canvas_id,
        skill_node_id=body.skill_node_id,
    )
    if _freezone._is_standalone_beat_context_input(_freezone._single_input(grouped, "beat_context")):
        auto_commit = False

    if skill_id in {"freezone.sketch_from_context", "freezone.sketch_from_director_combined"}:
        parameters = _freezone._skill_run_parameters(body)
        aspect_ratio = _freezone._normalize_mainline_skill_aspect_ratio(parameters.get("aspect_ratio"))
        beat_input = _freezone._required_input(grouped, "beat_context")
        is_standalone_beat_context = _freezone._is_standalone_beat_context_input(beat_input)
        if is_standalone_beat_context:
            if skill_id == "freezone.sketch_from_director_combined":
                reference_role = "director_combined"
                reference_input = _freezone._required_input(grouped, reference_role)
                source_label = "导演合成图"
            else:
                reference_role = "background"
                reference_input = _freezone._required_input(grouped, reference_role)
                source_label = "背景"
            reference_url = _freezone._required_image_url(reference_input, reference_role)
            reference_paths = _freezone._resolve_url_list(project_dir, [reference_url])
            model = str(parameters.get("model") or FREEZONE_DEFAULT_IMAGE_MODEL)
            accepted = await _freezone._start_or_enqueue_freezone_gen_job(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                output_dir=str(_output_dir),
                prompt=_freezone._standalone_beat_context_unified_sketch_prompt(
                    input_item=beat_input,
                    project_dir=project_dir,
                    reference_path=reference_paths[0] if reference_paths else "",
                    reference_role=reference_role,
                    aspect_ratio=aspect_ratio,
                    provider=None,
                    model=model,
                ),
                aspect_ratio=aspect_ratio,
                image_size=str(parameters.get("image_size") or "2K"),
                reference_urls=[reference_url],
                camera=None,
                style=None,
                provider=None,
                model=model,
                quality=str(parameters.get("quality") or "medium"),
                canvas_id=body.canvas_id,
                node_id=body.skill_node_id,
                task_display={
                    "task_label": "生成草图",
                    "display_name": "生成草图",
                    "source_label": source_label,
                    "target_label": "草图候选",
                    "skill_id": skill_id,
                },
            )
        else:
            episode, beat = _freezone._episode_and_beat_from_input(beat_input)
            if skill_id == "freezone.sketch_from_director_combined":
                director_combined = _freezone._required_input(grouped, "director_combined")
                accepted = await _freezone._start_or_enqueue_mainline_director_control_sketch_job(
                    ctx=ctx,
                    project_dir=project_dir,
                    episode=episode,
                    beat=beat,
                    director_combined_url=_freezone._required_image_url(
                        director_combined,
                        "director_combined",
                    ),
                    aspect_ratio=aspect_ratio,
                    canvas_id=body.canvas_id,
                    node_id=body.skill_node_id,
                    task_display={
                        "task_family": "mainline_skill",
                        "task_label": "导演合成图转草图",
                        "display_name": f"导演合成图转草图 · EP{episode} / Beat {beat}",
                        "source_label": "导演合成图",
                        "target_label": "当前草图候选",
                        "skill_id": skill_id,
                    },
                )
            else:
                background = _freezone._required_input(grouped, "background")
                accepted = await _freezone._start_or_enqueue_mainline_sketch_from_context_job(
                    ctx=ctx,
                    username=username,
                    project_name=project_name,
                    project_dir=project_dir,
                    episode=episode,
                    beat=beat,
                    beat_payload=_freezone._skill_beat_context_as_prompt_beat(beat_input),
                    background_url=_freezone._required_image_url(background, "background"),
                    aspect_ratio=aspect_ratio,
                    canvas_id=body.canvas_id,
                    node_id=body.skill_node_id,
                    task_display={
                        "task_family": "mainline_skill",
                        "task_label": "生成草图",
                        "display_name": f"生成草图 · EP{episode} / Beat {beat}",
                        "source_label": "背景",
                        "target_label": "当前草图",
                        "skill_id": skill_id,
                    },
                )
    elif skill_id == "freezone.frame_from_context":
        parameters = _freezone._skill_run_parameters(body)
        quality = _freezone._normalize_mainline_frame_quality(parameters.get("quality"))
        background_reference_mode = _freezone._skill_background_reference_mode(parameters)
        beat_input = _freezone._required_input(grouped, "beat_context")
        sketch = _freezone._required_input(grouped, "sketch")
        background = _freezone._single_input(grouped, "background")
        identity_references = _freezone._filter_canvas_references_by_beat_context(
            _freezone._canvas_references_from_inputs(grouped, "identity"),
            beat_input,
            "identity",
        )
        prop_references = _freezone._filter_canvas_references_by_beat_context(
            _freezone._canvas_references_from_inputs(grouped, "prop"),
            beat_input,
            "prop",
        )
        if _freezone._is_standalone_beat_context_input(beat_input):
            accepted = await _freezone._start_or_enqueue_standalone_frame_from_context_job(
                ctx=ctx,
                username=username,
                project_name=project_name,
                project_dir=project_dir,
                beat_input=beat_input,
                sketch_url=_freezone._required_image_url(sketch, "sketch"),
                reference_urls=(
                    [_freezone._required_image_url(background, "background")] if background else []
                ),
                extra_reference_urls=[],
                identity_references=identity_references,
                prop_references=prop_references,
                quality=quality,
                background_reference_mode=background_reference_mode,
                canvas_id=body.canvas_id,
                node_id=body.skill_node_id,
                task_display={
                    "task_family": "mainline_skill",
                    "task_label": "渲染分镜",
                    "display_name": "渲染分镜",
                    "source_label": "草图 + 背景 + 身份/道具",
                    "target_label": "分镜候选",
                    "skill_id": "freezone.frame_from_context",
                },
            )
        else:
            episode, beat = _freezone._episode_and_beat_from_input(beat_input)
            accepted = await _freezone._start_or_enqueue_mainline_frame_from_context_job(
                ctx=ctx,
                username=username,
                project_name=project_name,
                project_dir=project_dir,
                episode=episode,
                beat=beat,
                beat_payload=_freezone._skill_beat_context_as_prompt_beat(beat_input),
                sketch_url=_freezone._required_image_url(sketch, "sketch"),
                reference_urls=(
                    [_freezone._required_image_url(background, "background")] if background else []
                ),
                extra_reference_urls=[],
                identity_references=identity_references,
                prop_references=prop_references,
                quality=quality,
                background_reference_mode=background_reference_mode,
                canvas_id=body.canvas_id,
                node_id=body.skill_node_id,
                task_display={
                    "task_family": "mainline_skill",
                    "task_label": "渲染分镜",
                    "display_name": f"渲染分镜 · EP{episode} / Beat {beat}",
                    "source_label": "草图 + 背景 + 身份/道具",
                    "target_label": "当前分镜",
                    "skill_id": "freezone.frame_from_context",
                },
            )
    elif skill_id == "freezone.scene_360":
        scene_prompt = _freezone._scene_prompt_from_input(_freezone._single_input(grouped, "scene"))
        scene_master = _freezone._required_input(grouped, "scene_master")
        scene_reverse = _freezone._single_input(grouped, "scene_reverse_master")
        scene_id = _freezone._scene_id_from_scene_master_input(scene_master)
        description = _build_scene_360_prompt(scene_id)
        if scene_prompt:
            description = f"{description}\n\n场景提示词：{scene_prompt}"
        if auto_commit:
            accepted = await _freezone._start_or_enqueue_mainline_scene_360_task(
                ctx=ctx,
                project_dir=project_dir,
                scene_id=scene_id,
                description=description,
                master_url=_freezone._required_image_url(scene_master, "scene_master"),
                reverse_url=(
                    _freezone._required_image_url(scene_reverse, "scene_reverse_master")
                    if scene_reverse
                    else None
                ),
                model=None,
                image_size=MAINLINE_SCENE_360_IMAGE_SIZE,
                quality=None,
                canvas_id=body.canvas_id,
                node_id=body.skill_node_id,
                auto_commit=True,
                task_display={"skill_id": "freezone.scene_360"},
            )
        else:
            accepted = await _freezone._start_or_enqueue_mainline_scene_360_candidate_job(
                ctx=ctx,
                project_dir=project_dir,
                scene_id=str(scene_id),
                description=description,
                master_url=_freezone._required_image_url(scene_master, "scene_master"),
                reverse_url=(
                    _freezone._required_image_url(scene_reverse, "scene_reverse_master")
                    if scene_reverse
                    else None
                ),
                model=None,
                image_size=MAINLINE_SCENE_360_IMAGE_SIZE,
                quality=None,
                canvas_id=body.canvas_id,
                node_id=body.skill_node_id,
                task_display={"skill_id": "freezone.scene_360"},
            )
    elif skill_id == "freezone.set_selected_background":
        return await _freezone._run_set_selected_background_skill(
            project=project,
            project_dir=project_dir,
            ctx=ctx,
            username=username,
            project_name=project_name,
            skill=skill,
            grouped=grouped,
            body=body,
            user=user,
            idempotency_request_hash=idempotency_request_hash,
            auto_commit=auto_commit,
        )
    elif skill_id == "freezone.set_director_combined":
        return await _freezone._run_set_director_combined_skill(
            project=project,
            project_dir=project_dir,
            ctx=ctx,
            skill=skill,
            grouped=grouped,
            body=body,
            user=user,
            idempotency_request_hash=idempotency_request_hash,
            auto_commit=auto_commit,
        )
    elif skill_id == "agent.review_frame":
        run_id = f"agent.review_frame:{_freezone._new_job_id()}"
        output = _freezone._skill_output_metadata(skill, grouped)
        output["text"] = await _freezone._review_frame_text(body, grouped)
        _freezone._write_skill_run_metadata(
            project_dir,
            run_id,
            {
                "run_id": run_id,
                "skill_id": skill_id,
                "status": "completed",
                "outputs": [output],
                "canvas_id": body.canvas_id,
                "skill_node_id": body.skill_node_id,
            },
        )
        response = SkillRunResponse(run_id=run_id, status="completed")
        _freezone._append_canvas_event(
            project_dir=project_dir,
            project_id=project,
            canvas_id=body.canvas_id,
            event_type="skill.run_completed",
            actor=_freezone._canvas_event_actor(user),
            payload={
                "skill_id": skill_id,
                "skill_node_id": body.skill_node_id,
                "run_id": run_id,
                "status": response.status,
                "output_count": 1,
            },
        )
        _freezone._persist_skill_run_idempotency_response(
            project_dir,
            skill_id,
            body,
            idempotency_request_hash,
            response,
        )
        return response
    else:
        _freezone._raise_skill_error(
            501,
            code="skill_provider_not_runnable",
            category="unsupported",
            message="skill provider is not runnable",
            user_action_hint="Use a runnable skill provider or wait for its runtime integration.",
        )

    data = accepted.get("data") if isinstance(accepted, dict) else None
    if not isinstance(data, dict):
        _freezone._raise_skill_error(
            500,
            code="skill_run_metadata_missing",
            category="runtime",
            message="skill run did not return task metadata",
            retryable=True,
            user_action_hint="Retry the skill run. If this repeats, inspect the skill dispatcher.",
        )
    task_type = str(data.get("task_type") or "")
    job_id = str(data.get("job_id") or "")
    if not task_type or not job_id:
        _freezone._raise_skill_error(
            500,
            code="skill_run_metadata_incomplete",
            category="runtime",
            message="skill run missing task_type/job_id",
            retryable=True,
            user_action_hint="Retry the skill run. If this repeats, inspect the skill dispatcher.",
        )
    run_id = f"{task_type}:{job_id}"
    _freezone._write_skill_run_metadata(
        project_dir,
        run_id,
        {
            "run_id": run_id,
            "skill_id": skill_id,
            "status": "queued",
            "task_type": task_type,
            "job_id": job_id,
            "task_key": data.get("task_key"),
            "task_episode": data.get("task_episode", 0),
            "task_beat_num": data.get("task_beat_num"),
            "task_scope": data.get("task_scope") or job_id,
            "canvas_id": body.canvas_id,
            "skill_node_id": body.skill_node_id,
            "output": _freezone._skill_output_metadata(skill, grouped, auto_commit=auto_commit),
        },
    )
    response = SkillRunResponse(
        run_id=run_id,
        status="queued",
        task_key=data.get("task_key"),
        task_type=task_type,
        job_id=job_id,
    )
    _freezone._append_canvas_event(
        project_dir=project_dir,
        project_id=project,
        canvas_id=body.canvas_id,
        event_type="skill.run_requested",
        actor=_freezone._canvas_event_actor(user),
        payload={
            "skill_id": skill_id,
            "skill_node_id": body.skill_node_id,
            "run_id": run_id,
            "status": response.status,
            "task_type": task_type,
            "job_id": job_id,
        },
    )
    _freezone._persist_skill_run_idempotency_response(
        project_dir,
        skill_id,
        body,
        idempotency_request_hash,
        response,
    )
    return response


@router.post(
    "/projects/{project}/freezone/multi-view",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_multi_view(
    project: str,
    body: FreezoneCharacterMultiViewRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：基于单张源图做多角度重构 / 机位重定位。"""
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    return await _freezone._start_or_enqueue_freezone_edit_job(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        prompt=_build_multi_view_prompt(body),
        base_url=body.source_url,
        extra_reference_urls=[],
        aspect_ratio="16:9",
        image_size=body.image_size or "2K",
        camera=body.camera,
        style=body.style,
        provider=None,
        model=body.model or FREEZONE_DEFAULT_IMAGE_MODEL,
        quality=body.quality or "medium",
    )
