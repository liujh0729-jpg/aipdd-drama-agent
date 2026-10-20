# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent 待办计划：项目级任务清单的读写与自动清理。

- 模型通过 agent_update_plan 工具维护（拆解任务 / 勾掉完成）；
- 用户可以在 Agent 页「待办计划」面板直接增删改、勾选；
- 计划按当前状态注入系统提示，让每回合都知道还有什么任务没做；
- 条目可带 ``stage``（流水线里程碑）与 ``episode``（集号），由
  ``agent.plan_runner`` 自动执行到对应产物；不带 stage 的是纯手工待办。

存储：``<project_dir>/freezone/agent_plan.json``——跟项目走，随项目一起备份/清理。
容量：条目超过 PLAN_MAX_ITEMS 时自动删掉「已完成」的（从过去到现在，即最早完成的
先删）；未完成的条目永远不会被自动删除。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from novelvideo.agent.store import now_iso

logger = logging.getLogger("novelvideo.agent.plans")

# 清单容量：超过这个数就开始清理已完成的条目（未完成的保留）。
PLAN_MAX_ITEMS = 30
ITEM_TITLE_MAX_LEN = 200

# 可执行阶段：与 pipeline.planner 的阶段序列一一对应，顺序即先决顺序。
# ``story`` / ``screenplay`` / ``ingest`` / ``characters`` / ``episodes`` 是项目级；
# 其余是逐集阶段（``episode`` 为空表示全部集）。
PLAN_STAGE_ORDER: dict[str, int] = {
    "story": 0,
    "screenplay": 1,
    "ingest": 2,
    "characters": 3,
    "episodes": 4,
    "identities": 5,
    "script": 6,
    "scenes": 7,
    "props": 8,
    "sketches": 9,
    "frames": 10,
}
PLAN_STAGES: tuple[str, ...] = tuple(
    stage for stage, _rank in sorted(PLAN_STAGE_ORDER.items(), key=lambda kv: kv[1])
)
PROJECT_STAGES: frozenset[str] = frozenset(
    {"story", "screenplay", "ingest", "characters", "episodes"}
)
PLAN_STAGE_LABELS: dict[str, str] = {
    "story": "故事源",
    "screenplay": "整本剧本",
    "ingest": "剧本入库",
    "characters": "角色提取",
    "episodes": "分集规划",
    "identities": "人物身份",
    "script": "分镜脚本",
    "scenes": "场景规划",
    "props": "道具规划",
    "sketches": "草图与配色",
    "frames": "镜头首帧",
}

# 标题 → 阶段的保守推断（只在 run_plan 里对没标 stage 的旧条目用一次）。
# 顺序即优先级：越靠前的关键词越具体（「分镜草图」必须先命中 sketches，不能落到 script）。
_STAGE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("frames", ("首帧", "关键帧", "定稿帧", "frame")),
    ("sketches", ("草图", "故事版", "配色", "storyboard", "sketch")),
    ("props", ("道具", "prop")),
    ("scenes", ("场景", "scene")),
    ("identities", ("身份", "造型", "identity")),
    ("script", ("分镜脚本", "镜头脚本", "分镜", "script")),
    ("characters", ("角色", "人物", "character")),
    ("episodes", ("分集", "拆集", "episode")),
    ("ingest", ("摄入", "入库", "图谱", "ingest")),
    ("screenplay", ("剧本", "screenplay")),
    ("story", ("故事源", "故事正文", "小说", "原文", "story")),
)


def infer_stage(title: Any) -> str | None:
    """从条目标题推断里程碑；推断不出来返回 None（保持手工待办）。"""
    text = str(title or "").lower()
    if not text:
        return None
    for stage, words in _STAGE_HINTS:
        if any(word in text for word in words):
            return stage
    return None


def infer_episode(title: Any) -> int | None:
    """从标题里取「第 N 集 / EP N / episode N」；没有或非法返回 None。"""
    text = str(title or "")
    match = re.search(r"第\s*(\d+)\s*集", text) or re.search(
        r"\bep(?:isode)?\s*(\d+)\b", text, re.IGNORECASE
    )
    if not match:
        return None
    try:
        value = int(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


_UNSET = object()


def _clean_stage(value: Any) -> str | None:
    stage = str(value or "").strip().lower()
    if not stage:
        return None
    if stage not in PLAN_STAGE_ORDER:
        raise ValueError(
            f"未知阶段「{stage}」；可用阶段：{'/'.join(PLAN_STAGES)}"
        )
    return stage


def _clean_episode(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        episode = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("episode 必须是正整数") from exc
    if episode <= 0:
        raise ValueError("episode 必须是正整数")
    return episode


def plan_file(project_dir: Path) -> Path:
    return Path(project_dir) / "freezone" / "agent_plan.json"


def load_plan(project_dir: Path) -> list[dict[str, Any]]:
    """读计划；文件不存在/损坏按空计划处理（不打断会话）。"""
    path = plan_file(project_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("agent plan read failed: %s", path, exc_info=True)
        return []
    if not isinstance(data, list):
        return []
    items = [item for item in data if isinstance(item, dict) and item.get("id")]
    for item in items:
        _normalize_item(item)
    return items


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    """补齐可执行字段（旧计划文件没有这些键，读时就地补默认值）。"""
    item.setdefault("stage", None)
    item.setdefault("episode", None)
    item.setdefault("stage_inferred", False)
    item.setdefault("last_error", "")
    item.setdefault("last_run_at", None)
    return item


def _purge(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """超出容量时删掉已完成的条目（最早完成的先删）。返回（保留清单, 删除数）。"""
    removed = 0
    while len(items) > PLAN_MAX_ITEMS:
        completed = [item for item in items if item.get("done")]
        if not completed:
            break  # 全是未完成任务：宁超容量也不丢用户的事
        oldest = min(completed, key=lambda item: item.get("done_at") or item.get("created_at") or "")
        items.remove(oldest)
        removed += 1
    return items, removed


def save_plan(project_dir: Path, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """写盘（先按容量清理）。返回（保留清单, 清理数）。"""
    kept, removed = _purge(list(items))
    path = plan_file(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    return kept, removed


def _clean_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:ITEM_TITLE_MAX_LEN]


def _normalize(value: Any) -> str:
    return _clean_title(value).lower()


def _new_item(
    title: str,
    *,
    source: str,
    stage: str | None = None,
    episode: int | None = None,
) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "id": "plan_" + uuid.uuid4().hex[:10],
        "title": title,
        "done": False,
        "source": source if source in ("agent", "user") else "agent",
        "stage": stage,
        "episode": episode,
        "stage_inferred": False,
        "last_error": "",
        "last_run_at": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "done_at": None,
    }


def _item_spec(raw: Any) -> tuple[str, str | None, int | None]:
    """把字符串或 ``{title, stage, episode}`` 归一成（标题, 阶段, 集号）。"""
    if isinstance(raw, dict):
        title = _clean_title(raw.get("title") or raw.get("task") or raw.get("name"))
        stage = _clean_stage(raw.get("stage"))
        episode = _clean_episode(raw.get("episode"))
        return title, stage, episode
    return _clean_title(raw), None, None


def add_items(
    project_dir: Path, titles: list[Any], *, source: str = "agent"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """批量新增任务。返回（最新清单, 新增条目）。

    条目可以是标题字符串，也可以是 ``{"title", "stage", "episode"}``。stage 为
    None 的条目是手工待办，不会被 ``run_plan`` 自动执行。与「未完成」条目同名的
    标题跳过——模型容易把同一件事重复写两遍；已完成的历史任务不挡新任务。
    """
    items = load_plan(project_dir)
    pending_titles = {_normalize(item.get("title")) for item in items if not item.get("done")}
    added: list[dict[str, Any]] = []
    for raw in titles:
        title, stage, episode = _item_spec(raw)
        normalized = title.lower()
        if not title or normalized in pending_titles:
            continue
        pending_titles.add(normalized)
        item = _new_item(title, source=source, stage=stage, episode=episode)
        items.append(item)
        added.append(item)
    kept, _removed = save_plan(project_dir, items)
    return kept, added


def _match_item(items: list[dict[str, Any]], ref: str) -> dict[str, Any] | None:
    """按 id 精确匹配优先，其次按标题匹配（未完成的优先）。"""
    exact = next((item for item in items if item.get("id") == ref), None)
    if exact is not None:
        return exact
    normalized = _normalize(ref)
    if not normalized:
        return None
    pending = next(
        (item for item in items if not item.get("done") and _normalize(item.get("title")) == normalized),
        None,
    )
    if pending is not None:
        return pending
    return next((item for item in items if _normalize(item.get("title")) == normalized), None)


def complete_items(
    project_dir: Path, refs: list[Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """按 id/标题勾掉任务。返回（最新清单, 本次勾掉的条目, 未匹配的引用）。"""
    items = load_plan(project_dir)
    completed: list[dict[str, Any]] = []
    missing: list[str] = []
    for raw in refs:
        ref = str(raw or "").strip()
        if not ref:
            continue
        item = _match_item(items, ref)
        if item is None:
            missing.append(ref)
            continue
        if not item.get("done"):
            timestamp = now_iso()
            item["done"] = True
            item["done_at"] = timestamp
            item["updated_at"] = timestamp
            item["last_error"] = ""
            completed.append(item)
    kept, _removed = save_plan(project_dir, items)
    return kept, completed, missing


def mark_items(
    project_dir: Path,
    item_ids: list[str],
    *,
    done: bool | None = None,
    error: str | None = None,
) -> list[dict[str, Any]]:
    """按 id 批量改状态（供 plan_runner 使用）：一次读写，不碰标题/阶段。

    ``done=True`` 勾掉并清空上次失败；``done=False`` 重新打开（供重试）；
    ``error`` 非空时记到 ``last_error``，同时刷新 ``last_run_at``。
    """
    wanted = {str(item_id) for item_id in item_ids if item_id}
    if not wanted:
        return load_plan(project_dir)
    items = load_plan(project_dir)
    timestamp = now_iso()
    for item in items:
        if item.get("id") not in wanted:
            continue
        item["last_run_at"] = timestamp
        if error is not None:
            item["last_error"] = str(error)
        if done is not None:
            item["done"] = bool(done)
            item["done_at"] = timestamp if done else None
            if done:
                item["last_error"] = ""
        item["updated_at"] = timestamp
    kept, _removed = save_plan(project_dir, items)
    return kept


def remove_items(
    project_dir: Path, refs: list[Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """按 id/标题删除任务。返回（最新清单, 删除的条目, 未匹配的引用）。"""
    items = load_plan(project_dir)
    removed: list[dict[str, Any]] = []
    missing: list[str] = []
    for raw in refs:
        ref = str(raw or "").strip()
        if not ref:
            continue
        item = _match_item(items, ref)
        if item is None:
            missing.append(ref)
            continue
        items.remove(item)
        removed.append(item)
    kept, _purged = save_plan(project_dir, items)
    return kept, removed, missing


def update_item(
    project_dir: Path,
    item_id: str,
    *,
    title: Any = None,
    done: bool | None = None,
    stage: Any = _UNSET,
    episode: Any = _UNSET,
    stage_inferred: bool | None = None,
    last_error: Any = _UNSET,
    last_run_at: Any = _UNSET,
) -> dict[str, Any] | None:
    """改标题 / 勾选状态 / 可执行字段。找不到返回 None。

    ``stage`` / ``episode`` 传 ``_UNSET`` 表示保持不变；显式传 ``None``/``""``
    表示清空（退回手工待办）。
    """
    items = load_plan(project_dir)
    target = next((item for item in items if item.get("id") == item_id), None)
    if target is None:
        return None
    timestamp = now_iso()
    if title is not None:
        cleaned = _clean_title(title)
        if not cleaned:
            raise ValueError("任务标题不能为空")
        target["title"] = cleaned
        if target.get("stage_inferred"):
            # 推断出来的阶段跟着标题走；标题改了重新推断，推不出来就退回手工待办。
            inferred = infer_stage(cleaned)
            target["stage"] = inferred
            target["episode"] = infer_episode(cleaned)
            if not inferred:
                target["stage_inferred"] = False
    if stage is not _UNSET:
        target["stage"] = _clean_stage(stage)
        if stage_inferred is None:
            target["stage_inferred"] = False
    if episode is not _UNSET:
        target["episode"] = _clean_episode(episode)
    if stage_inferred is not None:
        target["stage_inferred"] = bool(stage_inferred)
    if last_error is not _UNSET:
        target["last_error"] = str(last_error or "")
    if last_run_at is not _UNSET:
        target["last_run_at"] = last_run_at
    if done is not None:
        target["done"] = bool(done)
        target["done_at"] = timestamp if done else None
        if done:
            target["last_error"] = ""
    target["updated_at"] = timestamp
    kept, _removed = save_plan(project_dir, items)
    return next((item for item in kept if item.get("id") == item_id), target)


def remove_item(project_dir: Path, item_id: str) -> bool:
    items = load_plan(project_dir)
    target = next((item for item in items if item.get("id") == item_id), None)
    if target is None:
        return False
    items.remove(target)
    save_plan(project_dir, items)
    return True


def render_plan_items(items: list[dict[str, Any]]) -> str:
    """给模型看的清单文本：带 id 与可执行阶段，便于后续引用 / 自动执行。"""
    if not items:
        return "（当前计划为空）"
    lines: list[str] = []
    for item in items:
        mark = "x" if item.get("done") else " "
        suffix = ""
        stage = item.get("stage")
        if stage in PLAN_STAGE_LABELS:
            scope = f"第 {item['episode']} 集" if item.get("episode") else "全部集"
            origin = "推断" if item.get("stage_inferred") else "指定"
            suffix = f"（stage={stage}，{scope}，{origin}）"
        line = f"- [{mark}] {item.get('id')} {item.get('title')}{suffix}"
        if item.get("last_error") and not item.get("done"):
            line += f"；上次失败：{item['last_error']}"
        lines.append(line)
    return "\n".join(lines)


def build_plan_block(project_dir: Path) -> str:
    """构建注入系统提示的待办计划块；无计划时返回空串。"""
    items = load_plan(project_dir)
    if not items:
        return ""
    pending = sum(1 for item in items if not item.get("done"))
    lines = [
        f"当前项目待办计划（共 {len(items)} 项，未完成 {pending} 项）：",
        render_plan_items(items),
        "维护方式：用户提出新的制作/创作需求时，先把工作拆成任务用 "
        "agent_update_plan（action=add）写进计划；完成一项就立刻用 complete 勾掉"
        "（传任务 id）；确认作废的任务用 remove。新任务优先服从用户口径。",
        "可执行计划：制作类任务在 add 时带上 stage（story/screenplay/ingest/"
        "characters/episodes/identities/script/scenes/props/sketches/frames）和"
        "可选 episode（集号，缺省=全部集）；用户说「按计划执行 / 执行计划」时调用 "
        "aipdd_drama_run_plan，它会按 stage 自动推进到对应产物并回勾任务。不带 stage "
        "的条目是手工待办，需要自己完成后用 complete 勾掉。",
    ]
    return "\n".join(lines)