# VectCutAPI Skill 使用指南

## 目录

1. [安装指南](#安装指南)
2. [快速开始](#快速开始)
3. [Claude Code 中使用](#claude-code-中使用)
4. [Python 客户端使用](#python-客户端使用)
5. [常见场景示例](#常见场景示例)
6. [故障排除](#故障排除)
7. [最佳实践](#最佳实践)

---

## 安装指南

### 前置要求

在开始之前，请确保已安装：

- **Python 3.10+** - [下载链接](https://www.python.org/downloads/)
- **Claude Code** - Anthropic 官方 CLI 工具
- **剪映** 或 **CapCut 国际版**
- **Git** - 用于克隆项目

### 步骤 1: 安装 VectCutAPI

VectCutAPI 是本技能依赖的核心服务，必须先安装并运行。

```bash
# 1. 克隆 VectCutAPI 项目
git clone https://github.com/sun-guannan/VectCutAPI.git
cd VectCutAPI

# 2. 创建虚拟环境 (推荐)
python -m venv venv-vectcut

# Windows 激活虚拟环境
venv-vectcut\Scripts\activate

# Linux/macOS 激活虚拟环境
source venv-vectcut/bin/activate

# 3. 安装依赖
pip install -r requirements.txt      # HTTP API 基础依赖
pip install -r requirements-mcp.txt  # MCP 协议支持 (可选)

# 4. 配置文件
cp config.json.example config.json

# 5. 编辑 config.json (可选)
# 根据需要修改配置，如端口、OSS 设置等

# 6. 启动服务
python capcut_server.py
```

服务启动后，默认监听 `http://localhost:9001`

### 步骤 2: 安装 Skill

```bash
# 1. 克隆本项目
git clone https://github.com/your-username/vectcut-skill.git
cd vectcut-skill

# 2. 复制 skill 文件到 Claude Code 技能目录

# Windows (PowerShell)
Copy-Item -Path "skill\*" -Destination "$env:USERPROFILE\.claude\skills\public\vectcut-api\" -Recurse -Force

# Windows (CMD)
xcopy "skill\*" "%USERPROFILE%\.claude\skills\public\vectcut-api\" /E /I /Y

# Linux/macOS
cp -r skill/* ~/.claude/skills/public/vectcut-api/
```

### 步骤 3: 验证安装

```bash
# 检查 skill 目录是否存在
# Windows
dir %USERPROFILE%\.claude\skills\public\vectcut-api

# Linux/macOS
ls ~/.claude/skills/public/vectcut-api
```

应该看到以下文件：
- `SKILL.md`
- `scripts/vectcut_client.py`
- `references/api_reference.md`
- `references/workflows.md`

---

## 快速开始

### 测试 VectCutAPI 服务

```bash
# 在新终端中测试 API
curl http://localhost:9001/

# 或者使用浏览器访问
# http://localhost:9001/
```

应该看到 API 文档页面。

### 测试 Python 客户端

```python
from skill.scripts.vectcut_client import VectCutClient

# 创建客户端
client = VectCutClient("http://localhost:9001")

# 创建草稿
draft = client.create_draft(width=1080, height=1920)
print(f"草稿 ID: {draft.draft_id}")

# 保存草稿
result = client.save_draft(draft.draft_id)
print(f"草稿 URL: {result.draft_url}")
```

---

## Claude Code 中使用

### 自动触发

当你提到以下关键词时，Claude Code 会自动加载 vectcut-api 技能：

- "创建视频草稿"
- "视频剪辑"
- "添加视频轨道"
- "添加文字到视频"
- "VectCutAPI"
- "剪映草稿"

### 示例对话

```
用户: 我需要创建一个 1080x1920 的竖屏视频，包含背景视频和标题文字

Claude: 我来帮你创建这个视频。首先，我会使用 VectCutAPI 创建草稿...

[自动加载 vectcut-api skill]

1. 创建草稿项目 (1080x1920)
2. 添加背景视频
3. 添加标题文字
4. 保存草稿

请提供以下信息：
- 背景视频的 URL 或本地路径
- 标题文字内容
```

### 手动指定 Skill

如果自动触发失败，可以手动指定：

```
用户: 使用 vectcut-api skill 创建一个视频草稿
```

---

## Python 客户端使用

### 基础用法

```python
from skill.scripts.vectcut_client import VectCutClient, Resolution, Transition

# 创建客户端
with VectCutClient("http://localhost:9001") as client:

    # 创建草稿
    draft = client.create_draft(
        width=Resolution.VERTICAL.value[0],
        height=Resolution.VERTICAL.value[1]
    )

    # 添加视频
    client.add_video(
        draft_id=draft.draft_id,
        video_url="https://example.com/video.mp4",
        volume=0.6,
        transition=Transition.FADE_IN.value
    )

    # 添加文字
    client.add_text(
        draft_id=draft.draft_id,
        text="标题文字",
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

### 使用预设值

```python
from skill.scripts.vectcut_client import VectCutClient, Resolution, Transition, TextAnimation

with VectCutClient() as client:
    # 使用预设分辨率
    draft = client.create_draft(
        width=Resolution.VERTICAL.value[0],
        height=Resolution.VERTICAL.value[1]
    )

    # 使用预设转场
    client.add_video(
        draft.draft_id,
        "video.mp4",
        transition=Transition.FADE_IN.value
    )

    # 使用预设文字动画
    client.add_text(
        draft.draft_id,
        "Hello",
        start=0,
        end=3,
        text_intro=TextAnimation.ZOOM_IN.value
    )
```

### 错误处理

```python
from skill.scripts.vectcut_client import VectCutClient

try:
    client = VectCutClient("http://localhost:9001")
    draft = client.create_draft()

    if client.add_video(draft.draft_id, "video.mp4"):
        print("视频添加成功")
    else:
        print("视频添加失败")

except Exception as e:
    print(f"发生错误: {e}")
finally:
    client.close()
```

---

## 常见场景示例

### 场景 1: 短视频制作

```python
from skill.scripts.vectcut_client import create_quick_video, Resolution

# 快速创建短视频
video_url = create_quick_video(
    base_url="http://localhost:9001",
    video_url="https://example.com/bg.mp4",
    text_content="欢迎关注我的频道",
    bgm_url="https://example.com/bgm.mp3",
    resolution=Resolution.VERTICAL
)

print(f"视频已生成: {video_url}")
```

### 场景 2: AI 文字转视频

```python
from skill.scripts.vectcut_client import VectCutClient

def text_to_video(text_lines, bg_video, bgm):
    """将文字转换为视频"""
    with VectCutClient() as client:
        draft = client.create_draft(1080, 1920)

        # 添加背景视频和音乐
        client.add_video(draft.draft_id, bg_video, volume=0.4)
        client.add_audio(draft.draft_id, bgm, volume=0.3)

        # 添加文字
        colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#FFA07A"]
        for i, line in enumerate(text_lines):
            client.add_text(
                draft.draft_id,
                line,
                start=i * 4,
                end=(i + 1) * 4,
                font_size=48,
                font_color=colors[i % len(colors)],
                shadow_enabled=True,
                background_alpha=0.6
            )

        # 保存
        result = client.save_draft(draft.draft_id)
        return result.draft_url

# 使用
video_url = text_to_video(
    ["第一段文字", "第二段文字", "第三段文字"],
    "bg.mp4",
    "bgm.mp3"
)
```

### 场景 3: 视频混剪

```python
from skill.scripts.vectcut_client import VectCutClient, Transition

def create_mashup(video_clips):
    """创建视频混剪"""
    with VectCutClient() as client:
        draft = client.create_draft(1080, 1920)

        transitions = [
            Transition.FADE_IN.value,
            Transition.WIPE_LEFT.value,
            Transition.WIPE_RIGHT.value
        ]

        current_time = 0
        for i, clip in enumerate(video_clips):
            transition = transitions[i % len(transitions)] if i > 0 else None

            client.add_video(
                draft.draft_id,
                clip["url"],
                start=0,
                end=clip["duration"],
                target_start=current_time,
                transition=transition,
                transition_duration=0.5
            )
            current_time += clip["duration"]

        result = client.save_draft(draft.draft_id)
        return result.draft_url

# 使用
clips = [
    {"url": "clip1.mp4", "duration": 5},
    {"url": "clip2.mp4", "duration": 4},
    {"url": "clip3.mp4", "duration": 6}
]

video_url = create_mashup(clips)
```

### 场景 4: 带字幕的视频

```python
from skill.scripts.vectcut_client import VectCutClient

def add_subtitles_to_video(video_url, srt_url):
    """为视频添加字幕"""
    with VectCutClient() as client:
        draft = client.create_draft(1920, 1080)

        # 添加视频
        client.add_video(draft.draft_id, video_url)

        # 添加字幕
        client.add_subtitle(
            draft_id=draft.draft_id,
            srt_url=srt_url,
            font_size=36,
            font_color="#FFFFFF",
            stroke_enabled=True,
            stroke_width=4.0,
            background_alpha=0.5
        )

        result = client.save_draft(draft.draft_id)
        return result.draft_url

# 使用
video_url = add_subtitles_to_video("video.mp4", "subtitles.srt")
```

---

## 故障排除

### 问题 1: VectCutAPI 服务无法启动

**症状**: 运行 `python capcut_server.py` 时出错

**解决方案**:

1. 检查 Python 版本 (需要 3.10+)
   ```bash
   python --version
   ```

2. 重新安装依赖
   ```bash
   pip install -r requirements.txt
   ```

3. 检查端口占用
   ```bash
   # Windows
   netstat -ano | findstr :9001

   # Linux/macOS
   lsof -i :9001
   ```

### 问题 2: Claude Code 不识别 Skill

**症状**: Claude 没有自动加载 vectcut-api skill

**解决方案**:

1. 检查 skill 目录位置
   ```bash
   # 应该在以下路径
   ~/.claude/skills/public/vectcut-api/
   # 或
   %USERPROFILE%\.claude\skills\public\vectcut-api\
   ```

2. 检查 SKILL.md 格式
   - 确保 YAML frontmatter 正确
   - 确保 name 和 description 字段存在

3. 重启 Claude Code

### 问题 3: API 请求失败

**症状**: 客户端返回错误

**解决方案**:

1. 检查 VectCutAPI 服务是否运行
   ```bash
   curl http://localhost:9001/
   ```

2. 检查网络连接
   ```bash
   ping localhost
   ```

3. 查看服务日志

### 问题 4: 草稿文件无法导入剪映

**症状**: 生成的草稿文件在剪映中看不到

**解决方案**:

1. 确认剪映/CapCut 草稿目录位置：
   - **Windows**: `C:\Users\用户名\AppData\Local\JianyingPro\User Data\Projects\`
   - **Mac**: `~/Movies/JianyingPro/User Data/Projects/`

2. 将 `dfd_xxxxx` 文件夹复制到上述目录

3. 重启剪映/CapCut

---

## 最佳实践

### 1. 时间轴管理

```python
# 好的做法：使用变量跟踪时间
current_time = 0
duration_per_clip = 5

for clip in clips:
    client.add_video(
        draft.draft_id,
        clip["url"],
        target_start=current_time,
        end=duration_per_clip
    )
    current_time += duration_per_clip

# 避免：硬编码时间
client.add_video(draft.draft_id, clip1, target_start=0)
client.add_video(draft.draft_id, clip2, target_start=5)
client.add_video(draft.draft_id, clip3, target_start=10)
```

### 2. 错误处理

```python
# 好的做法：完整的错误处理
try:
    draft = client.create_draft()
    if not client.add_video(draft.draft_id, video_url):
        print(f"添加视频失败: {video_url}")
except Exception as e:
    print(f"发生错误: {e}")
```

### 3. 资源清理

```python
# 好的做法：使用上下文管理器
with VectCutClient() as client:
    # 操作...
    pass  # 自动关闭连接

# 避免：忘记关闭
client = VectCutClient()
# 操作...
# 忘记调用 client.close()
```

### 4. 配置管理

```python
# 好的做法：使用配置文件
import json

with open("video_config.json") as f:
    config = json.load(f)

client = VectCutClient(config["api_url"])
draft = client.create_draft(
    width=config["width"],
    height=config["height"]
)
```

### 5. 批量操作

```python
# 好的做法：批量添加素材
texts = [
    {"text": "标题", "size": 64, "color": "#FFD700"},
    {"text": "副标题", "size": 48, "color": "#FFFFFF"},
    {"text": "说明", "size": 36, "color": "#CCCCCC"}
]

for text_config in texts:
    client.add_text(draft.draft_id, **text_config)
```

---

## 进阶技巧

### 1. 使用预检查

```python
# 在创建视频前检查媒体时长
duration = client.get_duration(media_url)
if duration:
    print(f"媒体时长: {duration} 秒")
else:
    print("无法获取媒体时长")
```

### 2. 动态参数构建

```python
# 根据条件动态构建参数
video_params = {
    "draft_id": draft.draft_id,
    "video_url": video_url
}

if needs_transition:
    video_params["transition"] = "fade_in"
    video_params["transition_duration"] = 0.8

if needs_volume_adjust:
    video_params["volume"] = 0.5

client.add_video(**video_params)
```

### 3. 创建可复用的函数

```python
def create_title_card(client, draft_id, title, subtitle=""):
    """创建标题卡片"""
    client.add_text(
        draft.draft_id,
        title,
        start=0,
        end=3,
        font_size=64,
        font_color="#FFD700",
        shadow_enabled=True
    )

    if subtitle:
        client.add_text(
            draft.draft_id,
            subtitle,
            start=1,
            end=4,
            font_size=36,
            pos_y=0.1
        )

# 使用
create_title_card(client, draft.draft_id, "主标题", "副标题")
```

---

## 相关资源

- [VectCutAPI GitHub](https://github.com/sun-guannan/VectCutAPI)
- [API 参考文档](skill/references/api_reference.md)
- [工作流示例](skill/references/workflows.md)
- [技术架构](ARCHITECTURE.md)
