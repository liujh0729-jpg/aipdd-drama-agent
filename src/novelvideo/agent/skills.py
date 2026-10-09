# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent Skill：SKILL.md 的安装 / 发现 / 启停 / 渐进式加载。

技能格式参考 nanobot：一个技能 = 一个目录（``<agent_root>/skills/<name>/``），
目录内含 ``SKILL.md``，头部是可选 YAML frontmatter：

    ---
    name: storyboard-helper
    description: 分镜草图相关的创作建议（一句话说明什么时候该用）
    ---
    （markdown 正文，模型通过 agent_load_skill 工具按需读取全文）

渐进式加载：系统提示里只注入 name + description 摘要；模型判断需要时再读全文，
避免把全部技能正文塞进上下文。

安装来源：
- 单个 markdown 文件（frontmatter 里写 name / description）；
- zip 包（含 SKILL.md，可带附件；允许一个顶层目录包裹）。

启停状态存 ``skills_disabled``（settings kv，JSON 数组）：禁用的技能不进摘要、
也不可加载，但文件保留。本模块不做任何网络访问，纯本地文件操作。
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from novelvideo.agent import store

logger = logging.getLogger("novelvideo.agent.skills")

SKILL_FILENAME = "SKILL.md"
SKILL_TEXT_MAX_BYTES = 256 * 1024  # 单个 SKILL.md 体积上限
ZIP_MAX_BYTES = 8 * 1024 * 1024  # 上传 zip 压缩包上限
ZIP_MAX_TOTAL_BYTES = 32 * 1024 * 1024  # zip 解压后总大小上限（防 zip 炸弹）
ZIP_MAX_MEMBERS = 256  # zip 成员数上限
SKILL_BODY_PROMPT_MAX_CHARS = 32000  # 载入正文注入模型上下文时的截断
SKILL_NAME_MAX_LEN = 64
DISABLED_KEY = "skills_disabled"

# 技能名规则：小写字母/数字开头，允许中划线与下划线（与目录名一一对应）。
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-_]{0,63}$")


class SkillError(ValueError):
    """技能安装/解析失败（消息面向用户可读）。"""


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 SKILL.md 头部 frontmatter（仅支持单行 ``key: value``，够用且零依赖）。

    没有 frontmatter / 没有闭合分隔符时按"没有元数据"处理，正文原样返回。
    """
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return {}, stripped
    lines = stripped.splitlines()
    end: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            end = index
            break
    if end is None:
        return {}, stripped
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip("'\"")
        if value:
            meta[key.strip().lower()] = value
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def parse_skill_document(text: str, *, fallback_name: str = "") -> dict[str, Any]:
    """把一份 SKILL.md 文本解析成 {name, description, body}。"""
    meta, body = _parse_frontmatter(text)
    return {
        "name": str(meta.get("name") or fallback_name or "").strip(),
        "description": str(meta.get("description") or "").strip(),
        "body": body,
    }


def _normalize_name(raw: str) -> str:
    """把用户输入收敛成合法技能名；收敛失败抛 SkillError。"""
    name = re.sub(r"\s+", "-", str(raw or "").strip().lower())
    name = re.sub(r"[^a-z0-9-_]+", "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-_")[:SKILL_NAME_MAX_LEN]
    if not name or not _NAME_RE.match(name):
        raise SkillError("技能名需为小写字母/数字/中划线/下划线（1-64 个字符）")
    return name


def _skill_dir(username: str, name: str) -> Path:
    return store.skills_root(username) / name


def disabled_skills(username: str) -> set[str]:
    raw = store.get_json_setting(username, DISABLED_KEY, [])
    if not isinstance(raw, list):
        return set()
    return {str(item) for item in raw if str(item or "").strip()}


def _write_disabled(username: str, names: set[str]) -> None:
    store.set_json_setting(username, DISABLED_KEY, sorted(names))


def _read_skill_meta(skill_dir: Path) -> dict[str, Any] | None:
    """读单个技能目录的元数据；目录里没有可解析的 SKILL.md 时返回 None。"""
    document = skill_dir / SKILL_FILENAME
    if not document.exists():
        return None
    try:
        text = document.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    parsed = parse_skill_document(text, fallback_name=skill_dir.name)
    if not parsed["name"]:
        parsed["name"] = skill_dir.name
    return {
        "name": skill_dir.name,
        "title": parsed["name"],
        "description": parsed["description"],
        "path": str(skill_dir),
    }


def list_skills(username: str) -> list[dict[str, Any]]:
    """列出用户已安装的技能（含启停状态），坏目录跳过。"""
    root = store.skills_root(username)
    disabled = disabled_skills(username)
    skills: list[dict[str, Any]] = []
    for skill_dir in sorted(root.iterdir() if root.exists() else []):
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        meta = _read_skill_meta(skill_dir)
        if meta is None:
            continue
        meta["enabled"] = skill_dir.name not in disabled
        skills.append(meta)
    return skills


def get_skill(username: str, name: str) -> dict[str, Any] | None:
    """读技能详情（含正文，按注入上限截断）。"""
    resolved = resolve_skill(username, name)
    if not resolved:
        return None
    skill_dir = _skill_dir(username, resolved)
    meta = _read_skill_meta(skill_dir)
    if meta is None:
        return None
    text = (skill_dir / SKILL_FILENAME).read_text(encoding="utf-8-sig", errors="replace")
    parsed = parse_skill_document(text, fallback_name=resolved)
    body = parsed["body"][:SKILL_BODY_PROMPT_MAX_CHARS]
    meta["body"] = body
    meta["truncated"] = len(parsed["body"]) > len(body)
    meta["enabled"] = resolved not in disabled_skills(username)
    return meta


def resolve_skill(username: str, reference: str) -> str | None:
    """把用户/模型给的技能引用解析成实际目录名（容忍大小写与写法差异）。"""
    raw = str(reference or "").strip()
    if not raw:
        return None
    root = store.skills_root(username)
    if not root.exists():
        return None
    normalized = re.sub(r"\s+", "-", raw.lower())
    for skill_dir in root.iterdir():
        if skill_dir.is_dir() and skill_dir.name.lower() == normalized:
            return skill_dir.name
    for skill_dir in root.iterdir():
        if not skill_dir.is_dir():
            continue
        meta = _read_skill_meta(skill_dir)
        if meta and str(meta.get("title") or "").strip().lower() == raw.lower():
            return skill_dir.name
    return None


def read_skill_body(username: str, reference: str) -> tuple[str, str, str] | None:
    """供 agent_load_skill 工具使用：返回 (目录名, description, 正文)。

    只允许加载已启用的技能；未启用/不存在返回 None。
    """
    resolved = resolve_skill(username, reference)
    if not resolved or resolved in disabled_skills(username):
        return None
    detail = get_skill(username, resolved)
    if detail is None:
        return None
    return resolved, str(detail.get("description") or ""), str(detail.get("body") or "")


def build_skills_summary(username: str) -> str:
    """构建注入系统提示的技能摘要（仅启用技能；无技能返回空串）。"""
    lines: list[str] = []
    for skill in list_skills(username):
        if not skill.get("enabled"):
            continue
        description = str(skill.get("description") or "").strip() or "（无描述）"
        lines.append(f"- {skill['name']}: {description}")
    if not lines:
        return ""
    joined = "\n".join(lines)
    return (
        "已安装技能（用 agent_load_skill 工具按需加载全文，加载后按技能指引工作）：\n"
        f"{joined}"
    )


def install_skill(username: str, *, filename: str, content: bytes) -> dict[str, Any]:
    """安装/更新一个技能。filename 以 .zip 结尾按 zip 包处理，否则按单个 markdown 文件。"""
    if not content:
        raise SkillError("上传内容为空")
    source_name = Path(str(filename or "")).name
    if source_name.lower().endswith(".zip"):
        return _install_from_zip(username, content)
    if len(content) > SKILL_TEXT_MAX_BYTES:
        raise SkillError(f"技能文件过大（上限 {SKILL_TEXT_MAX_BYTES // 1024}KB）")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SkillError("技能文件必须是 UTF-8 编码的 markdown") from exc
    fallback = Path(source_name).stem if source_name else ""
    if fallback.lower() == SKILL_FILENAME[:-3].lower():  # 文件名就叫 SKILL.md 时忽略它
        fallback = ""
    return _install_from_text(username, text, fallback_name=fallback)


def _install_from_text(username: str, text: str, *, fallback_name: str) -> dict[str, Any]:
    parsed = parse_skill_document(text, fallback_name=fallback_name)
    if not str(parsed["name"] or "").strip():
        raise SkillError("技能缺少 name：请在 SKILL.md 头部 frontmatter 里写 name")
    name = _normalize_name(str(parsed["name"]))
    # 重新安装时归一化 frontmatter，避免目录名与 frontmatter 不一致。
    normalized_text = text
    meta, body = _parse_frontmatter(text)
    if str(meta.get("name") or "").lower() != name:
        header_lines = [f"name: {name}"]
        if parsed["description"]:
            header_lines.append(f"description: {parsed['description']}")
        normalized_text = "---\n" + "\n".join(header_lines) + "\n---\n\n" + body
    target_dir = _skill_dir(username, name)
    existed = (target_dir / SKILL_FILENAME).exists()
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / SKILL_FILENAME).write_text(
        normalized_text if normalized_text.endswith("\n") else normalized_text + "\n",
        encoding="utf-8",
    )
    _set_enabled_internal(username, name, True)
    logger.info("agent skill installed: name=%s updated=%s", name, existed)
    return {"name": name, "updated": existed}


def _install_from_zip(username: str, data: bytes) -> dict[str, Any]:
    if len(data) > ZIP_MAX_BYTES:
        raise SkillError(f"技能包过大（上限 {ZIP_MAX_BYTES // 1024 // 1024}MB）")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillError("技能包不是有效的 zip 文件") from exc
    with archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if not members:
            raise SkillError("技能包是空的")
        if len(members) > ZIP_MAX_MEMBERS:
            raise SkillError(f"技能包文件数过多（上限 {ZIP_MAX_MEMBERS}）")
        total_bytes = sum(item.file_size for item in members)
        if total_bytes > ZIP_MAX_TOTAL_BYTES:
            raise SkillError("技能包解压后体积过大")
        # 防 zip-slip：成员必须是纯相对路径。
        for item in members:
            pure = PurePosixPath(item.filename)
            if pure.is_absolute() or ".." in pure.parts:
                raise SkillError(f"技能包包含非法路径：{item.filename}")
        candidates = [
            item
            for item in members
            if PurePosixPath(item.filename).name.lower() == SKILL_FILENAME.lower()
        ]
        if not candidates:
            raise SkillError("技能包中缺少 SKILL.md")
        # 优先根目录（层级最浅）的 SKILL.md。
        candidates.sort(key=lambda item: (len(PurePosixPath(item.filename).parts), item.filename))
        chosen = candidates[0]
        chosen_path = PurePosixPath(chosen.filename)
        prefix = chosen_path.parent
        try:
            document_text = archive.read(chosen).decode("utf-8-sig", errors="replace")
        except OSError as exc:
            raise SkillError("技能包中的 SKILL.md 无法读取") from exc
        fallback = "" if str(prefix) in ("", ".") else prefix.name
        parsed = parse_skill_document(document_text, fallback_name=fallback)
        if not str(parsed["name"] or "").strip():
            raise SkillError("技能缺少 name：请在 SKILL.md 头部 frontmatter 里写 name")
        name = _normalize_name(str(parsed["name"]))
        target_dir = _skill_dir(username, name)
        existed = target_dir.exists()
        if existed:
            shutil.rmtree(target_dir)  # 重新安装 = 整目录替换
        target_dir.mkdir(parents=True, exist_ok=True)
        prefix_str = "" if str(prefix) in ("", ".") else str(prefix)
        for item in members:
            raw_name = item.filename
            if prefix_str:
                if raw_name != prefix_str and not raw_name.startswith(prefix_str + "/"):
                    continue  # 技能目录之外的成员忽略
                relative = raw_name[len(prefix_str) + 1 :]
            else:
                relative = raw_name
            if not relative:
                continue
            destination = target_dir / PurePosixPath(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(item))
    _set_enabled_internal(username, name, True)
    logger.info("agent skill installed from zip: name=%s updated=%s", name, existed)
    return {"name": name, "updated": existed}


def delete_skill(username: str, name: str) -> bool:
    """删除技能（连同目录与文件）。"""
    resolved = resolve_skill(username, name)
    if not resolved:
        return False
    target_dir = _skill_dir(username, resolved)
    if not target_dir.exists():
        return False
    shutil.rmtree(target_dir)
    disabled = disabled_skills(username)
    if resolved in disabled:
        disabled.discard(resolved)
        _write_disabled(username, disabled)
    logger.info("agent skill deleted: name=%s", resolved)
    return True


def _set_enabled_internal(username: str, name: str, enabled: bool) -> None:
    disabled = disabled_skills(username)
    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    _write_disabled(username, disabled)


def set_skill_enabled(username: str, name: str, enabled: bool) -> dict[str, Any] | None:
    """启用/禁用一个技能，返回更新后的技能信息。"""
    resolved = resolve_skill(username, name)
    if not resolved:
        return None
    _set_enabled_internal(username, resolved, enabled)
    detail = get_skill(username, resolved)
    return detail