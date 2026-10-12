#!/usr/bin/env python3
# SPDX-License-Identifier: Elastic-2.0
"""从品牌源图派生「桌面图标 + 前端品牌资产」，一次生成、多处统一。

源图约定：一张方形、不透明底、白色字标的 logo（默认 desktop/assets/app-icon-source.png）。

产物（全部覆盖式写入，随工程提交）：
  · desktop/assets/app-icon-macos.png        macOS 圆角留白图标（喂给 `tauri icon`）
  · frontend/public/brand/dclogo.png         浏览器 favicon / og:image（黑底方图）
  · frontend/public/brand/logo-d.png         下载页 26px 圆角小徽标（黑底方图）
  · frontend/public/brand/dramaclaw-wordmark.png     登录/顶栏字标（白字透明底）
  · frontend/public/brand/dramaclaw-logo-login.png   同上（白字透明底）

为什么分两种底：favicon / 小徽标是自带背景的方块，任何页面背景都能读；登录/顶栏是贴在
深色 UI（#181818）上的图章，用「白字 + 透明底」才不像贴了块黑砖。

重生成桌面图标：  npx @tauri-apps/cli@^2 icon assets/app-icon-macos.png   （在 desktop/ 下）
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
SOURCE = os.path.join(ROOT, "desktop", "assets", "app-icon-source.png")
DESKTOP_ASSETS = os.path.join(ROOT, "desktop", "assets")
BRAND = os.path.join(ROOT, "frontend", "public", "brand")

# 字标在源图里是纯白，取亮度做 alpha：白→不透明、黑→透明（保留抗锯齿边缘）。
TEXT_BRIGHT_MIN = 128


def load_source() -> Image.Image:
    if not os.path.isfile(SOURCE):
        sys.exit(f"找不到品牌源图：{SOURCE}")
    return Image.open(SOURCE).convert("RGBA")


def white_text_on_transparent(src: Image.Image, size: int, fill: float = 0.82) -> Image.Image:
    """裁出白色字标，铺成透明底方形图章（字约占 fill 宽）。"""
    gray = src.convert("L")
    # 亮度阈值内接矩形＝字标包围盒
    bbox = gray.point(lambda p: 255 if p >= TEXT_BRIGHT_MIN else 0).getbbox()
    if not bbox:
        sys.exit("源图里没检出亮色字标（检查源图是否为白字黑底）。")
    mask = gray.crop(bbox)                       # 字标亮度图
    wordmark = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    wordmark.paste((255, 255, 255, 255), (0, 0), mask)  # 白色 + 亮度做 alpha

    # 等比缩放到目标画布内 fill 比例，居中
    tw, th = wordmark.size
    scale = (size * fill) / max(tw, th)
    nw, nh = max(1, int(tw * scale)), max(1, int(th * scale))
    wordmark = wordmark.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(wordmark, ((size - nw) // 2, (size - nh) // 2), wordmark)
    return canvas


def black_square(src: Image.Image, size: int) -> Image.Image:
    """不透明黑底方图（favicon / 小徽标用），直接等比缩放。"""
    return src.resize((size, size), Image.LANCZOS).convert("RGBA")


def macos_squircle(src: Image.Image, size: int = 1024) -> Image.Image:
    """Apple 图标栅格：居中圆角方块（约 0.805 画布）+ 四周透明留白，圆角半径约 0.181 画布。"""
    side = int(round(size * 0.805))
    margin = (size - side) // 2
    radius = int(round(size * 0.181))
    tile = src.resize((side, side), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, side - 1, side - 1], radius=radius, fill=255)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(tile, (margin, margin), mask)
    return canvas


def save(img: Image.Image, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG")
    print(f"  写出 {os.path.relpath(path, ROOT)}  {img.size[0]}x{img.size[1]}")


def main() -> None:
    src = load_source()
    print(f"源图 {os.path.relpath(SOURCE, ROOT)} {src.size[0]}x{src.size[1]}")
    save(macos_squircle(src, 1024), os.path.join(DESKTOP_ASSETS, "app-icon-macos.png"))
    save(black_square(src, 512), os.path.join(BRAND, "dclogo.png"))
    save(black_square(src, 311), os.path.join(BRAND, "logo-d.png"))
    save(white_text_on_transparent(src, 311), os.path.join(BRAND, "dramaclaw-wordmark.png"))
    save(white_text_on_transparent(src, 311), os.path.join(BRAND, "dramaclaw-logo-login.png"))
    print("完成。桌面图标记得再跑： (cd desktop && npx @tauri-apps/cli@^2 icon assets/app-icon-macos.png)")


if __name__ == "__main__":
    main()
