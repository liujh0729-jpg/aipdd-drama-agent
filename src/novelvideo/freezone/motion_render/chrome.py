# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 动效大片渲染：将随包/宿主的 Chrome 拉起来做逐帧截图。

设计口径沿用 `freezone/chrome_cdp.py`：**不装 Playwright、不加浏览器依赖**，
只用 `websockets` 裸走 CDP。差别在于那边连的是「客户本机已开着调试端口的
Chrome」，而这里要的是一条**可以被我们自己掌控时间轴**的浏览器 —— 逐帧
`window.render(t)` 必须独占一个实例，不能拿用户正在上网的窗口去截 900 张图。

于是三档来源，按优先级：

1. `ST_CHROME_CDP_URL`：运维已经自备一个专职调试实例（与抖音详情同一变量口径），
   直接连，不启动新进程；
2. `ST_CHROME_PATH`：指到某个可执行文件，由本模块拉起临时无头实例；
3. 桌面包内置：`<APP_RESOURCE_PATH>/[resources/]bin/chrome/chrome(.exe)`，即
   `desktop/src-tauri/resources/bin/chrome/` 那份 Chrome for Testing —— 装机用户
   不必自己装 Chrome；
4. PATH 上的 chrome/chromium（web 形态的容器/宿主机自装）。

内置与 PATH 档都走「本模块拉起 + 本模块收尾」：`--remote-debugging-port=0` 让
Chrome 自选端口，再从 `<user-data-dir>/DevToolsActivePort` 读回真实端口，避免多
任务并发时抢固定端口。收尾是 terminate 等 5 秒、没退就 kill，再删临时目录（先停后删，
见 `close_chrome`）—— 渲染任务被取消时不能留孤儿进程。临时无头实例没有用户数据，
不需要 `Browser.close` 那种优雅握手。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import platform
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CDP_URL_ENV = "ST_CHROME_CDP_URL"
CHROME_PATH_ENV = "ST_CHROME_PATH"
RESOURCE_PATH_ENV = "APP_RESOURCE_PATH"

# 等 Chrome 写出 DevToolsActivePort 的上限（秒）：冷启动 + 临时 profile 建目录。
_LAUNCH_TIMEOUT_SECONDS = 30.0
_PORT_POLL_SECONDS = 0.1

_IS_WINDOWS = platform.system() == "Windows"


class ChromeUnavailable(RuntimeError):
    """找不到可用的 Chrome —— 报错必须说清找过哪几档，否则用户无从下手。"""


def _chrome_file_names() -> tuple[str, ...]:
    if _IS_WINDOWS:
        return ("chrome.exe",)
    if platform.system() == "Darwin":
        return ("chrome", "chromium")
    return ("chrome", "chromium", "chromium-browser")


def _bundled_chrome() -> Optional[Path]:
    """桌面包内置的 Chrome；`APP_RESOURCE_PATH` 没注入（web 形态）时 None。

    打包后资源有两种落法（`resources/bin/...` 或直接 `bin/...`，取决于 Tauri
    版本对 glob 基路径的处理），与 `env.rs` 的 `rel()` 同一探法，两处都试。
    macOS 的 Chrome for Testing 是 `.app` 包，可执行文件在
    `<app>/Contents/MacOS/<name>`，所以 mac 上再往.app 里探一层。
    """
    resource = str(os.environ.get(RESOURCE_PATH_ENV) or "").strip()
    if not resource:
        return None
    root = Path(resource)
    on_macos = platform.system() == "Darwin"
    for base in (root / "resources" / "bin" / "chrome", root / "bin" / "chrome"):
        for name in _chrome_file_names():
            candidate = base / name
            if candidate.is_file():
                return candidate
        if on_macos:
            for bundle in sorted(base.glob("*.app")):
                binary = bundle / "Contents" / "MacOS"
                if not binary.is_dir():
                    continue
                names = sorted(p for p in binary.iterdir() if p.is_file())
                if names:
                    return names[0]
    return None


def chrome_executable() -> Optional[str]:
    """本机可用的 Chrome 可执行文件路径；一档也没有就 None。"""
    explicit = str(os.environ.get(CHROME_PATH_ENV) or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return str(path)
        # 指了但指错：吵闹地返回 None 之前先记一条，别让配置打错字变成「找不到浏览器」。
        logger.warning("[motion-render] %s 指向的文件不存在: %s", CHROME_PATH_ENV, explicit)
        return None
    bundled = _bundled_chrome()
    if bundled is not None:
        return str(bundled)
    for name in _chrome_file_names():
        found = shutil.which(name)
        if found:
            return found
    return None


def _launch_flags(user_data_dir: Path, window_size: tuple[int, int]) -> list[str]:
    return [
        "--headless=new",
        "--remote-debugging-port=0",
        f"--user-data-dir={user_data_dir}",
        f"--window-size={window_size[0]},{window_size[1]}",
        "--force-device-scale-factor=1",
        "--force-color-profile=srgb",
        "--hide-scrollbars",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-popup-blocking",
        # 渲染任务不出网：MG 页面是模型产出的任意 HTML，只要留一条 DNS 出路，页面
        # 里一句 <img src="https://…"> 就会在客户机器上替我们发外部请求（既算出网，
        # 也让同一条提示词今天能渲、明天渲不出）。契约要求单文件自包含，所以直接把
        # 域名解析全量打死，file:// 本地子资源不受影响。
        "--host-resolver-rules=MAP * ~NOTFOUND",
        "--lang=zh-CN",
    ]


async def _read_active_port(user_data_dir: Path, deadline: float) -> int:
    """轮询 Chrome 自写的 `DevToolsActivePort`（首行端口，次行 ws 路径）。"""
    port_file = user_data_dir / "DevToolsActivePort"
    loop = asyncio.get_running_loop()
    while loop.time() < deadline:
        try:
            text = port_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            await asyncio.sleep(_PORT_POLL_SECONDS)
            continue
        first = text.splitlines()[0].strip() if text.splitlines() else ""
        if first.isdigit():
            port = int(first)
            if 0 < port < 65536:
                return port
        await asyncio.sleep(_PORT_POLL_SECONDS)
    raise ChromeUnavailable(
        f"Chrome 启动后 {int(_LAUNCH_TIMEOUT_SECONDS)} 秒内没写出调试端口（{port_file}）"
    )


@dataclass
class ChromeSession:
    """一次渲染独占的 CDP 端点。``external`` 时进程不归我们管，收尾不杀。"""

    cdp_url: str
    process: Optional[asyncio.subprocess.Process]
    user_data_dir: Optional[Path]
    external: bool

    @property
    def owns_process(self) -> bool:
        return self.process is not None and not self.external


async def open_chrome(*, window_size: tuple[int, int] = (1280, 720)) -> ChromeSession:
    """拿到一个可用 CDP 端点：优先连现成的调试端口，否则自己拉一个无头实例。"""
    external = str(os.environ.get(CDP_URL_ENV) or "").strip()
    if external:
        url = external.rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        return ChromeSession(cdp_url=url, process=None, user_data_dir=None, external=True)

    executable = chrome_executable()
    if not executable:
        raise ChromeUnavailable(
            "找不到可用的 Chrome：可设 ST_CHROME_PATH 指到浏览器可执行文件、"
            "设 ST_CHROME_CDP_URL 连一个现成的调试端口，"
            "或用自带 Chrome 的桌面安装包"
        )
    user_data_dir = Path(tempfile.mkdtemp(prefix="novelvideo-mg-chrome-"))
    command = [executable, *_launch_flags(user_data_dir, window_size), "about:blank"]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        shutil.rmtree(user_data_dir, ignore_errors=True)
        raise ChromeUnavailable(f"启动 Chrome 失败（{executable}）：{exc}") from exc

    try:
        port = await _read_active_port(
            user_data_dir, asyncio.get_running_loop().time() + _LAUNCH_TIMEOUT_SECONDS
        )
    except ChromeUnavailable:
        await _stop_process(process)
        shutil.rmtree(user_data_dir, ignore_errors=True)
        raise
    logger.info(
        "[motion-render] 临时无头 Chrome pid=%s 就绪：%s", process.pid, executable
    )
    return ChromeSession(
        cdp_url=f"http://127.0.0.1:{port}",
        process=process,
        user_data_dir=user_data_dir,
        external=False,
    )


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    with contextlib.suppress(asyncio.TimeoutError, OSError):
        await asyncio.wait_for(process.wait(), timeout=5.0)
    if process.returncode is None:
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(Exception):
            await process.wait()


async def close_chrome(session: ChromeSession) -> None:
    """收尾自拉的实例并清临时 profile；外部端口只丢引用，绝不动用户的浏览器。

    顺序是先停进程再删目录：Windows 上 Chrome 还活着时 profile 被它占着，
    ``rmtree(ignore_errors=True)`` 会把删除失败悄悄咽掉，于是每次渲染都留一个临时目录。
    """
    if session.owns_process and session.process is not None:
        await _stop_process(session.process)
    if session.user_data_dir is not None:
        shutil.rmtree(session.user_data_dir, ignore_errors=True)


@contextlib.asynccontextmanager
async def chrome_endpoint(*, window_size: tuple[int, int] = (1280, 720)):
    """``async with chrome_endpoint() as cdp_url:`` —— 无论成功失败都收尾。"""
    session = await open_chrome(window_size=window_size)
    try:
        yield session.cdp_url
    finally:
        await close_chrome(session)
