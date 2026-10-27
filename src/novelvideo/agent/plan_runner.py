# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 待办计划执行器：读计划 → 委托权威计划器逐段推进 → 回勾任务。

计划条目的 ``stage`` 是流水线里程碑（见 ``agent.plans.PLAN_STAGE_ORDER``）。执行器
**不复制任何阶段逻辑**：决策仍由 ``pipeline.planner.plan_next`` 给出，发射仍由
``pipeline.planner.run_actions`` 完成；这里只做三件事：

1. 把「还没标 stage 的旧条目」按标题保守推断一次（例如「出草图」→ sketches）；
2. 每推进一段就按**真实产物**判断条目是否完成（故事源、剧本状态、分集的
   identity_ids / scene_menu / prop_menu、beat 的 sketch_url / frame_url），
   完成即回写 ``done``——因此重复调用幂等，不会重复生成；
3. 提供预算内循环，让一次工具调用可以跨多个阶段推进（上限 50 步）。

计划里没有 stage 的条目按手工待办处理，不会被执行器碰；执行器只推进到镜头首帧，
绝不提交视频（与 ``run_pipeline`` 的交付边界一致）。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Awaitable, Callable

from novelvideo.agent import plans
from novelvideo.i18n_message import MessageLike, lmsg, message_payload, message_text
from novelvideo.pipeline import planner

Read = Callable[..., Awaitable[dict[str, Any]]]

_ACTIVE_STATUSES = {"submitting", "queued", "running"}
_MAX_STEPS = 50
_MIN_INTERVAL_SECONDS = 2.0


class PlanRunError(RuntimeError):
    """执行器遇到不可恢复的读失败 / 非法计划。"""


# --------------------------------------------------------------------------- #
# 读状态
# --------------------------------------------------------------------------- #
def _rows(resp: Any, *keys: str) -> list[dict[str, Any]]:
    if not isinstance(resp, dict):
        return []
    for key in ("data", *keys):
        value = resp.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    data = resp.get("data")
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _checked(resp: Any, what: str) -> dict[str, Any]:
    if isinstance(resp, dict) and resp.get("ok") is False:
        detail = resp.get("error") or resp.get("status_code") or "unknown error"
        raise PlanRunError(f"{what}失败：{detail}")
    return resp if isinstance(resp, dict) else {}


def _story_text(resp: dict[str, Any]) -> str:
    data = resp.get("data")
    if isinstance(data, dict):
        return str(data.get("text") or "")
    return ""


def _screenplay_status(resp: dict[str, Any]) -> str:
    data = resp.get("data")
    if isinstance(data, dict):
        return str(data.get("status") or "")
    return ""


async def _load_state(
    read: Read,
    project: str,
    beat_episodes: list[int] | None = None,
) -> dict[str, Any]:
    """一次性读取判断条目完成所需的项目状态。

    ``beat_episodes`` 为 ``None`` 时按项目实际集数补全（用于「全部集」草稿/首帧条目）。
    """
    source = _checked(
        await read("GET", f"/api/v1/projects/{project}/story/source"), "读取故事源"
    )
    screenplay = _checked(
        await read("GET", f"/api/v1/projects/{project}/story/generate-script/result"),
        "读取剧本状态",
    )
    tasks = _rows(
        _checked(await read("GET", f"/api/v1/projects/{project}/tasks"), "读取任务"),
        "tasks",
        "items",
    )
    characters = _rows(
        _checked(
            await read("GET", f"/api/v1/projects/{project}/characters"), "读取角色"
        ),
        "characters",
        "items",
    )
    episodes = _rows(
        _checked(
            await read("GET", f"/api/v1/projects/{project}/episodes"), "读取分集"
        ),
        "episodes",
        "items",
    )
    episodes_by_number = {
        int(row.get("number") or 0): row
        for row in episodes
        if int(row.get("number") or 0) > 0
    }
    if beat_episodes is None:
        beat_episodes = sorted(episodes_by_number)
    beats: dict[int, list[dict[str, Any]]] = {}
    for episode in sorted({int(number) for number in beat_episodes if int(number) > 0}):
        rows = _rows(
            _checked(
                await read("GET", f"/api/v1/projects/{project}/episodes/{episode}/beats"),
                f"读取第 {episode} 集镜头",
            ),
            "beats",
            "items",
        )
        beats[episode] = rows
    return {
        "story_text": _story_text(source),
        "screenplay_status": _screenplay_status(screenplay),
        "tasks": tasks,
        "characters": characters,
        "episodes": episodes,
        "episodes_by_number": episodes_by_number,
        "beats": beats,
    }


# --------------------------------------------------------------------------- #
# 完成判定
# --------------------------------------------------------------------------- #
def _episode_override(opts: dict[str, Any]) -> int | None:
    try:
        number = int(opts.get("episode") or 0)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _target_episodes(
    item: dict[str, Any], state: dict[str, Any], override: int | None = None
) -> list[int]:
    wanted = override or _episode_number(item)
    available = sorted(state["episodes_by_number"])
    if wanted:
        return [wanted] if wanted in state["episodes_by_number"] else []
    return available


def _episode_number(item: dict[str, Any]) -> int | None:
    try:
        number = int(item.get("episode") or 0)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _episode_satisfied(
    stage: str, episode: int, state: dict[str, Any]
) -> bool:
    row = state["episodes_by_number"].get(episode) or {}
    if stage == "identities":
        return bool(row.get("identity_ids"))
    if stage == "script":
        return int(row.get("beat_count") or 0) > 0
    if stage == "scenes":
        # 规划跑完但本集确实没有场景（菜单为空）也算完成：否则流水线越过
        # scenes 后会被「产物仍为空」的守卫判失败，且永远无法回勾。
        return bool(row.get("scene_menu")) or _task_completed(
            state, "episode_scene_planner", episode
        )
    if stage == "props":
        return bool(row.get("prop_menu")) or _task_completed(
            state, "episode_prop_planner", episode
        )
    if stage in ("sketches", "frames"):
        beats = state["beats"].get(episode)
        if not beats:
            return False
        key = "sketch_url" if stage == "sketches" else "frame_url"
        return all(str(beat.get(key) or "") for beat in beats)
    return False


def _task_completed(state: dict[str, Any], task_type: str, episode: int = 0) -> bool:
    return any(
        str(row.get("task_type") or "") == task_type
        and int(row.get("episode") or 0) == episode
        and str(row.get("status") or "") == "completed"
        for row in state["tasks"]
    )


def _item_satisfied(
    item: dict[str, Any], state: dict[str, Any], override: int | None = None
) -> bool:
    stage = str(item.get("stage") or "")
    if stage not in plans.PLAN_STAGE_ORDER:
        return False
    if stage == "story":
        return bool(state["story_text"].strip())
    if stage == "screenplay":
        return state["screenplay_status"] == "completed"
    if stage == "ingest":
        # ingest 没有独立产物可读：任务完成、角色或分集任一落地都说明摄入已完成。
        # 只认 characters/episodes 会在「摄入刚完成、角色还没建出来」的窗口里误判失败。
        return (
            _task_completed(state, "ingest_fast")
            or bool(state["characters"])
            or bool(state["episodes"])
        )
    if stage == "characters":
        return bool(state["characters"])
    if stage == "episodes":
        return bool(state["episodes"])
    targets = _target_episodes(item, state, override)
    if not targets:
        return False
    return all(_episode_satisfied(stage, number, state) for number in targets)


def _first_open_episode(
    item: dict[str, Any], state: dict[str, Any], override: int | None = None
) -> int | None:
    for number in _target_episodes(item, state, override):
        if not _episode_satisfied(str(item["stage"]), number, state):
            return number
    return None


def _ordered_pending(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按先决顺序排队（项目级在前，同阶段保持用户写的顺序）。"""
    return sorted(
        items,
        key=lambda item: (
            plans.PLAN_STAGE_ORDER.get(str(item.get("stage") or ""), 99),
            _episode_number(item) or 0,
        ),
    )


def _beat_episodes_for(
    pending: list[dict[str, Any]], override: int | None = None
) -> list[int] | None:
    """需要读 beats 的集号；有「全部集」草稿/首帧条目时返回 None。"""
    wanted: set[int] = set()
    for item in pending:
        if item.get("stage") not in ("sketches", "frames"):
            continue
        episode = override or _episode_number(item)
        if not episode:
            return None
        wanted.add(episode)
    return sorted(wanted)


def _planner_stage_rank(stage: str) -> int | None:
    """把计划器的 stage 名映射到 PLAN_STAGE_ORDER，用于识别「流水线已越过目标阶段」。"""
    text = str(stage or "")
    if not text:
        return None
    if text == "done":
        return 99
    if re.search(r"episode_\d+_identity", text) or "identity_planner" in text:
        return 5
    if (
        re.search(r"episode_\d+_script", text)
        or "script_writer" in text
        or "_beats_missing" in text
    ):
        return 6
    if re.search(r"episode_\d+_scenes", text) or "scene_planner" in text:
        return 7
    if re.search(r"episode_\d+_props", text) or "prop_planner" in text:
        return 8
    if (
        re.search(r"episode_\d+_sketches", text)
        or "sketch_generation" in text
        or "sketch_grid_generation" in text
    ):
        return 9
    if re.search(r"episode_\d+_frames", text) or "selected_regen" in text:
        return 10
    if "screenplay" in text:
        return 1
    if "ingest" in text:
        return 2
    if "characters" in text:
        return 3
    if "episodes_planning" in text or "build_episodes" in text:
        return 4
    if "story" in text:
        return 0
    return None


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #
async def _wait_until_idle(read: Read, project: str, deadline: float) -> bool:
    """轮询项目任务直到无活跃任务返回 True；超过 deadline 返回 False。"""
    while True:
        rows = _rows(
            await read("GET", f"/api/v1/projects/{project}/tasks"), "tasks", "items"
        )
        active = any(
            str(row.get("status") or "") in _ACTIVE_STATUSES for row in rows
        )
        if not active:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(_MIN_INTERVAL_SECONDS, remaining))


def _mark_done(project_dir: Any, item_ids: list[str]) -> list[dict[str, Any]]:
    return plans.mark_items(project_dir, item_ids, done=True)


def _mark_failed(project_dir: Any, item_ids: list[str], error: str) -> list[dict[str, Any]]:
    return plans.mark_items(project_dir, item_ids, error=error)


def _pending(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if not item.get("done")]


def _executable(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in _pending(items)
        if str(item.get("stage") or "") in plans.PLAN_STAGE_ORDER
    ]


def _infer_missing_stages(project_dir: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给没标 stage 的待办按标题推断阶段（只推一次，结果落盘，UI 可见）。"""
    inferred: list[dict[str, Any]] = []
    for item in items:
        if item.get("done") or item.get("stage"):
            continue
        stage = plans.infer_stage(item.get("title"))
        if not stage:
            continue
        item["stage"] = stage
        item["stage_inferred"] = True
        if not item.get("episode"):
            item["episode"] = plans.infer_episode(item.get("title"))
        inferred.append(dict(item))
    if inferred:
        plans.save_plan(project_dir, items)
    return inferred


def _summary(
    items: list[dict[str, Any]],
    *,
    status: str,
    advanced: list[str],
    completed: list[str],
    inferred: list[dict[str, Any]],
    message: MessageLike,
    next_action: str,
    error: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pending = _pending(items)
    out: dict[str, Any] = {
        "ok": status not in ("failed",),
        "status": status,
        "advanced": advanced,
        "completed": completed,
        "items": items,
        "remaining": [item.get("id") for item in pending],
        "manual_pending": [
            {"id": item.get("id"), "title": item.get("title")}
            for item in pending
            if str(item.get("stage") or "") not in plans.PLAN_STAGE_ORDER
        ],
        "inferred": [
            {"id": item.get("id"), "title": item.get("title"), "stage": item.get("stage")}
            for item in inferred
        ],
        "message": message,
        "next_action": next_action,
    }
    # 走 i18n 约定：中文兜底留在 message，词条号与插值另走 message_code /
    # message_params（响应是 JSON，LocalizableMessage 不能原样出境）。
    # 上面保留 "message": message 字面量，棘轮的「包装形参直连流」才认得出
    # message 是上屏槽位。
    payload = message_payload(message)
    out["message"] = message_text(message)
    if payload:
        out["message_code"] = payload["code"]
        if payload.get("params"):
            out["message_params"] = payload["params"]
    if error:
        out["error"] = error
    if extra:
        out.update(extra)
    return out


async def run_plan(
    read: Read,
    project: str,
    project_dir: Any,
    opts: dict[str, Any] | None = None,
    *,
    mode: str = "auto",
    approve: bool = False,
    budget_s: float = 90.0,
    max_steps: int = _MAX_STEPS,
) -> dict[str, Any]:
    """按计划推进流水线。

    - ``checkpoint`` 且未 ``approve``：只预览下一步（零副作用）；
    - ``manual`` / ``checkpoint+approve``：只发射一段；
    - ``auto``：预算内循环推进，直到可执行条目全部完成 / 预算用尽 / 出错。

    返回的 ``items`` 是执行后的计划清单；``completed`` 是本次回勾的条目 id。
    """
    opts = dict(opts or {})
    opts.pop("execute", None)
    mode = str(mode or "auto").strip().lower()
    try:
        budget = max(5.0, min(600.0, float(budget_s or 90.0)))
    except (TypeError, ValueError):
        budget = 90.0
    try:
        limit = max(1, min(_MAX_STEPS, int(max_steps or _MAX_STEPS)))
    except (TypeError, ValueError):
        limit = _MAX_STEPS

    lock = _lock_for(project_dir)
    if lock.locked():
        items = plans.load_plan(project_dir)
        return _summary(
            items,
            status="busy",
            advanced=[],
            completed=[],
            inferred=[],
            message=lmsg(
                "tasks.progress.plan.busy", "计划正在由另一个请求执行，稍后再试"
            ),
            next_action="等待当前执行结束后再次调用 aipdd_drama_run_plan",
        )
    async with lock:
        try:
            return await _run_locked(
                read,
                project,
                project_dir,
                opts,
                mode=mode,
                approve=approve,
                budget=budget,
                limit=limit,
            )
        except (PlanRunError, ValueError) as exc:
            # planner 对非法参数（如 aspect_ratio）抛 ValueError：与运行时错误一样
            # 收敛成 failed 摘要，不让接口冒 500。
            items = plans.load_plan(project_dir)
            return _summary(
                items,
                status="failed",
                advanced=[],
                completed=[],
                inferred=[],
                message=str(exc),
                next_action="检查项目状态后用 aipdd_drama_run_plan 重试",
                error=str(exc),
            )


async def _run_locked(
    read: Read,
    project: str,
    project_dir: Any,
    opts: dict[str, Any],
    *,
    mode: str,
    approve: bool,
    budget: float,
    limit: int,
) -> dict[str, Any]:
    items = plans.load_plan(project_dir)
    inferred = _infer_missing_stages(project_dir, items)
    items = plans.load_plan(project_dir)

    executable = _executable(items)
    if not executable:
        if not _pending(items):
            return _summary(
                items,
                status="done",
                advanced=[],
                completed=[],
                inferred=inferred,
                message=lmsg("tasks.progress.plan.done", "计划已全部完成"),
                next_action="可以继续给用户安排新的制作任务",
            )
        return _summary(
            items,
            status="no_executable_items",
            advanced=[],
            completed=[],
            inferred=inferred,
            message=lmsg(
                "tasks.progress.plan.noAutoItems",
                "计划里没有可自动执行的条目（剩余条目缺少 stage，属于手工待办）",
            ),
            next_action=(
                "用 aipdd_drama_update_plan 给制作类条目补 stage（如 sketches/frames）后重试"
            ),
        )

    deadline = time.monotonic() + budget
    advanced: list[str] = []
    completed: list[str] = []
    last_fired_stage = ""
    repeat_fired = 0
    steps = 0
    override = _episode_override(opts)

    while True:
        if steps >= limit:
            break
        if time.monotonic() >= deadline:
            break

        pending_exec = _executable(plans.load_plan(project_dir))
        if not pending_exec:
            break

        state = await _load_state(
            read, project, _beat_episodes_for(pending_exec, override)
        )

        missing_episode = next(
            (
                item
                for item in pending_exec
                if str(item.get("stage") or "") not in plans.PROJECT_STAGES
                and (override or _episode_number(item))
                and (override or _episode_number(item))
                not in state["episodes_by_number"]
            ),
            None,
        )
        if missing_episode is not None:
            error = (
                f"第 {override or _episode_number(missing_episode)} 集不存在，"
                f"无法执行「{missing_episode['title']}」"
            )
            items = _mark_failed(project_dir, [str(missing_episode["id"])], error)
            return _summary(
                items,
                status="failed",
                advanced=advanced,
                completed=completed,
                inferred=inferred,
                message=error,
                next_action="用 aipdd_drama_get_plan 核对集号，或改成不指定 episode",
                error=error,
            )

        # 先按真实产物回勾（含上次运行已完成、但没来得及回写的条目）。
        newly_done = [
            str(item["id"])
            for item in pending_exec
            if _item_satisfied(item, state, override)
        ]
        if newly_done:
            items = _mark_done(project_dir, newly_done)
            completed.extend(newly_done)
            continue  # 重新读状态，避免刚勾掉的条目继续参与选目标

        ordered = _ordered_pending(pending_exec)
        target = ordered[0]
        target_stage = str(target.get("stage") or "")
        call_opts = dict(opts)
        if target_stage in plans.PROJECT_STAGES:
            call_opts.pop("episode", None)
        else:
            number = _first_open_episode(target, state, override)
            if number is None:
                # 理论上不会到这里（全部完成会先被回勾）；保险起见勾掉再继续。
                plans.mark_items(project_dir, [str(target["id"])], done=True)
                continue
            call_opts["episode"] = number
        # 场景/道具规划是计划器里的可选前置，计划点名了才打开（不擅自扩大范围）。
        if target_stage in ("scenes", "props"):
            call_opts["plan_episode_assets"] = True

        step = await planner.plan_next(read, project, call_opts)
        stage = str(step.get("stage") or "")
        status = str(step.get("status") or "")
        if stage and (not advanced or stage != advanced[-1]):
            advanced.append(stage)

        target_rank = plans.PLAN_STAGE_ORDER.get(target_stage)
        planner_rank = _planner_stage_rank(stage)
        if (
            status != "running"
            and target_rank is not None
            and planner_rank is not None
            and planner_rank > target_rank
        ):
            error = (
                f"流水线已越过「{plans.PLAN_STAGE_LABELS.get(target_stage, target_stage)}」"
                f"（当前阶段 {stage}），但该条目所需产物仍为空；"
                "请检查项目状态，补齐后重试或调整计划"
            )
            items = _mark_failed(project_dir, [str(target["id"])], error)
            return _summary(
                items,
                status="failed",
                advanced=advanced,
                completed=completed,
                inferred=inferred,
                message=lmsg(
                    "tasks.progress.plan.runFailed",
                    f"执行「{target['title']}」失败：{error}",
                    title=target["title"],
                    error=error,
                ),
                next_action="检查该阶段的产物后，用 aipdd_drama_run_plan 重试",
                error=error,
            )

        if status == "failed":
            error = str(step.get("error") or f"阶段 {stage} 失败")
            items = _mark_failed(project_dir, [str(target["id"])], error)
            return _summary(
                items,
                status="failed",
                advanced=advanced,
                completed=completed,
                inferred=inferred,
                message=lmsg(
                    "tasks.progress.plan.advanceFailed",
                    f"执行到「{target['title']}」失败：{error}",
                    title=target["title"],
                    error=error,
                ),
                next_action=str(step.get("next_hint") or "修正后再次调用 aipdd_drama_run_plan"),
                error=error,
            )
        if status == "requires_input":
            return _summary(
                plan_items(project_dir),
                status="requires_input",
                advanced=advanced,
                completed=completed,
                inferred=inferred,
                message=(
                    step.get("message")
                    or lmsg("tasks.progress.plan.missingStory", "流水线需要补充故事正文")
                ),
                next_action=str(step.get("next_hint") or "携带 story_text 再次调用"),
            )

        if stage == "done":
            break

        # checkpoint 预览：拿到下一段动作就返回，不发射（任务在跑时也如实回报）。
        if mode == "checkpoint" and not approve:
            if status == "running":
                message = f"当前任务仍在执行 {stage}，本次不发射新动作"
                next_action = "稍后再次调用 aipdd_drama_run_plan 继续"
            else:
                message = f"下一步将执行 {stage}（{target['title']}）"
                next_action = "确认后带 approve=true 再次调用 aipdd_drama_run_plan"
            return _summary(
                plan_items(project_dir),
                status="checkpoint",
                advanced=advanced,
                completed=completed,
                inferred=inferred,
                message=message,
                next_action=next_action,
                extra={
                    "next_stage": stage,
                    "planned_requests": step.get("actions") or [],
                    "step": step,
                },
            )

        if status == "running":
            repeat_fired = 0
            idle = await _wait_until_idle(read, project, deadline)
            if not idle and time.monotonic() >= deadline:
                return _summary(
                    plan_items(project_dir),
                    status="running",
                    advanced=advanced,
                    completed=completed,
                    inferred=inferred,
                    message=lmsg(
                        "tasks.progress.plan.runningBudgetExhausted",
                        f"仍在执行 {stage}，预算已用尽",
                        stage=stage,
                    ),
                    next_action="稍后再次调用 aipdd_drama_run_plan 继续（已完成的条目不会重跑）",
                )
            steps += 1
            continue

        actions = step.get("actions") or []
        if not actions:
            # 没有动作又不是 running/failed/idle：视为停住，避免空转。
            error = f"阶段 {stage} 没有可执行动作"
            items = _mark_failed(project_dir, [str(target["id"])], error)
            return _summary(
                items,
                status="failed",
                advanced=advanced,
                completed=completed,
                inferred=inferred,
                message=error,
                next_action=str(step.get("next_hint") or "稍后重试"),
                error=error,
            )

        fired = await planner.run_actions(read, actions)
        failure = next((row for row in fired if row.get("ok") is False and not row.get("deferred")), None)
        if failure is not None:
            error = str(
                failure.get("error")
                or f"{failure.get('method', '')} {failure.get('path', '')} 发射失败"
            )
            items = _mark_failed(project_dir, [str(target["id"])], error)
            return _summary(
                items,
                status="failed",
                advanced=advanced,
                completed=completed,
                inferred=inferred,
                message=lmsg(
                    "tasks.progress.plan.advanceFailed",
                    f"执行到「{target['title']}」失败：{error}",
                    title=target["title"],
                    error=error,
                ),
                next_action=str(step.get("next_hint") or "修正后再次调用 aipdd_drama_run_plan"),
                error=error,
            )
        if fired and all(row.get("deferred") for row in fired):
            return _summary(
                plan_items(project_dir),
                status="deferred",
                advanced=advanced,
                completed=completed,
                inferred=inferred,
                message=lmsg(
                    "tasks.progress.plan.needsClientImage",
                    f"阶段 {stage} 需要客户端生图能力，服务端无法代跑",
                    stage=stage,
                ),
                next_action="改由前端 driver 执行该步，或关闭 include_story_version",
            )

        steps += 1
        if stage == last_fired_stage:
            repeat_fired += 1
            if repeat_fired >= 3:
                error = f"阶段 {stage} 连续 3 次没有推进"
                items = _mark_failed(project_dir, [str(target["id"])], error)
                return _summary(
                    items,
                    status="failed",
                    advanced=advanced,
                    completed=completed,
                    inferred=inferred,
                    message=error,
                    next_action="检查任务错误后用 aipdd_drama_get_plan 查看失败条目",
                    error=error,
                )
        else:
            last_fired_stage = stage
            repeat_fired = 0

        # 手动模式只推一段；checkpoint+approve 同理。
        if mode == "manual" or mode == "checkpoint":
            return _summary(
                plan_items(project_dir),
                status="planned",
                advanced=advanced,
                completed=completed,
                inferred=inferred,
                message=lmsg(
                    "tasks.progress.plan.triggered",
                    f"已触发 {stage}（{target['title']}）",
                    stage=stage,
                    title=target["title"],
                ),
                next_action="稍后再次调用 aipdd_drama_run_plan 推进下一段",
            )
        idle = await _wait_until_idle(read, project, deadline)
        if not idle and time.monotonic() >= deadline:
            return _summary(
                plan_items(project_dir),
                status="running",
                advanced=advanced,
                completed=completed,
                inferred=inferred,
                message=lmsg(
                    "tasks.progress.plan.triggeredBudgetExhausted",
                    f"已触发 {stage}，任务仍在跑，预算已用尽",
                    stage=stage,
                ),
                next_action="稍后再次调用 aipdd_drama_run_plan 继续（已完成的条目不会重跑）",
            )

    # 循环结束：重新读一次状态，把本轮满足的条目回勾。
    final_items = plans.load_plan(project_dir)
    pending_exec = _executable(final_items)
    if pending_exec:
        state = await _load_state(
            read, project, _beat_episodes_for(pending_exec, override)
        )
        newly_done = [
            str(item["id"])
            for item in pending_exec
            if _item_satisfied(item, state, override)
        ]
        if newly_done:
            final_items = _mark_done(project_dir, newly_done)
            completed.extend(newly_done)

    remaining = _executable(final_items)
    manual = [
        item
        for item in _pending(final_items)
        if str(item.get("stage") or "") not in plans.PLAN_STAGE_ORDER
    ]
    if not remaining:
        message = "计划中的可执行条目已全部完成（推进到镜头首帧，未生成视频）"
        if manual:
            message += f"；另有 {len(manual)} 项手工待办仍需处理"
        return _summary(
            final_items,
            status="done",
            advanced=advanced,
            completed=completed,
            inferred=inferred,
            message=message,
            next_action=(
                "手工待办完成后用 aipdd_drama_update_plan(complete) 勾掉"
                if manual
                else "可按需用 aipdd_drama_video_range 生成镜头视频"
            ),
        )
    return _summary(
        final_items,
        status="running",
        advanced=advanced,
        completed=completed,
        inferred=inferred,
        message=lmsg(
            "tasks.progress.plan.progressed",
            f"预算内推进了 {steps} 段，仍有 {len(remaining)} 个可执行条目未完成",
            steps=steps,
            remaining=len(remaining),
        ),
        next_action="稍后再次调用 aipdd_drama_run_plan 继续（已完成的条目不会重跑）",
        extra={"steps": steps},
    )


def plan_items(project_dir: Any) -> list[dict[str, Any]]:
    return plans.load_plan(project_dir)


_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(project_dir: Any) -> asyncio.Lock:
    key = str(project_dir)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock
