# 更新日志 (Changelog)

本文档记录 VectCutAPI Skill 项目的所有重要更改。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [1.0.0] - 2025-01-25

### 新增

#### 核心功能
- 完整的 VectCutAPI Skill 封装
- Python 客户端库 `vectcut_client.py`
- 支持 35+ 个 HTTP API 端点
- 支持 11 个 MCP 工具

#### 文档
- SKILL.md 主文档 (11KB)
- API 参考文档 `api_reference.md` (13KB)
- 工作流示例 `workflows.md` (17KB)
- 技术架构文档 `ARCHITECTURE.md`
- 使用指南 `USAGE.md`
- 安装指南 `INSTALLATION.md`
- 中英文 README

#### Python 客户端功能
- VectCutClient 核心类
- 预设枚举类型 (Resolution, Transition, TextAnimation)
- 数据类 (DraftInfo, ApiResult)
- 上下文管理器支持
- 完整的错误处理

#### 工作流示例
- 基础视频制作
- AI 文字转视频
- 视频混剪
- 带字幕的视频制作
- 关键帧动画
- 产品介绍视频
- 分屏效果
- 图片轮播

#### 项目配置
- MIT License
- .gitignore 配置
- 完整的项目目录结构

---

## [未来计划]

### v1.1.0 (计划中)
- [ ] 添加单元测试
- [ ] 添加 CLI 工具
- [ ] 支持异步请求
- [ ] 添加更多预设值

### v1.2.0 (计划中)
- [ ] Web UI 界面
- [ ] 配置文件支持
- [ ] 插件系统
- [ ] 云端部署方案

---

## 版本说明

### 版本号格式

- **主版本号**: 不兼容的 API 变更
- **次版本号**: 向下兼容的功能新增
- **修订号**: 向下兼容的问题修复

### 变更类型

- **新增** - 新增功能
- **变更** - 功能变更
- **弃用** - 即将移除的功能
- **移除** - 已移除的功能
- **修复** - 问题修复
- **安全** - 安全相关修复

---

## 致谢

感谢以下项目对本项目的支持：

- [VectCutAPI](https://github.com/sun-guannan/VectCutAPI) - 核心视频编辑 API
- [Claude Code](https://claude.com/claude-code) - Anthropic 官方 CLI 工具
- [Anthropic](https://www.anthropic.com) - AI 技术支持

---

[1.0.0]: https://github.com/your-username/vectcut-skill/releases/tag/v1.0.0
