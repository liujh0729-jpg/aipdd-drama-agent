# VectCutAPI Skill 技术架构文档

## 概述

本文档详细说明 VectCutAPI Skill 的技术架构、设计原则和实现细节。

---

## 项目架构

### 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         Claude Code                              │
│                    (Anthropic CLI Tool)                          │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Skill System                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  vectcut-api Skill                                        │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │  │
│  │  │   SKILL.md   │  │  scripts/    │  │ references/  │   │  │
│  │  │  (主文档)     │  │ (可执行代码) │  │  (参考文档)  │   │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘   │  │
│  └──────────────────────────────────────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      VectCutAPI                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  HTTP API Server (capcut_server.py)                      │  │
│  │  Port: 9001                                              │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  pyJianYingDraft (剪映草稿核心库)                         │  │
│  │  - 草稿管理                                               │  │
│  │  - 轨道操作                                               │  │
│  │  - 片段处理                                               │  │
│  └──────────────────────────────────────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    剪映 / CapCut                                 │
│                    (视频编辑应用)                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Skill 结构设计

### Claude Code Skill 规范

本项目严格遵循 Anthropic 官方的 Skill 规范：

#### 1. 必需文件

```
skill/
├── SKILL.md              # 必需 - 技能主文档
└── [资源目录]            # 可选 - scripts/, references/, assets/
```

#### 2. SKILL.md 格式

SKILL.md 使用 YAML frontmatter 定义元数据：

```markdown
---
name: vectcut-api
description: VectCutAPI is a powerful cloud-based video editing API...
---

# 技能内容...
```

**元数据字段说明:**
- `name`: 技能标识符（用于触发技能）
- `description`: 技能描述（Claude 用于判断何时使用此技能）

#### 3. 渐进式披露设计

为了优化 token 使用，采用三级加载系统：

| 级别 | 内容 | 大小限制 | 加载时机 |
|------|------|----------|----------|
| **Metadata** | name + description | ~100 tokens | 始终加载 |
| **SKILL.md body** | 核心使用指南 | <5k tokens | 技能触发时 |
| **Bundled Resources** | scripts/references/assets | 无限制 | 按需加载 |

---

## Python 客户端设计

### 设计原则

1. **简洁性** - 提供直观的 API 接口
2. **类型安全** - 使用 dataclasses 和 Enum
3. **资源管理** - 支持上下文管理器
4. **错误处理** - 统一的错误处理机制

### 类结构

```python
# 数据类
@dataclass
class DraftInfo:
    """草稿信息"""
    draft_id: str
    draft_folder: Optional[str] = None
    draft_url: Optional[str] = None

@dataclass
class ApiResult:
    """API 响应结果"""
    success: bool
    output: Dict[str, Any]
    error: Optional[str] = None

# 枚举类
class Resolution(Enum):
    """常用视频分辨率预设"""
    VERTICAL = (1080, 1920)
    HORIZONTAL = (1920, 1080)
    SQUARE = (1080, 1080)

class Transition(Enum):
    """转场效果类型"""
    FADE_IN = "fade_in"
    FADE_OUT = "fade_out"

# 主客户端类
class VectCutClient:
    """VectCutAPI Python 客户端"""

    def __init__(self, base_url: str, timeout: int = 120)
    def create_draft(...) -> DraftInfo
    def save_draft(...) -> DraftInfo
    def add_video(...) -> bool
    def add_audio(...) -> bool
    def add_text(...) -> bool
    # ... 更多方法

    def __enter__(self)
    def __exit__(self, exc_type, exc_val, exc_tb)
```

### 方法设计模式

#### 1. 创建型方法

```python
def create_draft(self, width: int = 1080, height: int = 1920) -> DraftInfo:
    """
    创建新草稿

    Args:
        width: 视频宽度
        height: 视频高度

    Returns:
        DraftInfo: 草稿信息对象

    Raises:
        Exception: 创建失败时抛出异常
    """
```

#### 2. 操作型方法

```python
def add_video(self,
             draft_id: str,
             video_url: str,
             start: float = 0,
             # ... 更多参数
             **kwargs) -> bool:
    """
    添加视频轨道

    Args:
        draft_id: 草稿 ID
        video_url: 视频 URL
        ...

    Returns:
        bool: 操作是否成功
    """
```

#### 3. 查询型方法

```python
def get_duration(self, media_url: str) -> Optional[float]:
    """
    获取媒体文件时长

    Args:
        media_url: 媒体 URL

    Returns:
        Optional[float]: 时长(秒)，失败返回 None
    """
```

---

## 文档组织结构

### 1. SKILL.md (主文档)

**内容组织:**
- Overview - 技能概述
- System Requirements - 系统要求
- Quick Start - 快速开始
- Workflow - 标准工作流程
- API Interfaces - API 接口列表
- Usage Examples - 使用示例
- MCP Integration - MCP 协议集成
- Parameters - 参数说明

**设计原则:**
- 保持在 500 行以内
- 只包含核心流程和必要信息
- 详细内容放入 references/

### 2. references/ (参考文档)

**api_reference.md** - 完整 API 参考
- 所有 HTTP 端点的详细说明
- 请求参数和响应格式
- 错误处理说明
- 参数类型和默认值

**workflows.md** - 工作流示例
- 8+ 种常见场景的完整代码
- 每个示例包含详细注释
- 最佳实践说明

### 3. scripts/ (可执行代码)

**vectcut_client.py** - Python 客户端
- 可直接导入使用
- 可作为独立脚本运行
- 包含完整的类型注解

---

## API 映射关系

### HTTP API → 客户端方法映射

| HTTP API | 客户端方法 | 说明 |
|----------|-----------|------|
| POST /create_draft | `create_draft()` | 创建草稿 |
| POST /save_draft | `save_draft()` | 保存草稿 |
| POST /add_video | `add_video()` | 添加视频 |
| POST /add_audio | `add_audio()` | 添加音频 |
| POST /add_image | `add_image()` | 添加图片 |
| POST /add_text | `add_text()` | 添加文字 |
| POST /add_subtitle | `add_subtitle()` | 添加字幕 |
| POST /add_sticker | `add_sticker()` | 添加贴纸 |
| POST /add_effect | `add_effect()` | 添加特效 |
| POST /add_video_keyframe | `add_video_keyframe()` | 添加关键帧 |
| POST /get_duration | `get_duration()` | 获取时长 |
| GET /get_*_types | `get_*_types()` | 获取类型列表 |

---

## 数据流设计

### 视频制作数据流

```
用户请求
    │
    ▼
Claude 解析意图
    │
    ▼
加载 vectcut-api Skill
    │
    ▼
调用 Python 客户端
    │
    ▼
┌─────────────────────────────────────────┐
│ VectCutClient 方法调用                  │
│  ┌───────────────────────────────────┐ │
│  │ 1. create_draft()                 │ │
│  │ 2. add_video()                    │ │
│  │ 3. add_audio()                    │ │
│  │ 4. add_text()                     │ │
│  │ 5. save_draft()                   │ │
│  └───────────────────────────────────┘ │
└─────────────────┬───────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────┐
│ HTTP 请求 (requests 库)                 │
│  POST http://localhost:9001/...        │
└─────────────────┬───────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────┐
│ VectCutAPI Server                       │
│  Flask HTTP Server                      │
└─────────────────┬───────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────┐
│ pyJianYingDraft                         │
│  生成剪映草稿文件                        │
└─────────────────┬───────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────┐
│ 草稿文件 (dfd_xxxxx/)                   │
│  - draft_content.json                   │
│  - material_*.json                      │
└─────────────────────────────────────────┘
```

---

## 错误处理机制

### 1. API 响应处理

```python
def _post(self, endpoint: str, **kwargs) -> ApiResult:
    """发送 POST 请求"""
    try:
        response = self.session.post(url, json=kwargs, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        return ApiResult(
            success=data.get("success", False),
            output=data.get("output", {}),
            error=data.get("error")
        )
    except requests.RequestException as e:
        return ApiResult(success=False, output={}, error=str(e))
```

### 2. 方法级别错误处理

```python
def create_draft(self, ...) -> DraftInfo:
    result = self._post("/create_draft", ...)
    if result.success:
        return DraftInfo(...)
    raise Exception(f"创建草稿失败: {result.error}")
```

---

## 扩展性设计

### 1. 预设值扩展

通过 Enum 类型轻松添加新的预设：

```python
class Resolution(Enum):
    VERTICAL = (1080, 1920)
    HORIZONTAL = (1920, 1080)
    SQUARE = (1080, 1080)
    # 新增
    WIDE = (1920, 1200)
```

### 2. 自定义参数

所有方法支持 `**kwargs` 传递额外参数：

```python
client.add_video(draft_id, video_url, custom_param="value")
```

### 3. 工作流复用

示例代码可作为模板快速复用：

```python
# 从 workflows.md 复制模板
# 修改参数即可使用
```

---

## 性能优化

### 1. 连接复用

```python
self.session = requests.Session()  # 复用 TCP 连接
```

### 2. 按需加载

- SKILL.md 保持精简
- 详细文档按需加载到 references/

### 3. 异步支持 (未来)

可扩展支持异步请求：

```python
async def add_video_async(self, ...):
    async with aiohttp.ClientSession() as session:
        ...
```

---

## 安全考虑

### 1. URL 验证

客户端不验证 URL，由 VectCutAPI 服务端负责

### 2. 超时控制

默认 120 秒超时，防止长时间阻塞

### 3. 资源管理

支持上下文管理器确保资源释放：

```python
with VectCutClient() as client:
    # 自动关闭连接
    ...
```

---

## 测试策略

### 1. 单元测试 (计划中)

```python
def test_create_draft():
    client = VectCutClient()
    draft = client.create_draft()
    assert draft.draft_id is not None
```

### 2. 集成测试 (计划中)

```python
def test_full_workflow():
    # 测试完整的视频制作流程
    ...
```

---

## 依赖关系

```
vectcut-skill
    │
    ├── Python 3.10+
    │
    ├── requests (HTTP 库)
    │
    ├── dataclasses (Python 标准库)
    │
    ├── typing (Python 标准库)
    │
    └── VectCutAPI (外部依赖)
            │
            ├── Flask
            ├── pyJianYingDraft
            └── 剪映/CapCut
```

---

## 未来计划

### 短期目标

- [ ] 添加单元测试
- [ ] 添加更多工作流示例
- [ ] 支持异步请求
- [ ] 添加 CLI 工具

### 长期目标

- [ ] 支持更多视频平台
- [ ] Web UI 界面
- [ ] 云端部署方案
- [ ] 插件系统

---

## 贡献指南

欢迎贡献代码！请遵循以下原则：

1. 保持 SKILL.md 简洁 (<500 行)
2. 详细内容放入 references/
3. 添加完整的类型注解
4. 编写清晰的文档字符串
5. 遵循 PEP 8 代码风格

---

## 参考资料

- [Claude Code Skills Documentation](https://github.com/anthropics/claude-code-skills)
- [VectCutAPI Documentation](https://github.com/sun-guannan/VectCutAPI)
- [Python Requests Documentation](https://docs.python-requests.org/)
