# ⚠️ 提交说明 / PR Notice
> [!IMPORTANT]
>
> - 请提供**人工撰写**的简洁摘要，避免直接粘贴未经整理的 AI 输出。

## 📝 变更描述 / Description
(简述：做了什么？为什么这样改能生效？请基于你对代码逻辑的理解来写，避免粘贴未经整理的内容)

## 🚀 变更类型 / Type of change
- [ ] 🐛 Bug 修复 (Bug fix) - *请关联对应 Issue，避免将设计取舍、理解偏差或预期不一致直接归类为 bug*
- [ ] ✨ 新功能 (New feature) - *重大特性建议先通过 Issue 沟通*
- [ ] ⚡ 性能优化 / 重构 (Refactor)
- [ ] 📝 文档更新 (Documentation)

## 🔗 关联任务 / Related Issue
- Closes # (如有)

## ✅ 提交前检查项 / Checklist
- [ ] **人工确认:** 我已亲自整理并撰写此描述，没有直接粘贴未经处理的 AI 输出。
- [ ] **非重复提交:** 我已搜索 `dramaclaw-gateway` 现有的 [Issues](https://github.com/dramaclaw/dramaclaw-gateway/issues) 与 [PRs](https://github.com/dramaclaw/dramaclaw-gateway/pulls)，确认不是重复提交。
- [ ] **Bug fix 说明:** 若此 PR 标记为 `Bug fix`，我已提交或关联对应 Issue，且不会将设计取舍、预期不一致或理解偏差直接归类为 bug。
- [ ] **变更理解:** 我已理解这些更改的工作原理及可能影响。
- [ ] **范围聚焦:** 本 PR 未包含任何与当前任务无关的代码改动。
- [ ] **本地验证:** 已在本地运行并通过测试或手动验证，维护者可以据此复核结果。
- [ ] **安全合规:** 代码中无敏感凭据，且符合项目代码规范。

## 📸 运行证明 / Proof of Work
(请在此粘贴截图、关键日志或测试报告，以证明变更生效)

## 🔌 渠道适配检查 / Provider Adapter Checklist
<!-- 非渠道或模型适配 PR 可删除本节。Remove this section when the PR is not a provider/model adapter. -->
- [ ] 已记录官方接口文档、上游模型 ID、能力限制和验证日期。
- [ ] 已声明渠道级 capabilities，且未依赖模型名称猜测协议能力。
- [ ] 已覆盖 DC-Media 字段转换、素材角色和不支持组合。
- [ ] 异步任务已覆盖任务 ID、状态、错误、结果读取及取消语义。
- [ ] 已更新 `docs/providers/` 支持矩阵和供应商说明。
- [ ] 已使用真实供应商账号及 DramaClaw 完成脱敏端到端验证。
