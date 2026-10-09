#!/usr/bin/env python3
"""[aipdd fork delta] Windows 一键安装/启动脚本的静态守卫。

这些产物只能在 Windows 上真正执行，而开发机是 macOS，所以把「只在运行时暴露」的坑
前移成纯文本判据（tests/test_windows_launchers.py 会在每次 pytest 里跑它）：

  1) 编码与行尾（最容易在后续编辑中被悄悄破坏、且直接决定用户能不能看懂）：
     .ps1 必须 UTF-8 **带 BOM** + CRLF —— Windows PowerShell 5.1 读无 BOM 的 UTF-8 会按
        本地代码页解析，中文提示全成乱码；
     .bat 必须纯 ASCII + CRLF、**不带** BOM —— cmd.exe 按控制台代码页逐行解析批处理，
        中文会乱码，而开头的 BOM 会被当成第一条命令的内容。
  2) 命令名悬空：代码态里形如 `Verb-Noun` 且动词属于常见动词表的调用，必须要么在
     scripts/windows_*.ps1 里定义过，要么在内置 cmdlet 白名单里。
  3) 括号栈配平（`{}` `()` `[]`），只在代码态计数。
  4) .bat ↔ .ps1 的路径引用真实存在；退出码契约（Wait-Here 用 exit 3，bat 用
     `if %RC% GEQ 3` 认「脚本已自报并等过回车」）两侧一致；bat 必须带
     -ExecutionPolicy Bypass 和 cd /d "%~dp0"（compose 读 .env 需要仓库根做 cwd）。

为什么自带一个小扫描器而不是「成对删掉引号里的内容」：脚本里既有写在多行提示字符串里的
`1) 2) 3)`，也有 `'"'` 这种孤立引号字符，朴素配对会从那一刻起全面错位（实测误报 3 处）。
状态机按 PowerShell 规则处理：`''`/`""` 是转义、反引号是转义符、`#` 到行尾是注释、
`<# ... #>` 是块注释，并且普通字符串**允许跨行**。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

PS_FILES = ("scripts/windows_common.ps1", "scripts/windows_setup.ps1", "scripts/windows_start.ps1")
BAT_FILES = ("安装环境-Setup.bat", "启动系统-Start.bat")

# 本项目 ps1 用到的内置 cmdlet 白名单（以 Windows PowerShell 5.1 可用的为准）。
BUILTIN = {
    "Add-Member", "Clear-Host", "Compare-Object", "Copy-Item", "Get-Command", "Get-Content",
    "Get-Date", "Get-Host", "Get-Member", "Get-NetTCPConnection", "Get-PSDrive", "Get-Process",
    "Group-Object", "Invoke-Expression", "Invoke-WebRequest", "Join-Path", "New-Item",
    "Out-Host", "Out-Null", "Read-Host", "Remove-Item", "Select-Object", "Select-String",
    "Set-Location", "Set-Variable", "Sort-Object", "Split-Path", "Start-Process", "Start-Sleep",
    "Stop-Process", "Test-Path", "Where-Object", "Write-Host", "Write-Output", "Write-Warning",
}
VERBS = {"Write", "Get", "Test", "Wait", "Invoke", "Add", "Set", "Start", "Copy", "New",
         "Select", "Remove", "Clear", "Sort", "Group", "Stop", "Out"}
NAME_RE = re.compile(r"[A-Z][a-z]+-[A-Z][A-Za-z]+")
FUNC_DEF_RE = re.compile(r"^\s*function\s+([A-Z][a-z]+-[A-Z][A-Za-z]+)", re.M)
BOM = b"\xef\xbb\xbf"
PAIRS = {"{": "}", "(": ")", "[": "]"}
CLOSER_TO_OPENER = {v: k for k, v in PAIRS.items()}


def extract_code(text: str) -> tuple[list[str], str | None]:
    """把脚本变成「只含代码」的逐行文本：字符串与注释内容用空格占位。

    返回 (行列表, 文件结束时仍未闭合的语境)；后者非空时必须报出来，否则整段被当字符串
    吞掉会让括号检查静默通过。
    """
    out: list[str] = []
    cur: list[str] = []
    in_s = in_d = in_block = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "\n":
            out.append("".join(cur))
            cur = []
            i += 1
            continue
        if in_block:
            if ch == "#" and nxt == ">":
                in_block = False
                i += 2
                continue
            cur.append(" ")
            i += 1
            continue
        if in_s or in_d:
            quote = "'" if in_s else '"'
            if in_d and ch == "`":
                cur.append("  ")
                i += 2
                continue
            if ch == quote:
                if nxt == quote:   # '' / "" = 字面量引号，仍在串内
                    i += 2
                    continue
                in_s = in_d = False
            cur.append(" ")
            i += 1
            continue
        if ch == "'":
            in_s = True
            cur.append(" ")
            i += 1
            continue
        if ch == '"':
            in_d = True
            cur.append(" ")
            i += 1
            continue
        if ch == "#":
            if nxt == "<":
                in_block = True
                cur.append("  ")
                i += 2
                continue
            while i < n and text[i] != "\n":   # 行注释吃到行尾
                cur.append(" ")
                i += 1
            continue
        if ch == "`":
            cur.append("  ")
            i += 2
            continue
        cur.append(ch)
        i += 1
    if cur:
        out.append("".join(cur))
    leftover = "双引号" if in_d else ("单引号" if in_s else ("块注释" if in_block else None))
    return out, leftover


def _check_paren_stack(lines: list[str], label: str, problems: list[str]) -> None:
    stack: list[tuple[str, int]] = []
    for lineno, line in enumerate(lines, 1):
        for ch in line:
            if ch in PAIRS:
                stack.append((ch, lineno))
            elif ch in CLOSER_TO_OPENER:
                opener = CLOSER_TO_OPENER[ch]
                if not stack or stack[-1][0] != opener:
                    problems.append(
                        f"{label}:{lineno} 出现 {ch}，没有对应的 {opener}"
                        f"（栈顶={stack[-1] if stack else '空'}）"
                    )
                    return
                stack.pop()
    for opener, lineno in stack:
        problems.append(f"{label}:{lineno} 的 {opener} 没有闭合")


def _check_command_names(lines: list[str], defined: set[str], label: str, problems: list[str]) -> None:
    for lineno, line in enumerate(lines, 1):
        for name in NAME_RE.findall(line):
            if name.split("-", 1)[0] not in VERBS:
                continue
            if name in defined or name in BUILTIN:
                continue
            problems.append(f"{label}:{lineno} 调用了 {name}，既没定义过也不在 cmdlet 白名单里")


def _check_encoding(path: pathlib.Path, *, want_bom: bool, problems: list[str]) -> None:
    raw = path.read_bytes()
    has_bom = raw.startswith(BOM)
    if has_bom != want_bom:
        problems.append(f"{path.name}: BOM 期望{'有' if want_bom else '无'}，实际{'有' if has_bom else '无'}")
    body = raw[len(BOM):] if has_bom else raw
    if lf_only := body.count(b"\n") - body.count(b"\r\n"):
        problems.append(f"{path.name}: 有 {lf_only} 个裸 LF，Windows 侧要求 CRLF")
    if not body.count(b"\r\n"):
        problems.append(f"{path.name}: 没有 CRLF 行尾")
    text = body.decode("utf-8", errors="replace")
    if not want_bom:
        bad = sorted({c for c in text if ord(c) > 0x7F})
        if bad:
            problems.append(f"{path.name}: .bat 含非 ASCII 字节 {bad[:4]}（cmd 按代码页解析 → 乱码）")


def collect_problems(root: pathlib.Path | None = None) -> list[str]:
    root = root or ROOT
    problems: list[str] = []
    sources: dict[str, str] = {}
    codes: dict[str, list[str]] = {}
    for rel in PS_FILES:
        path = root / rel
        if not path.exists():
            problems.append(f"缺少 {rel}")
            continue
        sources[rel] = path.read_bytes().decode("utf-8-sig")
        lines, leftover = extract_code(sources[rel])
        codes[rel] = lines
        if leftover:
            problems.append(f"{rel}: 文件结束时仍在 {leftover} 里（少一个引号/闭合）")

    defined: set[str] = set()
    for text in sources.values():
        defined |= set(FUNC_DEF_RE.findall(text))

    for rel, lines in codes.items():
        _check_command_names(lines, defined, rel, problems)
        _check_paren_stack(lines, rel, problems)
        _check_encoding(root / rel, want_bom=True, problems=problems)
    for rel in BAT_FILES:
        path = root / rel
        if not path.exists():
            problems.append(f"缺少 {rel}")
            continue
        _check_encoding(path, want_bom=False, problems=problems)
        text = path.read_bytes().decode("utf-8")
        if "GEQ 3" not in text:
            problems.append(f"{rel}: 缺 `if %RC% GEQ 3`，与 Wait-Here 的 exit 3 契约不一致")
        if "-ExecutionPolicy Bypass" not in text:
            problems.append(f"{rel}: 没带 -ExecutionPolicy Bypass，首次双击会被默认策略拦住")
        if 'cd /d "%~dp0"' not in text:
            problems.append(f"{rel}: 没 cd 到脚本所在目录（compose 读 .env 需要仓库根做 cwd）")
        for ref in re.findall(r"[A-Za-z_][A-Za-z0-9_./\\-]*\.ps1", text.replace("%~dp0", "")):
            # `%~dp0` 是 bat 的「本文件所在目录」，上面先摘掉它再匹配，否则会把 dp0 当目录名。
            if not (root / ref.replace("\\", "/")).exists():
                problems.append(f"{rel}: 引用了不存在的 {ref}")
    return problems


def main() -> int:
    problems = collect_problems()
    print(f"检查 {len(PS_FILES)} 个 .ps1 + {len(BAT_FILES)} 个 .bat")
    if problems:
        print("\n发现问题：")
        for item in problems:
            print("  -", item)
        return 1
    print("Windows 一键脚本静态守卫通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
