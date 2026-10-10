# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 权威流水线计划器（单一逻辑源）。

`plan_next(read, project, opts)` 只读取项目进度、决定「下一步该做什么」，
返回一个 ``PlanStep``（含有序的 ``actions``），**绝不发射任何写请求**。真正的
执行交给薄消费方：MCP 经真实 HTTP、REST 路由经 ASGI 自调用、前端经 api client
+ ``awaitTaskCompletion``。阶段序列沿用既有步进器语义：

    story → 整本剧本(screenplay) → 项目级(sync/ingest/角色/分集)
          → 逐集(身份规划 → 分镜脚本 → 场景/道具规划 → 故事版 → 草图配色+生成 → 首帧)

全自动**只推进到镜头首帧，绝不提交视频**（视频由 aipdd_drama_video_range 单独触发）。

两个可开关的前置（由请求方按入口语义选择，本模块不猜）：

- ``plan_episode_assets``：把分镜脚本页「资产规划」三件里的**场景规划 / 道具规划**也
  自动补齐（身份规划一直都在链路里）。纯 LLM 文本规划，不出资产图；这两步紧跟在
  分镜脚本之后（规划器要吃剧本场景头，下游草图才是消费方）。
- ``include_story_version``：把**故事版**当成硬前置——剧本已出但该集还没有故事版时，
  单独返回一个 ``story_version_pending`` 阶段先补一张（类型化步，服务端 deferred、
  由拿得到 freezone 生图能力的客户端执行）。
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

Read = Callable[..., Awaitable[dict[str, Any]]]

_ACTIVE_STATUSES = {"submitting", "queued", "running"}
_EPISODE_TASK_TYPES = (
    "identity_planner",
    "episode_scene_planner",
    "episode_prop_planner",
    "script_writer",
    "sketch_generation",
    "selected_regen",
)
_MAX_SCREENPLAY_EPISODES = 60

ASPECT_RATIOS = ("2:3", "16:9")


# --------------------------------------------------------------------------- #
# 返回构造
# --------------------------------------------------------------------------- #
def _action(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    await_task: bool = False,
) -> dict[str, Any]:
    return {
        "method": method.upper(),
        "path": path,
        "query": query,
        "body": body,
        "await_task": await_task,
    }


def _step(
    stage: str,
    status: str,
    *,
    actions: list[dict[str, Any]] | None = None,
    message: str = "",
    next_hint: str = "",
    episode: int | None = None,
    task: dict[str, Any] | None = None,
    error: str = "",
    progress_counts: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        "stage": stage,
        "status": status,
        "actions": actions or [],
        "message": message,
        "next_hint": next_hint,
    }
    if episode is not None:
        out["episode"] = episode
    if task is not None:
        out["task"] = task
    if error:
        out["error"] = error
    if progress_counts is not None:
        out["progress_counts"] = progress_counts
    out.update(extra)
    return out


# --------------------------------------------------------------------------- #
# 状态读取 / 归约（全部经 read，仅 GET）
# --------------------------------------------------------------------------- #
def _rows(resp: Any, *keys: str) -> list[Any]:
    if not isinstance(resp, dict):
        return []
    for key in ("data", *keys):
        value = resp.get(key)
        if isinstance(value, list):
            return value
    data = resp.get("data")
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _task_phase(rows: list[dict[str, Any]]) -> str:
    statuses = [str(row.get("status") or "") for row in rows]
    if "completed" in statuses:
        return "completed"
    if any(status in _ACTIVE_STATUSES for status in statuses):
        return "running"
    if "failed" in statuses:
        return "failed"
    if "cancelled" in statuses:
        return "cancelled"
    return "none"


def _episode_count(opts: dict[str, Any]) -> int:
    raw = opts.get("episode_count")
    if raw is not None and str(raw).strip():
        count = int(float(raw))
    else:
        total = float(opts.get("total_seconds") or 0)
        per_episode = float(opts.get("seconds_per_episode") or 0)
        count = int(round(total / per_episode)) if total > 0 and per_episode > 0 else 10
    return max(1, min(_MAX_SCREENPLAY_EPISODES, count))


def _target_beats(opts: dict[str, Any]) -> int | None:
    if opts.get("target_beats") is not None and str(opts.get("target_beats")).strip():
        return max(8, int(float(opts["target_beats"])))
    per_episode = float(opts.get("seconds_per_episode") or 0)
    if per_episode <= 0:
        return None
    return max(8, int(round(per_episode / 3.0)))


async def _task_rows(read: Read, project: str) -> list[dict[str, Any]]:
    resp = await read("GET", f"/api/v1/projects/{project}/tasks")
    return [row for row in _rows(resp, "tasks", "items") if isinstance(row, dict)]


async def _beat_rows(read: Read, project: str, episode: int) -> list[dict[str, Any]]:
    resp = await read("GET", f"/api/v1/projects/{project}/episodes/{episode}/beats")
    return [row for row in _rows(resp, "beats", "items") if isinstance(row, dict)]


def _story_version_exists(items: list[Any], episode: int) -> bool:
    """资产库里该集是否已有「故事版」条目。

    命名约定与 ``story-version-panel.tsx`` / 前端 driver 入库处一致：``EP{n} {类目名}``。
    ``(?![0-9])`` 防止 ``EP1`` 误命中 ``EP10 故事版``。
    """
    pattern = re.compile(rf"^EP{episode}(?![0-9])")
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("category") or "") != "storyVersion":
            continue
        if pattern.search(str(item.get("name") or "")):
            return True
    return False


async def _has_story_version(read: Read, project: str, episode: int) -> bool:
    resp = await read("GET", f"/api/v1/projects/{project}/freezone/video/character-library")
    return _story_version_exists(_rows(resp, "items"), episode)


# --------------------------------------------------------------------------- #
# 项目级：剧本已出、分集尚空 → sync/ingest → 角色 → 分集
# --------------------------------------------------------------------------- #
async def _plan_project_stage(read: Read, project: str, opts: dict[str, Any]) -> dict[str, Any]:
    rows = await _task_rows(read, project)

    def _ep0(task_type: str) -> list[dict[str, Any]]:
        return [
            row
            for row in rows
            if str(row.get("task_type") or "") == task_type
            and int(row.get("episode") or 0) == 0
        ]

    def _running(task_type: str) -> dict[str, Any]:
        row = next(
            (r for r in _ep0(task_type) if str(r.get("status") or "") in _ACTIVE_STATUSES),
            {},
        )
        return _step(
            f"{task_type}_running",
            "running",
            message="任务仍在运行，稍后再次推进",
            next_hint="稍后再次调用计划器推进",
            task={
                "status": row.get("status"),
                "progress": row.get("progress"),
                "current_task": row.get("current_task"),
            },
        )

    ingest_phase = _task_phase(_ep0("ingest_fast"))
    if ingest_phase == "running":
        return _running("ingest_fast")
    if ingest_phase in ("none", "cancelled"):
        return _step(
            "ingest_started",
            "planned",
            actions=[
                _action("POST", f"/api/v1/projects/{project}/story/generate-script/sync"),
                _action(
                    "POST",
                    f"/api/v1/projects/{project}/ingest/start",
                    body={"filename": "novel.txt", "rebuild": False},
                    await_task=True,
                ),
            ],
            message="同步剧本产物并入队摄入（ingest_fast）",
            next_hint="摄入完成后再次调用计划器推进",
        )
    if ingest_phase == "failed":
        return _step(
            "ingest_restarted",
            "planned",
            actions=[
                _action(
                    "POST",
                    f"/api/v1/projects/{project}/ingest/start",
                    body={"filename": "novel.txt", "rebuild": True},
                    await_task=True,
                ),
            ],
            message="上次摄入失败，已以 rebuild=True 重试",
            next_hint="稍后再次调用计划器推进",
        )

    # 「一键自动化」（script_auto）是一条覆盖角色→分集→场景/道具→图谱的整链任务，
    # 写的是与下面分步动作同一批产物。它在跑时计划器必须让路，否则会自动排出第二个
    # build_characters，两个 writer 同时改同一份 cast 状态。
    if _task_phase(_ep0("script_auto")) == "running":
        return _running("script_auto")

    characters = _rows(
        await read("GET", f"/api/v1/projects/{project}/characters"), "characters", "items"
    )
    char_phase = _task_phase(_ep0("build_characters"))
    if char_phase == "running":
        return _running("build_characters")
    if not characters:
        if char_phase == "failed":
            return _step(
                "build_characters_failed",
                "failed",
                error="角色提取任务失败",
                next_hint="再次调用计划器会自动重试角色提取",
            )
        return _step(
            "characters_building",
            "planned",
            actions=[_action("POST", f"/api/v1/projects/{project}/characters/build", await_task=True)],
            message="角色提取（build_characters）已入队",
            next_hint="稍后再次调用计划器推进",
        )

    ep_phase = _task_phase(_ep0("build_episodes"))
    if ep_phase == "running":
        return _running("build_episodes")
    if ep_phase == "failed":
        return _step(
            "build_episodes_failed",
            "failed",
            error="分集规划任务失败",
            next_hint="再次调用计划器会重新发起分集规划",
        )
    episode_count = _episode_count(opts)
    return _step(
        "episodes_planning",
        "planned",
        actions=[
            _action(
                "POST",
                f"/api/v1/projects/{project}/episodes/plan",
                body={"target_episodes": episode_count},
                await_task=True,
            )
        ],
        message=f"分集规划已入队（目标 {episode_count} 集）",
        next_hint="稍后再次调用计划器推进",
    )


# --------------------------------------------------------------------------- #
# 单集：身份规划 → 分镜脚本 → 场景/道具规划 → 故事版 → 草图 → 首帧
# --------------------------------------------------------------------------- #
async def _plan_episode(
    read: Read,
    project: str,
    ep: int,
    ep_rows: list[dict[str, Any]],
    opts: dict[str, Any],
    ep_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    aspect = str(opts.get("aspect_ratio") or "2:3")

    def _phase(task_type: str) -> str:
        return _task_phase([r for r in ep_rows if str(r.get("task_type") or "") == task_type])

    active = next(
        (
            r
            for r in ep_rows
            if str(r.get("task_type") or "") in _EPISODE_TASK_TYPES
            and str(r.get("status") or "") in _ACTIVE_STATUSES
        ),
        None,
    )
    if active is not None:
        return _step(
            f"episode_{ep}_{active.get('task_type')}_running",
            "running",
            episode=ep,
            message="本集任务仍在运行",
            next_hint="稍后再次调用计划器推进",
            task={
                "task_type": active.get("task_type"),
                "status": active.get("status"),
                "progress": active.get("progress"),
                "current_task": active.get("current_task"),
            },
        )

    beats = await _beat_rows(read, project, ep)
    if not beats:
        if _phase("identity_planner") != "completed":
            return _step(
                f"episode_{ep}_identities_planning",
                "planned",
                episode=ep,
                actions=[
                    _action(
                        "POST",
                        f"/api/v1/projects/{project}/episodes/{ep}/identities/plan-async",
                        await_task=True,
                    )
                ],
                message=f"第 {ep} 集身份规划已入队",
                next_hint="稍后再次调用计划器推进",
            )
        script_phase = _phase("script_writer")
        if script_phase == "failed":
            error = next(
                (
                    str(r.get("error") or "")
                    for r in ep_rows
                    if str(r.get("task_type") or "") == "script_writer"
                    and str(r.get("status") or "") == "failed"
                ),
                "",
            )
            return _step(
                f"episode_{ep}_script_failed",
                "failed",
                episode=ep,
                error=error or "剧本拆解任务失败",
                next_hint="再次调用计划器会重新发起分镜脚本生成",
            )
        if script_phase == "completed":
            return _step(
                f"episode_{ep}_beats_missing",
                "failed",
                episode=ep,
                error=f"第 {ep} 集剧本已完成但镜头列表为空，请检查剧本内容或重跑分镜",
                next_hint="确认后再次调用计划器重试",
            )
        body: dict[str, Any] = {}
        if opts.get("story_style") is not None:
            body["story_style"] = str(opts["story_style"])
        target_beats = _target_beats(opts)
        if target_beats is not None:
            body["target_beats"] = target_beats
        actions: list[dict[str, Any]] = [
            _action(
                "POST",
                f"/api/v1/projects/{project}/episodes/{ep}/story/to-script",
                body=body,
                await_task=True,
            )
        ]
        if opts.get("include_story_version"):
            # 类型化步（脚本阶段一并产出故事版）：由客户端（前端 driver）用
            # freezone 生图 + 入库执行；REST 服务端 execute 不处理它（标 deferred），
            # MCP 从不请求故事版，故委托路径永不含此步。故事版紧跟 to-script 一次跑完，
            # beats 出现后本阶段不再返回，天然避免重入。
            actions.append({"kind": "story_version", "episode": ep})
        return _step(
            f"episode_{ep}_script_generating",
            "planned",
            episode=ep,
            actions=actions,
            message=f"第 {ep} 集分镜脚本（story→script）已入队"
            + (f"，目标 {target_beats} 镜" if target_beats is not None else ""),
            next_hint="稍后再次调用计划器推进",
        )

    # [aipdd fork delta] 「资产规划」的另一半：场景菜单 / 道具菜单（纯 LLM 文本规划，
    # 不发资产图）。排在分镜脚本**之后**、故事版之前，依据是实测依赖而非直觉：
    # - 真正的消费方是草图/渲染（task_backend/projection.py 把 scene_menu/prop_menu
    #   喂给 control_frame_to_sketch），脚本生成侧不读它们；
    # - 规划器在精品剧模板下优先解析**本集剧本的场景头**（``AssetCompiler.
    #   _load_scene_blocks``），解说剧模板走解说稿正文；没有剧本时只能回退到
    #   「正文语义规划」，质量更差；
    # - MCP 侧同名工具的说明也是 "after script generation and before sketch generation"。
    # 菜单已有条目就不重跑（用户可能手工传/改过）。
    if opts.get("plan_episode_assets"):
        if not (ep_row or {}).get("scene_menu") and _phase("episode_scene_planner") != "completed":
            return _step(
                f"episode_{ep}_scenes_planning",
                "planned",
                episode=ep,
                actions=[
                    _action(
                        "POST",
                        f"/api/v1/projects/{project}/episodes/{ep}/scenes/plan",
                        await_task=True,
                    )
                ],
                message=f"第 {ep} 集场景规划已入队",
                next_hint="稍后再次调用计划器推进",
            )
        if not (ep_row or {}).get("prop_menu") and _phase("episode_prop_planner") != "completed":
            return _step(
                f"episode_{ep}_props_planning",
                "planned",
                episode=ep,
                actions=[
                    _action(
                        "POST",
                        f"/api/v1/projects/{project}/episodes/{ep}/props/plan",
                        await_task=True,
                    )
                ],
                message=f"第 {ep} 集道具规划已入队",
                next_hint="稍后再次调用计划器推进",
            )

    # [aipdd fork delta] 故事版是草图/渲染图/视频共用的构图参考：剧本已出但该集还没故事版
    # 时先补一张，不留给下游“缺参考图”让效果跑偏。类型化步：服务端 execute 不代跑（deferred），
    # 所以下游入口（MCP）从不请求此开关，只有拿得到 freezone 生图的客户端（前端 driver）会拿到。
    if opts.get("include_story_version") and not await _has_story_version(read, project, ep):
        return _step(
            f"episode_{ep}_story_version_pending",
            "planned",
            episode=ep,
            actions=[{"kind": "story_version", "episode": ep}],
            message=f"第 {ep} 集还没有故事版，先生成故事版参考图",
            next_hint="稍后再次调用计划器推进",
        )

    sketch_missing = [row for row in beats if not str(row.get("sketch_url") or "")]
    if sketch_missing:
        sketch_body: dict[str, Any] = {
            "model": str(opts.get("model") or ""),
            "grid_index": 0,
            "sketch_scene_grouping": True,
            "aspect_ratio": aspect,
        }
        if opts.get("style"):
            sketch_body["style"] = str(opts["style"])
        actions = [
            _action("POST", f"/api/v1/projects/{project}/episodes/{ep}/sketches/assign-colors"),
            _action(
                "POST",
                f"/api/v1/projects/{project}/episodes/{ep}/sketches/generate",
                body=sketch_body,
                await_task=True,
            ),
        ]
        return _step(
            f"episode_{ep}_sketches_generating",
            "planned",
            episode=ep,
            actions=actions,
            message=f"第 {ep} 集缺 {len(sketch_missing)}/{len(beats)} 张草图，已入队草图生成（画幅 {aspect}）",
            next_hint="稍后再次调用计划器推进",
            progress_counts={
                "beats": len(beats),
                "sketches_done": len(beats) - len(sketch_missing),
            },
        )

    frame_missing = [
        int(row.get("beat_number"))
        for row in beats
        if not str(row.get("frame_url") or "") and row.get("beat_number") is not None
    ]
    if frame_missing:
        frame_body: dict[str, Any] = {"beat_indices": frame_missing}
        if opts.get("model"):
            frame_body["model"] = str(opts["model"])
        if opts.get("style"):
            frame_body["style"] = str(opts["style"])
        return _step(
            f"episode_{ep}_frames_generating",
            "planned",
            episode=ep,
            actions=[
                _action(
                    "POST",
                    f"/api/v1/projects/{project}/episodes/{ep}/beats/regenerate",
                    body=frame_body,
                    await_task=True,
                )
            ],
            message=f"第 {ep} 集缺 {len(frame_missing)} 个镜头首帧，已入队首帧生成（不生成视频）",
            next_hint="稍后再次调用计划器推进",
            progress_counts={"beats": len(beats), "frames_missing": frame_missing},
        )

    return _step(f"episode_{ep}_done", "idle", episode=ep)


# --------------------------------------------------------------------------- #
# 顶层：一次调用产出「下一个未完成阶段」的动作
# --------------------------------------------------------------------------- #
async def plan_next(read: Read, project: str, opts: dict[str, Any]) -> dict[str, Any]:
    """检测当前进度，返回下一个未完成阶段的 ``PlanStep``（只决策，不发射）。"""
    aspect = str(opts.get("aspect_ratio") or "2:3")
    if aspect not in ASPECT_RATIOS:
        raise ValueError("aspect_ratio must be '2:3' or '16:9'")

    # 1) 故事源文本
    source = await read("GET", f"/api/v1/projects/{project}/story/source")
    stored = ""
    if isinstance(source.get("data"), dict):
        stored = str(source["data"].get("text") or "")
    if not stored.strip():
        incoming = str(opts.get("story_text") or "").strip()
        if not incoming:
            return _step(
                "story",
                "requires_input",
                message="项目还没有故事源，首次调用请传 story_text 导入故事",
                next_hint="携带 story_text 重新调用",
            )
        return _step(
            "story_saved",
            "planned",
            actions=[
                _action(
                    "PUT",
                    f"/api/v1/projects/{project}/story/source",
                    body={"text": incoming},
                )
            ],
            message=f"已保存故事源（{len(incoming)} 字）",
            next_hint="再次调用计划器开始生成整本多集剧本",
        )

    # 2) 整本剧本生成（episode 0）
    result = await read("GET", f"/api/v1/projects/{project}/story/generate-script/result")
    data = result.get("data") if isinstance(result, dict) else None
    status = str((data or {}).get("status") or "")
    if status in _ACTIVE_STATUSES:
        return _step(
            "screenplay_generating",
            "running",
            message="剧本仍在生成，稍后再推进",
            next_hint="稍后再次调用计划器推进",
            progress=(data or {}).get("progress"),
            current_task=(data or {}).get("current_task"),
        )
    if status == "failed":
        return _step(
            "screenplay_failed",
            "failed",
            error=(data or {}).get("error") or "整本剧本生成失败",
            next_hint="修正故事内容后再次调用",
        )
    if not status or status == "cancelled":
        body: dict[str, Any] = {"episode_count": _episode_count(opts)}
        if opts.get("story_style") is not None:
            body["story_style"] = str(opts["story_style"])
        return _step(
            "screenplay_started",
            "planned",
            actions=[
                _action(
                    "POST",
                    f"/api/v1/projects/{project}/story/generate-script",
                    body=body,
                    await_task=True,
                )
            ],
            message=f"已开始生成 {body['episode_count']} 集剧本",
            next_hint="稍后再次调用计划器推进",
        )

    # 3) 分集尚未落地：项目级推进
    episodes = [
        row
        for row in _rows(await read("GET", f"/api/v1/projects/{project}/episodes"), "episodes", "items")
        if isinstance(row, dict)
    ]
    if not episodes:
        return await _plan_project_stage(read, project, opts)

    # 4) 逐集推进（取最早未完成的集；或指定集）
    available = sorted({int(row.get("number") or 0) for row in episodes if int(row.get("number") or 0) > 0})
    wanted = int(opts.get("episode") or 0)
    if wanted:
        if wanted not in available:
            raise ValueError(f"episode {wanted} not found in this project")
        targets = [wanted]
    else:
        targets = available

    task_rows = await _task_rows(read, project)
    counts_by_episode = {int(row.get("number") or 0): row.get("beat_count") for row in episodes}
    # 分集列表行已带 scene_menu / prop_menu，直接用作「资产规划是否已做过」的判据，
    # 不必再逐集拉详情。
    rows_by_number = {int(row.get("number") or 0): row for row in episodes}

    for ep in targets:
        ep_rows = [row for row in task_rows if int(row.get("episode") or 0) == ep]
        step = await _plan_episode(read, project, ep, ep_rows, opts, rows_by_number.get(ep))
        if step["status"] != "idle":
            return step

    return _step(
        "done",
        "done",
        message="全部集的分镜草图与镜头首帧已就绪；本流程不生成视频",
        next_hint="按需另行用 video_range 生成镜头视频",
        episodes=[{"episode": ep, "beats": counts_by_episode.get(ep)} for ep in available],
    )


async def run_actions(call: Read, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """服务端发射：按序执行含 ``path`` 的细粒度 REST 动作，遇失败即止。

    类型化步（如 ``kind=="story_version"``）需要客户端 freezone 生图 + 结果回读，
    服务端无法代跑，标记为 ``deferred`` 交由调用方（前端 driver）执行。MCP 从不
    请求故事版，因此 MCP 委托路径不含 deferred 动作。
    """
    fired: list[dict[str, Any]] = []
    for action in actions:
        if "path" not in action:
            fired.append({"kind": action.get("kind"), "ok": False, "deferred": True})
            continue
        resp = await call(
            action["method"],
            action["path"],
            query=action.get("query"),
            body=action.get("body"),
        )
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        entry: dict[str, Any] = {
            "method": action["method"],
            "path": action["path"],
            "ok": bool(resp.get("ok")),
            "status_code": resp.get("status_code"),
        }
        if data.get("task_key") or data.get("task_id"):
            entry["task_key"] = data.get("task_key") or data.get("task_id")
        if not resp.get("ok"):
            entry["error"] = resp.get("error")
            fired.append(entry)
            return fired
        fired.append(entry)
    return fired
