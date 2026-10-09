"""[aipdd fork delta] 「不给客户装浏览器」依赖棘轮：抖音文案提取只经宿主真实 Chrome CDP。

用户口径（明确要求两次）：抖音文案提取 / 音视频提取文案**不得引入 Playwright**，也**不得在
镜像里下载或安装任何浏览器**；必须复用客户本机已经安装、以调试端口启动的真实 Chrome，
与内置「Chrome MCP」（chrome-devtools-mcp）同一来源 ``ST_CHROME_CDP_URL``。

本文件把这句口径钉成可执行契约：依赖清单（Python / 前端 / 锁文件）、镜像构建脚本、以及
执行叶源码都必须保持「零浏览器安装」。注释里允许出现「不引入 Playwright」这类否定式
说明，因此检查只针对**安装/导入动作**，不针对措辞。
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 任何“把浏览器自动化塞回依赖”的包名特征。
_BROWSER_PACKAGES = re.compile(r"playwright|puppeteer|selenium|pyppeteer|chromedriver", re.I)


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _declared_python_requirements() -> list[str]:
    data = _pyproject()
    reqs: list[str] = list(data.get("project", {}).get("dependencies", []) or [])
    for extra in (data.get("project", {}).get("optional-dependencies", {}) or {}).values():
        reqs.extend(extra)
    for group in (data.get("dependency-groups", {}) or {}).values():
        for item in group:
            if isinstance(item, str):
                reqs.append(item)
            elif isinstance(item, dict):
                reqs.append(str(item.get("name", "")))
    return reqs


def test_python_dependencies_declare_no_browser_automation() -> None:
    offenders = [req for req in _declared_python_requirements() if _BROWSER_PACKAGES.search(req)]
    assert not offenders, f"browser automation must not be a Python dependency: {offenders}"


def test_uv_lock_resolves_no_browser_packages() -> None:
    with (ROOT / "uv.lock").open("rb") as fh:
        lock = tomllib.load(fh)
    names = {str(pkg.get("name", "")).lower() for pkg in lock.get("package", [])}
    offenders = sorted(name for name in names if _BROWSER_PACKAGES.fullmatch(name))
    assert not offenders, f"uv.lock resolved browser automation packages: {offenders}"


def test_frontend_manifest_has_no_browser_dependencies() -> None:
    manifest = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    deps: list[str] = []
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        deps.extend((manifest.get(key) or {}).keys())
    offenders = [name for name in deps if _BROWSER_PACKAGES.search(name)]
    assert not offenders, f"frontend must not depend on browser automation: {offenders}"


def test_dockerfiles_never_install_a_browser() -> None:
    # 只拦「安装动作」：注释里说明「不执行 playwright install」是允许的。
    install_line = re.compile(
        r"(?:pip3?\s+install|uv\s+pip\s+install|apt-get\s+install|apk\s+add|npm\s+(?:i|install))"
        r"[^\n#]*(playwright|puppeteer|chromium|chromedriver|google-chrome|selenium)",
        re.I,
    )
    checked = ["Dockerfile", "Dockerfile.cn", "frontend/Dockerfile"]
    offenders: list[str] = []
    for rel in checked:
        path = ROOT / rel
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("RUN echo"):
                continue
            if install_line.search(stripped):
                offenders.append(f"{rel}:{number}: {stripped}")
    assert not offenders, f"images must not bundle a browser: {offenders}"


def test_douyin_and_chrome_cdp_source_import_no_playwright() -> None:
    sources = [
        ROOT / "src" / "novelvideo" / "freezone" / "douyin_copywrite.py",
        ROOT / "src" / "novelvideo" / "freezone" / "chrome_cdp.py",
        ROOT / "src" / "novelvideo" / "utils" / "chrome_cdp.py",
    ]
    imported = re.compile(r"^\s*(?:import|from)\s+(?:playwright|selenium|pyppeteer)", re.I)
    for path in sources:
        text = path.read_text(encoding="utf-8")
        hits = [line for line in text.splitlines() if imported.match(line)]
        assert not hits, f"{path.name} must use bare Chrome CDP, not {hits}"


def test_host_chrome_cdp_is_the_shared_app_contract() -> None:
    """应用侧唯一契约是 ST_CHROME_CDP_URL：抖音拦截、Chrome MCP、compose 三处同源。"""
    douyin = (ROOT / "src" / "novelvideo" / "freezone" / "douyin_copywrite.py").read_text(
        encoding="utf-8"
    )
    agent_mcp = (ROOT / "src" / "novelvideo" / "agent" / "mcp.py").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.aipdd.yml").read_text(encoding="utf-8")
    assert "ST_CHROME_CDP_URL" in douyin
    assert 'CHROME_CDP_URL_ENV = "ST_CHROME_CDP_URL"' in agent_mcp
    assert "ST_CHROME_CDP_URL" in compose
    # 宿主机 Chrome 助手脚本同样只「拉起本机已装 Chrome」，不下载任何东西。
    helper = (ROOT / "scripts" / "douyin-cdp-chrome.sh").read_text(encoding="utf-8")
    assert "DOUYIN_CDP_BIN" in helper and "--remote-debugging-port" in helper
    assert not re.search(r"(?:curl|wget|npm\s+install|pip3?\s+install)", helper)
