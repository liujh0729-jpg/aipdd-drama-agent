# 贡献指南

感谢您对 VectCutAPI Skill 项目的关注！我们欢迎各种形式的贡献。

---

## 目录

1. [如何贡献](#如何贡献)
2. [开发环境设置](#开发环境设置)
3. [代码规范](#代码规范)
4. [提交规范](#提交规范)
5. [Pull Request 流程](#pull-request-流程)

---

## 如何贡献

### 贡献类型

我们欢迎以下类型的贡献：

- **Bug 修复** - 修复已知问题
- **新功能** - 添加新特性
- **文档改进** - 完善文档
- **代码优化** - 性能优化或代码重构
- **示例代码** - 添加新的使用示例
- **测试用例** - 添加或改进测试
- **问题反馈** - 报告 bug 或提出建议

### 开始贡献之前

1. 检查 [Issues](https://github.com/your-username/vectcut-skill/issues) 是否已有类似问题
2. 如果是大型功能，先创建 Issue 讨论设计方案
3. Fork 本项目到你的 GitHub 账号

---

## 开发环境设置

### 1. Fork 和克隆

```bash
# Fork 项目到你的账号
# 然后克隆你的 fork
git clone https://github.com/your-username/vectcut-skill.git
cd vectcut-skill
```

### 2. 设置上游仓库

```bash
# 添加上游仓库
git remote add upstream https://github.com/original-username/vectcut-skill.git

# 验证远程仓库
git remote -v
```

### 3. 创建虚拟环境

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate

# Linux/macOS:
source venv/bin/activate
```

### 4. 安装开发依赖

```bash
# 安装 VectCutAPI 依赖
pip install -r requirements.txt

# 安装开发工具
pip install pytest black flake8 mypy
```

---

## 代码规范

### Python 代码风格

我们遵循 [PEP 8](https://pep8.org/) 代码风格指南：

#### 1. 格式化

使用 [Black](https://black.readthedocs.io/) 进行代码格式化：

```bash
# 格式化代码
black skill/scripts/

# 检查格式
black --check skill/scripts/
```

#### 2. 代码检查

使用 [Flake8](https://flake8.pycqa.org/) 进行代码检查：

```bash
# 检查代码
flake8 skill/scripts/
```

#### 3. 类型注解

使用 [Mypy](https://mypy-lang.org/) 进行类型检查：

```bash
# 类型检查
mypy skill/scripts/vectcut_client.py
```

### 命名规范

- **类名**: PascalCase (例: `VectCutClient`)
- **函数/方法**: snake_case (例: `create_draft`)
- **常量**: UPPER_SNAKE_CASE (例: `MAX_RETRIES`)
- **私有方法**: _leading_underscore (例: `_post`)

### 文档字符串

使用 Google 风格的文档字符串：

```python
def create_draft(self, width: int = 1080, height: int = 1920) -> DraftInfo:
    """
    创建新草稿

    Args:
        width: 视频宽度，默认 1080
        height: 视频高度，默认 1920

    Returns:
        DraftInfo: 草稿信息对象

    Raises:
        Exception: 创建失败时抛出异常

    Example:
        >>> client = VectCutClient()
        >>> draft = client.create_draft(1080, 1920)
        >>> print(draft.draft_id)
    """
```

### 文档规范

#### Markdown 文档

- 使用清晰的标题结构
- 代码块指定语言
- 添加适当的表格
- 使用列表提高可读性

#### SKILL.md 规范

- 保持在 500 行以内
- 只包含核心流程
- 详细内容放入 references/

---

## 提交规范

### Commit Message 格式

我们使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
<类型>(<范围>): <简短描述>

<详细描述>

<关闭的 Issue>
```

### 类型

- **feat**: 新功能
- **fix**: Bug 修复
- **docs**: 文档更新
- **style**: 代码格式调整
- **refactor**: 代码重构
- **perf**: 性能优化
- **test**: 测试相关
- **chore**: 构建/工具链相关

### 示例

```bash
# 新功能
git commit -m "feat(client): add async support for API calls"

# Bug 修复
git commit -m "fix(client): handle timeout errors properly"

# 文档更新
git commit -m "docs(readme): add installation instructions for macOS"

# 代码重构
git commit -m "refactor(client): simplify error handling logic"
```

---

## Pull Request 流程

### 1. 同步上游代码

```bash
# 获取上游更新
git fetch upstream

# 合并上游主分支
git checkout main
git merge upstream/main
```

### 2. 创建功能分支

```bash
# 创建新分支
git checkout -b feature/your-feature-name

# 或修复分支
git checkout -b fix/your-bug-fix
```

### 3. 进行开发

```bash
# 进行你的修改
# ... 编写代码 ...

# 添加修改的文件
git add <files>

# 提交修改
git commit -m "feat: add your feature description"
```

### 4. 推送到你的 fork

```bash
git push origin feature/your-feature-name
```

### 5. 创建 Pull Request

1. 访问你的 fork 页面
2. 点击 "Compare & pull request"
3. 填写 PR 描述模板
4. 提交 PR

### PR 标题格式

```
[类型] 简短描述
```

示例：
```
[Feat] 添加异步请求支持
[Fix] 修复超时处理问题
[Docs] 更新安装指南
```

### PR 描述模板

```markdown
## 变更类型
- [ ] Bug 修复
- [ ] 新功能
- [ ] 代码重构
- [ ] 文档更新
- [ ] 性能优化

## 变更说明
<!-- 描述你的变更内容 -->

## 测试
- [ ] 已添加单元测试
- [ ] 已通过现有测试
- [ ] 已手动测试

## 关联 Issue
<!-- 关联的 Issue 编号，如: Closes #123 -->
```

---

## 代码审查

### 审查标准

我们会在以下方面审查你的 PR：

1. **代码质量**
   - 遵循代码规范
   - 适当的错误处理
   - 清晰的变量命名

2. **功能完整性**
   - 实现了描述的功能
   - 没有引入新的 bug
   - 向后兼容

3. **文档**
   - 更新了相关文档
   - 添加了代码注释
   - 更新了 CHANGELOG

4. **测试**
   - 添加了相应的测试
   - 测试覆盖率充足

### 反馈处理

- 及时响应审查意见
- 进行必要的修改
- 保持友好的沟通

---

## 发布流程

### 版本号更新

```bash
# 更新版本号
# 在 skill/scripts/vectcut_client.py 中修改 __version__
__version__ = "1.1.0"
```

### 更新 CHANGELOG

在 CHANGELOG.md 中添加新版本内容。

### 创建发布标签

```bash
# 创建标签
git tag -a v1.1.0 -m "Release v1.1.0"

# 推送标签
git push upstream v1.1.0
```

---

## 社区规范

### 行为准则

- 尊重所有贡献者
- 保持友好和专业的沟通
- 接受建设性的批评
- 关注对社区最有利的事情

### 沟通渠道

- **GitHub Issues**: 问题反馈和功能讨论
- **GitHub Discussions**: 一般性讨论
- **Pull Requests**: 代码审查

---

## 获取帮助

如果你在贡献过程中遇到问题：

1. 查看 [文档](docs/)
2. 搜索已有的 [Issues](https://github.com/your-username/vectcut-skill/issues)
3. 创建新的 Issue 寻求帮助

---

## 许可证

贡献的代码将采用与本项目相同的 [MIT License](LICENSE)。

---

## 致谢

感谢所有贡献者！

你的贡献让这个项目变得更好！
