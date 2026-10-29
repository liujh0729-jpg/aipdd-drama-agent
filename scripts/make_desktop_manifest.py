#!/usr/bin/env python3
"""Generate an electron-updater style manifest for a desktop installer.

The login page resolves the current installer from ``latest.yml`` /
``latest-mac.yml`` served straight out of **this project's own** GitHub
Release (see ``frontend/src/lib/desktop-download.ts``). That keeps the
download pointer under our control: nothing is read from the upstream
DramaClaw download site, so upstream renaming or pulling its packages cannot
change what our users get.

Usage::

    python scripts/make_desktop_manifest.py \\
        --installer "bundle/nsis/AIPDD DramaClaw_2.3.1_x64-setup.exe" \\
        --version 2.3.1 --platform windows

writes ``latest.yml`` next to the installer. Upload it to the release together
with the installer; ``sha512`` is computed locally so no extra download is
needed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
from datetime import datetime, timezone
from pathlib import Path

PLATFORM_MANIFEST = {"windows": "latest.yml", "mac": "latest-mac.yml"}


def _sha512_base64(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return base64.b64encode(digest.digest()).decode("ascii")


def build_manifest(
    version: str,
    name: str,
    sha512: str | None,
    size: int | None,
    released: str,
    extra_urls: tuple[str, ...] = (),
) -> str:
    # url 必须是**发布页上的资产名**：GitHub 会把文件名里的空格换成点
    # （`AIPDD DramaClaw_2.3.1_x64-setup.exe` → `AIPDD.DramaClaw_2.3.1_x64-setup.exe`），
    # 按本地文件名写就必然 404。
    lines = [f"version: {version}", "files:", f"  - url: {name}"]
    if sha512 is not None:
        lines.append(f"    sha512: {sha512}")
    if size is not None:
        lines.append(f"    size: {size}")
    lines.extend(f"  - url: {url}" for url in extra_urls)
    lines.append(f"path: {name}")
    if sha512 is not None:
        lines.append(f"sha512: {sha512}")
    lines.append(f"releaseDate: '{released}'")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, help="安装包；给了就顺带算 sha512/size")
    parser.add_argument("--version", required=True)
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORM_MANIFEST))
    parser.add_argument(
        "--asset-name",
        help="发布页上的资产名（默认取安装包名并把空格换成点）；只写清单不校验时可单独使用",
    )
    parser.add_argument(
        "--extra-url",
        action="append",
        default=[],
        help="追加到 files: 的备用资产名（如另一个架构的 dmg），可重复",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="输出路径；默认写在安装包同目录，文件名按平台取 latest.yml / latest-mac.yml",
    )
    args = parser.parse_args()

    installer: Path | None = args.installer
    if installer is not None and not installer.is_file():
        parser.error(f"安装包不存在：{installer}")
    if installer is None and not args.asset_name:
        parser.error("--installer 与 --asset-name 至少给一个")
    version = args.version.strip().lstrip("vV")
    if not version:
        parser.error("--version 不能为空")

    if installer is not None:
        name = args.asset_name or installer.name.replace(" ", ".")
        sha512 = _sha512_base64(installer)
        size = installer.stat().st_size
        released = datetime.fromtimestamp(
            installer.stat().st_mtime, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        default_dir = installer.parent
    else:
        name = args.asset_name
        sha512 = None
        size = None
        released = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        default_dir = Path.cwd()

    out = args.out or default_dir / PLATFORM_MANIFEST[args.platform]
    out.write_text(
        build_manifest(version, name, sha512, size, released, tuple(args.extra_url)),
        encoding="utf-8",
    )
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
