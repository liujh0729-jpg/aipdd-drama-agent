# 应用图标

品牌源图（AIPDD 白字黑底方形 logo，2000×2000，PNG）随工程提交在：

```
desktop/assets/app-icon-source.png
```

macOS 图标按 Apple 栅格加了**圆角 + 四周留白**（避免系统再套一层黑框），
派生图同样随工程提交：

```
desktop/assets/app-icon-macos.png   # 圆角留白版，喂给 tauri icon 的实际源
```

圆角留白版与前端 favicon/登录字标都由一个脚本一并派生（改 logo 后先跑它）：

```bash
.venv/bin/python3 scripts/gen_brand_assets.py   # 读 app-icon-source.png，产出 app-icon-macos.png + 前端 brand/*.png
```

`tauri.conf.json` 的 `bundle.icon` 引用本目录下由 Tauri CLI 生成的标准图标集，
**已随工程提交**（真实品牌资产，非占位）：

```
icons/32x32.png
icons/128x128.png
icons/128x128@2x.png
icons/icon.png          (Linux/通用)
icons/icon.icns         (macOS)
icons/icon.ico          (Windows)
icons/Square*Logo.png   (Windows 磁贴)
```

换 logo 或改尺寸后，先跑 `scripts/gen_brand_assets.py` 重生成 `app-icon-macos.png`，
再在 `desktop/` 目录一条命令重生成全套（勿手改 `tauri.conf.json` 的 icon 列表）：

```bash
npx @tauri-apps/cli@^2 icon assets/app-icon-macos.png
# 或已装 cargo 版：cargo tauri icon assets/app-icon-macos.png
```

`tauri icon` 还会顺带产出 `ios/`、`android/` 目录——本项目仅桌面，可删。

> 提示：若 `tauri build` 报“找不到 icons/icon.icns/ico”，即本目录尚未生成，先跑上面的命令。
> 占位生成器 `scripts/gen-placeholder-icons.py` 会把这些真实图标覆盖成扁平色，勿在已有品牌图标后再跑它。
