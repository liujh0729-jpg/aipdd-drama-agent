#!/usr/bin/env python3
"""拦截「会把外部更新带进产物」的引用。

两类需要拦的东西：

1. 上游 DramaClaw 的下载站 / CDN（``*.cdnfg.com``）。登录页素材、安装包清单、
   更新说明都已改为本项目仓库或 Release 提供，任何一处再指回去，上游换文件就会
   悄悄改变我们的安装页甚至装出别的包。
2. 构建期的 ``latest`` 解析：``releases/latest``、``/latest/download``、
   ``LATEST_RELEASE_STABLE``，以及不带版本号的全局安装。上游一旦发版，同一条
   构建命令就会产出不同的镜像/安装包。
3. README 里的图片/视频外链（shields.io 徽章、第三方图表、别的 CDN）。这些资源
   不在本仓库里，对方下线或改图，README 就跟着变样/断图。素材要么随仓库提交，
   要么挂在本项目自己的 Release 上。

用法::

    python scripts/check_external_hosts.py           # 扫描，违规即非 0 退出
    python scripts/check_external_hosts.py --list    # 打印扫描范围
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 只查「会进产物或决定产物」的路径；文档、随包的第三方源码、历史需求文档不算。
SCAN_DIRS = ("src/novelvideo", "frontend/src", "frontend/public", "desktop/src-tauri/src", "desktop/scripts", "scripts", "services", ".github/workflows")
SCAN_FILES = (
    "frontend/index.html",
    "frontend/Dockerfile",
    "Dockerfile",
    "Dockerfile.cn",
    "desktop/src-tauri/resources/nginx.conf",
    "frontend/docker/nginx.conf.template",
)
# 随包的第三方运行时（hermes site-packages、打包前端 dist）、测试夹具不做源码级审查。
SKIP_PARTS = (
    "/node_modules/",
    "/dist/",
    "/hermes/site-packages/",
    "/.venv/",
    "/__pycache__/",
    "/__tests__/",
    "/src-tauri/resources/app/",
)
# 「安装命令写没写版本」只在**决定我们自己产物**的构建脚本里检查；
# 面向用户环境的安装提示（src/、frontend/src 里的文案）不在此列。
BUILD_FILE_PREFIXES = ("desktop/scripts/", "scripts/", ".github/workflows/")
BUILD_FILE_NAMES = ("Dockerfile", "Dockerfile.cn", "frontend/Dockerfile")
# 构建期需要钉版本的 requirements：容器镜像直接装。faceswap 是用户机器上按需安装的
# 可选边车（insightface/onnx 组合对版本极敏感），不纳入本检查。
PINNED_REQUIREMENTS = ("services/vectcut/requirements.txt", "services/hyperframes-renderer/requirements.txt")
# 本项目自己的 Release 链接是「我们主动更新」的入口，不算上游联动。
OWN_REPO = "liujh0729-jpg/aipdd-drama-agent"

# 二进制/媒体不参与文本扫描。
TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".jsx", ".json", ".html", ".css",
    ".conf", ".template", ".sh", ".ps1", ".yml", ".yaml", ".toml", ".md",
    ".txt", ".env", "",
}

UPSTREAM_HOST = re.compile(r"[\w.-]*cdnfg\.com", re.IGNORECASE)

# 只看代码行；注释里说明「曾经指向哪里」不违规。
COMMENT_ONLY = re.compile(r"^\s*(#|//|/\*|\*|<!--)")

LATEST_PATTERNS = (
    (re.compile(r"releases/latest"), "release 链接用了 releases/latest（会跟着上游联动）"),
    (re.compile(r"/latest/download"), "资产链接用了 /latest/download"),
    (re.compile(r"\bgit (?:clone|fetch|pull)\b.*\bbranch\b"), "按分支名拉取依赖"),
)
# 只可能出现在构建脚本里的「按接口取最新版」。
BUILD_ONLY_PATTERNS = (
    (re.compile(r"LATEST_RELEASE"), "版本来自 LATEST_RELEASE* 接口"),
)

# `npm install -g x` / `npm i -g x` 必须带 @版本。
NPM_GLOBAL = re.compile(r"\bnpm (?:install|i)\b[^#\n]*?\s-g\s+([^\s;&|]+)")
# `uv tool install 'x'`、`pip install uv` 必须带 ==版本。
UV_TOOL = re.compile(r"\buv tool install\s+'?([^\s';&|]+)'?")
PIP_UV = re.compile(r"\bpip install\b[^#\n]*?\buv\b(?![=<>])")

# README 里的图/视频：Markdown `![](...)` 与 `<img>/<source>/<video>`，只允许
# 仓库内相对路径或本项目 Release 上的直链。
README_FILES = ("README.md", "readme/README_*.md")
MD_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
HTML_MEDIA = re.compile(r"<(?:img|source|video)\b[^>]*>", re.IGNORECASE)
MEDIA_ATTR = re.compile(r"\b(?:src|srcset)=\"([^\"]+)\"", re.IGNORECASE)


def tracked_files() -> list[Path]:
    if (ROOT / ".git").exists():
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        ).stdout
        files = [ROOT / p for p in out.decode("utf-8", "replace").split("\0") if p]
    else:
        files = [p for p in ROOT.rglob("*") if p.is_file()]
    picked: list[Path] = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if any(part in f"/{rel}" for part in SKIP_PARTS):
            continue
        in_dir = any(rel == d or rel.startswith(d + "/") for d in SCAN_DIRS)
        if not (in_dir or rel in SCAN_FILES):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        picked.append(path)
    return picked


def check_file(path: Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    is_build = rel.startswith(BUILD_FILE_PREFIXES) or rel in BUILD_FILE_NAMES
    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if COMMENT_ONLY.match(line):
            continue
        if UPSTREAM_HOST.search(line):
            problems.append(f"{rel}:{lineno}: 引用了上游 CDN 域名：{line.strip()}")
        own = OWN_REPO in line
        for pattern, reason in (LATEST_PATTERNS + BUILD_ONLY_PATTERNS if is_build else LATEST_PATTERNS):
            if own:
                break
            if pattern.search(line):
                problems.append(f"{rel}:{lineno}: {reason}：{line.strip()}")
        if not is_build:
            continue
        for match in NPM_GLOBAL.finditer(line):
            spec = match.group(1)
            if "@" not in spec.split("/")[-1]:
                problems.append(f"{rel}:{lineno}: 全局 npm 安装未钉版本：{spec}")
        for match in UV_TOOL.finditer(line):
            spec = match.group(1)
            if "==" not in spec and "@" not in spec:
                problems.append(f"{rel}:{lineno}: uv tool install 未钉版本：{spec}")
        if PIP_UV.search(line) and "uv==" not in line:
            problems.append(f"{rel}:{lineno}: pip 安装 uv 未钉版本：{line.strip()}")
    return problems


def check_readme(path: Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    problems: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        refs = list(MD_IMAGE.findall(line))
        for tag in HTML_MEDIA.findall(line):
            refs.extend(MEDIA_ATTR.findall(tag))
        for ref in refs:
            for url in (item.strip().split(" ")[0] for item in ref.split(",")):
                if not url.startswith(("http://", "https://")) or OWN_REPO in url:
                    continue
                problems.append(f"{rel}:{lineno}: README 图片/视频引用外部资源：{url}")
    return problems


def check_requirements(path: Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    problems: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        spec = line.split("#", 1)[0].strip()
        if not spec:
            continue
        if not re.search(r"==|@\s*https?://", spec):
            problems.append(f"{rel}:{lineno}: 依赖未钉版本：{spec}")
    return problems


def main() -> int:
    files = tracked_files()
    if "--list" in sys.argv:
        for path in files:
            print(path.relative_to(ROOT).as_posix())
        return 0

    problems: list[str] = []
    for path in files:
        problems.extend(check_file(path))
        if path.relative_to(ROOT).as_posix() in PINNED_REQUIREMENTS:
            problems.extend(check_requirements(path))
    for pattern in README_FILES:
        for path in sorted(ROOT.glob(pattern)):
            if path.is_file():
                problems.extend(check_readme(path))

    if problems:
        print("发现会把外部更新带进产物的引用：", file=sys.stderr)
        for item in problems:
            print(f"  {item}", file=sys.stderr)
        return 1
    print(f"外部依赖检查通过（{len(files)} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
