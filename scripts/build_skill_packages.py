# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 生成项目 SKILL 包：刷新 MCP 工具索引 → 校验工具名 → 打 zip。

用法：
    uv run python scripts/build_skill_packages.py            # 全部技能
    uv run python scripts/build_skill_packages.py --only short-drama-production
    uv run python scripts/build_skill_packages.py --no-zip   # 只刷新工具索引

产物：
- ``.hermes/skills/short-drama-production/references/tool-index.md``（从 MCP 注册表生成）
- ``dist/skills/<name>.zip``（每个技能目录一个包，包内顶层目录 = 技能名）
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / ".hermes" / "skills"
DIST_DIR = ROOT / "dist" / "skills"
TOOL_INDEX = SKILLS_DIR / "short-drama-production" / "references" / "tool-index.md"
# 旧 `dramaclaw_*` 名称也纳入校验：插件改名后仍留在技能文档里就会指向不存在的工具。
TOOL_NAME_RE = re.compile(r"\b(?:aipdd_drama|vectcut|dramaclaw)_[a-z0-9_]+\b")


def _tool_rows() -> list[tuple[str, str]]:
    sys.path.insert(0, str(ROOT / "src"))
    from novelvideo.chat import dramaclaw_tools

    rows: list[tuple[str, str]] = []
    for name, schema, _handler in dramaclaw_tools.TOOLS:
        fn = schema.get("function", schema) if isinstance(schema, dict) else {}
        description = str(fn.get("description") or "").strip()
        description = re.sub(r"\s+", " ", description).replace("|", "\\|")
        if len(description) > 220:
            description = description[:217].rstrip() + "…"
        rows.append((str(name), description))
    return sorted(rows)


def write_tool_index(rows: list[tuple[str, str]]) -> None:
    lines = [
        "# MCP 工具索引（自动生成）",
        "",
        "> 由 `scripts/build_skill_packages.py` 从 `novelvideo.chat.dramaclaw_tools.TOOLS`",
        "> 生成，请勿手改。共 %d 个工具。" % len(rows),
        "",
        "| 工具 | 说明 |",
        "|---|---|",
    ]
    lines.extend(f"| `{name}` | {description} |" for name, description in rows)
    TOOL_INDEX.parent.mkdir(parents=True, exist_ok=True)
    TOOL_INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_tool_references(rows: list[tuple[str, str]]) -> list[str]:
    known = {name for name, _ in rows}
    problems: list[str] = []
    for path in sorted(SKILLS_DIR.glob("*/**/*.md")):
        if path == TOOL_INDEX:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in sorted(set(TOOL_NAME_RE.findall(text))):
            if match not in known:
                problems.append(f"{path.relative_to(ROOT)}: 未知工具 {match}")
    return problems


def skill_dirs(only: list[str]) -> list[Path]:
    dirs = [
        path
        for path in sorted(SKILLS_DIR.iterdir())
        if path.is_dir() and not path.name.startswith("_") and (path / "SKILL.md").exists()
    ]
    if only:
        dirs = [path for path in dirs if path.name in set(only)]
    return dirs


def write_zip(skill_dir: Path) -> Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    target = DIST_DIR / f"{skill_dir.name}.zip"
    files = sorted(
        path
        for path in skill_dir.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name != ".DS_Store"
    )
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"{skill_dir.name}/{path.relative_to(skill_dir).as_posix()}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="生成项目 SKILL 包")
    parser.add_argument("--only", action="append", default=[], help="只处理指定技能（可重复）")
    parser.add_argument("--no-zip", action="store_true", help="只刷新工具索引，不打 zip")
    args = parser.parse_args()

    if not SKILLS_DIR.is_dir():
        print(f"技能目录不存在：{SKILLS_DIR}", file=sys.stderr)
        return 1

    rows = _tool_rows()
    write_tool_index(rows)
    print(f"工具索引：{TOOL_INDEX.relative_to(ROOT)}（{len(rows)} 个工具）")

    problems = validate_tool_references(rows)
    if problems:
        print("工具引用校验失败：", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 2

    if not args.no_zip:
        for skill_dir in skill_dirs(args.only):
            target = write_zip(skill_dir)
            print(f"技能包：{target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
