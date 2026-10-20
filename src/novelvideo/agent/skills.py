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

内置技能：仓库 ``.hermes/skills/<name>/`` 是项目级技能源（Hermes 后端也直接使用）。
只有 frontmatter 显式声明 ``agent: true`` 的技能才进入 Agent 面板与 ``agent_load_skill``
——``dramaclaw`` 这类只服务 Hermes 的旧技能不会污染 Agent 的工具口径。内置技能
只读（可停用、不可删除）；用户安装同名技能时覆盖内置版本。任何技能都可导出 zip
（``skill_zip_bytes``），供界面「查看 / 下载」使用。

启停状态存 ``skills_disabled``（settings kv，JSON 数组）：禁用的技能不进摘要、
也不可加载，但文件保留。本模块不做任何网络访问，纯本地文件操作。
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import tempfile
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
BUILTIN_SKILLS_ENV = "AGENT_BUILTIN_SKILLS_DIR"
BUILTIN_AGENT_FLAG = "agent"  # frontmatter `agent: true` 才进入 Agent 面板
SKILL_FILES_MAX = 200  # 面板详情/files 字段枚举上限（导出按安装上限单独校验）

# 技能名规则：小写字母/数字开头，允许中划线与下划线（与目录名一一对应）。
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-_]{0,63}$")


class SkillError(ValueError):
    """技能安装/解析失败（消息面向用户可读）。"""


class SkillNotFoundError(SkillError):
    """技能不存在（HTTP 映射 404）。"""


class SkillTooLargeError(SkillError):
    """上传/导出超过体积、数量上限（HTTP 映射 413）。"""


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
    name = str(meta.get("name") or fallback_name or "").strip()
    return {
        "name": name,
        "title": str(meta.get("title") or name).strip(),
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


def _with_normalized_name(text: str, name: str) -> str:
    """只改写 frontmatter 的 name 行，保留 title/agent/requires 等其他字段。"""
    stripped = text.lstrip("\ufeff")
    lines = stripped.splitlines()
    if lines and lines[0].strip() == "---":
        end = next(
            (index for index in range(1, len(lines)) if lines[index].strip() in ("---", "...")),
            None,
        )
        if end is not None:
            name_index = next(
                (
                    index
                    for index in range(1, end)
                    if lines[index].partition(":")[0].strip().lower() == "name"
                ),
                None,
            )
            if name_index is None:
                lines.insert(1, f"name: {name}")
            else:
                lines[name_index] = f"name: {name}"
            return "\n".join(lines) + "\n"
    return f"---\nname: {name}\n---\n\n{stripped}"


def _skill_dir(username: str, name: str) -> Path:
    return store.skills_root(username) / name


def builtin_skills_root() -> Path:
    """项目内置技能目录（仓库 ``.hermes/skills``；打包形态可用 env 覆盖）。"""
    raw = os.environ.get(BUILTIN_SKILLS_ENV, "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[3] / ".hermes" / "skills"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_builtin_agent_skill(skill_dir: Path) -> bool:
    """内置技能需 frontmatter 显式写 ``agent: true`` 才进入 Agent 面板。"""
    document = skill_dir / SKILL_FILENAME
    if not document.exists():
        return False
    try:
        text = document.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    meta, _body = _parse_frontmatter(text)
    return _truthy(meta.get(BUILTIN_AGENT_FLAG))


def _skill_files(skill_dir: Path, limit: int = SKILL_FILES_MAX) -> list[Path]:
    """技能目录内的普通文件（跳过临时/备份/隐藏文件，按相对路径排序）。"""
    if not skill_dir.is_dir():
        return []
    files = [
        path
        for path in skill_dir.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and not path.name.startswith(".")
        and not path.name.endswith((".bak", ".tmp"))
    ]
    # 按 POSIX 相对路径比较：Path 排序在 Windows 上大小写不敏感，结果不一致。
    files.sort(key=lambda path: path.relative_to(skill_dir).as_posix())
    return files[:limit]


def _skill_rel_files(skill_dir: Path) -> list[str]:
    return [path.relative_to(skill_dir).as_posix() for path in _skill_files(skill_dir)]


def _iter_candidate_dirs(username: str) -> list[tuple[Path, str]]:
    """候选技能目录：用户安装优先，其后为内置（同名时用户版本覆盖）。"""
    candidates: list[tuple[Path, str]] = []
    user_root = store.skills_root(username)
    if user_root.exists():
        candidates.extend(
            (path, "user")
            for path in sorted(user_root.iterdir())
            if path.is_dir() and not path.name.startswith(".")
        )
    builtin_root = builtin_skills_root()
    if builtin_root.exists():
        candidates.extend(
            (path, "builtin")
            for path in sorted(builtin_root.iterdir())
            if path.is_dir()
            and not path.name.startswith(".")
            and not path.name.startswith("_")
            and _is_builtin_agent_skill(path)
        )
    return candidates


def _skill_location(username: str, name: str) -> tuple[Path, str] | None:
    for skill_dir, source in _iter_candidate_dirs(username):
        if skill_dir.name == name and (skill_dir / SKILL_FILENAME).exists():
            return skill_dir, source
    return None


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
        "title": parsed["title"] or parsed["name"],
        "description": parsed["description"],
        "path": str(skill_dir),
    }


def list_skills(username: str) -> list[dict[str, Any]]:
    """列出可用技能（内置 + 用户安装，含启停状态），坏目录跳过。"""
    disabled = disabled_skills(username)
    merged: dict[str, dict[str, Any]] = {}
    for skill_dir, source in _iter_candidate_dirs(username):
        meta = _read_skill_meta(skill_dir)
        if meta is None:
            continue
        meta["source"] = source
        meta["files"] = _skill_rel_files(skill_dir)
        meta["enabled"] = skill_dir.name not in disabled
        # 用户目录先遍历：同名时用户安装的版本覆盖内置版本。
        merged.setdefault(skill_dir.name, meta)
    return [merged[name] for name in sorted(merged)]


def get_skill(username: str, name: str) -> dict[str, Any] | None:
    """读技能详情（含正文，按注入上限截断）。"""
    resolved = resolve_skill(username, name)
    if not resolved:
        return None
    location = _skill_location(username, resolved)
    if location is None:
        return None
    skill_dir, source = location
    meta = _read_skill_meta(skill_dir)
    if meta is None:
        return None
    text = (skill_dir / SKILL_FILENAME).read_text(encoding="utf-8-sig", errors="replace")
    parsed = parse_skill_document(text, fallback_name=resolved)
    body = parsed["body"][:SKILL_BODY_PROMPT_MAX_CHARS]
    meta["source"] = source
    meta["files"] = _skill_rel_files(skill_dir)
    meta["body"] = body
    meta["truncated"] = len(parsed["body"]) > len(body)
    meta["enabled"] = resolved not in disabled_skills(username)
    return meta


def resolve_skill(username: str, reference: str) -> str | None:
    """把用户/模型给的技能引用解析成实际目录名（容忍大小写与写法差异）。"""
    raw = str(reference or "").strip()
    if not raw:
        return None
    candidates = [
        (skill_dir, source)
        for skill_dir, source in _iter_candidate_dirs(username)
        if (skill_dir / SKILL_FILENAME).exists()
    ]
    normalized = re.sub(r"\s+", "-", raw.lower())
    for skill_dir, _source in candidates:
        if skill_dir.name.lower() == normalized:
            return skill_dir.name
    for skill_dir, _source in candidates:
        meta = _read_skill_meta(skill_dir)
        if meta and str(meta.get("title") or "").strip().lower() == raw.lower():
            return skill_dir.name
    return None


def read_skill_body(
    username: str, reference: str
) -> tuple[str, str, str, bool] | None:
    """供 agent_load_skill 工具使用：返回 (目录名, description, 正文, 是否截断)。

    只允许加载已启用的技能；未启用/不存在返回 None。
    """
    resolved = resolve_skill(username, reference)
    if not resolved or resolved in disabled_skills(username):
        return None
    detail = get_skill(username, resolved)
    if detail is None:
        return None
    return (
        resolved,
        str(detail.get("description") or ""),
        str(detail.get("body") or ""),
        bool(detail.get("truncated")),
    )


def skill_files(username: str, reference: str) -> list[str]:
    """已启用技能内的相对文件清单（供 agent_load_skill 提示可读的附件）。"""
    resolved = resolve_skill(username, reference)
    if not resolved or resolved in disabled_skills(username):
        return []
    location = _skill_location(username, resolved)
    return _skill_rel_files(location[0]) if location else []


def read_skill_file(
    username: str, reference: str, relative: str
) -> tuple[str, str] | None:
    """读取技能目录内的一个附件，返回 (目录名, 文本)。

    路径按技能目录解析并校验真实路径，阻止 ``..`` 与符号链接逃逸。
    """
    resolved = resolve_skill(username, reference)
    if not resolved or resolved in disabled_skills(username):
        return None
    location = _skill_location(username, resolved)
    if location is None:
        return None
    skill_dir = location[0]
    pure = PurePosixPath(str(relative or "").replace("\\", "/"))
    if not pure.parts or pure.is_absolute() or ".." in pure.parts:
        raise SkillError("技能文件路径非法")
    target = (skill_dir / Path(*pure.parts)).resolve()
    if not target.is_relative_to(skill_dir.resolve()) or not target.is_file():
        return None
    if target.stat().st_size > SKILL_TEXT_MAX_BYTES:
        raise SkillError("技能附件过大（上限 256KB）")
    try:
        text = target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    return resolved, text


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
        "可用技能（用 agent_load_skill 工具按需加载全文，加载后按技能指引工作）：\n"
        f"{joined}"
    )


def install_skill(username: str, *, filename: str, content: bytes) -> dict[str, Any]:
    """安装/更新一个技能。filename 以 .zip 结尾按 zip 包处理，否则按单个 markdown 文件。"""
    if not content:
        raise SkillError("上传内容为空")
    source_name = Path(str(filename or "")).name
    if source_name.lower().endswith(".zip"):
        zip_fallback = Path(source_name).stem
        if zip_fallback.lower() == SKILL_FILENAME[:-3].lower():
            zip_fallback = ""
        return _install_from_zip(username, content, fallback_name=zip_fallback)
    if len(content) > SKILL_TEXT_MAX_BYTES:
        raise SkillTooLargeError(f"技能文件过大（上限 {SKILL_TEXT_MAX_BYTES // 1024}KB）")
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
    # 重新安装时只归一化 name 行，title/agent/requires 等其他 frontmatter 字段原样保留。
    meta, _body = _parse_frontmatter(text)
    normalized_text = (
        _with_normalized_name(text, name)
        if str(meta.get("name") or "") != name
        else text
    )
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


def _safe_member_path(filename: str) -> PurePosixPath:
    """zip 成员路径归一化 + zip-slip 校验（反斜杠也按分隔符处理）。"""
    pure = PurePosixPath(str(filename or "").replace("\\", "/"))
    if (
        not pure.parts
        or pure.is_absolute()
        or ".." in pure.parts
        or any(":" in part for part in pure.parts)
    ):
        raise SkillError(f"技能包包含非法路径：{filename}")
    return pure


def _install_from_zip(
    username: str, data: bytes, *, fallback_name: str = ""
) -> dict[str, Any]:
    if len(data) > ZIP_MAX_BYTES:
        raise SkillTooLargeError(f"技能包过大（上限 {ZIP_MAX_BYTES // 1024 // 1024}MB）")
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
            raise SkillTooLargeError("技能包解压后体积过大")
        parsed_members = [(item, _safe_member_path(item.filename)) for item in members]
        candidates = [
            (item, pure)
            for item, pure in parsed_members
            if pure.name.lower() == SKILL_FILENAME.lower()
        ]
        if not candidates:
            raise SkillError("技能包中缺少 SKILL.md")
        # 优先根目录（层级最浅）的 SKILL.md。
        candidates.sort(key=lambda pair: (len(pair[1].parts), pair[0].filename))
        chosen, chosen_path = candidates[0]
        prefix = chosen_path.parent
        prefix_str = "" if str(prefix) in ("", ".") else prefix.as_posix()
        try:
            document_text = archive.read(chosen).decode("utf-8-sig", errors="replace")
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
            raise SkillError("技能包中的 SKILL.md 无法读取（文件可能损坏或被加密）") from exc
        # 无 frontmatter name 时依次退回顶层目录名、zip 文件名。
        fallback = prefix.name if prefix_str else fallback_name
        parsed = parse_skill_document(document_text, fallback_name=fallback)
        if not str(parsed["name"] or "").strip():
            raise SkillError(
                "技能缺少 name：请在 SKILL.md 头部 frontmatter 里写 name，"
                "或用「技能名.zip」命名安装包"
            )
        name = _normalize_name(str(parsed["name"]))
        meta, _body = _parse_frontmatter(document_text)
        normalized_document = (
            _with_normalized_name(document_text, name)
            if str(meta.get("name") or "") != name
            else document_text
        )
        target_dir = _skill_dir(username, name)
        existed = target_dir.exists()
        skills_root = store.skills_root(username)
        # 先解压到隐藏临时目录，全部成功后再整目录换入：损坏的包不会毁掉已装版本。
        staging = Path(tempfile.mkdtemp(prefix=f".install-{name}-", dir=skills_root))
        try:
            extracted_bytes = 0
            try:
                for item, pure in parsed_members:
                    if prefix_str:
                        if not pure.as_posix().startswith(prefix_str + "/"):
                            continue  # 技能目录之外的成员忽略
                        relative = pure.relative_to(prefix)
                    else:
                        relative = pure
                    if not relative.parts:
                        continue
                    destination = staging.joinpath(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    payload = archive.read(item)
                    extracted_bytes += len(payload)
                    if extracted_bytes > ZIP_MAX_TOTAL_BYTES:
                        raise SkillTooLargeError("技能包解压后体积过大")
                    destination.write_bytes(payload)
            except SkillError:
                raise
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
                raise SkillError("技能包解压失败（文件可能损坏或被加密）") from exc
            document_path = staging / chosen_path.name
            document_path.write_text(
                normalized_document
                if normalized_document.endswith("\n")
                else normalized_document + "\n",
                encoding="utf-8",
            )
            backup: Path | None = None
            try:
                if target_dir.exists():
                    backup = Path(
                        tempfile.mkdtemp(prefix=f".backup-{name}-", dir=skills_root)
                    )
                    backup.rmdir()
                    target_dir.rename(backup)
                staging.rename(target_dir)
            except OSError as exc:
                if backup is not None and backup.exists() and not target_dir.exists():
                    backup.rename(target_dir)
                raise SkillError("技能包安装失败（无法替换技能目录）") from exc
            if backup is not None:
                shutil.rmtree(backup, ignore_errors=True)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    _set_enabled_internal(username, name, True)
    logger.info("agent skill installed from zip: name=%s updated=%s", name, existed)
    return {"name": name, "updated": existed}


def delete_skill(username: str, name: str) -> bool:
    """删除用户安装的技能（内置技能只读，返回 False）。"""
    resolved = resolve_skill(username, name)
    if not resolved:
        return False
    location = _skill_location(username, resolved)
    if location is None or location[1] != "user":
        return False
    target_dir = location[0]
    shutil.rmtree(target_dir)
    disabled = disabled_skills(username)
    if resolved in disabled:
        disabled.discard(resolved)
        _write_disabled(username, disabled)
    logger.info("agent skill deleted: name=%s", resolved)
    return True


def skill_zip_bytes(username: str, reference: str) -> tuple[str, bytes]:
    """把一个技能目录打成 zip（顶层目录 = 技能名），供界面下载。

    包结构与 ``install_skill`` 接受的 zip 一致：重新安装即可还原（含附件）。
    """
    resolved = resolve_skill(username, reference)
    if not resolved:
        raise SkillNotFoundError("技能不存在")
    location = _skill_location(username, resolved)
    if location is None:
        raise SkillNotFoundError("技能不存在")
    skill_dir = location[0]
    # 导出包要能被 install_skill 原样装回，文件数必须与安装上限一致。
    files = _skill_files(skill_dir, limit=ZIP_MAX_MEMBERS + 1)
    if not files:
        raise SkillError("技能目录为空")
    if len(files) > ZIP_MAX_MEMBERS:
        raise SkillTooLargeError(f"技能文件数过多（上限 {ZIP_MAX_MEMBERS}），无法导出")
    root = skill_dir.resolve()
    buffer = io.BytesIO()
    total_bytes = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            try:
                real = path.resolve()
                if not real.is_relative_to(root):
                    continue  # 不跟随指向目录外的符号链接
                total_bytes += real.stat().st_size
            except OSError:
                continue
            if total_bytes > ZIP_MAX_TOTAL_BYTES:
                raise SkillTooLargeError("技能包体积过大")
            archive.write(real, f"{resolved}/{real.relative_to(root).as_posix()}")
    payload = buffer.getvalue()
    if not payload:
        raise SkillError("技能目录为空")
    return resolved, payload


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