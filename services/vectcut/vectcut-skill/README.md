# VectCutAPI Skill for Claude Code

<div align="center">

**[English](README_EN.md)** | **中文**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude_Code-Skill-purple.svg)](https://claude.com/claude-code)
[![VectCutAPI](https://img.shields.io/badge/VectCutAPI-1.5k%2B%20Stars-orange.svg)](https://github.com/sun-guannan/VectCutAPI)

让 AI 能够通过 VectCutAPI 进行专业视频剪辑

[快速开始](#快速开始) • [功能特性](#功能特性) • [使用示例](#使用示例) • [API 文档](#api-文档)

</div>

---

## 项目简介

**VectCutAPI Skill** 是为 [Claude Code](https://claude.com/claude-code) 封装的专业视频剪辑技能，基于强大的 [VectCutAPI](https://github.com/sun-guannan/VectCutAPI) 项目。

通过这个技能，Claude AI 可以直接调用 VectCutAPI 的全部功能，实现：
- 自动创建视频草稿项目
- 添加视频、音频、图片素材
- 添加文字、字幕、特效
- 应用转场和关键帧动画
- 批量视频处理
- AI 驱动的视频生成工作流

### 核心优势

- **无缝集成** - Claude Code 自动识别并加载技能
- **完整封装** - 支持 VectCutAPI 的所有 35+ 个 HTTP 接口和 11 个 MCP 工具
- **Python 客户端** - 提供优雅的 Python API 封装
- **丰富示例** - 包含 8+ 种常见工作流示例代码
- **双协议支持** - 同时支持 HTTP REST 和 MCP 协议

---

## 致谢与声明

本项目是基于以下开源项目进行封装和扩展的：

### 核心依赖项目

| 项目 | 作者 | 许可证 | 说明 |
|------|------|--------|------|
| [VectCutAPI](https://github.com/sun-guannan/VectCutAPI) | [@sun-guannan](https://github.com/sun-guannan) | Apache 2.0 | 强大的云端视频剪辑 API，提供对剪映/CapCut 的编程控制 |
| [Claude Code](https://claude.com/claude-code) | Anthropic | - | Anthropic 官方 CLI 工具，支持自定义技能扩展 |
| [skill-creator](https://github.com/anthropics/claude-code-skills) | Anthropic | - | Claude Code 技能创建指南和工具 |

### 特别感谢

- **@sun-guannan** - 感谢创建了如此优秀的 VectCutAPI 项目，填补了 AI 生成素材与专业视频编辑之间的空白
- **Anthropic** - 感谢提供 Claude Code 和技能系统，使 AI 能够无缝集成专业工具

### 声明

本封装项目仅作为 VectCutAPI 的配套技能存在，旨在为 Claude Code 用户提供便捷的集成方式。核心视频剪辑功能完全依赖于 VectCutAPI 原项目。

---

## 功能特性

### 支持的视频编辑功能

| 功能模块 | 描述 |
|---------|------|
| **草稿管理** | 创建、保存、查询剪映/CapCut 草稿文件 |
| **视频处理** | 多格式视频导入、剪辑、转场、特效、蒙版 |
| **音频编辑** | 音频轨道、音量控制、音效处理 |
| **图像处理** | 图片导入、动画、蒙版、滤镜 |
| **文本编辑** | 多样式文本、阴影、背景、动画 |
| **字幕系统** | SRT 字幕导入、样式设置、时间同步 |
| **特效引擎** | 视觉特效、滤镜、转场动画 |
| **贴纸系统** | 贴纸素材、位置控制、动画效果 |
| **关键帧** | 属性动画、时间轴控制、缓动函数 |
| **媒体分析** | 视频时长获取、格式检测 |

### Skill 内置资源

- **SKILL.md** - 完整的技能使用指南
- **Python 客户端** - `vectcut_client.py` 提供优雅的 API 封装
- **API 参考** - 详细的接口文档和参数说明
- **工作流示例** - 8+ 种常见视频制作场景的完整代码

---

## 快速开始

### 系统要求

- Python 3.10+
- Claude Code (Anthropic 官方 CLI)
- 剪映 或 CapCut 国际版
- FFmpeg (可选)

### 1. 安装 VectCutAPI

```bash
# 克隆 VectCutAPI 项目
git clone https://github.com/sun-guannan/VectCutAPI.git
cd VectCutAPI

# 安装依赖
pip install -r requirements.txt      # HTTP API 基础依赖
pip install -r requirements-mcp.txt  # MCP 协议支持 (可选)

# 配置文件
cp config.json.example config.json
# 根据需要编辑 config.json

# 启动服务
python capcut_server.py  # HTTP API 服务器 (默认端口: 9001)
```

### 2. 安装 Skill

```bash
# 克隆本项目
git clone https://github.com/HUNSETO1413/vectcut-skill.git
cd vectcut-skill

# 复制 skill 文件到 Claude Code 技能目录
# Windows:
copy skill\* %USERPROFILE%\.claude\skills\public\vectcut-api\ /E

# Linux/macOS:
cp -r skill/* ~/.claude/skills/public/vectcut-api/
```

### 3. 验证安装

在 Claude Code 中输入：

```
我需要创建一个 1080x1920 的视频草稿
```

Claude 应该会自动加载 vectcut-api 技能并调用相关功能。

---

## 使用示例

### 基础视频制作

```python
from skill.scripts.vectcut_client import VectCutClient

# 创建客户端
client = VectCutClient("http://localhost:9001")

# 创建草稿
draft = client.create_draft(width=1080, height=1920)

# 添加背景视频
client.add_video(
    draft.draft_id,
    "https://example.com/background.mp4",
    volume=0.6
)

# 添加标题文字
client.add_text(
    draft.draft_id,
    "欢迎使用 VectCutAPI",
    start=0,
    end=5,
    font_size=56,
    font_color="#FFD700",
    shadow_enabled=True
)

# 保存草稿
result = client.save_draft(draft.draft_id)
print(f"草稿已保存: {result.draft_url}")
```

### AI 文字转视频工作流

```python
import requests

BASE_URL = "http://localhost:9001"

# 1. 创建草稿
draft = requests.post(f"{BASE_URL}/create_draft", json={
    "width": 1080,
    "height": 1920
}).json()
draft_id = draft["output"]["draft_id"]

# 2. 添加背景视频
requests.post(f"{BASE_URL}/add_video", json={
    "draft_id": draft_id,
    "video_url": "https://example.com/bg.mp4",
    "volume": 0.4
})

# 3. 添加分段文字
segments = ["第一段文字", "第二段文字", "第三段文字"]
colors = ["#FF6B6B", "#4ECDC4", "#45B7D1"]

for i, (segment, color) in enumerate(zip(segments, colors)):
    requests.post(f"{BASE_URL}/add_text", json={
        "draft_id": draft_id,
        "text": segment,
        "start": i * 4,
        "end": (i + 1) * 4,
        "font_size": 48,
        "font_color": color,
        "shadow_enabled": True
    })

# 4. 保存草稿
result = requests.post(f"{BASE_URL}/save_draft", json={
    "draft_id": draft_id
}).json()

print(f"视频已生成: {result['output']['draft_url']}")
```

更多示例请查看 [workflows.md](skill/references/workflows.md)。

---

## 技术架构

### Skill 结构

```
vectcut-skill/
├── skill/                       # Claude Code Skill
│   ├── SKILL.md                 # 技能主文档
│   ├── scripts/                 # 可执行脚本
│   │   └── vectcut_client.py   # Python 客户端封装
│   ├── references/              # 参考文档
│   │   ├── api_reference.md    # API 接口参考
│   │   └── workflows.md        # 工作流示例
│   └── assets/                  # 资产文件
│       └── examples/            # 示例代码
├── docs/                        # 项目文档
│   ├── ARCHITECTURE.md          # 技术架构说明
│   ├── INSTALLATION.md          # 安装指南
│   └── USAGE.md                 # 使用指南
├── LICENSE                      # MIT 许可证
└── README.md                    # 项目说明
```

### 封装技术

本项目采用以下技术进行封装：

1. **Claude Code Skill System**
   - 遵循 Anthropic 官方的 Skill 规范
   - 使用 YAML frontmatter 定义技能元数据
   - 采用渐进式披露设计原则

2. **Python 客户端封装**
   - 使用 dataclasses 定义数据结构
   - 使用 Enum 类型定义预设值
   - 提供上下文管理器支持
   - 完整的错误处理

3. **文档组织**
   - SKILL.md: 核心使用指南
   - references/: 详细参考文档
   - scripts/: 可执行代码

---

## API 文档

### 核心 API

| API | 功能 |
|-----|------|
| `create_draft()` | 创建视频草稿 |
| `save_draft()` | 保存草稿并生成 URL |
| `add_video()` | 添加视频轨道 |
| `add_audio()` | 添加音频轨道 |
| `add_image()` | 添加图片素材 |
| `add_text()` | 添加文字元素 |
| `add_subtitle()` | 添加 SRT 字幕 |
| `add_effect()` | 添加视频特效 |
| `add_sticker()` | 添加贴纸 |
| `add_video_keyframe()` | 添加关键帧动画 |

完整的 API 文档请查看 [api_reference.md](skill/references/api_reference.md)。

---

## 工作流示例

项目包含以下完整的工作流示例：

1. **基础视频制作** - 竖屏短视频制作流程
2. **AI 文字转视频** - 文字内容自动转换为视频
3. **视频混剪** - 多段视频拼接和转场
4. **带字幕视频** - SRT 字幕导入
5. **关键帧动画** - 图片动画展示
6. **产品介绍视频** - 专业产品展示
7. **分屏效果** - 左右分屏对比
8. **图片轮播** - 图片幻灯片

详见 [workflows.md](skill/references/workflows.md)。

---

## 相关项目

- [VectCutAPI](https://github.com/sun-guannan/VectCutAPI) - 核心视频剪辑 API
- [pyJianYingDraft](https://github.com/sun-guannan/pyJianYingDraft) - 剪映草稿 Python 库
- [Claude Code Skills](https://github.com/anthropics/claude-code-skills) - 官方技能集合

---

## 许可证

本项目采用 [MIT License](LICENSE) 开源许可证。

**注意**: 本项目封装的 VectCutAPI 核心库采用 Apache 2.0 许可证。

---

## 贡献指南

欢迎贡献代码！请遵循以下流程：

1. Fork 本项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 提交 Pull Request

### 贡献方向

- 新增工作流示例
- 优化 Python 客户端
- 完善文档
- Bug 修复

---

## 联系方式

### 作者信息

**项目作者**: HUNSETO1413

- **项目主页**: [GitHub Repository](https://github.com/HUNSETO1413/vectcut-skill)
- **问题反馈**: [Issues](https://github.com/HUNSETO1413/vectcut-skill/issues)
- **VectCutAPI 原项目**: [sun-guannan/VectCutAPI](https://github.com/sun-guannan/VectCutAPI)

### 微信联系

扫码添加作者微信，交流技术问题：

<div align="center">

![作者微信](Mark微信.png)

**微信号**: `399187854`

</div>

---

## 更新日志

### v1.0.0 (2025-01-25)

初始版本发布

- ✅ 完整的 VectCutAPI Skill 封装
- ✅ Python 客户端库
- ✅ 8+ 种工作流示例
- ✅ 完整的 API 参考文档
- ✅ 支持所有 VectCutAPI 功能

---

## Star History

如果这个项目对你有帮助，请给一个 Star ⭐️

同时也欢迎给原项目 [VectCutAPI](https://github.com/sun-guannan/VectCutAPI) 点 Star 🌟

---

<div align="center">

**Made with ❤️ by HUNSETO1413**

Based on [VectCutAPI](https://github.com/sun-guannan/VectCutAPI) by [@sun-guannan](https://github.com/sun-guannan)

微信: **399187854**

</div>
