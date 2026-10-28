#!/usr/bin/env python3
"""本地脚手架占位生成器（非品牌资产，仅为让 cargo check / tauri build 通过）。
生成：src-tauri 资源 glob 需要的占位文件 + 一套扁平色应用图标（png/icns/ico）。
真实图标应由 `cargo tauri icon <源图.png>` 覆盖。"""
import os, struct, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # desktop/
TAURI = os.path.join(ROOT, "src-tauri")
RES = os.path.join(TAURI, "resources")
ICONS = os.path.join(TAURI, "icons")


def ensure(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"")


def png(path, w, h, rgba=(224, 82, 60, 255)):
    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    row = bytearray()
    for _ in range(w):
        row += bytes(rgba)
    raw = bytearray()
    for _ in range(h):
        raw.append(0)
        raw += row
    idat = zlib.compress(bytes(raw), 9)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def ico(path, entries):
    hdr = struct.pack("<HHH", 0, 1, len(entries))
    off = 6 + 16 * len(entries)
    dirblock, body = b"", b""
    for (w, h, fp) in entries:
        with open(fp, "rb") as f:
            b = f.read()
        dirblock += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(b), off)
        body += b
        off += len(b)
    with open(path, "wb") as f:
        f.write(hdr + dirblock + body)


# 1) 资源 glob 占位（均为 gitignore 的构建期目录）
# 两套平台命名都占位，让 cargo check / tauri build 在 mac 与 Windows 上都能过 glob 校验：
# 受管二进制 Windows 带 .exe；便携 Python 在 Unix 是 python/bin/python3，
# 在 Windows 是 python/python.exe（install_only 包没有 bin/ 这一层）。
placeholder_rels = [
    "app/dist/index.html",
    "app/src/novelvideo/__init__.py",
    "app/services/vectcut/aipdd_server.py",
    "python/bin/python3",
    "python/python.exe",
]
for b in ["nginx", "localapi", "ffmpeg"]:
    placeholder_rels += [f"bin/{b}", f"bin/{b}.exe"]
for rel in placeholder_rels:
    ensure(os.path.join(RES, rel))

# 2) 图标：tauri.conf 引用的三个 png
os.makedirs(ICONS, exist_ok=True)
png(os.path.join(ICONS, "32x32.png"), 32, 32)
png(os.path.join(ICONS, "128x128.png"), 128, 128)
png(os.path.join(ICONS, "128x128@2x.png"), 256, 256)

# iconset -> .icns（供 macOS 打包）
iconset = os.path.join("/tmp", "appicon.iconset")
os.makedirs(iconset, exist_ok=True)
sizes = {
    "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
}
for name, s in sizes.items():
    png(os.path.join(iconset, name), s, s)

# .ico（供 Windows 打包）：嵌入 16/32/48 三个 png 尺寸
tmp16 = os.path.join("/tmp", "ico16.png")
tmp32 = os.path.join("/tmp", "ico32.png")
tmp48 = os.path.join("/tmp", "ico48.png")
png(tmp16, 16, 16); png(tmp32, 32, 32); png(tmp48, 48, 48)
ico(os.path.join(ICONS, "icon.ico"), [(16, 16, tmp16), (32, 32, tmp32), (48, 48, tmp48)])

print("placeholders + png/ico icons ready; icns via iconutil next")
print("ICONSET=" + iconset)
