"""Backend-neutral project task execution core."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from novelvideo.egress_context import (
    TRUSTED_EGRESS_CONTEXT_KEY,
    TrustedEgressContext,
    TrustedRunnerEnvelope,
)
from novelvideo.i18n_message import LocalizableMessage
from novelvideo.model_gateway_runtime import model_gateway_scope_for_runner
from novelvideo.ports import get_usage_meter
from novelvideo.ports.authz import AdmissionContext
from novelvideo.project_context import require_project_home_node
from novelvideo.shared.billing_errors import billing_error_payload, find_billing_error
# [aipdd fork delta] WP-D: 额度体系已拆除，这里只判定上游账户配额。
from novelvideo.shared.upstream_quota_errors import (
    UPSTREAM_QUOTA_EXCEEDED_MESSAGE,
    is_upstream_quota_error,
    upstream_quota_payload,
)
from novelvideo.task_backend.consumer import VerifiedTaskDelivery
from novelvideo.task_backend.envelope import (
    InvalidTaskEnvelope,
    RunningTaskAuthorityIndeterminate,
)
from novelvideo.task_backend.cancel import (
    TaskCancelled,
    TaskTimedOut,
    is_cancel_requested,
)
from novelvideo.task_backend.registry import (
    get_project_task_runner,
    project_task_requires_home_node,
)
from novelvideo.task_backend.projection import PROJECTION_REQUIREMENTS, read_projection
from novelvideo.task_backend.subprocesses import project_task_subprocess_context
from novelvideo.task_state import project_task_run_context

logger = logging.getLogger(__name__)


#: Result key a runner may use to hand the task center its closing line. See
#: ``_take_completion_message``.
COMPLETION_MESSAGE_KEY = "completion_message"

_PROJECT_TASK_RESOURCE_KINDS = {
    "ingest_fast": "ingest",
    "build_characters": "script",
    "build_scenes": "script",
    "build_props": "script",
    "build_episodes": "script",
    "graph_preview": "script",
    "script_auto": "script",
    "script_writer": "script",
    "novel_to_screenplay": "script",
    "beat_video_prompt": "script",
    "identity_planner": "portrait",
    "episode_scene_planner": "script",
    "episode_prop_planner": "script",
    "character_portrait": "portrait",
    "identity_image": "portrait",
    "scene_reference_asset": "render",
    "prop_reference_asset": "render",
    "stage_asset": "render",
    "freezone_image_to_3gs": "render",
    "sketch_generation": "sketch",
    "director_control_to_sketch": "sketch",
    "sketch_grid_generation": "sketch",
    "sketch_regen": "sketch",
    "mainline_sketch_from_context": "sketch",
    "mainline_frame_from_context": "render",
    "mainline_director_control_sketch": "sketch",
    "sketch_edit_execute": "sketch",
    "action_sketch": "sketch",
    "selected_regen": "render",
    "grid_regenerate": "render",
    "single_video": "video",
    "compose_episode": "video",
    "global_optimize_video": "script",
    "audio_generation": "tts",
    "indextts2_audio_generation": "tts",
    "audio_generation_indextts2": "tts",
    "freezone_video_gen": "video",
    "freezone_analyze": "video",
    "freezone_video_story": "video",
    "freezone_video_clone": "video",
    "freezone_video_mix": "video",
    "freezone_whiteboard_animation": "video",
    "freezone_explainer_video": "video",
    # [aipdd fork delta] 动效大片渲染：逐帧截图 + 编码，与讲解视频同为长任务走 video 队列。
    "freezone_motion_render": "video",
    # [aipdd fork delta] 动效大片拍点预检：同样要独占一个 Chrome 逐张截图，排进同一条
    # 队列才不会让预检和成片同时拉起两个浏览器实例。
    "freezone_motion_stills": "video",
    # [aipdd fork delta] 动效大片交付：落一盘剪映工程目录，与口播剪辑的草稿导出同为
    # 交付类任务走 video 队列（不占渲染资源，但口径一致更好排程）。
    "freezone_motion_draft": "video",
    "freezone_channel_short_video": "video",
    "freezone_narration_smart_edit": "video",
    "freezone_video_idea": "video",
    "freezone_image_reverse_prompt": "script",
    "freezone_text_generate": "script",
    "freezone_article_write": "script",
    # [aipdd fork delta] 短视频口播稿创作：一次文本生成（硬拦时多一轮），与文章同为 script 档。
    "freezone_oral_script": "script",
    # [aipdd fork delta] 讲解类视频台词草稿：两段式第一段，只调文本叶，与口播稿同档。
    "freezone_explainer_script": "script",
    "freezone_story_script": "script",
}


def _resource_kind_for_task(task_type: str) -> str:
    return _PROJECT_TASK_RESOURCE_KINDS.get(task_type, "")


def _take_completion_message(result: Any) -> LocalizableMessage | None:
    """Pull a runner's own closing line off its result, if it published one.

    The completed row used to always read 「完成」, which threw away the graded
    verdict a chained runner just reported: a one-click automation that ended with
    an empty cast left 「完成」 as the last thing the user saw, indistinguishable
    from a run that produced everything. Opt-in by design - a runner that does not
    set ``completion_message`` keeps the old copy, and the word 「完成」 stays in the
    row as the fallback for every caller that still greps for it.

    The key is removed rather than read, so it never reaches ``result_json``.
    """
    if not isinstance(result, dict):
        return None
    entry = result.pop(COMPLETION_MESSAGE_KEY, None)
    if isinstance(entry, LocalizableMessage):
        return entry
    if not isinstance(entry, dict):
        return None
    code = str(entry.get("code") or "").strip()
    if not code:
        return None
    params = entry.get("params")
    return LocalizableMessage(
        code=code,
        text=str(entry.get("text") or ""),
        params=params if isinstance(params, dict) and params else None,
    )


def _metrics_user_id_for_project_context(ctx: Any) -> str:
    requester_user_id = str(getattr(ctx, "requester_user_id", "") or "").strip()
    if requester_user_id:
        return requester_user_id
    return str(getattr(ctx, "owner_id", "") or "").strip()


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _episode_ref(episode: int) -> str:
    return f"ep{episode:03d}" if episode > 0 else "project"


def _beat_ref(episode: int, beat_num: int, *, scope: Any = None) -> str:
    ref = f"{_episode_ref(episode)}:beat{beat_num:03d}"
    clean_scope = str(scope or "").strip()
    return f"{ref}:{clean_scope}" if clean_scope else ref


def _int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        values: list[Any] = [value]
    else:
        try:
            values = list(value)
        except TypeError:
            values = [value]
    out: list[int] = []
    for item in values:
        parsed = _positive_int(item)
        if parsed is not None and parsed not in out:
            out.append(parsed)
    return out


def _beat_numbers_from_result(result: Any) -> list[int]:
    if not isinstance(result, dict):
        return []
    for key in ("beat_numbers", "updated_beats", "generated_beats"):
        beats = _int_list(result.get(key))
        if beats:
            return beats
    beat_num = _positive_int(result.get("beat_num") or result.get("beat"))
    if beat_num:
        return [beat_num]
    items = result.get("items")
    if isinstance(items, list):
        beats: list[int] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("beat_num", "beat"):
                parsed = _positive_int(item.get(key))
                if parsed is not None and parsed not in beats:
                    beats.append(parsed)
        return beats
    return []


def _resource_refs_for_task_success(
    *,
    task_type: str,
    episode: int,
    beat_num: Any = None,
    scope: Any = None,
    result: Any = None,
) -> list[str]:
    kind = _resource_kind_for_task(task_type)
    if not kind:
        return []
    if kind == "ingest":
        return []
    if kind == "script":
        return [_episode_ref(episode)]

    explicit_beat = _positive_int(beat_num)
    if explicit_beat is not None:
        return [_beat_ref(episode, explicit_beat, scope=scope)]

    beats = _beat_numbers_from_result(result)
    if beats:
        return [_beat_ref(episode, beat, scope=scope) for beat in beats]

    clean_scope = str(scope or "").strip()
    if clean_scope:
        return [f"{_episode_ref(episode)}:{clean_scope}"]
    return [f"{_episode_ref(episode)}:{task_type}"]


def _clean_billing_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = str(key or "").strip()
        if not clean_key or item is None:
            continue
        if isinstance(item, str):
            clean_item = item.strip()
            if not clean_item:
                continue
            cleaned[clean_key] = clean_item
        else:
            cleaned[clean_key] = item
    return cleaned


def _build_trusted_egress_context(
    delivery: VerifiedTaskDelivery,
    ctx: Any,
    *,
    run_task_id: str,
) -> TrustedEgressContext:
    admission = delivery.admission
    if type(admission) is not AdmissionContext:
        raise InvalidTaskEnvelope() from None
    if (
        type(run_task_id) is not str
        or not run_task_id
        or admission.requester_user_id != delivery.requester_user_id
        or admission.root_task_id != run_task_id
        or getattr(ctx, "project_id", None) != delivery.project_id
        or getattr(ctx, "requester_user_id", None) != delivery.requester_user_id
    ):
        raise InvalidTaskEnvelope() from None
    try:
        return TrustedEgressContext(
            envelope_id=delivery.envelope_id,
            project_id=delivery.project_id,
            task_type=delivery.task_type,
            requester_user_id=delivery.requester_user_id,
            root_task_id=admission.root_task_id,
            admission_id=admission.admission_id,
            admitted_at=admission.admitted_at,
            membership_id=admission.membership_id,
            authz_version=admission.authz_version,
            billing_principal=admission.billing_principal,
            credential=admission.credential,
        )
    except (TypeError, ValueError):
        raise InvalidTaskEnvelope() from None


def _set_project_task_metrics_context(
    ctx: Any,
    task_type: str,
    billing_metadata: dict[str, Any] | None = None,
) -> None:
    billing_user_id = _metrics_user_id_for_project_context(ctx)
    context_metadata = {
        "billing_user_id": billing_user_id,
        "requester_user_id": str(getattr(ctx, "requester_user_id", "") or "").strip(),
        "project_owner_id": str(getattr(ctx, "owner_id", "") or "").strip(),
        "billing_task_type": task_type,
    }
    context_metadata.update(_clean_billing_metadata(billing_metadata))
    get_usage_meter().set_llm_usage_context(
        billing_user_id,
        project_id=str(getattr(ctx, "project_id", "") or ""),
        resource_kind=_resource_kind_for_task(task_type),
        billing_metadata={
            key: value for key, value in context_metadata.items() if value
        },
    )


def _clear_project_task_metrics_context() -> None:
    get_usage_meter().clear_llm_usage_context()


async def _emit_project_task_metrics(
    ctx: Any,
    task_type: str,
    *,
    episode: int,
    beat_num: Any = None,
    scope: Any = None,
    result: Any = None,
    outcome: str = "success",
) -> None:
    try:
        usage_meter = get_usage_meter()
        user_id = _metrics_user_id_for_project_context(ctx)
        project_id = str(getattr(ctx, "project_id", "") or "")
        kind = _resource_kind_for_task(task_type)
        clean_outcome = "failed" if outcome == "failed" else "success"

        if task_type == "ingest_fast":
            # [aipdd fork delta] 记账标签用实际解析出来的调用名：``COGNEE_LLM_MODEL`` env 已不再
            # 参与模型名选定，账单上也不能写一个设置页看不见的旧名字。
            try:
                from novelvideo.config import get_newapi_text_model_name
                from novelvideo.official_defaults import DEFAULT_COGNEE_LLM_MODEL

                model = get_newapi_text_model_name(
                    "COGNEE_LLM_MODEL", DEFAULT_COGNEE_LLM_MODEL
                )
            except Exception:  # noqa: BLE001 - 记账失败不能影响已完成的任务
                model = ""
            if clean_outcome == "success":
                await usage_meter.bump_content_counter(
                    user_id=user_id,
                    metric="ingests_completed",
                    value=1,
                    model=model,
                    project_id=project_id,
                    resource_kind="ingest",
                )
            await usage_meter.log_resource_attempts(
                user_id=user_id,
                project_id=project_id,
                kind="ingest",
                refs=[f"project:{project_id}"],
                outcome=clean_outcome,
                model=model,
            )
            return

        if clean_outcome == "success" and task_type == "script_writer":
            beats = _positive_int(
                (result or {}).get("beats") if isinstance(result, dict) else None
            )
            await usage_meter.bump_content_counter(
                user_id=user_id,
                metric="scripts_written",
                value=1,
                project_id=project_id,
            )
            if beats:
                await usage_meter.bump_content_counter(
                    user_id=user_id,
                    metric="beats_written",
                    value=beats,
                    project_id=project_id,
                )

        refs = _resource_refs_for_task_success(
            task_type=task_type,
            episode=episode,
            beat_num=beat_num,
            scope=scope,
            result=result,
        )
        if not refs or not kind:
            return
        model = ""
        if isinstance(result, dict):
            model = str(result.get("model") or "").strip()
        await usage_meter.log_resource_attempts(
            user_id=user_id,
            project_id=project_id,
            kind=kind,
            refs=refs,
            outcome=clean_outcome,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("project task metrics emit failed: %s", exc)


def _project_task_timeout_seconds() -> int:
    raw_value = os.environ.get("ST_PROJECT_TASK_TIMEOUT_S")
    if raw_value:
        try:
            return int(raw_value)
        except ValueError:
            logger.warning(
                "Invalid ST_PROJECT_TASK_TIMEOUT_S=%r; using default", raw_value
            )
    return 30 * 60


def _project_task_failure_for_exception(
    exc: BaseException,
) -> tuple[str, dict[str, Any], bool]:
    from novelvideo.freezone.audio_node import VoicePrerequisiteError
    from novelvideo.generators.render_identity_guard import RenderIdentityDetectionRequired
    from novelvideo.authz.identity_prerequisites import IdentityPlanningPrerequisiteError
    from novelvideo.novel_source import NovelImportRequiredError
    from novelvideo.scene_prerequisites import ScenePlanningPrerequisiteError
    from novelvideo.video_prompt_prerequisite import VideoPromptPrerequisiteError

    if isinstance(exc, VoicePrerequisiteError):
        return str(exc), {"error_code": exc.error_code}, True

    if isinstance(exc, RenderIdentityDetectionRequired):
        return str(exc), {"error_code": exc.error_code}, True

    if isinstance(exc, IdentityPlanningPrerequisiteError):
        return str(exc), {"error_code": exc.error_code}, True

    if isinstance(exc, ScenePlanningPrerequisiteError):
        return str(exc), {"error_code": exc.error_code}, True

    if isinstance(exc, NovelImportRequiredError):
        return str(exc), {"error_code": exc.error_code}, True

    if isinstance(exc, VideoPromptPrerequisiteError):
        return str(exc), {"error_code": exc.error_code}, True

    if isinstance(exc, TaskTimedOut):
        timeout_seconds = int(getattr(exc, "timeout_seconds", None) or 30 * 60)
        timeout_minutes = max(round(timeout_seconds / 60), 1)
        return (
            f"任务超过 {timeout_minutes} 分钟未完成，已自动放弃",
            {"error_code": "TASK_TIMEOUT", "timeout_seconds": timeout_seconds},
            True,
        )

    try:
        from celery.exceptions import SoftTimeLimitExceeded

        if isinstance(exc, SoftTimeLimitExceeded):
            timeout_seconds = _project_task_timeout_seconds()
            timeout_minutes = max(round(timeout_seconds / 60), 1)
            return (
                f"任务超过 {timeout_minutes} 分钟未完成，已自动放弃",
                {"error_code": "TASK_TIMEOUT", "timeout_seconds": timeout_seconds},
                True,
            )
    except Exception:
        pass

    if is_upstream_quota_error(exc):
        return (
            UPSTREAM_QUOTA_EXCEEDED_MESSAGE,
            upstream_quota_payload(exc),
            True,
        )

    billing_error = find_billing_error(exc)
    if billing_error is not None:
        logger.warning(
            "typed billing failure in project task: %s", billing_error, exc_info=exc
        )
        return (
            billing_error.user_message,
            billing_error_payload(billing_error),
            True,
        )

    try:
        from novelvideo.director_world.pano_sharp import Sharp3DUnavailable

        if isinstance(exc, Sharp3DUnavailable):
            return str(exc), {"error_code": exc.error_code}, True
    except Exception:
        pass

    try:
        from novelvideo.director_world.block_world_builder import BlockWorldUnavailable

        if isinstance(exc, BlockWorldUnavailable):
            return str(exc), {"error_code": exc.error_code}, True
    except Exception:
        pass

    try:
        from novelvideo.shared.provider_errors import (
            content_moderation_payload,
            is_content_moderation_error,
        )

        if is_content_moderation_error(exc):
            payload = content_moderation_payload(exc)
            return str(payload.get("message") or ""), payload, True
    except Exception:
        pass

    try:
        from novelvideo.shared.provider_errors import provider_video_error_payload

        provider_payload = provider_video_error_payload(exc)
        if provider_payload is not None:
            return (
                str(provider_payload.get("message") or ""),
                provider_payload,
                True,
            )
    except Exception:
        pass

    if not isinstance(exc, Exception):
        raise exc
    from novelvideo.utils.error_redaction import safe_exception_message

    return safe_exception_message(exc), {}, False


def _completion_metadata_with_provider_task_id(
    metadata: dict[str, Any],
    result: Any,
) -> dict[str, Any]:
    completion_metadata = dict(metadata)
    if isinstance(result, dict):
        provider_task_id = (
            result.get("provider_task_id")
            or result.get("huimeng_task_id")
            or result.get("newapi_task_id")
        )
        if provider_task_id:
            completion_metadata["provider_task_id"] = str(provider_task_id)
    return completion_metadata


def _ensure_builtin_runners_registered() -> None:
    from novelvideo.task_backend.runners import (  # noqa: F401
        audio,
        character_image,
        episode_assets,
        freezone,
        graph_build,
        identity,
        ingest,
        prop_reference,
        render,
        scene_reference,
        script,
        sketch,
        sketch_edit_execute,
        stage_asset,
        video,
    )


def run_project_task_core_sync(
    delivery: VerifiedTaskDelivery,
    ctx: Any,
    manager: Any,
    *,
    run_task_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if type(delivery) is not VerifiedTaskDelivery:
        raise InvalidTaskEnvelope() from None

    trusted_egress_context = _build_trusted_egress_context(
        delivery,
        ctx,
        run_task_id=run_task_id,
    )

    task_type = str(delivery.task_type)
    # Placement is checked once, here, before anything is started. Until now a
    # misrouted task only tripped over the guard inside the first progress
    # write, i.e. after it had already taken the dedup slot and begun working.
    # The registry is populated lazily, so make sure it is loaded before asking
    # it — an empty registry would read as "unregistered", not as "free".
    _ensure_builtin_runners_registered()
    requires_home_node = project_task_requires_home_node(task_type)
    if requires_home_node:
        require_project_home_node(ctx, operation="run project task")
    elif not ctx.is_home_node and task_type in PROJECTION_REQUIREMENTS:
        projection = read_projection(delivery.payload)
        if projection is None or projection.task_type != task_type:
            # Projection is optional for the inline/home-node rollback path,
            # but a foreign worker must never fall back to project-local state.
            require_project_home_node(ctx, operation="run unprojected project task")
    episode = int(delivery.episode or 0)
    beat_num = delivery.beat_num
    scope = delivery.scope
    envelope = TrustedRunnerEnvelope(
        {
            "project_id": delivery.project_id,
            "requester_user_id": delivery.requester_user_id,
            "task_type": task_type,
            "episode": episode,
            "beat_num": beat_num,
            "scope": scope,
            "queue_kind": delivery.queue_kind,
            "payload": delivery.payload,
            TRUSTED_EGRESS_CONTEXT_KEY: trusted_egress_context,
        }
    )
    run_metadata = dict(metadata or {})

    timeout_seconds = _project_task_timeout_seconds()
    deadline_monotonic = (
        time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    )

    _clear_project_task_metrics_context()

    if asyncio.run(
        is_cancel_requested(
            project_id=str(envelope["project_id"]),
            task_type=task_type,
            episode=episode,
            task_id=run_task_id,
            beat_num=beat_num,
            scope=scope,
        )
    ):
        manager.update_progress_for_project(
            ctx,
            task_type,
            episode,
            beat_num=beat_num,
            scope=scope,
            progress=0.0,
            current_task="任务已取消",
            metadata=run_metadata,
            status="cancelled",
            expected_task_id=run_task_id,
        )
        return {"cancelled": True}

    try:
        with (
            project_task_run_context(run_task_id),
            project_task_subprocess_context(
                project_id=str(envelope["project_id"]),
                task_type=task_type,
                episode=episode,
                task_id=run_task_id,
                beat_num=beat_num,
                scope=scope,
                deadline_monotonic=deadline_monotonic,
                timeout_seconds=timeout_seconds,
            ),
        ):
            _set_project_task_metrics_context(ctx, task_type)
            execution_started = manager.begin_task_execution_for_project(
                ctx,
                task_type,
                episode,
                beat_num=beat_num,
                scope=scope,
                expected_task_id=run_task_id,
                metadata=run_metadata,
            )
            if not execution_started:
                logger.info(
                    "Skip project task whose queued state was cancelled or replaced: "
                    "project=%s task_type=%s task_id=%s",
                    ctx.project_id,
                    task_type,
                    run_task_id,
                )
                return {"cancelled": True, "cancelled_before_execution": True}

            _ensure_builtin_runners_registered()
            runner = get_project_task_runner(task_type)
            if runner is None:
                error = f"No project task runner registered for task_type={task_type}"
                manager.fail_task_for_project(
                    ctx,
                    task_type,
                    episode,
                    beat_num=beat_num,
                    scope=scope,
                    error=error,
                    metadata=run_metadata,
                    expected_task_id=run_task_id,
                )
                raise RuntimeError(error)

            try:
                envelope["__run_task_id"] = run_task_id
                if deadline_monotonic is not None:
                    envelope["__deadline_monotonic"] = deadline_monotonic
                    envelope["__timeout_seconds"] = timeout_seconds
                # 唯一派发点，绑定本次投递的身份，使闸门总有一个请求作用域的
                # 身份可读——否则「平台任务，允许」与「调用点漏传参数」都是 None。
                # 5 个 runner 在自己函数体内另有绑定，嵌套安全（set/reset 成对）。
                with model_gateway_scope_for_runner(envelope):
                    result = runner(envelope, ctx)
            except BaseException as exc:
                if isinstance(exc, RunningTaskAuthorityIndeterminate):
                    logger.warning(
                        "running_task_authz_outcome_indeterminate",
                        extra={"failure_kind": exc.failure_kind},
                    )
                    failure_payload = {
                        "error_code": exc.code,
                        "failure_kind": exc.failure_kind,
                    }
                    logger.info(
                        "task_authz_revalidation_indeterminate",
                        extra={"failure_kind": exc.failure_kind},
                    )
                    manager.fail_task_for_project(
                        ctx,
                        task_type,
                        episode,
                        beat_num=beat_num,
                        scope=scope,
                        error=str(exc),
                        metadata={**run_metadata, **failure_payload},
                        expected_task_id=run_task_id,
                    )
                    asyncio.run(
                        _emit_project_task_metrics(
                            ctx,
                            task_type,
                            episode=episode,
                            beat_num=beat_num,
                            scope=scope,
                            outcome="failed",
                        )
                    )
                    return {"failed": True, **failure_payload}
                if isinstance(exc, TaskCancelled):
                    manager.update_progress_for_project(
                        ctx,
                        task_type,
                        episode,
                        beat_num=beat_num,
                        scope=scope,
                        progress=0.0,
                        current_task="任务已取消",
                        metadata=run_metadata,
                        status="cancelled",
                        expected_task_id=run_task_id,
                    )
                    return {"cancelled": True}
                error, failure_payload, handled = _project_task_failure_for_exception(
                    exc
                )
                manager.fail_task_for_project(
                    ctx,
                    task_type,
                    episode,
                    beat_num=beat_num,
                    scope=scope,
                    error=error,
                    metadata={**run_metadata, **failure_payload},
                    expected_task_id=run_task_id,
                )
                asyncio.run(
                    _emit_project_task_metrics(
                        ctx,
                        task_type,
                        episode=episode,
                        beat_num=beat_num,
                        scope=scope,
                        outcome="failed",
                    )
                )
                if handled:
                    return {"failed": True, **failure_payload}
                raise

            completion_error: BaseException | None = None
            try:
                completion_message = _take_completion_message(result)
                manager.complete_task_for_project(
                    ctx,
                    task_type,
                    episode,
                    beat_num=beat_num,
                    scope=scope,
                    result=result or {"ok": True},
                    # A runner that ends on a graded verdict gets to keep it on the
                    # completed row instead of being flattened to 「完成」.
                    current_task=completion_message or "完成",
                    logs=[completion_message or "完成"],
                    metadata=_completion_metadata_with_provider_task_id(
                        run_metadata, result
                    ),
                    expected_task_id=run_task_id,
                )
            except BaseException as exc:
                # Runner results are the delivery evidence.  The task-center
                # SQLite row is only an observability/read-model write and may
                # fail after an asset has already been durably saved.
                completion_error = exc

            if completion_error is not None:
                raise completion_error
            asyncio.run(
                _emit_project_task_metrics(
                    ctx,
                    task_type,
                    episode=episode,
                    beat_num=beat_num,
                    scope=scope,
                    result=result,
                )
            )
        return result or {"ok": True}
    finally:
        _clear_project_task_metrics_context()
