#!/usr/bin/env python3
"""导入守栏（import-lint，硬零）。

下列模块不属于本仓库，任何导入都不得出现。硬零：命中即失败，无 baseline 留存。

扫描对象（AST，覆盖顶层/惰性导入）：
  - src/novelvideo/**/*.py 的静态 import / from-import
  - importlib.import_module(...) / __import__(...) 的字面量字符串
  - pyproject.toml 里所有字符串（entry-points / scripts 等元数据）

禁止的模块前缀：
  - novelvideo_ee
  - supertale_admin
  - novelvideo.control_plane（含 `from novelvideo import control_plane`）

用法：
    python scripts/lint_ce_imports.py            # 扫全仓（CI 默认）
    python scripts/lint_ce_imports.py --root .   # 指定根

命中任一禁止导入 → 退出码 1，并打印 file:line 与导入内容。
"""
from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 禁止的模块前缀：命中即违规。规则为「等于前缀」或「以 前缀. 开头」。
FORBIDDEN_PREFIXES = (
    "novelvideo_ee",
    "supertale_admin",
    "novelvideo.control_plane",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str  # import | from | import_module | __import__ | metadata
    module: str

    def describe(self) -> str:
        return f"{self.path}:{self.line}: {self.kind} {self.module}"


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_PREFIXES
    )


def _iter_python_files(root: Path) -> list[Path]:
    src_root = root / "src" / "novelvideo"
    if not src_root.exists():
        return []
    return sorted(
        path
        for path in src_root.rglob("*.py")
        if "__pycache__" not in path.relative_to(root).parts
    )


def _literal_string_arg(node: ast.Call) -> str | None:
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return node.args[0].value
    return None


def _dynamic_import_kind(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name) and func.id in {"import_module", "__import__"}:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in {"import_module", "__import__"}:
        return func.attr
    return None


def _scan_python(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in _iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise SystemExit(f"无法解析 {path}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden(alias.name):
                        findings.append(Finding(rel, node.lineno, "import", alias.name))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _is_forbidden(module):
                    findings.append(Finding(rel, node.lineno, "from", module))
                elif module == "novelvideo":
                    for alias in node.names:
                        if alias.name == "control_plane":
                            findings.append(
                                Finding(rel, node.lineno, "from", "novelvideo.control_plane")
                            )
            elif isinstance(node, ast.Call):
                kind = _dynamic_import_kind(node)
                target = _literal_string_arg(node) if kind else None
                if kind and target and _is_forbidden(target):
                    findings.append(Finding(rel, node.lineno, kind, target))
    return findings


def _iter_toml_strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _iter_toml_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_toml_strings(item)


def _line_for_text(path: Path, text: str) -> int:
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if text in line:
            return index
    return 1


def _scan_metadata(root: Path) -> list[Finding]:
    path = root / "pyproject.toml"
    if not path.exists():
        return []
    try:
        metadata = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"无法解析 {path}: {exc}") from exc
    findings: list[Finding] = []
    for value in _iter_toml_strings(metadata):
        module = value.split(":", 1)[0]  # entry-point 形如 "pkg.mod:obj"
        if _is_forbidden(module):
            findings.append(
                Finding("pyproject.toml", _line_for_text(path, value), "metadata", module)
            )
    return findings


def scan(root: Path = REPO_ROOT) -> list[Finding]:
    root = root.resolve()
    findings = _scan_python(root) + _scan_metadata(root)
    return sorted(findings, key=lambda f: (f.path, f.line, f.module))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CE import-lint（硬零）")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    findings = scan(args.root)
    if not findings:
        print("✓ import-lint：无禁止导入。")
        return 0

    print(f"✖ 命中 {len(findings)} 处禁止导入：", file=sys.stderr)
    for finding in findings:
        print(f"  {finding.describe()}", file=sys.stderr)
    print(
        "\n禁止前缀："
        + " / ".join(FORBIDDEN_PREFIXES)
        + "\n相关能力只能通过 novelvideo.ports.* 抽象消费，不得直接 import。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
