# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent 待办计划：项目级任务清单的读写与自动清理。

- 模型通过 agent_update_plan 工具维护（拆解任务 / 勾掉完成）；
- 用户可以在 Agent 页「待办计划」面板直接增删改、勾选；
- 计划按当前状态注入系统提示，让每回合都知道还有什么任务没做。

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
    return [item for item in data if isinstance(item, dict) and item.get("id")]


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


def _new_item(title: str, *, source: str) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "id": "plan_" + uuid.uuid4().hex[:10],
        "title": title,
        "done": False,
        "source": source if source in ("agent", "user") else "agent",
        "created_at": timestamp,
        "updated_at": timestamp,
        "done_at": None,
    }


def add_items(
    project_dir: Path, titles: list[Any], *, source: str = "agent"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """批量新增任务。返回（最新清单, 新增条目）。

    与「未完成」条目同名的标题跳过——模型容易把同一件事重复写两遍；
    已完成的历史任务不挡新任务（同一个名字的活儿可能又来一轮）。
    """
    items = load_plan(project_dir)
    pending_titles = {_normalize(item.get("title")) for item in items if not item.get("done")}
    added: list[dict[str, Any]] = []
    for raw in titles:
        title = _clean_title(raw)
        normalized = title.lower()
        if not title or normalized in pending_titles:
            continue
        pending_titles.add(normalized)
        item = _new_item(title, source=source)
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
            completed.append(item)
    kept, _removed = save_plan(project_dir, items)
    return kept, completed, missing


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
) -> dict[str, Any] | None:
    """改标题 / 勾选状态（面板编辑用）。找不到返回 None。"""
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
    if done is not None:
        target["done"] = bool(done)
        target["done_at"] = timestamp if done else None
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
    """给模型看的清单文本：带 id，便于后续 complete/remove 引用。"""
    if not items:
        return "（当前计划为空）"
    lines: list[str] = []
    for item in items:
        mark = "x" if item.get("done") else " "
        lines.append(f"- [{mark}] {item.get('id')} {item.get('title')}")
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
    ]
    return "\n".join(lines)