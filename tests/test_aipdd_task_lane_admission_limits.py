"""[aipdd fork delta] 单机自建部署解除任务准入并发闸。

上游默认「项目闸 12 + 人均闸 3」（default 车道，含全部图片类任务）是为多租户
SaaS 做公平性用的；本 fork 一台机器一个操作者，批量派发几百张图/五官垫图时会
被 `lane is full (3/3)` 直接 429 拒掉，视频车道更是人均 1。

钉三件事：
1. ``task_backend/limits.py`` 的 ``<=0`` 语义 —— 置 0 后两道闸都完全不判；
2. ``TaskStateManager.reserve_task_for_project`` 在闸关掉后可以连续占 5 个 active，
   而不关时第 4 个必须仍被人均闸拦住（保证fork 改动只放开闸门、没改判定本身）；
3. ``docker-compose.aipdd.yml`` 必须带上四条车道的置 0 配置，rebase 时不能静默丢。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.limits import (
    ProjectUserTaskLimitExceeded,
    project_lane_active_limit,
    project_lane_effective_active_limit,
    project_user_lane_active_limit,
)
from novelvideo.task_state import TaskStateManager

COMPOSE_FILE = Path(__file__).resolve().parents[1] / "docker-compose.aipdd.yml"

_LANES = ("default", "video", "world", "ffmpeg")


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_aipdd_lane",
        project_name="demo",
        owner_type="user",
        owner_id="owner",
        owner_username="local",
        requester_user_id="local",
        requester_username="local",
        requester_principals=(("user", "local"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


def _clear_lane_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for lane in _LANES:
        upper = lane.upper()
        monkeypatch.delenv(f"ST_PROJECT_MAX_ACTIVE_{upper}_TASKS", raising=False)
        monkeypatch.delenv(
            f"ST_PROJECT_USER_MAX_ACTIVE_{upper}_TASKS", raising=False
        )


def _disable_all_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_lane_env(monkeypatch)
    for lane in _LANES:
        upper = lane.upper()
        monkeypatch.setenv(f"ST_PROJECT_MAX_ACTIVE_{upper}_TASKS", "0")
        monkeypatch.setenv(f"ST_PROJECT_USER_MAX_ACTIVE_{upper}_TASKS", "0")


def test_zero_env_disables_both_admission_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_all_gates(monkeypatch)

    for lane in _LANES:
        assert project_lane_active_limit(lane) is None
        assert project_user_lane_active_limit(lane) is None
        assert (
            project_lane_effective_active_limit(lane, eligible_user_count=1) is None
        )


def test_defaults_still_cap_one_user_at_three_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """官方默认值不动：图片（default 车道）单人仍是 3，本 fork 靠部署配置放开。"""
    _clear_lane_env(monkeypatch)

    assert project_lane_effective_active_limit("default", eligible_user_count=1) == 3
    assert project_lane_effective_active_limit("video", eligible_user_count=1) == 1


def test_reserve_five_active_tasks_with_gates_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_all_gates(monkeypatch)
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)

    for index in range(5):
        _state, reserved = manager.reserve_task_for_project(
            ctx,
            "identity_image",
            0,
            scope=f"character:秦:identity_image:id-{index}",
            queue_kind="default",
        )

        assert reserved is True


def test_reserve_is_still_rejected_when_gates_are_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """对照组：闸没关时第 4 个提交必须照旧被人均闸拒掉。"""
    _clear_lane_env(monkeypatch)
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)

    for index in range(3):
        manager.reserve_task_for_project(
            ctx,
            "identity_image",
            0,
            scope=f"character:秦:identity_image:id-{index}",
            queue_kind="default",
        )

    with pytest.raises(ProjectUserTaskLimitExceeded) as exc:
        manager.reserve_task_for_project(
            ctx,
            "identity_image",
            0,
            scope="character:秦:identity_image:id-3",
            queue_kind="default",
        )

    assert exc.value.limit == 3
    assert exc.value.active == 3


def test_aipdd_compose_turns_off_every_lane_gate() -> None:
    services = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))["services"]
    environment = services["api"]["environment"]

    for lane in _LANES:
        upper = lane.upper()
        for key in (
            f"ST_PROJECT_MAX_ACTIVE_{upper}_TASKS",
            f"ST_PROJECT_USER_MAX_ACTIVE_{upper}_TASKS",
        ):
            value = str(environment.get(key) or "")
            # 保留 .env 可覆盖的插值写法，但默认值必须是 0（= 不判该闸）
            assert value.endswith(":-0}"), f"{key}={value!r} 未置 0"
