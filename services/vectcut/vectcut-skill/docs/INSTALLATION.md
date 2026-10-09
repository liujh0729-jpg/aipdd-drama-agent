# 安装指南

本文档提供 VectCutAPI Skill 的详细安装说明。

---

## 目录

1. [系统要求](#系统要求)
2. [安装 VectCutAPI](#安装-vectcutapi)
3. [安装 Skill](#安装-skill)
4. [验证安装](#验证安装)
5. [卸载](#卸载)

---

## 系统要求

### 必需组件

| 组件 | 最低版本 | 推荐版本 | 下载链接 |
|------|----------|----------|----------|
| Python | 3.10 | 3.11+ | [python.org](https://www.python.org/downloads/) |
| Claude Code | 最新版 | 最新版 | [claude.com](https://claude.com/claude-code) |
| 剪映/CapCut | 最新版 | 最新版 | [capcut.com](https://www.capcut.com/) |

### 可选组件

| 组件 | 用途 | 下载链接 |
|------|------|----------|
| FFmpeg | 视频处理 | [ffmpeg.org](https://ffmpeg.org/download.html) |
| Git | 版本控制 | [git-scm.com](https://git-scm.com/downloads) |

### 操作系统支持

- **Windows**: Windows 10/11 (推荐)
- **macOS**: macOS 11+ (Big Sur 或更高)
- **Linux**: Ubuntu 20.04+, Debian 11+, CentOS 8+

---

## 安装 VectCutAPI

VectCutAPI 是本技能依赖的核心服务。

### Windows 安装

#### 步骤 1: 安装 Python

1. 访问 [python.org](https://www.python.org/downloads/)
2. 下载 Python 3.10 或更高版本
3. 运行安装程序，**务必勾选 "Add Python to PATH"**
4. 验证安装：
   ```cmd
   python --version
   ```

#### 步骤 2: 克隆项目

```cmd
# 使用 Git (推荐)
git clone https://github.com/sun-guannan/VectCutAPI.git
cd VectCutAPI

# 或直接下载 ZIP
# https://github.com/sun-guannan/VectCutAPI/archive/refs/heads/main.zip
```

#### 步骤 3: 创建虚拟环境

```cmd
# 创建虚拟环境
python -m venv venv-vectcut

# 激活虚拟环境
venv-vectcut\Scripts\activate

# 验证激活 (命令行前缀应显示 (venv-vectcut))
```

#### 步骤 4: 安装依赖

```cmd
# 升级 pip
python -m pip install --upgrade pip

# 安装基础依赖
pip install -r requirements.txt

# 安装 MCP 支持 (可选)
pip install -r requirements-mcp.txt
```

#### 步骤 5: 配置

```cmd
# 复制配置文件
copy config.json.example config.json

# 使用记事本编辑配置
notepad config.json
```

根据需要修改配置项：

```json
{
  "is_capcut_env": true,
  "draft_domain": "https://www.capcutapi.top",
  "port": 9001,
  "preview_router": "/draft/downloader",
  "is_upload_draft": false
}
```

#### 步骤 6: 启动服务

```cmd
# 启动 HTTP API 服务器
python capcut_server.py

# 服务将在 http://localhost:9001 启动
```

### macOS/Linux 安装

#### 步骤 1: 安装 Python

**macOS:**
```bash
# 使用 Homebrew (推荐)
brew install python@3.11

# 或从官网下载安装包
# https://www.python.org/downloads/
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip
```

**CentOS/RHEL:**
```bash
sudo yum install python311 python311-pip
```

#### 步骤 2: 克隆项目

```bash
git clone https://github.com/sun-guannan/VectCutAPI.git
cd VectCutAPI
```

#### 步骤 3: 创建虚拟环境

```bash
# 创建虚拟环境
python3 -m venv venv-vectcut

# 激活虚拟环境
source venv-vectcut/bin/activate
```

#### 步骤 4: 安装依赖

```bash
# 升级 pip
python -m pip install --upgrade pip

# 安装基础依赖
pip install -r requirements.txt

# 安装 MCP 支持 (可选)
pip install -r requirements-mcp.txt
```

#### 步骤 5: 配置

```bash
# 复制配置文件
cp config.json.example config.json

# 编辑配置
nano config.json  # 或使用 vim、其他编辑器
```

#### 步骤 6: 启动服务

```bash
# 启动 HTTP API 服务器
python capcut_server.py

# 服务将在 http://localhost:9001 启动
```

---

## 安装 Skill

### Windows 安装

#### 方法 1: 使用 Git (推荐)

```powershell
# 克隆项目
git clone https://github.com/your-username/vectcut-skill.git
cd vectcut-skill

# 复制 skill 文件
Copy-Item -Path "skill\*" -Destination "$env:USERPROFILE\.claude\skills\public\vectcut-api\" -Recurse -Force
```

#### 方法 2: 手动复制

1. 下载项目的 ZIP 文件
2. 解压到任意目录
3. 手动复制 `skill` 文件夹内容到：
   ```
   C:\Users\你的用户名\.claude\skills\public\vectcut-api\
   ```

#### 方法 3: 使用 CMD

```cmd
xcopy "skill\*" "%USERPROFILE%\.claude\skills\public\vectcut-api\" /E /I /Y
```

### macOS/Linux 安装

```bash
# 克隆项目
git clone https://github.com/your-username/vectcut-skill.git
cd vectcut-skill

# 复制 skill 文件
cp -r skill/* ~/.claude/skills/public/vectcut-api/

# 或创建符号链接 (推荐)
ln -s $(pwd)/skill ~/.claude/skills/public/vectcut-api
```

---

## 验证安装

### 1. 验证 VectCutAPI 服务

```bash
# 使用 curl
curl http://localhost:9001/

# 或使用浏览器访问
# http://localhost:9001/
```

应该看到 API 文档页面。

### 2. 验证 Skill 文件

```bash
# Windows
dir %USERPROFILE%\.claude\skills\public\vectcut-api

# Linux/macOS
ls -la ~/.claude/skills/public/vectcut-api
```

应该看到以下文件：
```
SKILL.md
scripts/
  └── vectcut_client.py
references/
  ├── api_reference.md
  └── workflows.md
assets/
  └── examples/
```

### 3. 验证 Claude Code 集成

1. 启动 Claude Code
2. 输入测试命令：
   ```
   使用 vectcut-api skill 创建一个视频草稿
   ```
3. Claude 应该自动识别并加载技能

### 4. 运行测试脚本

创建测试文件 `test_installation.py`:

```python
from skill.scripts.vectcut_client import VectCutClient

def test_installation():
    """测试安装是否成功"""
    print("测试 VectCutAPI 连接...")

    try:
        # 创建客户端
        client = VectCutClient("http://localhost:9001")

        # 测试创建草稿
        draft = client.create_draft(width=1080, height=1920)
        print(f"✓ 草稿创建成功: {draft.draft_id}")

        # 测试保存草稿
        result = client.save_draft(draft.draft_id)
        print(f"✓ 草稿保存成功: {result.draft_url}")

        print("\n✅ 安装验证成功！")
        return True

    except Exception as e:
        print(f"\n❌ 安装验证失败: {e}")
        return False

if __name__ == "__main__":
    test_installation()
```

运行测试：
```bash
python test_installation.py
```

---

## 卸载

### Windows 卸载

#### 卸载 Skill

```cmd
# 删除 skill 目录
rmdir /s /q %USERPROFILE%\.claude\skills\public\vectcut-api
```

#### 卸载 VectCutAPI

```cmd
# 停止服务 (Ctrl+C)

# 激活虚拟环境
venv-vectcut\Scripts\activate

# 卸载依赖
pip freeze | xargs pip uninstall -y

# 退出虚拟环境
deactivate

# 删除项目目录
rmdir /s /q VectCutAPI
```

### macOS/Linux 卸载

#### 卸载 Skill

```bash
# 删除 skill 目录
rm -rf ~/.claude/skills/public/vectcut-api
```

#### 卸载 VectCutAPI

```bash
# 停止服务 (Ctrl+C)

# 激活虚拟环境
source venv-vectcut/bin/activate

# 卸载依赖
pip freeze | xargs pip uninstall -y

# 退出虚拟环境
deactivate

# 删除项目目录
rm -rf VectCutAPI
```

---

## 常见问题

### Q1: Python 版本不兼容

**问题**: `Python version error`

**解决方案**:
```bash
# 检查 Python 版本
python --version

# 如果版本低于 3.10，请升级 Python
```

### Q2: 端口 9001 被占用

**问题**: `Port 9001 already in use`

**解决方案**:
```bash
# Windows - 查找占用端口的进程
netstat -ano | findstr :9001

# 终止进程 (使用 PID)
taskkill /PID <PID> /F

# 或修改 config.json 中的端口
```

### Q3: pip 安装依赖失败

**问题**: `pip install failed`

**解决方案**:
```bash
# 升级 pip
python -m pip install --upgrade pip

# 使用国内镜像源
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q4: Claude Code 无法找到 Skill

**问题**: `Skill not found`

**解决方案**:
1. 检查 skill 目录路径是否正确
2. 确认 SKILL.md 文件存在且格式正确
3. 重启 Claude Code

### Q5: 虚拟环境激活失败

**问题**: `venv activation failed`

**解决方案**:
```bash
# Windows - 以管理员身份运行 PowerShell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 重新激活
venv-vectcut\Scripts\Activate.ps1
```

---

## 下一步

安装完成后，建议查看：

1. [使用指南](USAGE.md) - 学习如何使用
2. [工作流示例](skill/references/workflows.md) - 查看实际案例
3. [API 参考](skill/references/api_reference.md) - 了解完整 API

---

## 获取帮助

如果遇到问题：

1. 查看 [故障排除](USAGE.md#故障排除) 部分
2. 搜索 [Issues](https://github.com/your-username/vectcut-skill/issues)
3. 提交新的 Issue
4. 联系原项目 [VectCutAPI](https://github.com/sun-guannan/VectCutAPI)
