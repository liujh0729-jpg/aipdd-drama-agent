# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 权威流水线计划器单测（单一逻辑源）。

阶段状态机已从 MCP 插件下沉到 ``novelvideo.pipeline.planner``：这里用一个假的
``read``（只 GET）钉住「下一个未完成阶段」返回的 stage/status/actions 形状，以及
全自动只到镜头首帧、绝不提交视频、逐集前置（场景/道具规划与故事版）、run_actions
只发射含 path 的 REST 且遇失败即止的边界。
"""

from __future__ import annotations

import pytest

from novelvideo.pipeline.planner import _story_version_exists, plan_next, run_actions

P = "/api/v1/projects/test"


def make_read(routes: dict, default=None):
    calls: list[dict] = []

    async def read(method, path, *, query=None, body=None):
        calls.append({"method": method, "path": path, "query": query, "body": body})
        resp = routes.get((method, path), default)
        return resp if resp is not None else {"ok": True}

    return read, calls


def story(text=""):
    return {"ok": True, "data": {"text": text, "chars": len(text)}}


def screenplay(status):
    return {"ok": True, "data": {"status": status, "progress": 0.4, "current_task": "写第2集"}}


# --------------------------------------------------------------------------- #
# 故事源 + 整本剧本
# --------------------------------------------------------------------------- #
async def test_requires_story_text_on_empty_source():
    read, calls = make_read({("GET", f"{P}/story/source"): story("")})
    step = await plan_next(read, "test", {})
    assert step["stage"] == "story" and step["status"] == "requires_input"
    assert "story_text" in step["message"]
    assert [c["method"] for c in calls] == ["GET"]


async def test_saves_story_then_reports_next_step():
    read, calls = make_read(
        {
            ("GET", f"{P}/story/source"): story(""),
            ("PUT", f"{P}/story/source"): {"ok": True, "data": {"chars": 5}},
        }
    )
    step = await plan_next(read, "test", {"story_text": "从前有座山"})
    assert step["stage"] == "story_saved" and step["status"] == "planned"
    # plan_next 只决策不发射：PUT 作为动作返回，read 不应被以 PUT 调用。
    puts = [a for a in step["actions"] if a["method"] == "PUT"]
    assert puts and puts[0]["path"] == f"{P}/story/source"
    assert puts[0]["body"] == {"text": "从前有座山"}
    assert not [c for c in calls if c["method"] != "GET"]


async def test_waits_while_screenplay_generating():
    read, calls = make_read(
        {
            ("GET", f"{P}/story/source"): story("山"),
            ("GET", f"{P}/story/generate-script/result"): screenplay("running"),
        }
    )
    step = await plan_next(read, "test", {})
    assert step["stage"] == "screenplay_generating" and step["status"] == "running"
    assert step["progress"] == 0.4
    assert all(c["method"] == "GET" for c in calls)


async def test_submits_screenplay_with_derived_count():
    read, calls = make_read(
        {
            ("GET", f"{P}/story/source"): story("山"),
            ("GET", f"{P}/story/generate-script/result"): {"ok": True, "data": {"status": None}},
            ("POST", f"{P}/story/generate-script"): {"ok": True, "task_id": "t1"},
        }
    )
    step = await plan_next(read, "test", {"total_seconds": 300, "seconds_per_episode": 60})
    assert step["stage"] == "screenplay_started"
    post = next(a for a in step["actions"] if a["method"] == "POST")
    assert post["path"] == f"{P}/story/generate-script"
    assert post["body"]["episode_count"] == 5


async def test_reports_screenplay_failure():
    read, _ = make_read(
        {
            ("GET", f"{P}/story/source"): story("山"),
            ("GET", f"{P}/story/generate-script/result"): {
                "ok": True,
                "data": {"status": "failed", "error": "模型超时"},
            },
        }
    )
    step = await plan_next(read, "test", {})
    assert step["stage"] == "screenplay_failed" and step["status"] == "failed"
    assert "模型超时" in step["error"]


# --------------------------------------------------------------------------- #
# 项目级：分集尚空
# --------------------------------------------------------------------------- #
async def test_syncs_and_starts_ingest_when_no_episodes():
    read, _ = make_read(
        {
            ("GET", f"{P}/story/source"): story("山"),
            ("GET", f"{P}/story/generate-script/result"): screenplay("completed"),
            ("GET", f"{P}/episodes"): {"ok": True, "data": []},
            ("GET", f"{P}/tasks"): {"ok": True, "data": []},
        }
    )
    step = await plan_next(read, "test", {})
    assert step["stage"] == "ingest_started"
    posts = [a["path"] for a in step["actions"]]
    assert posts == [f"{P}/story/generate-script/sync", f"{P}/ingest/start"]


async def test_waits_on_running_ingest():
    read, _ = make_read(
        {
            ("GET", f"{P}/story/source"): story("山"),
            ("GET", f"{P}/story/generate-script/result"): screenplay("completed"),
            ("GET", f"{P}/episodes"): {"ok": True, "data": []},
            ("GET", f"{P}/tasks"): {
                "ok": True,
                "data": [{"task_type": "ingest_fast", "episode": 0, "status": "running", "progress": 0.2}],
            },
        }
    )
    step = await plan_next(read, "test", {})
    assert step["stage"] == "ingest_fast_running" and step["status"] == "running"


async def test_plans_episodes_when_characters_ready():
    read, _ = make_read(
        {
            ("GET", f"{P}/story/source"): story("山"),
            ("GET", f"{P}/story/generate-script/result"): screenplay("completed"),
            ("GET", f"{P}/episodes"): {"ok": True, "data": []},
            ("GET", f"{P}/tasks"): {
                "ok": True,
                "data": [
                    {"task_type": "ingest_fast", "episode": 0, "status": "completed"},
                    {"task_type": "build_characters", "episode": 0, "status": "completed"},
                ],
            },
            ("GET", f"{P}/characters"): {"ok": True, "data": [{"name": "小明"}]},
        }
    )
    step = await plan_next(read, "test", {"episode_count": 8})
    assert step["stage"] == "episodes_planning"
    post = next(a for a in step["actions"] if a["path"] == f"{P}/episodes/plan")
    assert post["body"] == {"target_episodes": 8}


# --------------------------------------------------------------------------- #
# 逐集：身份 → 脚本（含可选故事版）→ 草图 → 首帧 → done
# --------------------------------------------------------------------------- #
async def test_plans_identities_for_empty_episode():
    read, _ = make_read(
        {
            ("GET", f"{P}/story/source"): story("山"),
            ("GET", f"{P}/story/generate-script/result"): screenplay("completed"),
            ("GET", f"{P}/episodes"): {"ok": True, "data": [{"number": 1, "beat_count": 0}]},
            ("GET", f"{P}/tasks"): {"ok": True, "data": []},
            ("GET", f"{P}/episodes/1/beats"): {"ok": True, "data": []},
        }
    )
    step = await plan_next(read, "test", {})
    assert step["stage"] == "episode_1_identities_planning"
    assert step["actions"][0]["path"] == f"{P}/episodes/1/identities/plan-async"


async def test_script_stage_appends_story_version_when_requested():
    read, _ = make_read(
        {
            ("GET", f"{P}/story/source"): story("山"),
            ("GET", f"{P}/story/generate-script/result"): screenplay("completed"),
            ("GET", f"{P}/episodes"): {"ok": True, "data": [{"number": 1, "beat_count": 0}]},
            ("GET", f"{P}/tasks"): {
                "ok": True,
                "data": [{"task_type": "identity_planner", "episode": 1, "status": "completed"}],
            },
            ("GET", f"{P}/episodes/1/beats"): {"ok": True, "data": []},
        }
    )
    step = await plan_next(read, "test", {"seconds_per_episode": 90, "include_story_version": True})
    assert step["stage"] == "episode_1_script_generating"
    assert step["actions"][0]["body"] == {"target_beats": 30}
    # 故事版作为脚本阶段尾部的类型化步（无 path，交由客户端执行）。
    assert step["actions"][1] == {"kind": "story_version", "episode": 1}


# --------------------------------------------------------------------------- #
# 逐集前置：资产规划（场景/道具，紧跟脚本之后）与故事版
# --------------------------------------------------------------------------- #
def _episode_routes(overrides: dict | None = None):
    routes = {
        ("GET", f"{P}/story/source"): story("山"),
        ("GET", f"{P}/story/generate-script/result"): screenplay("completed"),
        ("GET", f"{P}/episodes"): {"ok": True, "data": [{"number": 1, "beat_count": 0}]},
        ("GET", f"{P}/tasks"): {
            "ok": True,
            "data": [{"task_type": "identity_planner", "episode": 1, "status": "completed"}],
        },
        ("GET", f"{P}/episodes/1/beats"): {"ok": True, "data": []},
    }
    if overrides:
        routes.update(overrides)
    return routes


# 「该集分镜脚本已出」的最小路由：场景/道具规划必须在脚本之后才轮得到。
_BEATS: dict = {
    ("GET", f"{P}/episodes"): {"ok": True, "data": [{"number": 1, "beat_count": 1}]},
    ("GET", f"{P}/episodes/1/beats"): {
        "ok": True,
        "data": [{"beat_number": 1, "sketch_url": "", "frame_url": ""}],
    },
}


async def test_asset_planning_waits_for_the_script_instead_of_racing_it():
    """剧本还没出时绝不抢跑场景/道具规划：规划器要吃本集剧本的场景头。"""
    read, _ = make_read(_episode_routes())
    step = await plan_next(read, "test", {"plan_episode_assets": True})
    assert step["stage"] == "episode_1_script_generating"


async def test_scene_planning_follows_the_script_when_assets_requested():
    read, _ = make_read(_episode_routes(_BEATS))
    step = await plan_next(read, "test", {"plan_episode_assets": True})
    assert step["stage"] == "episode_1_scenes_planning"
    assert step["actions"][0]["path"] == f"{P}/episodes/1/scenes/plan"
    assert step["actions"][0]["await_task"] is True


async def test_prop_planning_follows_completed_scene_planning():
    read, _ = make_read(
        _episode_routes(
            {
                **_BEATS,
                ("GET", f"{P}/tasks"): {
                    "ok": True,
                    "data": [
                        {"task_type": "identity_planner", "episode": 1, "status": "completed"},
                        {"task_type": "episode_scene_planner", "episode": 1, "status": "completed"},
                    ],
                },
            }
        )
    )
    step = await plan_next(read, "test", {"plan_episode_assets": True})
    assert step["stage"] == "episode_1_props_planning"
    assert step["actions"][0]["path"] == f"{P}/episodes/1/props/plan"


async def test_menu_items_make_planning_idempotent():
    """菜单已有条目（可能手工传/改过）就不重跑规划，直接进下一步。"""
    read, _ = make_read(
        _episode_routes(
            {
                **_BEATS,
                ("GET", f"{P}/episodes"): {
                    "ok": True,
                    "data": [
                        {
                            "number": 1,
                            "beat_count": 1,
                            "scene_menu": [{"scene_id": "天台"}],
                            "prop_menu": [{"prop_id": "钥匙"}],
                        }
                    ],
                },
            }
        )
    )
    step = await plan_next(read, "test", {"plan_episode_assets": True})
    assert step["stage"] == "episode_1_sketches_generating"


async def test_asset_planning_skipped_when_not_requested():
    """不传该开关（自动化草图 / MCP 入口）时行为与旧版一致：有剧本就直接进草图。"""
    read, _ = make_read(_episode_routes(_BEATS))
    step = await plan_next(read, "test", {})
    assert step["stage"] == "episode_1_sketches_generating"


async def test_running_scene_planner_is_awaited_not_resubmitted():
    read, _ = make_read(
        _episode_routes(
            {
                **_BEATS,
                ("GET", f"{P}/tasks"): {
                    "ok": True,
                    "data": [
                        {"task_type": "identity_planner", "episode": 1, "status": "completed"},
                        {"task_type": "episode_scene_planner", "episode": 1, "status": "running"},
                    ],
                },
            }
        )
    )
    step = await plan_next(read, "test", {"plan_episode_assets": True})
    assert step["status"] == "running"
    assert step["actions"] == []


async def test_story_version_is_a_prerequisite_before_sketches():
    read, _ = make_read(
        _episode_routes({
                ("GET", f"{P}/episodes"): {"ok": True, "data": [{"number": 1, "beat_count": 2}]},
                ("GET", f"{P}/episodes/1/beats"): {
                    "ok": True,
                    "data": [
                        {"beat_number": 1, "sketch_url": "", "frame_url": ""},
                        {"beat_number": 2, "sketch_url": "", "frame_url": ""},
                    ],
                },
                ("GET", f"{P}/freezone/video/character-library"): {"ok": True, "data": []},
            }
        )
    )
    step = await plan_next(read, "test", {"include_story_version": True})
    assert step["stage"] == "episode_1_story_version_pending"
    assert step["actions"] == [{"kind": "story_version", "episode": 1}]


async def test_existing_story_version_asset_skips_straight_to_sketches():
    read, calls = make_read(
        _episode_routes({
                ("GET", f"{P}/episodes"): {"ok": True, "data": [{"number": 1, "beat_count": 1}]},
                ("GET", f"{P}/episodes/1/beats"): {
                    "ok": True,
                    "data": [{"beat_number": 1, "sketch_url": "", "frame_url": ""}],
                },
                ("GET", f"{P}/freezone/video/character-library"): {
                    "ok": True,
                    "data": [{"name": "EP1 故事版", "category": "storyVersion"}],
                },
            }
        )
    )
    step = await plan_next(read, "test", {"include_story_version": True})
    assert step["stage"] == "episode_1_sketches_generating"
    # 已有故事版就不该重入这一步（靠资产库回读终止，而不是靠“发过就算”）。
    assert [c["path"] for c in calls if c["path"].endswith("character-library")] == [
        f"{P}/freezone/video/character-library"
    ]


async def test_story_version_check_does_not_read_library_when_not_requested():
    """不开开关（MCP / 服务端发射路径）时不该白读一次资产库。"""
    read, calls = make_read(
        _episode_routes({
                ("GET", f"{P}/episodes"): {"ok": True, "data": [{"number": 1, "beat_count": 1}]},
                ("GET", f"{P}/episodes/1/beats"): {
                    "ok": True,
                    "data": [{"beat_number": 1, "sketch_url": "", "frame_url": ""}],
                },
            }
        )
    )
    step = await plan_next(read, "test", {})
    assert step["stage"] == "episode_1_sketches_generating"
    assert not any(c["path"].endswith("character-library") for c in calls)


def test_story_version_exists_matches_episode_exactly():
    items = [
        {"name": "EP10 故事版", "category": "storyVersion"},
        {"name": "EP1 草图", "category": "sketch"},
    ]
    assert not _story_version_exists(items, 1)  # EP10 不被 EP1 误命中；类目不对也不算
    assert _story_version_exists(items, 10)
    assert _story_version_exists([{"name": "EP1 故事版", "category": "storyVersion"}], 1)


async def test_generates_sketches_for_missing_beat_urls():
    read, _ = make_read(
        {
            ("GET", f"{P}/story/source"): story("山"),
            ("GET", f"{P}/story/generate-script/result"): screenplay("completed"),
            ("GET", f"{P}/episodes"): {"ok": True, "data": [{"number": 1, "beat_count": 2}]},
            ("GET", f"{P}/tasks"): {"ok": True, "data": []},
            ("GET", f"{P}/episodes/1/beats"): {
                "ok": True,
                "data": [
                    {"beat_number": 1, "sketch_url": "/static/a.png", "frame_url": ""},
                    {"beat_number": 2, "sketch_url": "", "frame_url": ""},
                ],
            },
        }
    )
    step = await plan_next(read, "test", {"aspect_ratio": "16:9", "model": "nanobanana"})
    assert step["stage"] == "episode_1_sketches_generating"
    posts = [a["path"] for a in step["actions"]]
    assert posts == [f"{P}/episodes/1/sketches/assign-colors", f"{P}/episodes/1/sketches/generate"]
    gen = next(a for a in step["actions"] if a["path"].endswith("sketches/generate"))
    assert gen["body"]["aspect_ratio"] == "16:9" and gen["body"]["model"] == "nanobanana"


async def test_regenerates_missing_frames_only():
    read, _ = make_read(
        {
            ("GET", f"{P}/story/source"): story("山"),
            ("GET", f"{P}/story/generate-script/result"): screenplay("completed"),
            ("GET", f"{P}/episodes"): {"ok": True, "data": [{"number": 1, "beat_count": 3}]},
            ("GET", f"{P}/tasks"): {"ok": True, "data": []},
            ("GET", f"{P}/episodes/1/beats"): {
                "ok": True,
                "data": [
                    {"beat_number": 1, "sketch_url": "/s.png", "frame_url": "/f.png"},
                    {"beat_number": 2, "sketch_url": "/s.png", "frame_url": ""},
                    {"beat_number": 3, "sketch_url": "/s.png", "frame_url": ""},
                ],
            },
        }
    )
    step = await plan_next(read, "test", {})
    assert step["stage"] == "episode_1_frames_generating"
    assert step["actions"][0]["path"] == f"{P}/episodes/1/beats/regenerate"
    assert step["actions"][0]["body"]["beat_indices"] == [2, 3]


async def test_done_never_emits_actions():
    read, calls = make_read(
        {
            ("GET", f"{P}/story/source"): story("山"),
            ("GET", f"{P}/story/generate-script/result"): screenplay("completed"),
            ("GET", f"{P}/episodes"): {"ok": True, "data": [{"number": 1, "beat_count": 2}]},
            ("GET", f"{P}/tasks"): {"ok": True, "data": []},
            ("GET", f"{P}/episodes/1/beats"): {
                "ok": True,
                "data": [
                    {"beat_number": 1, "sketch_url": "/s.png", "frame_url": "/f.png"},
                    {"beat_number": 2, "sketch_url": "/s.png", "frame_url": "/f.png"},
                ],
            },
        }
    )
    step = await plan_next(read, "test", {})
    assert step["stage"] == "done" and step["status"] == "done"
    assert step["actions"] == []
    assert "video" in step["message"] or "视频" in step["message"]
    assert not [c for c in calls if c["method"] != "GET"]


async def test_rejects_unknown_episode_and_bad_aspect():
    read, _ = make_read(
        {
            ("GET", f"{P}/story/source"): story("山"),
            ("GET", f"{P}/story/generate-script/result"): screenplay("completed"),
            ("GET", f"{P}/episodes"): {"ok": True, "data": [{"number": 1, "beat_count": 0}]},
        }
    )
    with pytest.raises(ValueError, match="not found"):
        await plan_next(read, "test", {"episode": 9})
    with pytest.raises(ValueError, match="aspect_ratio"):
        await plan_next(read, "test", {"aspect_ratio": "4:3"})


# --------------------------------------------------------------------------- #
# run_actions：只发射含 path 的 REST，typed 步标 deferred，遇失败即止
# --------------------------------------------------------------------------- #
async def test_run_actions_fires_rest_defers_typed_and_stops_on_failure():
    fired: list[dict] = []

    async def call(method, path, *, query=None, body=None):
        fired.append({"method": method, "path": path})
        if path.endswith("boom"):
            return {"ok": False, "status_code": 500, "error": "kaboom", "data": {}}
        return {"ok": True, "status_code": 200, "data": {"task_key": f"k-{len(fired)}"}}

    actions = [
        {"kind": "story_version", "episode": 1},
        {"method": "POST", "path": f"{P}/a", "query": None, "body": None, "await_task": True},
        {"method": "POST", "path": f"{P}/boom", "query": None, "body": None, "await_task": False},
        {"method": "POST", "path": f"{P}/never", "query": None, "body": None, "await_task": False},
    ]
    result = await run_actions(call, actions)
    assert result[0] == {"kind": "story_version", "ok": False, "deferred": True}
    assert result[1]["ok"] is True and result[1]["task_key"] == "k-1"
    assert result[2]["ok"] is False and result[2]["error"] == "kaboom"
    # 失败后不再发射第三个 REST。
    assert [f["path"] for f in fired] == [f"{P}/a", f"{P}/boom"]
