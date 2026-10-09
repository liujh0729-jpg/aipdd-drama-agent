# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent 长期记忆：MEMORY.md 的读写。

参考 nanobot MemoryStore 的最小实现，收敛为单文件长期记忆：
- 模型通过 agent_save_memory 工具追加（规范化去重，重复内容只留一条）；
- 用户可以在「记忆」面板直接编辑全文（PUT /agent/memory）；
- 对话时按注入上限截断进系统提示（保留较新的尾部内容）。

存储：``<agent_root>/memory/MEMORY.md``。本模块纯本地文件操作。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from novelvideo.agent import store

logger = logging.getLogger("novelvideo.agent.memory")

# 存储上限（写盘截断，保护文件不无限膨胀）与注入上限（进系统提示的字符数）。
MEMORY_STORE_MAX_CHARS = 64000
MEMORY_PROMPT_MAX_CHARS = 16000


def memory_file(username: str) -> Path:
    return store.memory_dir(username) / "MEMORY.md"


def read_memory(username: str) -> str:
    """读全文；不存在或读取失败按空记忆处理。"""
    path = memory_file(username)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.warning("agent memory read failed", exc_info=True)
        return ""


def write_memory(username: str, text: str) -> str:
    """整文覆盖写（面板编辑用）；超长时保留尾部（较新的内容更重要）。"""
    cleaned = str(text or "").replace("\r\n", "\n").strip()
    if len(cleaned) > MEMORY_STORE_MAX_CHARS:
        cleaned = "(较早的记忆已省略)\n\n" + cleaned[-MEMORY_STORE_MAX_CHARS:]
    path = memory_file(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cleaned, encoding="utf-8")
    return cleaned


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def append_memory(username: str, note: str) -> tuple[str, bool]:
    """追加一条记忆（模型工具调用路径）。

    规范化后已经出现过的内容直接跳过（返回 added=False），避免模型反复记同一条。
    返回 ``(最新全文, 是否新增)``。
    """
    cleaned = str(note or "").replace("\r\n", "\n").strip()
    if not cleaned:
        return read_memory(username), False
    existing = read_memory(username)
    if _normalize(cleaned) and _normalize(cleaned) in _normalize(existing):
        return existing, False
    entry = cleaned if cleaned.startswith("- ") else f"- {cleaned}"
    updated = f"{existing.rstrip()}\n{entry}\n".lstrip("\n") if existing.strip() else f"{entry}\n"
    stored = write_memory(username, updated)
    logger.info("agent memory appended: chars=%d", len(entry))
    return stored, True


def build_memory_block(username: str) -> str:
    """构建注入系统提示的记忆块；无记忆时返回空串。

    截断保留尾部：MEMORY.md 按时间顺序追加，越靠后越新。
    """
    content = read_memory(username).strip()
    if not content:
        return ""
    if len(content) > MEMORY_PROMPT_MAX_CHARS:
        content = content[-MEMORY_PROMPT_MAX_CHARS:]
    return f"长期记忆（来自历史对话的沉淀，写作时遵循）：\n{content}"